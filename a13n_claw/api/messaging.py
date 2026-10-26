"""Trusted operator API composing public Host operations and Claw business services."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import Field

from a13n_claw.api.dependencies import Application
from a13n_claw.async_utils import database
from a13n_claw.configuration.models import ProductModel
from a13n_claw.schedules.views import Occurrence

router = APIRouter()


class InboxSummary(ProductModel):
    id: str
    connection: str
    channel_id: str | None
    disposition: str
    reason: str
    blocked: str | None
    created: float


class AttemptSummary(ProductModel):
    id: str
    kind: str
    status: str
    receipt_id: str | None
    evidence: str
    created: float


class DeliverySummary(ProductModel):
    id: str
    channel_id: str
    status: str
    evidence: str
    created: float


class ThreadBusinessView(ProductModel):
    """Exact retained associations, with each collection independently bounded."""

    inbox: list[InboxSummary]
    attempts: list[AttemptSummary]
    occurrences: list[Occurrence]
    deliveries: list[DeliverySummary]
    limit: int


@router.get("/threads/{thread_id}/business", response_model=ThreadBusinessView)
async def thread_business(
    thread_id: str, app: Application, limit: int = Query(default=20, ge=1, le=100)
):
    await app.host.get_thread(thread_id)
    inbox = await database(
        app.store.rows,
        "SELECT i.id,i.connection,i.channel_id,i.disposition,i.reason,i.blocked,i.created "
        "FROM inbox i WHERE i.thread_id=? OR EXISTS "
        "(SELECT 1 FROM attempt_items ai JOIN attempts a ON a.id=ai.attempt_id "
        "WHERE ai.item_id=i.id AND a.thread_id=?) ORDER BY i.created DESC,i.id LIMIT ?",
        (thread_id, thread_id, limit),
    )
    attempts = await database(
        app.store.rows,
        "SELECT id,kind,status,receipt_id,evidence,created FROM attempts "
        "WHERE thread_id=? ORDER BY created DESC,id LIMIT ?",
        (thread_id, limit),
    )
    occurrences = await database(
        app.store.rows,
        "SELECT * FROM occurrences WHERE thread_id=? ORDER BY created DESC,id LIMIT ?",
        (thread_id, limit),
    )
    deliveries = await database(
        app.store.rows,
        "SELECT id,channel_id,status,evidence,created FROM deliveries "
        "WHERE thread_id=? ORDER BY created DESC,id LIMIT ?",
        (thread_id, limit),
    )
    return dict(
        inbox=inbox, attempts=attempts, occurrences=occurrences, deliveries=deliveries, limit=limit
    )


@router.get("/occurrences/{occurrence_id}/deliveries", response_model=list[DeliverySummary])
async def occurrence_deliveries(occurrence_id: str, app: Application):
    await database(app.scheduler.store.get, occurrence_id)
    return await database(
        app.store.rows,
        "SELECT d.id,d.channel_id,d.status,d.evidence,d.created FROM deliveries d "
        "JOIN job_deliveries j ON j.delivery_id=d.id "
        "WHERE j.occurrence_id=? ORDER BY d.created DESC,d.id",
        (occurrence_id,),
    )


class AttemptResolution(ProductModel):
    action: Literal["retry", "settle"]
    evidence: str = Field(min_length=1)


class DeliveryResolution(ProductModel):
    outcome: Literal["sent", "not_sent"]
    evidence: str = Field(min_length=1)
    retry: bool = False


class TargetResolution(ProductModel):
    key: str
    action: Literal["adopt", "retry"]
    evidence: str = Field(min_length=1)


class InboxChange(ProductModel):
    expected_version: int = Field(ge=1)
    disposition: Literal["handled", "ignored", "pending"]
    reason: str = Field(min_length=1)


@router.get("/targets")
async def targets(app: Application):
    return await database(app.store.rows, "SELECT * FROM targets ORDER BY key")


@router.post("/targets/resolve")
async def resolve_target(body: TargetResolution, app: Application):
    return await app.resolve_target(body.key, body.action, body.evidence)


@router.get("/channels")
async def channels(app: Application):
    return app.settings.channels


@router.get("/inbox")
async def inbox(
    app: Application,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    return await database(
        app.store.rows,
        "SELECT id,channel_id,thread_id,disposition,version,reason,blocked,"
        "next_check,dependency,created "
        "FROM inbox ORDER BY created,id LIMIT ? OFFSET ?",
        (limit, offset),
    )


@router.get("/inbox/{item_id}")
async def inbox_item(item_id: str, app: Application):
    import json

    row = await database(app.store.item, item_id)
    if row["body"] is not None:
        body = json.loads(row["body"])
        body["files"] = [{"name": f["name"]} for f in body["files"]]
        row["body"] = body
    return row


@router.patch("/inbox/{item_id}")
async def update_inbox(item_id: str, body: InboxChange, app: Application):
    async with app.lock:
        result = await database(
            app.store.settle, item_id, body.expected_version, body.disposition, body.reason
        )
        app.wake.set()
        return {key: result[key] for key in ("id", "disposition", "version", "reason")}


@router.get("/attempts")
async def attempts(app: Application, offset: int = Query(default=0, ge=0)):
    return await database(
        app.store.rows, "SELECT * FROM attempts ORDER BY created DESC LIMIT 100 OFFSET ?", (offset,)
    )


@router.post("/attempts/{attempt_id}/resolve")
async def resolve_attempt(attempt_id: str, body: AttemptResolution, app: Application):
    async with app.lock:
        await database(app.store.resolve_attempt, attempt_id, body.action, body.evidence)
        app.wake.set()
        return {"resolved": attempt_id}


@router.get("/deliveries")
async def deliveries(app: Application, offset: int = Query(default=0, ge=0)):
    return await database(
        app.store.rows,
        "SELECT * FROM deliveries ORDER BY created DESC LIMIT 100 OFFSET ?",
        (offset,),
    )


@router.post("/deliveries/{delivery_id}/resolve")
async def resolve_delivery(delivery_id: str, body: DeliveryResolution, app: Application):
    async with app.lock:
        result = await database(
            app.store.resolve_delivery, delivery_id, body.outcome, body.evidence, retry=body.retry
        )
        app.wake.set()
        return result
