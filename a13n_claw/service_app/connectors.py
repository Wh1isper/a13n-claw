"""Connector configuration, filtering, and fan-in through the shared Inbox."""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

import httpx
from a13n_logging import get_logger
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, disabled, not_found
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.app_plugins import policy
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.tables import OrganizationRow, WorkspaceRow
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import inbox, main_thread
from .feishu import InboundEvent, InboundMessage, LarkAdapter, LarkSettings, reply_key
from .github_poll import GitHubSettings
from .models import APP_ID, Connector

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
        adapter = LarkAdapter(connector_id, LarkSettings.model_validate(config), token)
        try:
            await adapter.exchange_token()
        finally:
            await adapter.close()
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
