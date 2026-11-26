"""Claw-owned schedule and occurrence state."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Literal

from a13n_service.app_sdk import schedules as app_schedules
from a13n_service.app_sdk import threads as app_threads
from a13n_service.infra.cursors import keyset_page, query_owner
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.resources.environment_instances.tables import EnvironmentRow
from a13n_service.resources.memories.tables import MemorySpaceRow
from a13n_service.runs.schedules.calendar import Calendar
from a13n_service.runs.schedules.schemas import (
    CronSchedule,
    OnceSchedule,
)
from a13n_service.runs.tables import RunRow
from a13n_service.tenancy.authorize import (
    WorkspaceScope,
)
from a13n_service.tenancy.tables import GrantRow, PrincipalRow, WorkspaceRow
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from . import inbox, main_thread
from .models import APP_ID, Claw, Occurrence, Schedule, claw_context

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=32000)
    target: Literal["main", "fresh"] = "main"
    rule: CronSchedule | OnceSchedule = Field(discriminator="type")
    lateness_seconds: int = Field(default=300, ge=60, le=86400)


class ScheduleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    prompt: str
    target: Literal["main", "fresh"]
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
    error: str | None = None
    item_id: str | None
    session_id: str | None = None
    thread_id: str | None = None
    run_id: str | None
    delivery_id: str | None = None


async def create(
    session: AsyncSession,
    claw: Claw,
    body: ScheduleInput,
    *,
    schedule_id: str | None = None,
    request_key: str | None = None,
) -> Schedule:
    existing = None
    if request_key is not None:
        existing = await session.scalar(
            select(Schedule).where(Schedule.claw_id == claw.id, Schedule.request_key == request_key)
        )
    elif schedule_id is not None:
        existing = await session.get(Schedule, schedule_id)
    if existing is not None:
        digest = sha256(body.model_dump_json().encode()).hexdigest()
        if existing.claw_id != claw.id or existing.request_digest != digest:
            raise conflict("schedule", existing.id, "idempotency_key_reused")
        return existing
    if body.target == "fresh" and claw.environment_id is None:
        raise conflict("claw", claw.id, "project_environment_required")
    cron, timezone, next_at = schedule_rule(body.rule, await now(session))
    row = Schedule(
        id=schedule_id or inbox.new_id("sch"),
        claw_id=claw.id,
        name=body.name,
        prompt=body.prompt,
        target=body.target,
        request_key=request_key,
        request_digest=sha256(body.model_dump_json().encode()).hexdigest()
        if schedule_id or request_key
        else None,
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
    target: Literal["main", "fresh"] | None = None
    lateness_seconds: int = Field(ge=60, le=86400)


async def edit(session: AsyncSession, claw: Claw, schedule_id: str, body: ScheduleEdit) -> Schedule:
    row = await find_schedule(session, claw, schedule_id)
    if body.target == "fresh" and claw.environment_id is None:
        raise conflict("claw", claw.id, "project_environment_required")
    if row.target == "fresh" and body.target == "main" and await settle(session, row.id):
        raise conflict("schedule", row.id, "fresh_occurrence_outstanding")
    if body.target is not None:
        row.target = body.target
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


async def list_schedules(
    session: AsyncSession,
    claw: Claw,
    cursor: str | None,
    limit: int,
    target: Literal["main", "fresh"] | None = None,
):
    query = select(Schedule).where(Schedule.claw_id == claw.id, Schedule.deleted_at.is_(None))
    if target is not None:
        query = query.where(Schedule.target == target)
    return await keyset_page(
        session,
        query,
        (Schedule.created_at, Schedule.id),
        kind="claw_schedule",
        owner=query_owner(claw.id, target),
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


async def start_fresh(
    session: AsyncSession,
    runtime: Runtime,
    admission: app_threads.Admission,
    claw: Claw,
    row: Schedule,
    occurrence: Occurrence,
) -> None:
    """Create a scheduled native Thread in the prepared outer transaction."""
    if claw.environment_id is None:
        raise conflict("claw", claw.id, "project_environment_required")
    state = await session.scalar(
        select(EnvironmentRow.status).where(
            EnvironmentRow.workspace_id == claw.workspace_id,
            EnvironmentRow.id == claw.environment_id,
        )
    )
    if state not in {"reserved", "creating", "ready", "stopped", "starting"}:
        raise conflict("environment", claw.environment_id, "unavailable")
    agent = await session.get(AgentRow, claw.agent_id)
    if agent is None or agent.workspace_id != claw.workspace_id or agent.archived_at is not None:
        raise conflict("agent", claw.agent_id, "unavailable")
    space = await session.get(MemorySpaceRow, claw.memory_space_id)
    if space is None or not space.enabled:
        raise conflict("memory space", claw.memory_space_id, "unavailable")
    workspace = await session.get_one(WorkspaceRow, claw.workspace_id)
    actor = await main_thread.automation_account(
        session,
        runtime,
        WorkspaceScope(workspace.organization_id, workspace.id),
        claw.service_account_id,
    )
    claw.schedule_session_id = await app_schedules.ensure_schedule_session(
        session,
        runtime,
        claw.workspace_id,
        app_id=APP_ID,
        principal_id=actor.id,
        app_context=claw_context(claw.id),
        existing_session_id=claw.schedule_session_id,
        session_create=main_thread.project_session(claw, f"{claw.name} · Scheduled work"),
    )
    body = app_threads.NewThread(
        agent_id=agent.id,
        agent_revision_id=main_thread.selected_revision(claw, agent),
        payload=main_thread.payload(row.prompt),
        options=await main_thread.options(session, claw, agent, role="scheduled"),
        session_id=claw.schedule_session_id,
        environments=main_thread.project_mounts(claw),
        browser_binding=main_thread.project_browser(claw),
    )
    thread, receipt = await app_threads.create_thread_in_transaction(
        session,
        runtime,
        admission,
        claw.workspace_id,
        body,
        request_key=f"claw:schedule:{occurrence.id}",
    )
    occurrence.session_id = thread.session_id
    occurrence.thread_id = thread.id
    occurrence.run_id = receipt.entry.assigned_run_id
    occurrence.status = "processing"


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
    session: AsyncSession,
    claw: Claw,
    schedule_id: str,
    *,
    occurrence_id: str,
    runtime: Runtime,
    admission: app_threads.Admission | None,
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
        id=occurrence_id,
        schedule_id=row.id,
        scheduled_at=current,
        status="unread",
        target=row.target,
    )
    if await settle(session, row.id):
        occurrence.status = "overlap"
    elif row.target == "fresh":
        assert admission is not None
        await start_fresh(session, runtime, admission, claw, row, occurrence)
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


async def reconcile_archived(runtime: Runtime, workspace_id: str, claw_id: str) -> None:
    """Settle terminal Fresh Runs after archive without firing or authorizing work."""
    async with transaction(runtime.storage) as session:
        claw = await inbox.lock_claw(session, workspace_id, claw_id, include_archived=True)
        if claw.archived_at is None:
            return
        rows = (
            await session.execute(
                select(Occurrence, RunRow.status)
                .join(Schedule, Schedule.id == Occurrence.schedule_id)
                .join(RunRow, RunRow.id == Occurrence.run_id)
                .where(
                    Schedule.claw_id == claw.id,
                    Schedule.target == "fresh",
                    Occurrence.target == "fresh",
                    Occurrence.status == "processing",
                    Occurrence.thread_id == RunRow.thread_id,
                    Occurrence.session_id == RunRow.session_id,
                    RunRow.workspace_id == claw.workspace_id,
                    RunRow.principal_id == claw.service_account_id,
                    RunRow.status.in_(("completed", "failed", "cancelled")),
                )
                .order_by(Occurrence.scheduled_at, Occurrence.id)
                .limit(50)
                .with_for_update(of=Occurrence)
            )
        ).all()
        for occurrence, status in rows:
            occurrence.status = status


def advance(row: Schedule, current: datetime) -> datetime:
    """Advance a due clock and return the coalesced occurrence time."""
    assert row.next_at is not None
    scheduled_at = row.next_at
    if row.cron is None:
        row.next_at = None
        row.enabled = False
    else:
        calendar = Calendar.parse(row.cron, row.timezone)
        scheduled_at = calendar.previous(current + timedelta(microseconds=1))
        row.next_at = calendar.next(current)
    return scheduled_at


async def definition_snapshot(session: AsyncSession, claw: Claw, row: Schedule) -> tuple:
    """Capture the definition and resource selections used for an attempted fire."""
    env = await session.get(EnvironmentRow, claw.environment_id) if claw.environment_id else None
    agent = await session.get(AgentRow, claw.agent_id)
    space = await session.get(MemorySpaceRow, claw.memory_space_id)
    account = await session.get(PrincipalRow, claw.service_account_id)
    grants = (
        await session.execute(
            select(GrantRow.id, GrantRow.role, GrantRow.workspace_id)
            .where(GrantRow.principal_id == claw.service_account_id)
            .order_by(GrantRow.id)
        )
    ).all()
    return deepcopy(
        (
            row.name,
            row.prompt,
            row.cron,
            row.timezone,
            row.lateness_seconds,
            claw.name,
            claw.environment_id,
            claw.browser_id,
            claw.memory_space_id,
            claw.agent_id,
            claw.agent_revision_id,
            claw.service_account_id,
            claw.shell_review,
            env.status if env else None,
            account.status if account else None,
            tuple(grants),
            agent is not None,
            agent.archived_at if agent else None,
            agent.default_revision_id if agent else None,
            space.enabled if space else None,
        )
    )


async def sweep(runtime: Runtime, workspace_id: str, claw_id: str) -> None:
    async with short_session(runtime.storage) as session:
        current = await now(session)
        due = (
            select(Schedule, Claw)
            .join(Claw, Claw.id == Schedule.claw_id)
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
        snapshots = {}
        for row, claw in (await session.execute(due)).all():
            signature = (
                await definition_snapshot(session, claw, row) if row.target == "fresh" else None
            )
            snapshots[row.id] = (row.next_at, row.target, signature)
        for sid in await session.scalars(processing):
            snapshots.setdefault(sid, (None, None, None))
    for schedule_id, (due_at, target, signature) in snapshots.items():
        attempted_at = current
        try:
            async with transaction(runtime.storage) as session:
                admission = None
                if due_at is not None and target == "fresh":
                    account_id = await session.scalar(
                        select(Claw.service_account_id).where(
                            Claw.workspace_id == workspace_id,
                            Claw.id == claw_id,
                            Claw.archived_at.is_(None),
                        )
                    )
                    if account_id is None:
                        continue
                    admission = await app_threads.prepare_admission(
                        session,
                        runtime,
                        workspace_id,
                        account_id,
                        app_id=APP_ID,
                        app_context=claw_context(claw_id),
                    )
                claw = await inbox.lock_claw(session, workspace_id, claw_id)
                row = await session.scalar(
                    select(Schedule)
                    .where(Schedule.claw_id == claw.id, Schedule.id == schedule_id)
                    .with_for_update()
                )
                if row is None:
                    continue
                outstanding = await settle(session, row.id)
                current = await now(session)
                attempted_at = current
                if (
                    due_at is None
                    or not row.enabled
                    or row.deleted_at is not None
                    or row.next_at != due_at
                    or row.target != target
                    or due_at > current
                ):
                    continue
                if (
                    signature is not None
                    and await definition_snapshot(session, claw, row) != signature
                ):
                    continue
                scheduled_at = advance(row, current)
                if await occurred(session, row.id, scheduled_at):
                    continue
                occurrence = Occurrence(
                    id=inbox.new_id("occ"),
                    schedule_id=row.id,
                    scheduled_at=scheduled_at,
                    status="unread",
                    target=row.target,
                )
                if current - scheduled_at > timedelta(seconds=row.lateness_seconds):
                    occurrence.status = "late"
                elif outstanding:
                    occurrence.status = "overlap"
                elif row.target == "fresh":
                    assert admission is not None
                    await start_fresh(session, runtime, admission, claw, row, occurrence)
                else:
                    title = (
                        f"Scheduled occurrence: {row.name} · "
                        f"{scheduled_at.astimezone(UTC).isoformat()}"
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
        except ServiceError as error:
            if due_at is None or target != "fresh":
                raise
            # Admission may have made native writes. Its entire transaction has
            # rolled back; advance only if the same fresh slot still owns the clock.
            async with transaction(runtime.storage) as session:
                claw = await inbox.lock_claw(session, workspace_id, claw_id)
                row = await session.scalar(
                    select(Schedule)
                    .where(Schedule.claw_id == claw.id, Schedule.id == schedule_id)
                    .with_for_update()
                )
                current = await now(session)
                if (
                    row is None
                    or not row.enabled
                    or row.deleted_at is not None
                    or row.target != "fresh"
                    or row.next_at != due_at
                    or due_at > current
                ):
                    continue
                if await definition_snapshot(session, claw, row) != signature:
                    continue
                outstanding = await settle(session, row.id)
                scheduled_at = advance(row, attempted_at)
                if await occurred(session, row.id, scheduled_at):
                    continue
                status = (
                    "late"
                    if attempted_at - scheduled_at > timedelta(seconds=row.lateness_seconds)
                    else "overlap"
                    if outstanding
                    else "unavailable"
                )
                session.add(
                    Occurrence(
                        id=inbox.new_id("occ"),
                        schedule_id=row.id,
                        scheduled_at=scheduled_at,
                        target="fresh",
                        status=status,
                        error=error.code if status == "unavailable" else None,
                    )
                )
