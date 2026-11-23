"""Connector configuration, filtering, and fan-in through the shared Inbox."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote
from uuid import uuid4

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
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import github_intake, inbox, lark_retry, main_thread
from .feishu import InboundEvent, InboundMessage, LarkAdapter, LarkSettings, reply_key
from .github_poll import GitHubSettings
from .models import APP_ID, Claw, Connector

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)
LARK_CHANGES = "claw:lark:changes"
STATUS_TTL = 30
# A replaced listener must neither update nor erase the new holder's status.
WRITE_STATUS = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return end
if ARGV[2] == '' then return redis.call('DEL', KEYS[2]) end
return redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
"""


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
    return None


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


async def reply_credentials(
    kind: str, connector_id: str, config: dict, token: str
) -> dict[str, str]:
    # Service injects the credential only into this invocation. When the
    # runtime supports broker grants, substitute the allowed-host placeholder here.
    return {
        **await command_environment(kind, connector_id, config, token),
        "GH_DEBUG": "",
        "DEBUG": "",
        "LARKSUITE_CLI_DEBUG": "",
    }


def listener_key(connector_id: str) -> str:
    return f"claw:lark:status:{connector_id}"


async def listener_status(runtime: Runtime, connector_id: str) -> str:
    with suppress(RedisError):
        status = await runtime.redis.get(listener_key(connector_id))
        if status in ("connected", "disconnected", "conflict"):
            return status
    return "not observed"


async def lark_changed(runtime: Runtime, organization_id: str) -> None:
    try:
        await runtime.redis.publish(LARK_CHANGES, organization_id)
    except RedisError:
        # Configuration is already committed. The sweep will reconcile it.
        logger.warning("Lark reconcile signal unavailable")


async def check(kind: str, connector_id: str, config: dict, token: str) -> None:
    if kind == "lark":
        await command_environment(kind, connector_id, config, token)
        return
    settings = GitHubSettings.model_validate(config)
    async with httpx.AsyncClient(timeout=4) as client:
        # The check is read-only and bounded, even for very large allowlists.
        if not settings.channels:
            response = await client.get(
                f"{settings.api_url}/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                follow_redirects=False,
            )
            response.raise_for_status()
            return
        repo = settings.channels[0]
        owner, sep, name = repo.partition("/")
        if not (sep and owner and name and "/" not in name):
            raise ValueError("Invalid GitHub repository")
        response = await client.get(
            f"{settings.api_url}/repos/{quote(owner, safe='')}/{quote(name, safe='')}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()


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
        settings = GitHubSettings.model_validate(connector.config)
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
    if (
        "im.message.receive_v1" not in settings.event_types
        or message.sender_id not in settings.senders
    ):
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
    runtime: Runtime, intake: Intake, message: InboundMessage
) -> inbox.Admission | None:
    workspace_id, claw_id = intake.workspace_id, intake.claw_id
    async with transaction(runtime.storage) as session:
        if not await intake_enabled(session, workspace_id, lock=True):
            return None
        try:
            claw = await inbox.lock_claw(session, workspace_id, claw_id)
        except inbox.ClawNotFound:
            return None
        connector = await session.get(Connector, intake.connector_id)
        if (
            connector is None
            or connector.claw_id != claw.id
            or not connector.enabled
            or connector.kind != "lark"
            or message.connection != connector.id
        ):
            return None
        settings = LarkSettings.model_validate(connector.config)
        if (
            (settings.app_id, settings.bot_open_id, settings.domain)
            != (intake.settings.app_id, intake.settings.bot_open_id, intake.settings.domain)
            or credential(runtime, intake.organization_id, connector) != intake.token
            or not allows_lark(settings, message)
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
                    "reply_in_thread": bool(message.topic_id),
                    "idempotency_key": reply_key(message.event_id),
                },
                content[:65536],
                content[:200],
                volatile_source_fields=(
                    "previous_messages_snapshot",
                    "reply_in_thread",
                    "idempotency_key",
                ),
                legacy_optional_source_fields=("root_id", "create_time"),
            ),
        )
    if result.status == "accepted":
        await main_thread.notify(runtime, workspace_id, claw_id, delivery_id=result.delivery_id)
    return result


def allows_lark_event(settings: LarkSettings, event: InboundEvent) -> bool:
    if (
        event.event_type not in settings.event_types
        or event.sender_id not in settings.senders
        or event.sender_id == settings.bot_open_id
    ):
        return False
    if event.event_type == "drive.notice.comment_add_v1":
        return event.chat_id.startswith("drive/")
    return event.chat_id in settings.groups


async def offer_lark_event(
    runtime: Runtime, intake: Intake, event: InboundEvent
) -> inbox.Admission | None:
    content = json.dumps(event.payload, ensure_ascii=False, separators=(",", ":"))
    if len(content) > 65536:
        return None
    async with transaction(runtime.storage) as session:
        if not await intake_enabled(session, intake.workspace_id, lock=True):
            return None
        try:
            claw = await inbox.lock_claw(session, intake.workspace_id, intake.claw_id)
        except inbox.ClawNotFound:
            return None
        connector = await session.get(Connector, intake.connector_id)
        if (
            connector is None
            or connector.claw_id != claw.id
            or not connector.enabled
            or connector.kind != "lark"
            or event.connection != connector.id
        ):
            return None
        settings = LarkSettings.model_validate(connector.config)
        if (
            settings != intake.settings
            or credential(runtime, intake.organization_id, connector) != intake.token
            or not allows_lark_event(settings, event)
        ):
            return None
        connector.last_event_at = await now(session)
        result = await inbox.admit(
            session,
            claw,
            inbox.Offer(
                "user",
                "lark",
                event_key(connector.id, event.event_id),
                {
                    **event.model_dump(mode="json", exclude={"connection"}),
                    "connector_id": connector.id,
                },
                content,
                f"Lark event: {event.event_type}",
            ),
        )
    if result.status == "accepted":
        await main_thread.notify(
            runtime, intake.workspace_id, intake.claw_id, delivery_id=result.delivery_id
        )
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
        self.github_tasks: dict[str, tuple[github_intake.Intake, asyncio.Task[None]]] = {}
        self.card_tasks: dict[str, asyncio.Task[None]] = {}
        self.egress_tasks: dict[str, asyncio.Task[None]] = {}
        self._card_slots = asyncio.Semaphore(16)
        self._egress_slots = asyncio.Semaphore(8)
        self._reconcile_lock = asyncio.Lock()
        self._subscription = asyncio.create_task(self._subscribe())
        self._write_status = runtime.redis.register_script(WRITE_STATUS)
        self._closing = False

    async def _subscribe(self) -> None:
        while True:
            try:
                async with self.runtime.redis.pubsub() as pubsub:
                    await pubsub.subscribe(LARK_CHANGES)
                    async for message in pubsub.listen():
                        organization_id = message["data"]
                        if message["type"] != "message" or not isinstance(organization_id, str):
                            continue
                        try:
                            await self.reconcile(organization_id)
                        except Exception:
                            logger.warning("Lark configuration reconciliation failed")
            except RedisError:
                logger.warning("Lark reconcile subscription unavailable")
                await asyncio.sleep(1)

    async def close(self) -> None:
        self._closing = True
        self._subscription.cancel()
        await asyncio.gather(self._subscription, return_exceptions=True)
        async with self._reconcile_lock:
            owned = [task for _, task in self.tasks.values() if not task.cancelling()]
            for task in owned:
                task.cancel()
            github = [task for _, task in self.github_tasks.values()]
            cards = list(self.card_tasks.values())
            egress_tasks = list(self.egress_tasks.values())
            for task in (*github, *cards, *egress_tasks):
                task.cancel()
            await asyncio.gather(
                *(task for _, task in self.tasks.values()),
                *github,
                *cards,
                *egress_tasks,
                return_exceptions=True,
            )

    def queue_cards(self, workspace_id: str, claw_id: str) -> None:
        """Keep provider latency out of the bounded organization sweep."""
        if self._closing or claw_id in self.card_tasks:
            return

        async def run() -> None:
            try:
                async with self._card_slots:
                    await lark_retry.reconcile(self.runtime, workspace_id, claw_id)
            except Exception:
                logger.warning("Lark retry card reconciliation failed", extra={"claw_id": claw_id})
            finally:
                self.card_tasks.pop(claw_id, None)

        self.card_tasks[claw_id] = asyncio.create_task(run())

    def queue_egress(self, workspace_id: str, claw_id: str) -> None:
        if self._closing or claw_id in self.egress_tasks:
            return

        async def run() -> None:
            from . import egress
            from .models import ConnectorEgress

            try:
                async with self._egress_slots:
                    async with short_session(self.runtime.storage) as session:
                        ids = list(
                            await session.scalars(
                                select(ConnectorEgress.id)
                                .where(
                                    ConnectorEgress.claw_id == claw_id,
                                    or_(
                                        ConnectorEgress.state.in_(
                                            ("pending", "preparing", "revoke_pending", "unbinding")
                                        ),
                                        (ConnectorEgress.state == "ready")
                                        & ConnectorEgress.refresh_at.is_not(None)
                                        & (ConnectorEgress.refresh_at <= func.now()),
                                    ),
                                )
                                .order_by(ConnectorEgress.created_at, ConnectorEgress.id)
                                .limit(50)
                            )
                        )
                    for generation_id in ids:
                        await egress.process_generation(
                            self.runtime, workspace_id, claw_id, generation_id
                        )
            except Exception:
                logger.warning("Connector egress reconciliation failed", extra={"claw_id": claw_id})
            finally:
                self.egress_tasks.pop(claw_id, None)

        self.egress_tasks[claw_id] = asyncio.create_task(run())

    async def reconcile(self, organization_id: str) -> None:
        async with self._reconcile_lock:
            if not self._closing:
                await self._reconcile(organization_id)

    async def _reconcile(self, organization_id: str) -> None:
        async with short_session(self.runtime.storage) as session:
            settings = await session.scalar(
                select(OrganizationRow.settings).where(OrganizationRow.id == organization_id)
            )
            rows = (
                (
                    await session.execute(
                        select(Connector, Claw.id, Claw.workspace_id)
                        .join(Claw, Claw.id == Connector.claw_id)
                        .join(WorkspaceRow, WorkspaceRow.id == Claw.workspace_id)
                        .where(
                            WorkspaceRow.organization_id == organization_id,
                            WorkspaceRow.archived_at.is_(None),
                            Claw.archived_at.is_(None),
                            Connector.kind.in_(("lark", "github")),
                            Connector.enabled,
                        )
                    )
                ).all()
                if settings is not None and APP_ID in policy(settings).enabled
                else ()
            )
        needed = {}
        github_needed = {}
        for row, claw, workspace in rows:
            try:
                token = credential(self.runtime, organization_id, row)
            except ServiceError:
                logger.warning("Connector credential unavailable", extra={"connector_id": row.id})
                continue
            if token and row.kind == "github":
                settings = GitHubSettings.model_validate(row.config)
                if settings.mode == "polling":
                    github_needed[row.id] = github_intake.Intake(
                        organization_id, workspace, claw, row.id, settings, token
                    )
            elif token:
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
                if not task.done():
                    if not task.cancelling():
                        task.cancel()
                    await asyncio.wait((task,))
                if not task.cancelled() and task.exception() is not None:
                    logger.warning("Lark listener stopped", extra={"connector_id": key})
                del self.tasks[key]
        for key, intake in needed.items():
            if key not in self.tasks:
                self.tasks[key] = (intake, asyncio.create_task(self.listen(intake, intake.token)))
        for key, (current, task) in list(self.github_tasks.items()):
            if current.organization_id == organization_id and (
                task.done() or current != github_needed.get(key)
            ):
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                del self.github_tasks[key]
        for key, intake in github_needed.items():
            if key not in self.github_tasks:
                self.github_tasks[key] = (
                    intake,
                    asyncio.create_task(github_intake.listen(self.runtime, intake)),
                )

    async def listen(self, intake: Intake, token: str) -> None:
        redis = self.runtime.redis
        lease_key = f"claw:lark:{intake.settings.app_id}"
        status_key = listener_key(intake.connector_id)
        prefix = f"{intake.connector_id}:"
        owner = f"{prefix}{uuid4().hex}"
        lock = redis.lock(lease_key, timeout=45, blocking=False, thread_local=False)
        if not await lock.acquire(token=owner):
            # A replica of this connector is normal; another connector with this App ID is not.
            holder = await redis.get(lease_key)
            if isinstance(holder, str) and not holder.startswith(prefix):
                await self._write_status(
                    keys=[lease_key, status_key], args=[holder, "conflict", STATUS_TTL]
                )
            return

        async def status_changed(status: str) -> None:
            with suppress(RedisError):
                await self._write_status(
                    keys=[lease_key, status_key], args=[owner, status, STATUS_TTL]
                )

        async def on_card(
            card_id: str, secret: str, open_id: str, chat_id: str, message_id: str
        ) -> bool:
            if not await lock.owned():
                raise LockError("Feishu listener has moved")
            return await lark_retry.retry(
                self.runtime, intake, card_id, secret, open_id, chat_id, message_id
            )

        adapter = LarkAdapter(
            intake.connector_id, intake.settings, token, on_status=status_changed, on_card=on_card
        )

        async def receive(event: InboundMessage | InboundEvent) -> None:
            if not await lock.owned():
                raise LockError("Feishu listener has moved")
            if isinstance(event, InboundEvent):
                if allows_lark_event(intake.settings, event):
                    await offer_lark_event(self.runtime, intake, event)
                return
            if not allows_lark(intake.settings, event):
                return
            try:
                snapshot = await asyncio.wait_for(adapter.snapshot(event), timeout=1.5)
                event = event.model_copy(update={"previous_messages_snapshot": snapshot})
            except Exception:
                # Provider content and credentials must never be logged; intake is independent.
                logger.warning(
                    "Lark context snapshot unavailable", extra={"connector_id": intake.connector_id}
                )
            if not await lock.owned():
                raise LockError("Feishu listener has moved")
            result = await offer_lark(self.runtime, intake, event)
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
            await status_changed(adapter.status)
            while not run.done():
                done, _ = await asyncio.wait((run,), timeout=15)
                if not done:
                    async with short_session(self.runtime.storage) as session:
                        if not await intake_enabled(session, intake.workspace_id):
                            return
                    await lock.extend(45, replace_ttl=True)
                    await status_changed(adapter.status)
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
                await status_changed("")
                with suppress(LockError, RedisError):
                    await lock.release()
