"""Managed native Worker Threads and exact-Run steering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from a13n_service.infra.db import lock, now
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.attempts import AuthorityRevoked, authorize_execution
from a13n_service.runs.schedules.origin import origins
from a13n_service.runs.schedules.tables import ScheduleRow
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.tenancy.authorize import (
    Principal,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Claw

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime


@dataclass(frozen=True, slots=True)
class Binding:
    claw: Claw
    run: RunRow
    role: Literal["main", "worker", "scheduled"]
    attempt_id: str


async def bind(session: AsyncSession, run_id: str, attempt_id: str) -> Binding | None:
    pair = (
        await session.execute(
            select(RunRow, Claw)
            .join(Claw, Claw.main_thread_id == RunRow.thread_id)
            .join(AttemptRow, AttemptRow.id == RunRow.current_attempt_id)
            .where(
                RunRow.id == run_id,
                AttemptRow.id == attempt_id,
                AttemptRow.lease_expires_at > await now(session),
                RunRow.status == "running",
                Claw.workspace_id == RunRow.workspace_id,
                Claw.service_account_id == RunRow.principal_id,
                Claw.archived_at.is_(None),
            )
        )
    ).one_or_none()
    if pair is not None:
        run, claw = pair
        return Binding(claw, run, "main", attempt_id)
    origin = (await origins(session, [run_id])).get(run_id)
    if origin is None:
        return None
    schedule = await session.get(ScheduleRow, origin.schedule_id)
    run = await session.get(RunRow, run_id)
    if schedule is None or run is None or run.current_attempt_id != attempt_id:
        return None
    claw = (
        await session.get(Claw, schedule.labels.get("claw"))
        if schedule.labels.get("claw")
        else None
    )
    if (
        claw is None
        or claw.archived_at is not None
        or claw.workspace_id != run.workspace_id
        or run.principal_id != claw.service_account_id
        or schedule.principal_id != claw.service_account_id
    ):
        return None
    return Binding(claw, run, "scheduled", attempt_id)


async def fence(
    session: AsyncSession, runtime: Runtime, claw: Claw, binding: Binding
) -> tuple[RunRow, Principal]:
    # The in-process Harness host supplies the exact Attempt ID. Service claim locks
    # Run then Attempt; check database clock time after taking the same locks.
    run = await lock(session, RunRow, binding.run.id)
    attempt = await lock(session, AttemptRow, binding.attempt_id)
    if (
        run is None
        or run.status != "running"
        or run.cancel_requested_at is not None
        or run.principal_id != claw.service_account_id
        or (binding.role == "main" and run.thread_id != claw.main_thread_id)
        or attempt is None
        or run.current_attempt_id != attempt.id
        or attempt.run_id != run.id
        or attempt.status not in ("leased", "running")
        or attempt.lease_expires_at <= await now(session)
    ):
        raise ServiceError("conflict", "This Claw Run is no longer active")
    try:
        return run, await authorize_execution(session, runtime.access, run)
    except AuthorityRevoked:
        raise ServiceError("forbidden", "This Claw Run no longer has execution access") from None
