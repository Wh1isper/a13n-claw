"""Durable GitHub scan admission and retryable provider feedback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from urllib.parse import quote

import httpx
from a13n_logging import get_logger
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.runtime import Runtime
from sqlalchemy import or_, select, update

from . import connectors, inbox, main_thread
from .github_poll import GitHubClient, GitHubSettings, Scan, same_origin
from .models import Connector, GitHubPollState, GitHubReceipt

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Intake:
    organization_id: str
    workspace_id: str
    claw_id: str
    connector_id: str
    settings: GitHubSettings
    token: str = field(repr=False)


def receipt_id(connector_id: str, api_url: str, source_key: str) -> str:
    return sha256(f"{connector_id}\0{api_url}\0{source_key}".encode()).hexdigest()


async def reconfigure(session, connector_id: str, old: GitHubSettings, new: GitHubSettings) -> None:
    state = await session.get(GitHubPollState, connector_id, with_for_update=True)
    if old.api_url != new.api_url:
        # Provider identities and pending endpoint URLs do not transfer to another API.
        if state is not None:
            await session.delete(state)
        await session.execute(
            update(GitHubReceipt)
            .where(
                GitHubReceipt.connector_id == connector_id,
                GitHubReceipt.reaction_state == "pending",
            )
            .values(reaction_state="unavailable")
        )
        await session.execute(
            update(GitHubReceipt)
            .where(
                GitHubReceipt.connector_id == connector_id,
                GitHubReceipt.mark_read_state == "pending",
            )
            .values(mark_read_state="unavailable")
        )
    else:
        if state is not None:
            # Preserve backlog even when credentials, filters or intervals change.
            state.last_modified = None
        if old.mark_read and not new.mark_read:
            await session.execute(
                update(GitHubReceipt)
                .where(
                    GitHubReceipt.connector_id == connector_id,
                    GitHubReceipt.mark_read_state == "pending",
                )
                .values(mark_read_state="disabled")
            )


async def current(session, runtime: Runtime, intake: Intake) -> Connector | None:
    if not await connectors.intake_enabled(session, intake.workspace_id, lock=True):
        return None
    row = await session.get(Connector, intake.connector_id, populate_existing=True)
    if (
        row is None
        or row.claw_id != intake.claw_id
        or row.kind != "github"
        or not row.enabled
        or GitHubSettings.model_validate(row.config) != intake.settings
        or connectors.credential(runtime, intake.organization_id, row) != intake.token
    ):
        return None
    return row


async def checkpoint(runtime: Runtime, intake: Intake) -> tuple[datetime | None, str | None, bool]:
    async with transaction(runtime.storage) as session:
        if await current(session, runtime, intake) is None:
            return None, None, False
        try:
            await inbox.lock_claw(session, intake.workspace_id, intake.claw_id)
        except inbox.ClawNotFound:
            return None, None, False
        if await current(session, runtime, intake) is None:
            return None, None, False
        state = await session.get(GitHubPollState, intake.connector_id, with_for_update=True)
        current_time = await now(session)
        if state is None:
            # Persist the initial boundary before any provider I/O. Full Inbox or a crash
            # must not move the lookback window forward on every retry.
            state = GitHubPollState(
                connector_id=intake.connector_id,
                cursor=current_time - timedelta(seconds=intake.settings.initial_lookback_seconds),
            )
            session.add(state)
            await session.flush()
        due = state.next_poll_at is None or state.next_poll_at <= current_time
        if due:
            # Reserve before HTTP; failures and crashes respect the last known interval.
            state.next_poll_at = current_time + timedelta(
                seconds=max(intake.settings.poll_interval_seconds, state.server_poll_interval or 0)
            )
        return state.cursor, state.last_modified, due


async def accept_scan(
    runtime: Runtime,
    intake: Intake,
    scan: Scan,
    cursor: datetime | None,
    last_modified: str | None,
) -> tuple[bool, list[str]]:
    deliveries: list[str] = []
    async with transaction(runtime.storage) as session:
        if await current(session, runtime, intake) is None:
            return False, []
        try:
            claw = await inbox.lock_claw(session, intake.workspace_id, intake.claw_id)
        except inbox.ClawNotFound:
            return False, []
        if await current(session, runtime, intake) is None:
            return False, []
        state = await session.get(GitHubPollState, intake.connector_id, with_for_update=True)
        if state is None:
            if cursor is not None or last_modified is not None:
                return False, []
            state = GitHubPollState(connector_id=intake.connector_id)
            session.add(state)
        elif (state.cursor, state.last_modified) != (cursor, last_modified):
            return False, []
        complete = True
        for event in scan.events:
            identity = receipt_id(intake.connector_id, intake.settings.api_url, event.key)
            if await session.get(GitHubReceipt, identity) is not None:
                continue
            source = {
                **event.source,
                "platform": "github",
                "connector_id": intake.connector_id,
                "channel": event.channel,
                "sender": event.sender,
                "notification_thread_id": event.thread_id,
                "event_id": event.key,
            }
            content = event.content[:65536]
            admission = await inbox.admit(
                session,
                claw,
                inbox.Offer("user", "github", f"github:{identity}", source, content, content[:200]),
            )
            if admission.status == "full":
                complete = False
                break
            session.add(
                GitHubReceipt(
                    id=identity,
                    connector_id=intake.connector_id,
                    source_key=event.key[:200],
                    reaction_url=event.reaction_url,
                    reaction_state="pending",
                    thread_id=event.thread_id,
                    mark_read_state="pending" if intake.settings.mark_read else "disabled",
                )
            )
            await session.flush()
            if admission.status == "accepted" and admission.delivery_id:
                deliveries.append(admission.delivery_id)
        if complete:
            state.cursor = max(cursor, scan.date) if cursor else scan.date
            state.last_modified = scan.last_modified
        # A full Inbox needs a later rescan, not a busy loop or a lost cursor.
        deadline = await now(session) + timedelta(seconds=scan.poll_interval)
        state.next_poll_at = max(state.next_poll_at, deadline) if state.next_poll_at else deadline
        row = await session.get(Connector, intake.connector_id)
        if row is not None and deliveries:
            row.last_event_at = await now(session)
    return complete, deliveries


async def feedback(runtime: Runtime, intake: Intake, client: httpx.AsyncClient) -> None:
    async with short_session(runtime.storage) as session:
        pending = (
            await session.execute(
                select(
                    GitHubReceipt.id,
                    GitHubReceipt.reaction_url,
                    GitHubReceipt.reaction_state,
                    GitHubReceipt.thread_id,
                    GitHubReceipt.mark_read_state,
                )
                .where(
                    GitHubReceipt.connector_id == intake.connector_id,
                    or_(
                        GitHubReceipt.reaction_state == "pending",
                        GitHubReceipt.mark_read_state == "pending",
                    ),
                )
                .order_by(
                    GitHubReceipt.feedback_attempted_at.asc().nulls_first(),
                    GitHubReceipt.created_at,
                    GitHubReceipt.id,
                )
                .limit(10)
            )
        ).all()
    headers = {"Authorization": f"Bearer {intake.token}", "Accept": "application/vnd.github+json"}
    for identity, reaction_url, reaction_state, thread_id, mark_state in pending:
        async with short_session(runtime.storage) as session:
            if await current(session, runtime, intake) is None:
                return
        updates: dict[str, str] = {}
        for field_name, pending_state, method, target, body in (
            ("reaction_state", reaction_state, "POST", reaction_url, {"content": "eyes"}),
            (
                "mark_read_state",
                mark_state,
                "PATCH",
                f"{intake.settings.api_url}/notifications/threads/{quote(thread_id, safe='')}",
                None,
            ),
        ):
            if pending_state != "pending":
                continue
            try:
                response = await client.request(
                    method,
                    same_origin(intake.settings.api_url, target),
                    headers=headers,
                    json=body,
                    follow_redirects=False,
                )
                if response.is_success:
                    updates[field_name] = "sent"
                elif response.status_code in (404, 410):
                    updates[field_name] = "unavailable"
            except (httpx.HTTPError, ValueError):
                pass
        async with transaction(runtime.storage) as session:
            if await current(session, runtime, intake) is None:
                return
            row = await session.get(GitHubReceipt, identity, with_for_update=True)
            if row is not None:
                row.feedback_attempted_at = await now(session)
                for field_name, value in updates.items():
                    if getattr(row, field_name) == "pending":
                        setattr(row, field_name, value)


async def record_interval(runtime: Runtime, intake: Intake, seconds: int) -> None:
    async with transaction(runtime.storage) as session:
        if await current(session, runtime, intake) is None:
            return
        try:
            await inbox.lock_claw(session, intake.workspace_id, intake.claw_id)
        except inbox.ClawNotFound:
            return
        if await current(session, runtime, intake) is None:
            return
        state = await session.get(GitHubPollState, intake.connector_id, with_for_update=True)
        if state is not None:
            state.server_poll_interval = seconds
            deadline = await now(session) + timedelta(
                seconds=max(seconds, intake.settings.poll_interval_seconds)
            )
            state.next_poll_at = (
                max(state.next_poll_at, deadline) if state.next_poll_at else deadline
            )


async def poll(runtime: Runtime, intake: Intake, client: httpx.AsyncClient) -> None:
    cursor, modified, due = await checkpoint(runtime, intake)
    if not due:
        await feedback(runtime, intake, client)
        return

    async def interval(seconds: int) -> None:
        await record_interval(runtime, intake, seconds)

    scan = await GitHubClient(intake.settings, intake.token, client).scan(
        cursor, modified, interval
    )
    _, deliveries = await accept_scan(runtime, intake, scan, cursor, modified)
    for delivery_id in deliveries:
        try:
            await main_thread.notify(
                runtime, intake.workspace_id, intake.claw_id, delivery_id=delivery_id
            )
        except (ServiceError, inbox.ClawNotFound):
            logger.warning(
                "GitHub admission wake failed", extra={"connector_id": intake.connector_id}
            )
    await feedback(runtime, intake, client)


async def listen(runtime: Runtime, intake: Intake) -> None:
    lock = runtime.redis.lock(
        f"claw:github:{intake.connector_id}", timeout=90, blocking=False, thread_local=False
    )
    if not await lock.acquire():
        return

    async def renew() -> None:
        while True:
            await asyncio.sleep(30)
            await lock.extend(90, replace_ttl=True)

    renewal = asyncio.create_task(renew())
    attempt: asyncio.Task[None] | None = None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            while True:
                attempt = asyncio.create_task(poll(runtime, intake, client))
                done, _ = await asyncio.wait(
                    (attempt, renewal), return_when=asyncio.FIRST_COMPLETED
                )
                if renewal in done:
                    await renewal
                    return
                try:
                    await attempt
                except (httpx.HTTPError, ValueError, ServiceError):
                    logger.warning(
                        "GitHub poll failed", extra={"connector_id": intake.connector_id}
                    )
                attempt = None
                done, _ = await asyncio.wait(
                    (renewal,), timeout=min(30, intake.settings.poll_interval_seconds)
                )
                if done:
                    await renewal
                    return
    finally:
        if attempt is not None:
            attempt.cancel()
        renewal.cancel()
        await asyncio.gather(
            renewal, *([attempt] if attempt is not None else []), return_exceptions=True
        )
        try:
            await lock.release()
        except Exception:
            logger.warning(
                "GitHub listener lease release failed", extra={"connector_id": intake.connector_id}
            )
