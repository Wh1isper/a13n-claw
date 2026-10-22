"""Trusted operator API composing public Host operations and Claw business services."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import Field

from a13n_claw import __version__
from a13n_claw.api.dependencies import Application
from a13n_claw.async_utils import database
from a13n_claw.configuration.models import ProductModel
from a13n_claw.operations import maintenance

router = APIRouter()


class RetentionRequest(ProductModel):
    older_than_days: int = Field(ge=1)
    apply: bool = False


@router.get("/status")
async def status(app: Application):
    return {
        "version": __version__,
        "instance": await database(app.store.instance),
        "reconciliation_error": app.last_error,
        "scheduler": app.scheduler.view(),
        "connection_errors": app.connection_errors,
        "host": "open",
        "connectivity": "not_probed",
        "connections": {
            key: {
                "platform": config.platform,
                "enabled": config.enabled,
                "status": app.adapters[key].status if key in app.adapters else "disabled",
                "interactions": (
                    ["text", "files", "mentions", "commands", "decision_json"]
                    if config.platform == "lark"
                    else ["polling", "comments"]
                ),
            }
            for key, config in app.settings.connections.items()
        },
    }


@router.get("/diagnostics")
async def diagnostics(app: Application):
    return await maintenance.diagnostics(app)


@router.post("/retention")
async def retention(body: RetentionRequest, app: Application):
    return await maintenance.retain(app, older_than_days=body.older_than_days, apply=body.apply)


@router.get("/github")
async def github_status(app: Application):
    return await database(app.store.rows, "SELECT * FROM github_poll_state ORDER BY connection")


@router.get("/github/events")
async def github_events(app: Application, offset: int = Query(default=0, ge=0)):
    return await database(
        app.store.rows,
        "SELECT connection,event_key,notification_id,updated,next_check,state,read_status,detail "
        "FROM github_events ORDER BY updated DESC LIMIT 100 OFFSET ?",
        (offset,),
    )
