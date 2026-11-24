"""Main-mode schedule state. Service supplies the clock and owns fresh-Thread schedules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from a13n_service.app_sdk import schedules as app_schedules
from a13n_service.infra.cursors import keyset_page
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import conflict, invalid, not_found
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.resources.memories.tables import MemorySpaceRow
from a13n_service.runs.schedules.calendar import Calendar
from a13n_service.runs.schedules.schemas import (
    CronSchedule,
    OnceSchedule,
    ScheduleCreate,
    ScheduleInvocation,
    ScheduleTarget,
)
from a13n_service.runs.tables import RunRow
from a13n_service.tenancy.authorize import (
    Principal,
    WorkspaceScope,
)
from a13n_service.tenancy.tables import WorkspaceRow
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from . import inbox, main_thread
from .models import APP_ID, Claw, Occurrence, Schedule

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=32000)
    rule: CronSchedule | OnceSchedule = Field(discriminator="type")
    lateness_seconds: int = Field(default=300, ge=60, le=86400)


async def fresh_definition(
    session: AsyncSession, runtime: Runtime, claw: Claw, body: ScheduleInput, actor: Principal
) -> ScheduleCreate:
    if claw.environment_id is None:
        raise conflict("claw", claw.id, "project_environment_required")
    agent = await session.get(AgentRow, claw.agent_id)
    if agent is None:
        raise not_found("agent", claw.agent_id)
    space = await session.get(MemorySpaceRow, claw.memory_space_id)
    if space is None:
        raise not_found("memory space", claw.memory_space_id)
    if not space.enabled:
        raise conflict("memory space", space.id, "disabled")
    if claw.schedule_session_id is None:
        details = main_thread.project_session(claw, f"{claw.name} · Scheduled work")
        claw.schedule_session_id = await app_schedules.ensure_schedule_session(
            session,
            runtime,
            claw.workspace_id,
            app_id=APP_ID,
            principal_id=actor.id,
            existing_session_id=None,
            session_create=details,
        )
    return ScheduleCreate(
        name=body.name,
        labels={"claw": claw.id},
        schedule=body.rule,
        max_lateness_seconds=body.lateness_seconds,
        invocation=ScheduleInvocation(
            agent_id=agent.id,
            agent_revision_id=main_thread.selected_revision(claw, agent),
            payload=main_thread.payload(body.prompt),
            options=await main_thread.options(session, claw, agent, role="scheduled"),
            # Native Schedule invocations are immutable. Do not capture a
            # revocable connector generation in a recurring definition.
            environments=main_thread.project_mounts(claw),
            browser_binding=main_thread.project_browser(claw),
        ),
        target=ScheduleTarget(mode="new_thread", session_id=claw.schedule_session_id),
        state="enabled",
    )


async def fresh_account(session: AsyncSession, runtime: Runtime, claw: Claw) -> Principal:
    workspace = await session.get_one(WorkspaceRow, claw.workspace_id)
    scope = WorkspaceScope(workspace.organization_id, workspace.id)
    return await main_thread.automation_account(session, runtime, scope, claw.service_account_id)


async def fresh_schedule_count(session: AsyncSession, claw: Claw) -> int:
    return await app_schedules.count_active_fresh_schedules(
        session, claw.workspace_id, app_id=APP_ID, labels={"claw": claw.id}
    )


async def require_no_fresh_work(session: AsyncSession, claw: Claw, *, reason: str) -> None:
    if await fresh_schedule_count(session, claw):
        raise conflict("claw", claw.id, reason)


async def create_fresh_in_transaction(
    session: AsyncSession,
    runtime: Runtime,
    claw: Claw,
    body: ScheduleInput,
    request_key: str,
    actor: Principal,
):
    definition = await fresh_definition(session, runtime, claw, body, actor)
    return await app_schedules.create_fresh_schedule(
        session,
        runtime,
        claw.workspace_id,
        app_id=APP_ID,
        actor_id=actor.id,
        body=definition,
        request_key=request_key,
    )


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
    deleted_at: datetime | None
    created_at: datetime


class OccurrenceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    schedule_id: str
    scheduled_at: datetime
    status: str
    item_id: str | None
    run_id: str | None
    delivery_id: str | None = None


async def create(
    session: AsyncSession, claw: Claw, body: ScheduleInput, *, schedule_id: str | None = None
) -> Schedule:
    if (
        schedule_id is not None
        and (existing := await session.get(Schedule, schedule_id)) is not None
    ):
        return existing
    cron, timezone, next_at = schedule_rule(body.rule, await now(session))
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


def schedule_rule(
    rule: CronSchedule | OnceSchedule, current: datetime
) -> tuple[str | None, str, datetime]:
    if isinstance(rule, CronSchedule):
        return (
            rule.expression,
            rule.timezone,
            Calendar.parse(rule.expression, rule.timezone).next(current),
        )
    if rule.run_at <= current:
        raise invalid("rule.run_at", "must_be_in_the_future")
    return None, "UTC", rule.run_at


class ScheduleEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=32000)
    rule: CronSchedule | OnceSchedule | None = Field(default=None, discriminator="type")
    lateness_seconds: int = Field(ge=60, le=86400)


async def edit(session: AsyncSession, claw: Claw, schedule_id: str, body: ScheduleEdit) -> Schedule:
    row = await find_schedule(session, claw, schedule_id)
    if body.rule is not None:
        cron, timezone, next_at = schedule_rule(body.rule, await now(session))
        if cron is None and await occurred(session, row.id, next_at):
            raise conflict("schedule", row.id, "occurrence_already_recorded")
        row.cron, row.timezone, row.next_at = cron, timezone, next_at
    row.name, row.prompt, row.lateness_seconds = body.name, body.prompt, body.lateness_seconds
    return row


async def occurred(session: AsyncSession, schedule_id: str, at: datetime) -> bool:
    return (
        await session.scalar(
            select(Occurrence.id).where(
                Occurrence.schedule_id == schedule_id, Occurrence.scheduled_at == at
            )
        )
    ) is not None


async def remove(session: AsyncSession, claw: Claw, schedule_id: str) -> None:
    row = await find_schedule(session, claw, schedule_id, include_deleted=True)
    if row.deleted_at is None:
        row.enabled, row.next_at, row.deleted_at = False, None, await now(session)


async def list_schedules(session: AsyncSession, claw: Claw, cursor: str | None, limit: int):
    return await keyset_page(
        session,
        select(Schedule).where(Schedule.claw_id == claw.id, Schedule.deleted_at.is_(None)),
        (Schedule.created_at, Schedule.id),
        kind="claw_schedule",
        owner=claw.id,
        cursor=cursor,
        limit=limit,
        newest_first=True,
    )


async def find_schedule(
    session: AsyncSession, claw: Claw, schedule_id: str, *, include_deleted: bool = False
) -> Schedule:
    query = select(Schedule).where(Schedule.claw_id == claw.id, Schedule.id == schedule_id)
    if not include_deleted:
        query = query.where(Schedule.deleted_at.is_(None))
    row = await session.scalar(query)
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


async def trigger(
    session: AsyncSession, claw: Claw, schedule_id: str, *, occurrence_id: str
) -> Occurrence:
    """Record an immediate occurrence without moving the schedule's future rule."""
    row = await find_schedule(session, claw, schedule_id, include_deleted=True)
    previous = await session.get(Occurrence, occurrence_id)
    if previous is not None:
        if previous.schedule_id != row.id:
            raise conflict("occurrence", occurrence_id, "schedule_mismatch")
        return previous
    if row.deleted_at is not None:
        raise not_found("schedule", schedule_id)
    current = await now(session)
    while await occurred(session, row.id, current):
        current += timedelta(microseconds=1)
    occurrence = Occurrence(
        id=occurrence_id, schedule_id=row.id, scheduled_at=current, status="unread"
    )
    if await settle(session, row.id):
        occurrence.status = "overlap"
    else:
        title = f"Scheduled occurrence: {row.name} · {current.astimezone(UTC).isoformat()}"
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
    await session.flush()
    return occurrence


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
            row = await find_schedule(session, claw, schedule_id, include_deleted=True)
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
            if await occurred(session, row.id, scheduled_at):
                continue
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
