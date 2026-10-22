"""Trusted operator API composing public Host operations and Claw business services."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import Field

from a13n_claw.api.dependencies import Application
from a13n_claw.async_utils import database
from a13n_claw.configuration.models import ProductModel
from a13n_claw.schedules.models import ScheduleCatalog, ScheduleDefinition

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


@router.get("/schedules")
async def schedules(app: Application):
    return app.scheduler.view()


@router.put("/schedules")
async def replace_schedules(body: ScheduleReplace, app: Application):
    async with app.lock:
        return await app.scheduler.replace(body.catalog, body.expected_digest)


@router.post("/schedules/reload")
async def reload_schedules(app: Application):
    async with app.lock:
        return await app.scheduler.reload()


@router.put("/schedules/{schedule_id}")
async def put_schedule(schedule_id: str, body: ScheduleWrite, app: Application):
    async with app.lock:
        return await app.scheduler.put(schedule_id, body.definition, body.expected_digest)


@router.post("/schedules/{schedule_id}/trigger", status_code=202)
async def trigger_schedule(schedule_id: str, body: ScheduleTrigger, app: Application):
    async with app.lock:
        return await app.scheduler.trigger(schedule_id, body.request_key)


@router.get("/occurrences")
async def occurrences(
    app: Application, schedule_id: str | None = None, limit: int = Query(default=50, ge=1, le=100)
):
    return await database(app.scheduler.store.list, schedule_id, limit)


@router.get("/occurrences/{occurrence_id}")
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
