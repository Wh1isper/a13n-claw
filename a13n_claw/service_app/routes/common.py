"""Claw common routes."""

from typing import Annotated

from a13n_service.app_sdk import threads as app_threads
from a13n_service.infra.errors import not_found
from a13n_service.runs.runtime import Runtime
from fastapi import Query
from pydantic import StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import inbox
from ..models import (
    APP_ID,
    Claw,
    claw_context,
)

ResourceId = Annotated[str, StringConstraints(min_length=1, max_length=64)]


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


PageSize = Annotated[int, Query(ge=1, le=100)]


def scope_claw(workspace_id: str, claw_id: str):
    return Claw.workspace_id == workspace_id, Claw.id == claw_id, Claw.archived_at.is_(None)


async def get_claw(session: AsyncSession, workspace_id: str, claw_id: str) -> Claw:
    claw = await session.scalar(select(Claw).where(*scope_claw(workspace_id, claw_id)))
    if claw is None:
        raise not_found("claw", claw_id)
    return claw


async def lock_active_claw(session: AsyncSession, workspace_id: str, claw_id: str) -> Claw:
    try:
        return await inbox.lock_claw(session, workspace_id, claw_id)
    except inbox.ClawNotFound:
        raise not_found("claw", claw_id) from None


async def prepare_claw_admission(
    session: AsyncSession, runtime: Runtime, workspace_id: str, claw_id: str
) -> app_threads.Admission:
    # Service App locks precede Claw locks for every SDK write.
    account_id = await session.scalar(
        select(Claw.service_account_id).where(
            Claw.workspace_id == workspace_id, Claw.id == claw_id, Claw.archived_at.is_(None)
        )
    )
    if account_id is None:
        raise not_found("claw", claw_id)
    return await app_threads.prepare_admission(
        session, runtime, workspace_id, account_id, app_id=APP_ID, app_context=claw_context(claw_id)
    )
