"""Claw connectors routes."""

from datetime import datetime
from typing import Literal
from unicodedata import category

import httpx
import pydantic
from a13n_service.infra.cursors import keyset_page
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.requests import Actor, WorkspaceId
from fastapi import Response
from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import connectors, egress, github_intake, inbox, main_thread, schedules
from ..feishu import LarkSettings
from ..github_poll import GitHubSettings
from ..models import (
    APP_ID,
    Claw,
    Connector,
    ConnectorEgress,
)
from .common import Name, PageSize, ResourceId, get_claw, lock_active_claw

router = app_router(APP_ID)


class ConnectorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    kind: Literal["lark", "github"]
    config: dict[str, JsonValue]
    enabled: bool = True
    egress_primary: bool = False

    @pydantic.model_validator(mode="after")
    def validate_config(self):
        schema = LarkSettings if self.kind == "lark" else GitHubSettings
        self.config = schema.model_validate(self.config).model_dump(mode="json")
        return self


class ConnectorWrite(ConnectorInput):
    credential: pydantic.SecretStr | None = None
    webhook_secret: pydantic.SecretStr | None = None
    egress_environment_id: ResourceId | None = None

    @pydantic.field_validator("webhook_secret")
    @classmethod
    def valid_webhook_secret(cls, secret: pydantic.SecretStr | None) -> pydantic.SecretStr | None:
        if secret is not None:
            value = secret.get_secret_value()
            if not 1 <= len(value) <= 8192 or any(category(c) == "Cc" for c in value):
                raise ValueError("Enter a non-empty webhook secret without control characters")
        return secret

    @pydantic.model_validator(mode="after")
    def github_secret_only(self):
        if self.kind != "github" and self.webhook_secret is not None:
            raise ValueError("Webhook secrets require a GitHub connector")
        return self

    @pydantic.field_validator("credential")
    @classmethod
    def valid_credential(cls, secret: pydantic.SecretStr | None) -> pydantic.SecretStr | None:
        if secret is not None:
            value = secret.get_secret_value()
            if (
                not 1 <= len(value) <= 8192
                or not value.isascii()
                or not all(33 <= ord(char) <= 126 for char in value)
            ):
                raise ValueError("Enter a non-empty platform credential without spaces")
        return secret


class ConnectorView(ConnectorInput):
    model_config = ConfigDict(from_attributes=True)
    id: str
    last_event_at: datetime | None
    created_at: datetime
    credential_configured: bool = False
    webhook_secret_configured: bool = False
    listener_status: str | None = None
    egress_status: str | None = None
    egress_failure: str | None = None


async def connector_view(
    runtime: CurrentRuntime, row: Connector, generation: ConnectorEgress | None
) -> ConnectorView:
    view = ConnectorView.model_validate(row)
    return view.model_copy(
        update={
            "credential_configured": row.credential is not None,
            "webhook_secret_configured": row.webhook_secret is not None,
            "egress_status": generation.state if generation else None,
            "egress_failure": generation.failure if generation else None,
            "listener_status": await connectors.listener_status(runtime, row.id)
            if row.enabled and row.kind == "lark" and row.credential is not None
            else None,
        }
    )


async def connector_generations(
    session: AsyncSession, ids: list[str]
) -> dict[str, ConnectorEgress]:
    if not ids:
        return {}
    rows = await session.scalars(
        select(ConnectorEgress)
        .where(ConnectorEgress.connector_id.in_(ids), ConnectorEgress.state != "revoked")
        .distinct(ConnectorEgress.connector_id)
        .order_by(
            ConnectorEgress.connector_id,
            ConnectorEgress.failure.is_not(None).desc(),
            ConnectorEgress.created_at.desc(),
            ConnectorEgress.id.desc(),
        )
    )
    return {row.connector_id: row for row in rows}


async def load_connector_view(runtime: CurrentRuntime, row: Connector) -> ConnectorView:
    async with short_session(runtime.storage) as session:
        generations = await connector_generations(session, [row.id])
    return await connector_view(runtime, row, generations.get(row.id))


@router.get("/claws/{claw_id}/connectors", name="claw_list_connectors")
async def list_connectors(
    claw_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    cursor: str | None = None,
    limit: PageSize = 50,
) -> dict:
    async with short_session(runtime.storage) as session:
        await get_claw(session, workspace_id, claw_id)
        rows, next_cursor = await keyset_page(
            session,
            select(Connector).where(Connector.claw_id == claw_id),
            (Connector.created_at, Connector.id),
            kind="claw_connector",
            owner=claw_id,
            cursor=cursor,
            limit=limit,
            newest_first=False,
        )
        generations = await connector_generations(session, [row.id for row in rows])
    return {
        "items": [await connector_view(runtime, row, generations.get(row.id)) for row in rows],
        "next_cursor": next_cursor,
    }


@router.get("/claws/{claw_id}/connectors/{connector_id}", name="claw_connector_detail")
async def connector_detail(
    claw_id: ResourceId,
    connector_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
) -> ConnectorView:
    async with short_session(runtime.storage) as session:
        await get_claw(session, workspace_id, claw_id)
        row = await connectors.find(session, claw_id, connector_id)
    return await load_connector_view(runtime, row)


async def check_egress_primary(
    session: AsyncSession, claw: Claw, body: ConnectorWrite, exclude: str | None = None
) -> None:
    if body.egress_primary:
        existing = await session.scalar(
            select(Connector.id).where(
                Connector.claw_id == claw.id,
                Connector.kind == body.kind,
                Connector.egress_primary.is_(True),
                Connector.id != exclude if exclude else Connector.id.is_not(None),
            )
        )
        if existing:
            raise conflict("connector", existing, "egress_primary_already_selected")


def check_reply_environment(claw: Claw, body: ConnectorWrite) -> None:
    if body.egress_primary and (
        body.egress_environment_id is None or body.egress_environment_id != claw.environment_id
    ):
        raise conflict("claw", claw.id, "egress_environment_changed")


@router.post("/claws/{claw_id}/connectors", status_code=201, name="claw_create_connector")
async def create_connector(
    claw_id: ResourceId,
    body: ConnectorWrite,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> ConnectorView:
    if (
        body.kind == "github"
        and body.config.get("mode") == "webhook"
        and body.webhook_secret is None
    ):
        raise invalid("webhook_secret", "required for webhook intake")
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        claw = await get_claw(session, workspace_id, claw_id)
        if body.kind == "github" and body.config.get("principal_id"):
            await main_thread.account(session, runtime, scope, str(body.config["principal_id"]))
        claw = await lock_active_claw(session, workspace_id, claw_id)
        check_reply_environment(claw, body)
        await check_egress_primary(session, claw, body)
        if await session.scalar(
            select(Connector.id).where(Connector.claw_id == claw.id, Connector.name == body.name)
        ):
            raise conflict("connector", body.name, "name_in_use")
        row = Connector(
            id=inbox.new_id("con"),
            claw_id=claw.id,
            **body.model_dump(exclude={"credential", "webhook_secret", "egress_environment_id"}),
        )
        if body.credential is not None:
            row.credential = connectors.protect(
                runtime, scope.organization_id, row.id, body.credential.get_secret_value()
            )
        if body.webhook_secret is not None:
            row.webhook_secret = connectors.protect_webhook_secret(
                runtime, scope.organization_id, row.id, body.webhook_secret.get_secret_value()
            )
        session.add(row)
        await session.flush()
        await egress.enqueue_generation(session, claw, row)
    await connectors.connectors_changed(runtime, scope.organization_id)
    return await load_connector_view(runtime, row)


@router.put("/claws/{claw_id}/connectors/{connector_id}", name="claw_update_connector")
async def update_connector(
    claw_id: ResourceId,
    connector_id: ResourceId,
    body: ConnectorWrite,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> ConnectorView:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        check_reply_environment(claw, body)
        row = await connectors.find(session, claw.id, connector_id)
        await check_egress_primary(session, claw, body, row.id)
        secret_present = (
            body.webhook_secret is not None
            if "webhook_secret" in body.model_fields_set
            else row.webhook_secret is not None
        )
        if body.kind == "github" and body.config.get("mode") == "webhook" and not secret_present:
            raise invalid("webhook_secret", "required for webhook intake")
        if row.kind != body.kind:
            raise conflict("connector", connector_id, "connector_kind_is_immutable")
        if body.kind == "github" and body.config.get("principal_id"):
            await main_thread.account(session, runtime, scope, str(body.config["principal_id"]))
        if row.name != body.name and await session.scalar(
            select(Connector.id).where(
                Connector.claw_id == claw.id,
                Connector.name == body.name,
            )
        ):
            raise conflict("connector", body.name, "name_in_use")
        poll_changed = row.kind == "github" and (
            row.config != body.config or "credential" in body.model_fields_set
        )
        if poll_changed:
            await github_intake.reconfigure(
                session,
                row.id,
                GitHubSettings.model_validate(row.config),
                GitHubSettings.model_validate(body.config),
            )
        target_changed = egress.target(row.kind, row.config) != egress.target(row.kind, body.config)
        credential_changed = "credential" in body.model_fields_set
        egress_changed = (
            row.egress_primary != body.egress_primary
            or row.enabled != body.enabled
            or target_changed
            or (credential_changed and ((row.credential is None) != (body.credential is None)))
        )
        rotate = (
            not egress_changed
            and row.egress_primary
            and row.enabled
            and (
                (credential_changed and body.credential is not None)
                or (row.kind == "lark" and row.config.get("app_id") != body.config.get("app_id"))
            )
        )
        if egress_changed:
            await egress.invalidate_generations(session, row.id)
        elif rotate:
            generations = await session.scalars(
                select(ConnectorEgress)
                .where(
                    ConnectorEgress.connector_id == row.id,
                    ConnectorEgress.state.in_(("ready", "preparing", "pending")),
                )
                .with_for_update()
            )
            for generation in generations:
                generation.state = "ready" if generation.credential_id else "pending"
                generation.refresh_at = await schedules.now(session)
                generation.attempt_started_at = None
                generation.failure = None
        row.name, row.enabled, row.config = body.name, body.enabled, body.config
        row.egress_primary = body.egress_primary
        if "credential" in body.model_fields_set:
            row.credential = (
                connectors.protect(
                    runtime, scope.organization_id, row.id, body.credential.get_secret_value()
                )
                if body.credential is not None
                else None
            )
        if "webhook_secret" in body.model_fields_set:
            row.webhook_secret = (
                connectors.protect_webhook_secret(
                    runtime, scope.organization_id, row.id, body.webhook_secret.get_secret_value()
                )
                if body.webhook_secret is not None
                else None
            )
        if egress_changed:
            await egress.enqueue_generation(session, claw, row)
    await connectors.connectors_changed(runtime, scope.organization_id)
    return await load_connector_view(runtime, row)


@router.delete(
    "/claws/{claw_id}/connectors/{connector_id}", status_code=204, name="claw_delete_connector"
)
async def delete_connector(
    claw_id: ResourceId,
    connector_id: ResourceId,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> None:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        await lock_active_claw(session, workspace_id, claw_id)
        row = await connectors.find(session, claw_id, connector_id)
        await egress.invalidate_generations(session, row.id)
        await session.delete(row)
    await connectors.connectors_changed(runtime, scope.organization_id)


class ConnectorCheck(BaseModel):
    state: Literal["not configured", "failed", "verified"]
    reason: str | None = None
    http_status: int | None = None


@router.post(
    "/claws/{claw_id}/connectors/{connector_id}/check",
    name="claw_check_connector",
    response_model_exclude_none=True,
)
async def check_connector(
    claw_id: ResourceId,
    connector_id: ResourceId,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> ConnectorCheck:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        await get_claw(session, workspace_id, claw_id)
        row = await connectors.find(session, claw_id, connector_id)
        try:
            token = connectors.credential(runtime, scope.organization_id, row)
        except ServiceError:
            return ConnectorCheck(state="failed", reason="credential_unavailable")
    if token is None:
        return ConnectorCheck(state="not configured")
    try:
        await connectors.check(row.kind, row.id, row.config, token)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if row.kind == "github" and status in (403, 404):
            reason = "repository_unavailable"
        elif status in (400, 401, 403):
            reason = "credentials_rejected"
        else:
            reason = "provider_http_error"
        return ConnectorCheck(state="failed", reason=reason, http_status=status)
    except httpx.RequestError:
        return ConnectorCheck(state="failed", reason="provider_unreachable")
    except ValueError:
        return ConnectorCheck(
            state="failed",
            reason="check_region_and_credentials" if row.kind == "lark" else "invalid_repository",
        )
    return ConnectorCheck(state="verified")


@router.post(
    "/claws/{claw_id}/connectors/{connector_id}/input",
    status_code=201,
    name="claw_receive_connector",
)
async def receive_connector(
    claw_id: ResourceId,
    connector_id: ResourceId,
    body: connectors.ExternalMessage,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    response: Response,
) -> inbox.Admission:
    try:
        result = await connectors.offer_external(
            runtime, actor, workspace_id, claw_id, connector_id, body
        )
    except inbox.ClawNotFound:
        raise not_found("claw", claw_id) from None
    if result.status == "full":
        raise conflict("claw", claw_id, "inbox_full", delivery_id=result.delivery_id)
    if result.status == "duplicate":
        response.status_code = 200
    return result
