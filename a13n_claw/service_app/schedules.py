"""Main-mode schedule state. Service supplies the clock and owns fresh-Thread schedules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from a13n_service.infra.cursors import keyset_page
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import conflict, invalid, not_found
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.resources.memories.schemas import MemoryMount
from a13n_service.resources.memories.tables import MemorySpaceRow
from a13n_service.runs.schedules import service as native_service
from a13n_service.runs.schedules.calendar import Calendar
from a13n_service.runs.schedules.schemas import (
    CronSchedule,
    OnceSchedule,
    ScheduleCreate,
    ScheduleInvocation,
)
from a13n_service.runs.schemas import ExistingRunEnvironment
from a13n_service.runs.tables import RunRow
from a13n_service.tenancy.authorize import Principal, WorkspaceScope
from a13n_service.tenancy.tables import WorkspaceRow
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from . import inbox, main_thread
from .models import Claw, Occurrence, Schedule

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=32000)
    rule: CronSchedule | OnceSchedule = Field(discriminator="type")
    lateness_seconds: int = Field(default=300, ge=60, le=86400)


async def create_fresh(runtime: Runtime, claw: Claw, body: ScheduleInput, request_key: str):
    async with short_session(runtime.storage) as session:
        workspace = await session.get_one(WorkspaceRow, claw.workspace_id)
        scope = WorkspaceScope(workspace.organization_id, workspace.id)
        actor: Principal = await main_thread.account(
            session, runtime, scope, claw.service_account_id
        )
        agent = await session.get_one(AgentRow, claw.agent_id)
        space = await session.get_one(MemorySpaceRow, claw.memory_space_id)
        if not space.enabled:
            raise conflict("memory space", space.id, "disabled")
        options = await main_thread.options(session, claw, agent, role="scheduled")
        options = options.model_copy(
            update={
                "environment": ExistingRunEnvironment(
                    mode="existing", environment_id=claw.environment_id
                )
            }
        )
        invocation = ScheduleInvocation(
            agent_id=agent.id,
            agent_revision_id=main_thread.selected_revision(claw, agent),
            payload=main_thread.payload(body.prompt),
            options=options,
            memories=(MemoryMount(name="project", memory_id=space.memory_id, access="write"),),
        )
    schedule, _ = await native_service.create(
        runtime,
        actor,
        claw.workspace_id,
        ScheduleCreate(
            name=body.name,
            labels={"claw": claw.id},
            schedule=body.rule,
            max_lateness_seconds=body.lateness_seconds,
            invocation=invocation,
            state="enabled",
        ),
        request_key=request_key,
    )
    return schedule


class ScheduleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    prompt: str
    cron: str | None
    timezone: str
    lateness_seconds: int
    next_at: datetime | None
    enabled: bool
    created_at: datetime


class OccurrenceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    schedule_id: str
    scheduled_at: datetime
    status: str
    item_id: str | None
    run_id: str | None


async def create(
    session: AsyncSession, claw: Claw, body: ScheduleInput, *, schedule_id: str | None = None
) -> Schedule:
    if (
        schedule_id is not None
        and (existing := await session.get(Schedule, schedule_id)) is not None
    ):
        return existing
    current = await now(session)
    rule = body.rule
    if isinstance(rule, CronSchedule):
        next_at = Calendar.parse(rule.expression, rule.timezone).next(current)
        cron, timezone = rule.expression, rule.timezone
    else:
        if rule.run_at <= current:
            raise invalid("rule.run_at", "must_be_in_the_future")
        next_at, cron, timezone = rule.run_at, None, "UTC"
    row = Schedule(
        id=schedule_id or inbox.new_id("sch"),
        claw_id=claw.id,
        name=body.name,
        prompt=body.prompt,
        cron=cron,
        timezone=timezone,
        next_at=next_at,
        lateness_seconds=body.lateness_seconds,
        enabled=True,
    )
    session.add(row)
    await session.flush()
    return row


async def list_schedules(session: AsyncSession, claw: Claw, cursor: str | None, limit: int):
    return await keyset_page(
        session,
        select(Schedule).where(Schedule.claw_id == claw.id),
        (Schedule.created_at, Schedule.id),
        kind="claw_schedule",
        owner=claw.id,
        cursor=cursor,
        limit=limit,
        newest_first=True,
    )


async def find_schedule(session: AsyncSession, claw: Claw, schedule_id: str) -> Schedule:
    row = await session.scalar(
        select(Schedule).where(Schedule.claw_id == claw.id, Schedule.id == schedule_id)
    )
    if row is None:
        raise not_found("schedule", schedule_id)
    return row


async def set_enabled(
    session: AsyncSession, claw: Claw, schedule_id: str, enabled: bool
) -> Schedule:
    schedule = await find_schedule(session, claw, schedule_id)
    if enabled and not schedule.enabled and schedule.cron is not None:
        schedule.next_at = Calendar.parse(schedule.cron, schedule.timezone).next(await now(session))
    if enabled and schedule.next_at is None:
        raise conflict("schedule", schedule_id, "one_time_schedule_fired")
    schedule.enabled = enabled
    return schedule


async def settle(session: AsyncSession, schedule_id: str) -> bool:
    """An occurrence remains outstanding across an exact native decision/resume."""
    row = await session.scalar(
        select(Occurrence).where(
            Occurrence.schedule_id == schedule_id,
            Occurrence.status.in_(("unread", "processing")),
        )
    )
    if row is None:
        return False
    if row.run_id is None:
        return True
    run = await session.get(RunRow, row.run_id)
    if run is None:
        row.status = "unknown"
        return False
    if run.status == "waiting":
        successor = await session.scalar(
            select(RunRow)
            .where(
                RunRow.thread_id == run.thread_id,
                RunRow.parent_run_id == run.id,
                RunRow.resume.is_not(None),
            )
            .order_by(RunRow.created_at, RunRow.id)
            .limit(1)
        )
        if successor is None:
            return True
        row.run_id = successor.id
        run = successor
    if run.status in {"completed", "failed", "cancelled"}:
        row.status = run.status
        return False
    return True


async def sweep(runtime: Runtime, workspace_id: str, claw_id: str) -> None:
    async with short_session(runtime.storage) as session:
        current = await now(session)
        due = (
            select(Schedule.id)
            .where(Schedule.claw_id == claw_id, Schedule.enabled, Schedule.next_at <= current)
            .order_by(Schedule.next_at, Schedule.id)
            .limit(50)
        )
        successor = aliased(RunRow)
        resumed = (
            select(successor.id)
            .where(successor.parent_run_id == RunRow.id, successor.resume.is_not(None))
            .exists()
        )
        processing = (
            select(Schedule.id)
            .join(Occurrence, Occurrence.schedule_id == Schedule.id)
            .outerjoin(RunRow, RunRow.id == Occurrence.run_id)
            .where(
                Schedule.claw_id == claw_id,
                Occurrence.status == "processing",
                or_(
                    RunRow.id.is_(None),
                    RunRow.status.in_(("completed", "failed", "cancelled")),
                    (RunRow.status == "waiting") & resumed,
                ),
            )
            .order_by(Occurrence.scheduled_at, Occurrence.id)
            .limit(50)
        )
        schedule_ids = dict.fromkeys(
            [*(await session.scalars(due)), *(await session.scalars(processing))]
        )
    for schedule_id in schedule_ids:
        async with transaction(runtime.storage) as session:
            claw = await inbox.lock_claw(session, workspace_id, claw_id)
            row = await find_schedule(session, claw, schedule_id)
            outstanding = await settle(session, row.id)
            current = await now(session)
            if not row.enabled or row.next_at is None or row.next_at > current:
                continue
            scheduled_at = row.next_at
            if row.cron is None:
                row.next_at = None
                row.enabled = False
            else:
                calendar = Calendar.parse(row.cron, row.timezone)
                scheduled_at = calendar.previous(current + timedelta(microseconds=1))
                row.next_at = calendar.next(current)
            occurrence = Occurrence(
                id=inbox.new_id("occ"),
                schedule_id=row.id,
                scheduled_at=scheduled_at,
                status="unread",
            )
            if current - scheduled_at > timedelta(seconds=row.lateness_seconds):
                occurrence.status = "late"
            elif outstanding:
                occurrence.status = "overlap"
            else:
                title = (
                    f"Scheduled occurrence: {row.name} · {scheduled_at.astimezone(UTC).isoformat()}"
                )
                result = await inbox.admit(
                    session,
                    claw,
                    inbox.Offer(
                        "schedule",
                        "schedule",
                        f"schedule:{occurrence.id}",
                        {"schedule_id": row.id, "occurrence_id": occurrence.id},
                        row.prompt,
                        title,
                    ),
                )
                occurrence.item_id = result.item_id
                occurrence.status = "unread" if result.status == "accepted" else "full"
            session.add(occurrence)
