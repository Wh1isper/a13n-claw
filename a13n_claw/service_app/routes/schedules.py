"""Claw schedules routes."""

from hashlib import sha256
from typing import Annotated, Literal

from a13n_service.infra.cursors import keyset_page
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.requests import Actor, WorkspaceId
from fastapi import Header
from pydantic import BaseModel
from sqlalchemy import select

from .. import inbox, main_thread, schedules
from ..models import (
    APP_ID,
    Delivery,
    Occurrence,
)
from .common import PageSize, ResourceId, get_claw, lock_active_claw, prepare_claw_admission

router = app_router(APP_ID)


@router.get("/claws/{claw_id}/schedules", name="claw_list_schedules")
async def list_schedules(
    claw_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    cursor: str | None = None,
    limit: PageSize = 50,
    target: Literal["main", "fresh"] | None = None,
) -> dict:
    async with short_session(runtime.storage) as session:
        claw = await get_claw(session, workspace_id, claw_id)
        rows, next_cursor = await schedules.list_schedules(session, claw, cursor, limit, target)
        return {
            "items": [schedules.ScheduleView.model_validate(row) for row in rows],
            "next_cursor": next_cursor,
        }


@router.get("/claws/{claw_id}/schedules/{schedule_id}", name="claw_schedule_detail")
async def schedule_detail(
    claw_id: ResourceId, schedule_id: ResourceId, workspace_id: WorkspaceId, runtime: CurrentRuntime
) -> schedules.ScheduleView:
    async with short_session(runtime.storage) as session:
        claw = await get_claw(session, workspace_id, claw_id)
        return schedules.ScheduleView.model_validate(
            await schedules.find_schedule(session, claw, schedule_id, include_deleted=True)
        )


@router.post("/claws/{claw_id}/schedules", status_code=201, name="claw_create_schedule")
async def create_schedule(
    claw_id: ResourceId,
    body: schedules.ScheduleInput,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    request_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> schedules.ScheduleView:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        return schedules.ScheduleView.model_validate(
            await schedules.create(session, claw, body, request_key=request_key)
        )


@router.post("/claws/{claw_id}/schedules/{schedule_id}/trigger", name="claw_trigger_schedule")
async def trigger_schedule(
    claw_id: ResourceId,
    schedule_id: ResourceId,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    request_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> schedules.OccurrenceView:
    occurrence_id = "ct_" + sha256(f"{claw_id}:{actor.id}:{request_key}".encode()).hexdigest()

    async def before(session):
        await workspace_scope(session, actor, workspace_id, "write")

    async def prepare(session):
        return await prepare_claw_admission(session, runtime, workspace_id, claw_id)

    async def lock(session):
        return await lock_active_claw(session, workspace_id, claw_id), None

    result = await schedules.trigger_occurrence(
        runtime,
        workspace_id,
        claw_id,
        schedule_id,
        occurrence_id,
        before=before,
        prepare=prepare,
        lock=lock,
    )
    if result.status == "unread":
        await main_thread.notify(runtime, workspace_id, claw_id)
    return result


class ScheduleState(BaseModel):
    enabled: bool


@router.patch("/claws/{claw_id}/schedules/{schedule_id}", name="claw_set_schedule_state")
async def set_schedule_state(
    claw_id: ResourceId,
    schedule_id: ResourceId,
    body: ScheduleState,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> schedules.ScheduleView:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        schedule = await schedules.set_enabled(session, claw, schedule_id, body.enabled)
        return schedules.ScheduleView.model_validate(schedule)


@router.put("/claws/{claw_id}/schedules/{schedule_id}", name="claw_edit_schedule")
async def edit_schedule(
    claw_id: ResourceId,
    schedule_id: ResourceId,
    body: schedules.ScheduleEdit,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> schedules.ScheduleView:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        return schedules.ScheduleView.model_validate(
            await schedules.edit(session, claw, schedule_id, body)
        )


@router.delete(
    "/claws/{claw_id}/schedules/{schedule_id}", status_code=204, name="claw_delete_schedule"
)
async def delete_schedule(
    claw_id: ResourceId,
    schedule_id: ResourceId,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> None:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        await schedules.remove(session, claw, schedule_id)


@router.get("/claws/{claw_id}/schedules/{schedule_id}/occurrences", name="claw_occurrences")
async def occurrences(
    claw_id: ResourceId,
    schedule_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    cursor: str | None = None,
    limit: PageSize = 50,
) -> dict:
    async with short_session(runtime.storage) as session:
        claw = await get_claw(session, workspace_id, claw_id)
        await schedules.find_schedule(session, claw, schedule_id, include_deleted=True)
        rows, next_cursor = await keyset_page(
            session,
            select(Occurrence).where(Occurrence.schedule_id == schedule_id),
            (Occurrence.scheduled_at, Occurrence.id),
            kind="claw_occurrence",
            owner=schedule_id,
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        item_ids = {row.item_id for row in rows if row.item_id is not None}
        links = (
            dict(
                (
                    await session.execute(
                        select(Delivery.item_id, Delivery.id).where(
                            Delivery.claw_id == claw.id,
                            Delivery.item_id.in_(item_ids),
                            Delivery.status == "accepted",
                            Delivery.created_at >= await now(session) - inbox.HISTORY_AGE,
                        )
                    )
                )
                .tuples()
                .all()
            )
            if item_ids
            else {}
        )
        return {
            "items": [
                schedules.OccurrenceView.model_validate(row).model_copy(
                    update={"delivery_id": links.get(row.item_id)}
                )
                for row in rows
            ],
            "next_cursor": next_cursor,
        }
