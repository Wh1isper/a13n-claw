"""Main-mode schedule state. Service supplies the clock and owns fresh-Thread schedules."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

from a13n_service.infra.cursors import keyset_page
from a13n_service.infra.db import advisory_lock, now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.resources.memories.tables import MemorySpaceRow
from a13n_service.runs.memories.session_memory import configure
from a13n_service.runs.schedules import queries as native_queries
from a13n_service.runs.schedules import service as native_service
from a13n_service.runs.schedules.calendar import Calendar
from a13n_service.runs.schedules.schemas import (
    CronSchedule,
    OnceSchedule,
    ScheduleCreate,
    ScheduleInvocation,
    ScheduleTarget,
)
from a13n_service.runs.schedules.tables import ScheduleRow as NativeScheduleRow
from a13n_service.runs.schemas import ExistingRunEnvironment
from a13n_service.runs.sessions import new_session
from a13n_service.runs.tables import RunRow
from a13n_service.tenancy.authorize import (
    ExecutionAuthority,
    Principal,
    WorkspaceScope,
    execution_authority,
)
from a13n_service.tenancy.tables import WorkspaceRow
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import LockError
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from . import inbox, main_thread
from .models import Claw, Occurrence, Schedule, ScheduleRequest

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=32000)
    rule: CronSchedule | OnceSchedule = Field(discriminator="type")
    lateness_seconds: int = Field(default=300, ge=60, le=86400)


async def fresh_definition(
    session: AsyncSession, claw: Claw, body: ScheduleInput, actor: Principal
) -> ScheduleCreate:
    if claw.environment_id is None:
        raise conflict("claw", claw.id, "project_environment_required")
    if claw.browser_id is not None:
        raise conflict("claw", claw.id, "fresh_schedule_cannot_bind_project_browser")
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
        owner = new_session(space.organization_id, claw.workspace_id, actor.id, details)
        session.add(owner)
        await session.flush()
        assert details.memory is not None
        await configure(session, owner, details.memory)
        claw.schedule_session_id = owner.id
    options = await main_thread.options(session, claw, agent, role="scheduled")
    options = options.model_copy(
        update={
            "environment": ExistingRunEnvironment(
                mode="existing", environment_id=claw.environment_id
            )
        }
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
            options=options,
        ),
        target=ScheduleTarget(session_id=claw.schedule_session_id),
        state="enabled",
    )


async def fresh_account(session: AsyncSession, runtime: Runtime, claw: Claw) -> Principal:
    workspace = await session.get_one(WorkspaceRow, claw.workspace_id)
    scope = WorkspaceScope(workspace.organization_id, workspace.id)
    return await main_thread.account(session, runtime, scope, claw.service_account_id)


async def require_no_fresh_work(session: AsyncSession, claw: Claw) -> None:
    pending = (
        select(ScheduleRequest.id)
        .where(ScheduleRequest.claw_id == claw.id, ScheduleRequest.completed_at.is_(None))
        .exists()
    )
    native = (
        select(NativeScheduleRow.id)
        .where(
            NativeScheduleRow.workspace_id == claw.workspace_id,
            NativeScheduleRow.principal_id == claw.service_account_id,
            or_(
                NativeScheduleRow.session_id == claw.schedule_session_id,
                NativeScheduleRow.labels["claw"].astext == claw.id,
            ),
            NativeScheduleRow.thread_id.is_(None),
            or_(
                NativeScheduleRow.state.in_(("enabled", "paused")),
                NativeScheduleRow.pending_occurrence_id.is_not(None),
            ),
        )
        .exists()
    )
    if await session.scalar(select(or_(pending, native))):
        raise conflict("claw", claw.id, "archive_fresh_schedules_before_rebinding")


async def create_fresh(runtime: Runtime, claw: Claw, body: ScheduleInput, request_key: str):
    async with transaction(runtime.storage) as session:
        claw = await inbox.lock_claw(session, claw.workspace_id, claw.id)
        actor = await fresh_account(session, runtime, claw)
        assert actor.confinement is not None
        request_id = "scq_" + sha256(request_key.encode()).hexdigest()
        row = await session.get(ScheduleRequest, request_id)
        digest = native_service.request_digest(
            "claw.schedule", claw.id, body.model_dump(mode="json")
        )
        if row is None:
            request = FreshRequest(
                definition=await fresh_definition(session, claw, body, actor),
                authority=execution_authority(actor, actor.confinement),
                input_digest=digest,
                native_key=request_key,
            )
            session.add(
                ScheduleRequest(
                    id=request_id,
                    claw_id=claw.id,
                    request=request.model_dump(mode="json"),
                )
            )
        elif row.request is not None and row.request.get("input_digest") != digest:
            raise conflict("request", request_key, "idempotency_key_reused")
    result = await submit_fresh(runtime, claw, request_id, report_errors=True)
    if result.schedule_id is not None:
        return await native_queries.get(
            runtime.storage, actor, claw.workspace_id, result.schedule_id
        )
    if result.failure is not None:
        raise conflict("schedule request", request_id, result.failure)
    raise ServiceError(
        "unavailable", "Schedule creation is pending; retry with the same request key"
    )


class FreshRequest(BaseModel):
    definition: ScheduleCreate
    authority: ExecutionAuthority
    input_digest: str | None = None
    native_key: str | None = None


class ScheduleReceipt(BaseModel):
    id: str
    state: str
    schedule_id: str | None = None
    failure: str | None = None


def receipt(row: ScheduleRequest) -> ScheduleReceipt:
    return ScheduleReceipt(
        id=row.id,
        state="pending" if row.completed_at is None else "failed" if row.failure else "created",
        schedule_id=row.schedule_id,
        failure=row.failure,
    )


async def submit_fresh(
    runtime: Runtime, claw: Claw, request_id: str, *, report_errors: bool = False
) -> ScheduleReceipt:
    lock = runtime.redis.lock(f"claw:schedule-create:{request_id}", timeout=60, blocking=False)
    if not await lock.acquire():
        return ScheduleReceipt(id=request_id, state="pending")
    try:
        async with asyncio.timeout(45):
            return await _submit_fresh(runtime, claw, request_id, report_errors=report_errors)
    except TimeoutError:
        return ScheduleReceipt(id=request_id, state="pending")
    finally:
        with suppress(LockError):
            await lock.release()


async def _submit_fresh(
    runtime: Runtime, claw: Claw, request_id: str, *, report_errors: bool
) -> ScheduleReceipt:
    async with short_session(runtime.storage) as session:
        row = await session.get(ScheduleRequest, request_id)
        if row is None:
            return ScheduleReceipt(id=request_id, state="pending")
        if row.completed_at is not None:
            return receipt(row)
        request = FreshRequest.model_validate(row.request)
    native_key = request.native_key or request_id
    submission_error = None
    try:
        async with short_session(runtime.storage) as session:
            actor = await fresh_account(session, runtime, claw)
        if actor.id != request.authority.principal_id:
            raise conflict("claw", claw.id, "automation_account_changed")
        actor = replace(
            actor,
            grants=tuple(
                replace(grant, verbs=grant.verbs & request.authority.verbs)
                for grant in actor.grants
            ),
        )
        schedule, _ = await native_service.create(
            runtime, actor, claw.workspace_id, request.definition, request_key=native_key
        )
        schedule_id, failure = schedule.id, None
    except ServiceError as error:
        if error.code == "unavailable" and datetime.now(UTC) - row.created_at < inbox.HISTORY_AGE:
            return receipt(row)
        submission_error = error
        schedule_id = None
        failure = str(error.details.get("reason") or error.code)[:200]
    async with transaction(runtime.storage) as session:
        if schedule_id is None:
            # The native write may already have committed before the App receipt, even
            # if the original account can no longer replay the call.
            await advisory_lock(session, "schedules", claw.workspace_id)
            schedule_id = await session.scalar(
                select(NativeScheduleRow.id).where(
                    NativeScheduleRow.workspace_id == claw.workspace_id,
                    NativeScheduleRow.created_by_id == request.authority.principal_id,
                    NativeScheduleRow.create_request_key == native_key,
                    NativeScheduleRow.create_request_digest
                    == native_service.request_digest(
                        "schedule.create",
                        claw.workspace_id,
                        request.definition.model_dump(mode="json"),
                    ),
                )
            )
            if schedule_id is not None:
                failure = None
        if schedule_id is None and request.native_key is not None:
            # A definitive HTTP failure must not consume the client's idempotency key.
            await session.execute(delete(ScheduleRequest).where(ScheduleRequest.id == request_id))
        else:
            # Native creation is idempotent across workers and process restarts.
            await session.execute(
                update(ScheduleRequest)
                .where(
                    ScheduleRequest.id == request_id,
                    ScheduleRequest.completed_at.is_(None),
                )
                .values(
                    request={"input_digest": request.input_digest}
                    if request.input_digest
                    else None,
                    schedule_id=schedule_id,
                    failure=failure,
                    completed_at=await now(session),
                )
            )
            return receipt(await session.get_one(ScheduleRequest, request_id))
    if submission_error is not None and report_errors:
        raise submission_error
    return ScheduleReceipt(id=request_id, state="failed", failure=failure)


async def retry_fresh(runtime: Runtime, workspace_id: str, claw_id: str) -> None:
    async with short_session(runtime.storage) as session:
        claw = await session.scalar(
            select(Claw).where(Claw.workspace_id == workspace_id, Claw.id == claw_id)
        )
        if claw is None:
            return
        requests = (
            await session.scalars(
                select(ScheduleRequest.id)
                .where(
                    ScheduleRequest.claw_id == claw_id,
                    ScheduleRequest.completed_at.is_(None),
                )
                .order_by(ScheduleRequest.created_at, ScheduleRequest.id)
                .limit(8)
            )
        ).all()
    for request_id in requests:
        await submit_fresh(runtime, claw, request_id)


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
