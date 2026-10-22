"""Trusted operator API composing public Host operations and Claw business services."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import Field

from a13n_claw.api.dependencies import Application
from a13n_claw.async_utils import database
from a13n_claw.configuration.models import ProductModel

router = APIRouter()


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
