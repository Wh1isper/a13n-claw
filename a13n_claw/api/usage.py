"""Recorded consumption, delegated to the Host's retained accounting ledger."""

from typing import Annotated

from a13n_harness_ui.storage.usage import ThreadUsageView
from a13n_harness_ui.storage.usage_analytics import UsageAnalyticsView, UsageQuery
from fastapi import APIRouter, Query

from a13n_claw.api.dependencies import Application

router = APIRouter()


@router.get("/usage")
async def usage(app: Application, query: Annotated[UsageQuery, Query()]) -> UsageAnalyticsView:
    return await app.host.usage_analytics(query)


@router.get("/threads/{thread_id}/usage")
async def thread_usage(thread_id: str, app: Application) -> ThreadUsageView:
    return await app.host.thread_usage(thread_id=thread_id)
