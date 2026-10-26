"""Trusted operator API composing public Host operations and Claw business services."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import Field

from a13n_claw.api.dependencies import Application
from a13n_claw.async_utils import database
from a13n_claw.configuration.models import ProductModel
from a13n_claw.schedules.models import ScheduleCatalog, ScheduleDefinition
from a13n_claw.schedules.views import (
    Occurrence,
    OccurrenceSummary,
    ScheduleCatalogView,
    ScheduleStatus,
    ScheduleView,
    TimingPreview,
    TimingPreviewResult,
    preview,
)

router = APIRouter()


class ScheduleWrite(ProductModel):
    definition: ScheduleDefinition | None
    expected_digest: str | None


class ScheduleReplace(ProductModel):
    catalog: ScheduleCatalog
    expected_digest: str | None


class ScheduleTrigger(ProductModel):
    request_key: str = Field(min_length=1, max_length=200)


class OccurrenceResolution(ProductModel):
    action: Literal["retry", "abandon"]
    evidence: str = Field(min_length=1, max_length=4000)


class OccurrenceAttach(ProductModel):
    receipt_id: str = Field(min_length=1)


@router.get("/schedules", response_model=ScheduleCatalogView)
async def schedules(app: Application):
    return app.scheduler.view()


@router.get("/schedules/status")
async def schedule_status(app: Application) -> ScheduleView:
    async with app.lock:
        scheduler = app.scheduler
        clocks = {row["id"]: row for row in await database(scheduler.store.clocks)}
        latest = {row["schedule_id"]: row for row in await database(scheduler.store.latest)}
        status = {}
        for key, definition in scheduler.catalog.schedules.items():
            clock = clocks.get(key)
            current = clock is not None and clock["revision"] == definition.revision
            due = clock["next_due"] if current and clock is not None else None
            state = (
                "paused"
                if not definition.enabled
                else "pending_discovery"
                if not current
                else "finished"
                if due is None
                else "scheduled"
            )
            row = latest.get(key)
            status[key] = ScheduleStatus(
                state=state,
                next_due=datetime.fromtimestamp(due, UTC)
                if definition.enabled and due is not None
                else None,
                latest=OccurrenceSummary.model_validate(
                    {field: row[field] for field in OccurrenceSummary.model_fields}
                )
                if row is not None
                else None,
            )
        return ScheduleView(
            digest=scheduler.digest, catalog=scheduler.catalog, error=scheduler.error, status=status
        )


@router.post("/schedules/preview")
async def timing_preview(body: TimingPreview) -> TimingPreviewResult:
    return preview(body)


@router.put("/schedules", response_model=ScheduleCatalogView)
async def replace_schedules(body: ScheduleReplace, app: Application):
    async with app.lock:
        return await app.scheduler.replace(body.catalog, body.expected_digest)


@router.post("/schedules/reload", response_model=ScheduleCatalogView)
async def reload_schedules(app: Application):
    async with app.lock:
        return await app.scheduler.reload()


@router.put("/schedules/{schedule_id}", response_model=ScheduleCatalogView)
async def put_schedule(schedule_id: str, body: ScheduleWrite, app: Application):
    async with app.lock:
        return await app.scheduler.put(schedule_id, body.definition, body.expected_digest)


@router.post("/schedules/{schedule_id}/trigger", status_code=202, response_model=Occurrence)
async def trigger_schedule(schedule_id: str, body: ScheduleTrigger, app: Application):
    async with app.lock:
        return await app.scheduler.trigger(schedule_id, body.request_key)


@router.get("/occurrences", response_model=list[Occurrence])
async def occurrences(
    app: Application, schedule_id: str | None = None, limit: int = Query(default=50, ge=1, le=100)
):
    return await database(app.scheduler.store.list, schedule_id, limit)


@router.get("/occurrences/{occurrence_id}", response_model=Occurrence)
async def occurrence(occurrence_id: str, app: Application):
    return await database(app.scheduler.store.get, occurrence_id)


@router.post("/occurrences/{occurrence_id}/resolve")
async def resolve_occurrence(occurrence_id: str, body: OccurrenceResolution, app: Application):
    async with app.lock:
        result = await database(
            app.scheduler.store.resolve, occurrence_id, body.action, body.evidence
        )
        app.wake.set()
        return result


@router.post("/occurrences/{occurrence_id}/attach")
async def attach_occurrence(occurrence_id: str, body: OccurrenceAttach, app: Application):
    async with app.lock:
        return await app.scheduler.attach(occurrence_id, body.receipt_id)


@router.post("/occurrences/{occurrence_id}/cancel")
async def cancel_occurrence(occurrence_id: str, app: Application):
    async with app.lock:
        return await app.scheduler.cancel(occurrence_id)
