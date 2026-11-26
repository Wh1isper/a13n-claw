"""Claw deliveries routes."""

from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal

from a13n_service.infra.cursors import keyset_page
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import conflict, not_found
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.requests import Actor, WorkspaceId
from fastapi import Header, Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import with_expression

from .. import inbox, main_thread
from ..models import (
    APP_ID,
    Claw,
    Delivery,
    InboxItem,
)
from .common import PageSize, ResourceId, get_claw, lock_active_claw, scope_claw

router = app_router(APP_ID)


class DeliveryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    item_id: str | None
    kind: str | None = None
    preview: str | None = None
    read_at: datetime | None = None
    origin: str
    status: str
    thread_id: str | None
    session_id: str | None
    run_id: str | None
    created_at: datetime


class DeliveryPage(BaseModel):
    items: list[DeliveryView]
    next_cursor: str | None


class DeliveryDetail(DeliveryView):
    content: str | None
    source: dict[str, JsonValue] | None


class InputMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=65536)
    source: dict[str, JsonValue] = Field(default_factory=dict, max_length=24)


@router.post("/claws/{claw_id}/input", status_code=201, name="claw_receive")
async def receive(
    claw_id: ResourceId,
    body: InputMessage,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    response: Response,
    request_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> inbox.Admission:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "run")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        result = await inbox.admit(
            session,
            claw,
            inbox.Offer(
                "user",
                "input",
                f"input:{actor.id}:{sha256(request_key.encode()).hexdigest()}",
                {**body.source, "principal_id": actor.id},
                body.content,
                body.content[:200],
            ),
        )
    if result.status == "full":
        raise conflict("claw", claw_id, "inbox_full", delivery_id=result.delivery_id)
    if result.status == "duplicate":
        response.status_code = 200
    else:
        await main_thread.notify(runtime, workspace_id, claw_id, delivery_id=result.delivery_id)
    return result


@router.get("/claws/{claw_id}/deliveries", name="claw_deliveries")
async def deliveries(
    claw_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    status: Literal["accepted", "duplicate", "full"] | None = None,
    inbox_state: Literal["unread", "read", "expired"] | None = None,
    cursor: str | None = None,
    limit: PageSize = 30,
) -> DeliveryPage:
    async with short_session(runtime.storage) as session:
        await get_claw(session, workspace_id, claw_id)
        cutoff = await now(session) - inbox.HISTORY_AGE
        query = (
            select(Delivery)
            .outerjoin(InboxItem, retained_item(cutoff))
            .where(Delivery.claw_id == claw_id, Delivery.created_at >= cutoff)
            .options(
                with_expression(Delivery.kind, InboxItem.kind),
                with_expression(Delivery.preview, InboxItem.preview),
                with_expression(Delivery.read_at, InboxItem.read_at),
            )
        )
        if inbox_state is not None:
            if inbox_state == "expired":
                query = query.where(Delivery.item_id.is_not(None), InboxItem.id.is_(None))
            else:
                query = query.where(
                    InboxItem.id.is_not(None),
                    InboxItem.read_at.is_(None)
                    if inbox_state == "unread"
                    else InboxItem.read_at.is_not(None),
                )
        if status is not None:
            query = query.where(Delivery.status == status)
        rows, next_cursor = await keyset_page(
            session,
            query,
            (Delivery.created_at, Delivery.id),
            kind="claw_delivery",
            owner=f"{claw_id}:{status or 'all'}:{inbox_state or 'all'}",
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        return DeliveryPage(
            items=[DeliveryView.model_validate(row) for row in rows], next_cursor=next_cursor
        )


def still_retained(cutoff: datetime):
    return or_(InboxItem.read_at.is_(None), InboxItem.read_at >= cutoff)


def retained_item(cutoff: datetime):
    return and_(
        InboxItem.claw_id == Delivery.claw_id,
        InboxItem.id == Delivery.item_id,
        still_retained(cutoff),
    )


@router.get("/claws/{claw_id}/deliveries/{delivery_id}", name="claw_delivery_detail")
async def delivery_detail(
    claw_id: ResourceId,
    delivery_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
) -> DeliveryDetail:
    async with short_session(runtime.storage) as session:
        cutoff = await now(session) - inbox.HISTORY_AGE
        result = (
            await session.execute(
                select(Delivery, InboxItem)
                .join(Claw, Claw.id == Delivery.claw_id)
                .outerjoin(InboxItem, retained_item(cutoff))
                .where(
                    *scope_claw(workspace_id, claw_id),
                    Delivery.id == delivery_id,
                    Delivery.created_at >= cutoff,
                )
            )
        ).one_or_none()
        if result is None:
            raise not_found("delivery", delivery_id)
        delivery, item = result
        return DeliveryDetail(
            **DeliveryView.model_validate(delivery).model_dump(
                exclude={"kind", "preview", "read_at"}
            ),
            kind=item.kind if item else None,
            preview=item.preview if item else None,
            content=item.content if item else None,
            source=item.source if item else None,
            read_at=item.read_at if item else None,
        )
