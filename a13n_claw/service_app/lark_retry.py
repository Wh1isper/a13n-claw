"""Recoverable failure cards and idempotent Main retry admission."""

from __future__ import annotations

import asyncio
import hmac
import json
import secrets
from datetime import timedelta
from hashlib import sha256
from urllib.parse import quote

import httpx
from a13n_logging import get_logger
from a13n_service.app_sdk import threads as app_threads
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.runs.schemas import Message
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.runs.threads import get_thread
from a13n_service.tenancy.authorize import WorkspaceScope
from a13n_service.tenancy.tables import WorkspaceRow
from sqlalchemy import select

from . import inbox, main_thread
from .feishu import LarkAdapter, LarkSettings
from .models import APP_ID, Claw, Connector, Delivery, InboxItem, LarkRetryCard, claw_context

logger = get_logger(__name__)


def location(organization_id: str, card_id: str) -> SecretLocation:
    return SecretLocation(organization_id, LarkRetryCard.__tablename__, "token", card_id)


def card_body(
    run_url: str,
    card_id: str,
    token: str,
    *,
    failure_code: str | None = None,
    submitted: bool = False,
) -> dict:
    reasons = {
        "usage_limit_exceeded": "The model request limit was reached.",
        "model_call_unknown": "The configured model was unavailable.",
        "provider_endpoint_denied": "The model provider endpoint was not allowed.",
        "environment_unavailable": "The execution environment was unavailable.",
        "lease_expired": "The worker stopped responding.",
        "authority_revoked": "The Run's authorization was revoked.",
        "attempt_failed": "The execution attempt failed.",
    }
    reason = reasons.get(failure_code or "", "The Main Run failed.")
    summary = f"{reason} Review the Run before retrying."
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": summary,
            },
        },
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "Open Run"},
                    "url": run_url,
                },
            ],
        },
    ]
    if submitted:
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": "Retry submitted"}})
    else:
        elements[-1]["actions"].append(
            {
                "tag": "button",
                "type": "primary",
                "text": {"tag": "plain_text", "content": "Retry"},
                "value": {"card_id": card_id, "token": token},
            }
        )
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Claw Main Run failed"},
            "template": "red",
        },
        "elements": elements,
    }


def run_url(runtime, workspace_id: str, session_id: str, thread_id: str, run_id: str) -> str:
    path = "/workspace/{}/sessions/{}/threads/{}/runs/{}".format(
        *(quote(value, safe="") for value in (workspace_id, session_id, thread_id, run_id))
    )
    return runtime.settings.server.public_url.rstrip("/") + path


async def discover(runtime, workspace_id: str, claw_id: str) -> None:
    """Recover a missed seal hint by discovering the current failed Main head."""
    async with transaction(runtime.storage) as session:
        try:
            claw = await inbox.lock_claw(session, workspace_id, claw_id)
        except inbox.ClawNotFound:
            return
        if not claw.main_thread_id:
            return
        thread = await session.get(ThreadRow, claw.main_thread_id)
        if thread is None or thread.current_run_id is not None or not thread.last_run_id:
            return
        run = await session.get(RunRow, thread.last_run_id)
        if run is None or run.status != "failed" or run.principal_id != claw.service_account_id:
            return
        rows = (
            (
                await session.execute(
                    select(InboxItem.source)
                    .join(Delivery, Delivery.item_id == InboxItem.id)
                    .where(
                        Delivery.claw_id == claw.id,
                        Delivery.run_id == run.id,
                        Delivery.origin == "lark",
                        Delivery.status == "accepted",
                    )
                    .order_by(Delivery.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        # A retry is a native Service Inbox entry rather than a new Claw Delivery.
        # Follow its durable assigned Run back to the card that authorized it so
        # a second failure can notify the same conversation without rewriting history.
        previous_cards = (
            await session.scalars(
                select(LarkRetryCard)
                .join(
                    InboxEntryRow,
                    InboxEntryRow.request_key == ("claw:retry:" + LarkRetryCard.run_id),
                )
                .where(
                    LarkRetryCard.claw_id == claw.id,
                    LarkRetryCard.state == "admitted",
                    InboxEntryRow.workspace_id == workspace_id,
                    InboxEntryRow.thread_id == thread.id,
                    InboxEntryRow.principal_id == claw.service_account_id,
                    InboxEntryRow.assigned_run_id == run.id,
                )
            )
        ).all()
        rows = list(rows) + [
            {
                "connector_id": card.connector_id,
                "chat_id": card.chat_id,
                "message_id": card.source_message_id,
                "is_group": card.is_group,
                "reply_in_thread": card.reply_in_thread,
            }
            for card in previous_cards
        ]
        workspace = await session.get_one(WorkspaceRow, workspace_id)
        seen: set[tuple[str, str]] = set()
        for source in rows:
            connector_id, chat_id, message_id = (
                source.get("connector_id"),
                source.get("chat_id"),
                source.get("message_id"),
            )
            if not (
                isinstance(connector_id, str)
                and connector_id
                and isinstance(chat_id, str)
                and chat_id
                and isinstance(message_id, str)
                and message_id
            ):
                continue
            key = (connector_id, chat_id)
            if key in seen:
                continue
            seen.add(key)
            connector = await session.get(Connector, connector_id)
            if (
                connector is None
                or connector.claw_id != claw.id
                or connector.kind != "lark"
                or not connector.enabled
            ):
                continue
            card_id = (
                "lrc_"
                + sha256(f"{claw.id}\0{run.id}\0{connector_id}\0{chat_id}".encode()).hexdigest()[
                    :32
                ]
            )
            if await session.get(LarkRetryCard, card_id) is not None:
                continue
            token = secrets.token_urlsafe(32)
            session.add(
                LarkRetryCard(
                    id=card_id,
                    claw_id=claw.id,
                    connector_id=connector_id,
                    run_id=run.id,
                    chat_id=chat_id,
                    source_message_id=message_id,
                    is_group=bool(source.get("is_group")),
                    reply_in_thread=bool(source.get("reply_in_thread")),
                    token=runtime.keys.protect(
                        token.encode(), location(workspace.organization_id, card_id)
                    ).model_dump(),
                )
            )


async def retry(
    runtime, intake, card_id: str, token: str, open_id: str, chat_id: str, message_id: str
) -> bool:
    """Authorize and commit exactly one native App submission before returning success."""
    from .connectors import credential, intake_enabled

    async with transaction(runtime.storage) as session:
        account_id = await session.scalar(
            select(Claw.service_account_id).where(
                Claw.id == intake.claw_id,
                Claw.workspace_id == intake.workspace_id,
                Claw.archived_at.is_(None),
            )
        )
        if account_id is None:
            return False
        admission = await app_threads.prepare_admission(
            session,
            runtime,
            intake.workspace_id,
            account_id,
            app_id=APP_ID,
            app_context=claw_context(intake.claw_id),
        )
        await main_thread.automation_account(
            session,
            runtime,
            WorkspaceScope(intake.organization_id, intake.workspace_id),
            account_id,
        )
        if not await intake_enabled(session, intake.workspace_id, lock=True):
            return False
        try:
            claw = await inbox.lock_claw(session, intake.workspace_id, intake.claw_id)
        except inbox.ClawNotFound:
            return False
        connector = await session.get(Connector, intake.connector_id, populate_existing=True)
        card = await session.get(LarkRetryCard, card_id, with_for_update=True)
        if (
            connector is None
            or connector.claw_id != claw.id
            or connector.kind != "lark"
            or not connector.enabled
            or LarkSettings.model_validate(connector.config) != intake.settings
            or credential(runtime, intake.organization_id, connector) != intake.token
            or open_id not in intake.settings.senders
            or open_id == intake.settings.bot_open_id
            or card is None
            or card.claw_id != claw.id
            or card.connector_id != connector.id
            or card.chat_id != chat_id
            or (card.is_group and chat_id not in intake.settings.groups)
            or (not card.is_group and not intake.settings.direct_messages)
            or (card.card_message_id is not None and card.card_message_id != message_id)
        ):
            return False
        expected = runtime.keys.reveal(
            Envelope.model_validate(card.token), location(intake.organization_id, card.id)
        ).decode()
        if not hmac.compare_digest(expected, token):
            return False
        if card.state == "admitted":
            return True
        if card.state not in ("pending", "sent") or not claw.main_thread_id:
            return False
        thread = await get_thread(
            session, claw.workspace_id, claw.main_thread_id, lock="acceptance", history=False
        )
        run = await session.get(RunRow, card.run_id)
        if (
            thread.current_run_id is not None
            or thread.last_run_id != card.run_id
            or run is None
            or run.status != "failed"
            or run.principal_id != claw.service_account_id
        ):
            return False
        agent = await session.scalar(
            select(AgentRow).where(
                AgentRow.id == claw.agent_id, AgentRow.workspace_id == claw.workspace_id
            )
        )
        if agent is None:
            return False
        message = Message(
            delivery="next_run",
            agent_id=agent.id,
            agent_revision_id=main_thread.selected_revision(claw, agent),
            payload=main_thread.payload(
                "The user requested a retry of the preceding failed Main Run. "
                "Review its context and continue safely."
            ),
            options=await main_thread.options(session, claw, agent, role="main"),
        )
        receipt, _ = await app_threads.submit_message_in_transaction(
            session,
            runtime,
            admission,
            claw.workspace_id,
            thread.id,
            message,
            request_key=f"claw:retry:{run.id}",
        )
        if receipt.entry.status != "assigned" or receipt.entry.assigned_run_id is None:
            # Do not claim a retry was submitted if Service merely queued it or
            # rejected it; rollback the entry and leave the card actionable.
            raise ServiceError("conflict", "Main retry admission was not started")
        card.state = "admitted"
        card.card_message_id = message_id
        card.admitted_at = await now(session)
        card.patch_pending = True
    return True


async def reconcile(runtime, workspace_id: str, claw_id: str) -> None:
    await discover(runtime, workspace_id, claw_id)
    async with short_session(runtime.storage) as session:
        ids = (
            await session.scalars(
                select(LarkRetryCard.id)
                .where(
                    LarkRetryCard.claw_id == claw_id,
                    (LarkRetryCard.state == "pending") | (LarkRetryCard.patch_pending.is_(True)),
                )
                .order_by(
                    LarkRetryCard.delivery_attempted_at.asc().nullsfirst(),
                    LarkRetryCard.created_at,
                    LarkRetryCard.id,
                )
                .limit(4)
            )
        ).all()
    for card_id in ids:
        lock = runtime.redis.lock(
            f"claw:lark-retry-card:{card_id}", timeout=30, blocking=False, thread_local=False
        )
        if not await lock.acquire():
            continue
        try:
            # Advance even for disabled connectors and failed requests so one broken
            # card cannot starve the rest of this Claw's delivery queue.
            async with transaction(runtime.storage) as session:
                card = await session.get(LarkRetryCard, card_id, with_for_update=True)
                if card is not None and card.claw_id == claw_id:
                    card.delivery_attempted_at = await now(session)
            async with asyncio.timeout(8):
                await deliver(runtime, workspace_id, claw_id, card_id)
        except (TimeoutError, httpx.HTTPError, ServiceError, ValueError):
            # A timeout after dispatch is unknown; the stable provider UUID bounds retries.
            logger.warning("Lark retry card delivery pending", extra={"card_id": card_id})
        finally:
            try:
                await lock.release()
            except Exception:
                logger.warning("Lark retry card lease release failed", extra={"card_id": card_id})


async def deliver(runtime, workspace_id: str, claw_id: str, card_id: str) -> None:
    from .connectors import credential, intake_enabled

    async with transaction(runtime.storage) as session:
        if not await intake_enabled(session, workspace_id, lock=True):
            return
        try:
            claw = await inbox.lock_claw(session, workspace_id, claw_id)
        except inbox.ClawNotFound:
            return
        card = await session.get(LarkRetryCard, card_id, with_for_update=True)
        if (
            card is None
            or card.claw_id != claw.id
            or not claw.main_thread_id
            or not claw.main_session_id
        ):
            return
        connector = await session.get(Connector, card.connector_id)
        if (
            connector is None
            or connector.claw_id != claw.id
            or connector.kind != "lark"
            or not connector.enabled
        ):
            return
        settings = LarkSettings.model_validate(connector.config)
        if (card.is_group and card.chat_id not in settings.groups) or (
            not card.is_group and not settings.direct_messages
        ):
            return
        workspace = await session.get_one(WorkspaceRow, workspace_id)
        secret = credential(runtime, workspace.organization_id, connector)
        if not secret:
            return
        if card.state == "pending":
            thread = await session.get(ThreadRow, claw.main_thread_id)
            if (
                thread is None
                or thread.last_run_id != card.run_id
                or thread.current_run_id is not None
            ):
                card.state = "stale"
                return
            current = await now(session)
            if card.send_started_at is None:
                card.send_started_at = current
            elif current - card.send_started_at >= timedelta(minutes=55):
                # Deduplication expires; do not resend an unknown outcome later.
                card.state = "unknown"
                return
        elif not (card.state == "admitted" and card.patch_pending and card.card_message_id):
            return
        submitted = card.state == "admitted"
        token = runtime.keys.reveal(
            Envelope.model_validate(card.token), location(workspace.organization_id, card.id)
        ).decode()
        failed_run = await session.get(RunRow, card.run_id)
        failure = failed_run.failure if failed_run is not None else None
        code = failure.get("code") if isinstance(failure, dict) else None
        body = card_body(
            run_url(runtime, workspace_id, claw.main_session_id, claw.main_thread_id, card.run_id),
            card.id,
            token,
            failure_code=code if isinstance(code, str) else None,
            submitted=submitted,
        )
        source_id, card_message_id = card.source_message_id, card.card_message_id
        in_thread = card.reply_in_thread
        connector_id = connector.id
    adapter = LarkAdapter(connector_id, settings, secret)
    try:
        access = await adapter.access_token()
        headers = {"Authorization": f"Bearer {access}"}
        content = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        if submitted:
            assert card_message_id is not None
            response = await adapter.client.patch(
                settings.domain + f"/open-apis/im/v1/messages/{quote(card_message_id, safe='')}",
                headers=headers,
                json={"content": content},
            )
        else:
            response = await adapter.client.post(
                settings.domain + f"/open-apis/im/v1/messages/{quote(source_id, safe='')}/reply",
                headers=headers,
                json={
                    "msg_type": "interactive",
                    "content": content,
                    "reply_in_thread": in_thread,
                    "uuid": f"claw-retry-{card_id[4:]}",
                },
            )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("code") != 0:
            raise ValueError("Lark rejected retry card")
        result_id = (
            (result.get("data") or {}).get("message_id") if not submitted else card_message_id
        )
        if not isinstance(result_id, str) or not result_id:
            raise ValueError("Lark did not return the retry card message ID")
    finally:
        await adapter.close()
    async with transaction(runtime.storage) as session:
        card = await session.get(LarkRetryCard, card_id, with_for_update=True)
        if card is None or card.claw_id != claw_id:
            return
        if submitted:
            if card.state == "admitted" and card.card_message_id == result_id:
                card.patch_pending = False
        else:
            if card.card_message_id is None:
                card.card_message_id = result_id
            if card.state == "pending":
                card.state = "sent"
