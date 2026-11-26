"""Signed GitHub webhook intake."""

import hmac
import json
from contextlib import suppress
from dataclasses import asdict
from hashlib import sha256

from a13n_service.app_sdk import is_app_enabled
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.app_plugins import app_webhook_router
from a13n_service.tenancy.tables import WorkspaceRow
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from . import connectors, inbox, main_thread
from .github_poll import GitHubSettings, allowed_sender, explicitly_mentions
from .models import APP_ID, Claw, Connector

router = app_webhook_router(APP_ID)


def normalize(
    event: str, payload: dict, settings: GitHubSettings
) -> tuple[str, connectors.ExternalMessage] | None:
    action = payload.get("action")
    pairs = {
        ("issue_comment", "created"): ("comment", "issue", "issue_comment"),
        ("pull_request_review_comment", "created"): (
            "comment",
            "pull_request",
            "pr_review_comment",
        ),
        ("issues", "opened"): ("issue", "issue", "issue"),
        ("pull_request", "opened"): ("pull_request", "pull_request", "pull_request"),
        ("pull_request_review", "submitted"): ("review", "pull_request", "pr_review"),
    }
    if not isinstance(action, str):
        return None
    pair = pairs.get((event, action))
    if pair is None:
        return None
    obj = payload.get(pair[0])
    subject = payload.get(pair[1])
    repo = payload.get("repository")
    if not isinstance(obj, dict) or not isinstance(subject, dict) or not isinstance(repo, dict):
        return None
    user = obj.get("user")
    sender = user.get("login") if isinstance(user, dict) else None
    channel, body = repo.get("full_name"), obj.get("body")
    number, identity = subject.get("number"), obj.get("id")
    if not (
        isinstance(channel, str)
        and 0 < len(channel) <= 200
        and isinstance(sender, str)
        and 0 < len(sender) <= 200
        and isinstance(body, str)
        and isinstance(number, int)
        and number > 0
        and isinstance(identity, int)
        and identity > 0
    ):
        return None
    if settings.channels and channel.casefold() not in {v.casefold() for v in settings.channels}:
        return None
    bot = settings.bot_login or ""
    if not allowed_sender(settings, sender, bot) or not explicitly_mentions(body, bot):
        return None
    content = body
    if event in ("issues", "pull_request"):
        title = obj.get("title")
        if not isinstance(title, str):
            return None
        content = f"{title}\n\n{body}".strip()
    key = f"webhook:{pair[2]}:{identity}"
    message = connectors.ExternalMessage(
        event_id=key,
        channel=channel,
        sender=sender,
        content=content[:65536],
        github_issue_number=number,
        github_comment_id=identity
        if event in ("issue_comment", "pull_request_review_comment")
        else None,
        github_comment_kind="review" if event == "pull_request_review_comment" else "issue",
    )
    return key, message


@router.post("/github/{connector_id}", status_code=201, name="claw_github_webhook")
async def github_webhook(connector_id: str, request: Request, runtime: CurrentRuntime):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 1024 * 1024:
            raise ServiceError("payload_too_large", "Webhook body exceeds 1 MiB")
        raw.extend(chunk)
    missing = not_found("connector", connector_id)
    async with short_session(runtime.storage) as session:
        row = (
            await session.execute(
                select(Connector, Claw, WorkspaceRow)
                .join(Claw, Connector.claw_id == Claw.id)
                .join(WorkspaceRow, Claw.workspace_id == WorkspaceRow.id)
                .where(
                    Connector.id == connector_id,
                    Claw.archived_at.is_(None),
                    WorkspaceRow.archived_at.is_(None),
                )
            )
        ).one_or_none()
        if row is None:
            raise missing
        connector, claw, workspace = row
        settings = (
            GitHubSettings.model_validate(connector.config) if connector.kind == "github" else None
        )
        if (
            not connector.enabled
            or settings is None
            or settings.mode != "webhook"
            or connector.webhook_secret is None
            or not await is_app_enabled(
                session, runtime, APP_ID, organization_id=workspace.organization_id
            )
        ):
            raise missing
        secret = connectors.webhook_secret(runtime, workspace.organization_id, connector)
        workspace_id, claw_id, organization_id = workspace.id, claw.id, workspace.organization_id
    if secret is None:
        raise missing
    signature = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(secret.encode(), raw, sha256).hexdigest()
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        raise ServiceError("unauthenticated", "Invalid webhook signature")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ServiceError("invalid_argument", "Invalid webhook JSON") from None
    event = request.headers.get("X-GitHub-Event", "").strip()
    if not isinstance(payload, dict) or not event:
        raise ServiceError("invalid_argument", "Webhook event and JSON object are required")
    if event == "ping":
        return JSONResponse({"status": "pong"})
    normalized = normalize(event, payload, settings)
    if normalized is None:
        return JSONResponse({"status": "ignored"})
    key, message = normalized
    async with transaction(runtime.storage) as session:
        if not await connectors.intake_enabled(session, workspace_id, lock=True):
            raise missing
        try:
            claw = await inbox.lock_claw(session, workspace_id, claw_id)
        except inbox.ClawNotFound:
            raise missing from None
        connector = await session.get(Connector, connector_id, populate_existing=True)
        if (
            connector is None
            or connector.claw_id != claw_id
            or connector.kind != "github"
            or not connector.enabled
            or connector.webhook_secret is None
            or connectors.webhook_secret(runtime, organization_id, connector) != secret
        ):
            raise missing
        settings = GitHubSettings.model_validate(connector.config)
        if settings.mode != "webhook":
            raise missing
        normalized = normalize(event, payload, settings)
        if normalized is None:
            return JSONResponse({"status": "ignored"})
        key, message = normalized
        connector.last_event_at = await now(session)
        reply_token = None
        with suppress(ServiceError):
            reply_token = connectors.credential(runtime, organization_id, connector)
        source = {
            "platform": "github",
            "connector_id": connector_id,
            "channel": message.channel,
            "sender": message.sender,
            "event_id": key,
            "issue_number": message.github_issue_number,
            "comment_id": message.github_comment_id,
            "comment_kind": message.github_comment_kind,
            "delivery_id": request.headers.get("X-GitHub-Delivery"),
        }
        result = await inbox.admit(
            session,
            claw,
            inbox.Offer(
                "user",
                "github",
                connectors.event_key(connector_id, key),
                source,
                message.content,
                message.content[:200],
                volatile_source_fields=("delivery_id",),
            ),
        )
    if result.status == "full":
        raise conflict("claw", claw_id, "inbox_full", delivery_id=result.delivery_id)
    if result.status == "duplicate":
        return JSONResponse(asdict(result))
    try:
        await main_thread.notify(runtime, workspace_id, claw_id, delivery_id=result.delivery_id)
    finally:
        if event != "pull_request_review":
            await connectors.github_eyes(connector_id, message, reply_token, settings.api_url)
    return asdict(result)
