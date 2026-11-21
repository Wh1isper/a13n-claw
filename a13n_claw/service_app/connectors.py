"""Connector configuration, filtering, and fan-in through the shared Inbox."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

import httpx
from a13n_logging import get_logger
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, disabled, not_found
from a13n_service.tenancy.app_plugins import policy
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.tables import OrganizationRow, WorkspaceRow
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from redis.exceptions import LockError, RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import inbox, main_thread
from .feishu import InboundMessage, LarkAdapter, LarkSettings
from .models import APP_ID, Claw, Connector

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)


class ExternalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    principal_id: str = Field(min_length=1, max_length=72)
    channels: tuple[str, ...] = Field(min_length=1, max_length=500)
    senders: tuple[str, ...] = Field(min_length=1, max_length=500)


class ExternalMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=200)
    channel: str = Field(min_length=1, max_length=200)
    sender: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=65536)
    source: dict[str, JsonValue] = Field(default_factory=dict, max_length=24)
    github_issue_number: int | None = Field(default=None, gt=0)
    github_comment_id: int | None = Field(default=None, gt=0)
    github_comment_kind: Literal["issue", "review"] = "issue"


async def find(session: AsyncSession, claw_id: str, connector_id: str) -> Connector:
    connector = await session.scalar(
        select(Connector).where(Connector.claw_id == claw_id, Connector.id == connector_id)
    )
    if connector is None:
        raise not_found("connector", connector_id)
    return connector


def location(organization_id: str, connector_id: str) -> SecretLocation:
    return SecretLocation(organization_id, Connector.__tablename__, "credential", connector_id)


def protect(
    runtime: Runtime, organization_id: str, connector_id: str, value: str
) -> dict[str, str]:
    return runtime.keys.protect(
        value.encode(), location(organization_id, connector_id)
    ).model_dump()


def credential(runtime: Runtime, organization_id: str, row: Connector) -> str | None:
    if row.credential is not None:
        return runtime.keys.reveal(
            Envelope.model_validate(row.credential), location(organization_id, row.id)
        ).decode()
    return secret(row.id)


async def command_environment(
    kind: str, connector_id: str, config: dict, token: str
) -> dict[str, str]:
    if kind == "github":
        return {"GH_TOKEN": token, "GH_HOST": "github.com", "GH_PROMPT_DISABLED": "1"}
    settings = LarkSettings.model_validate(config)
    adapter = LarkAdapter(connector_id, settings, token)
    try:
        tenant_token = await adapter.access_token()
    finally:
        await adapter.close()
    return {
        "LARKSUITE_CLI_APP_ID": settings.app_id,
        "LARKSUITE_CLI_TENANT_ACCESS_TOKEN": tenant_token,
        "LARKSUITE_CLI_BRAND": "lark" if settings.domain.endswith("larksuite.com") else "feishu",
        "LARKSUITE_CLI_DEFAULT_AS": "bot",
        "LARKSUITE_CLI_STRICT_MODE": "bot",
        "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
        "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
    }


def listener_key(connector_id: str) -> str:
    return f"claw:lark:status:{connector_id}"


async def listener_status(runtime: Runtime, connector_id: str) -> str:
    with suppress(RedisError):
        status = await runtime.redis.get(listener_key(connector_id))
        if status in (b"connected", "connected"):
            return "connected"
        if status in (b"disconnected", "disconnected"):
            return "disconnected"
    return "not observed"


async def check(kind: str, connector_id: str, config: dict, token: str) -> None:
    if kind == "lark":
        await command_environment(kind, connector_id, config, token)
        return
    settings = ExternalSettings.model_validate(config)
    async with httpx.AsyncClient(timeout=4) as client:
        # The check is read-only and bounded, even for very large allowlists.
        repo = settings.channels[0]
        owner, sep, name = repo.partition("/")
        if not (sep and owner and name and "/" not in name):
            raise ValueError("Invalid GitHub repository")
        response = await client.get(
            f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(name, safe='')}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()


def secret_path(connector_id: str) -> Path | None:
    root = os.environ.get("CLAW_SECRET_DIR")
    return Path(root) / connector_id if root else None


def secret(connector_id: str) -> str | None:
    path = secret_path(connector_id)
    if path is not None:
        with suppress(OSError):
            return path.read_text().strip() or None
    return None


def event_key(connector_id: str, event_id: str) -> str:
    return f"{connector_id}:{sha256(event_id.encode()).hexdigest()}"


async def offer_external(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    claw_id: str,
    connector_id: str,
    body: ExternalMessage,
) -> inbox.Admission:
    from a13n_service.tenancy.access import workspace_scope

    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        claw = await inbox.lock_claw(session, workspace_id, claw_id)
        connector = await find(session, claw.id, connector_id)
        if connector.kind != "github":
            raise not_found("connector", connector_id)
        settings = ExternalSettings.model_validate(connector.config)
        if actor.id != settings.principal_id or actor.kind != "service_account":
            raise not_found("connector", connector_id)
        if not connector.enabled:
            raise disabled("connector", connector.id)
        if body.channel not in settings.channels or body.sender not in settings.senders:
            raise ServiceError(
                "forbidden", "Sender or channel is not configured for this connector"
            )
        connector.last_event_at = await now(session)
        kind = connector.kind
        source = {
            **body.source,
            "connector_id": connector_id,
            "channel": body.channel,
            "sender": body.sender,
            "event_id": body.event_id,
            "platform": kind,
        }
        if kind == "github":
            source |= {
                "issue_number": body.github_issue_number,
                "comment_id": body.github_comment_id,
            }
        reply_token = None
        if kind == "github":
            with suppress(ServiceError):
                reply_token = credential(runtime, scope.organization_id, connector)
        result = await inbox.admit(
            session,
            claw,
            inbox.Offer(
                "user",
                kind,
                event_key(connector_id, body.event_id),
                source,
                body.content,
                body.content[:200],
            ),
        )
    if result.status == "accepted":
        try:
            await main_thread.notify(runtime, workspace_id, claw_id, delivery_id=result.delivery_id)
        finally:
            if kind == "github":
                await github_eyes(connector_id, body, reply_token)
    return result


async def github_eyes(connector_id: str, event: ExternalMessage, token: str | None) -> None:
    owner, sep, repo = event.channel.partition("/")
    if not (sep and owner and repo and "/" not in repo) or not token:
        return
    if event.github_comment_id is not None:
        resource = "issues" if event.github_comment_kind == "issue" else "pulls"
        target = f"{resource}/comments/{event.github_comment_id}"
    elif event.github_issue_number is not None:
        target = f"issues/{event.github_issue_number}"
    else:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                f"https://api.github.com/repos/{quote(owner, safe='')}"
                f"/{quote(repo, safe='')}/{target}/reactions",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                json={"content": "eyes"},
            )
            response.raise_for_status()
    except (httpx.HTTPError, ValueError):
        logger.warning("GitHub intake feedback failed", extra={"connector_id": connector_id})


def allows_lark(settings: LarkSettings, message: InboundMessage) -> bool:
    if message.sender_id not in settings.senders:
        return False
    if not message.is_group:
        return settings.direct_messages
    return message.chat_id in settings.groups and (
        message.mentioned or message.chat_id in settings.unmentioned_groups
    )


async def intake_enabled(session: AsyncSession, workspace_id: str, *, lock: bool = False) -> bool:
    query = (
        select(OrganizationRow.settings)
        .join(WorkspaceRow, WorkspaceRow.organization_id == OrganizationRow.id)
        .where(WorkspaceRow.id == workspace_id, WorkspaceRow.archived_at.is_(None))
    )
    if lock:
        query = query.with_for_update(read=True, of=(OrganizationRow, WorkspaceRow))
    settings = await session.scalar(query)
    return settings is not None and APP_ID in policy(settings).enabled


async def offer_lark(
    runtime: Runtime, workspace_id: str, claw_id: str, message: InboundMessage
) -> inbox.Admission | None:
    async with transaction(runtime.storage) as session:
        if not await intake_enabled(session, workspace_id, lock=True):
            return None
        claw = await inbox.lock_claw(session, workspace_id, claw_id)
        connector = await find(session, claw.id, message.connection)
        if not connector.enabled or not allows_lark(
            LarkSettings.model_validate(connector.config), message
        ):
            return None
        connector.last_event_at = await now(session)
        content = (
            message.text or "Feishu message with attachments"
            if message.attachments
            else message.text or "Feishu message"
        )
        result = await inbox.admit(
            session,
            claw,
            inbox.Offer(
                "user",
                "lark",
                event_key(connector.id, message.event_id),
                {
                    **message.model_dump(mode="json", exclude={"text", "connection"}),
                    "connector_id": connector.id,
                },
                content[:65536],
                content[:200],
            ),
        )
    if result.status == "accepted":
        await main_thread.notify(runtime, workspace_id, claw_id, delivery_id=result.delivery_id)
    return result


async def read_feedback(runtime: Runtime, workspace_id: str, claw_id: str, source: dict) -> None:
    if source.get("platform") != "lark":
        return
    connector_id = source.get("connector_id")
    if not isinstance(connector_id, str):
        return
    try:
        async with short_session(runtime.storage) as session:
            connector = await find(session, claw_id, connector_id)
            settings = LarkSettings.model_validate(connector.config)
            organization_id = await session.scalar(
                select(WorkspaceRow.organization_id).where(WorkspaceRow.id == workspace_id)
            )
            token = credential(runtime, organization_id, connector) if organization_id else None
        if connector.enabled and token:
            adapter = LarkAdapter(connector.id, settings, token)
            try:
                if isinstance(message_id := source.get("message_id"), str):
                    await adapter.react(message_id, "OK")
            finally:
                await adapter.close()
    except (ServiceError, httpx.HTTPError, ValueError):
        logger.warning("Feishu read feedback failed", extra={"connector_id": connector_id})


@dataclass(frozen=True, slots=True)
class Intake:
    organization_id: str
    workspace_id: str
    claw_id: str
    connector_id: str
    settings: LarkSettings
    token: str = field(repr=False)


class Listeners:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.tasks: dict[str, tuple[Intake, asyncio.Task[None]]] = {}

    async def close(self) -> None:
        owned = [task for _, task in self.tasks.values()]
        for task in owned:
            task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)

    async def reconcile(self, organization_id: str) -> None:
        async with short_session(self.runtime.storage) as session:
            rows = (
                await session.execute(
                    select(Connector, Claw.id, Claw.workspace_id)
                    .join(Claw, Claw.id == Connector.claw_id)
                    .join(WorkspaceRow, WorkspaceRow.id == Claw.workspace_id)
                    .where(
                        WorkspaceRow.organization_id == organization_id,
                        WorkspaceRow.archived_at.is_(None),
                        Connector.kind == "lark",
                        Connector.enabled,
                    )
                )
            ).all()
        needed = {}
        for row, claw, workspace in rows:
            try:
                token = credential(self.runtime, organization_id, row)
            except ServiceError:
                logger.warning("Connector credential unavailable", extra={"connector_id": row.id})
                continue
            if token:
                needed[row.id] = Intake(
                    organization_id,
                    workspace,
                    claw,
                    row.id,
                    LarkSettings.model_validate(row.config),
                    token,
                )
        for key, (current, task) in list(self.tasks.items()):
            if current.organization_id == organization_id and (
                task.done() or current != needed.get(key)
            ):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                del self.tasks[key]
        for key, intake in needed.items():
            if key not in self.tasks:
                self.tasks[key] = (intake, asyncio.create_task(self.listen(intake, intake.token)))

    async def listen(self, intake: Intake, token: str) -> None:
        lock = self.runtime.redis.lock(
            f"claw:lark:{intake.settings.app_id}", timeout=45, blocking=False, thread_local=False
        )
        if not await lock.acquire():
            return
        adapter = LarkAdapter(intake.connector_id, intake.settings, token)

        async def receive(event: InboundMessage) -> None:
            if not await lock.owned():
                raise LockError("Feishu listener has moved")
            result = await offer_lark(self.runtime, intake.workspace_id, intake.claw_id, event)
            if result is not None and result.status == "full":
                try:
                    await adapter.react(event.message_id, "ERROR")
                except (httpx.HTTPError, ValueError):
                    logger.warning(
                        "Feishu Inbox-full feedback failed",
                        extra={"connector_id": intake.connector_id},
                    )

        run = asyncio.create_task(adapter.run(receive))
        try:
            while not run.done():
                done, _ = await asyncio.wait((run,), timeout=15)
                if not done:
                    async with short_session(self.runtime.storage) as session:
                        if not await intake_enabled(session, intake.workspace_id):
                            return
                    await lock.extend(45, replace_ttl=True)
                    with suppress(RedisError):
                        await self.runtime.redis.set(
                            listener_key(intake.connector_id), adapter.status, ex=30
                        )
            await run
        except LockError:
            logger.warning(
                "Feishu listener lease ended", extra={"connector_id": intake.connector_id}
            )
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
            try:
                await adapter.close()
            finally:
                with suppress(LockError):
                    await lock.release()
