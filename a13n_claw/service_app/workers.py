"""Managed native Worker Threads and exact-Run steering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from a13n_service.infra.db import lock, now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.runs import submit
from a13n_service.runs.accept import accept
from a13n_service.runs.attempts import AuthorityRevoked, authorize_execution
from a13n_service.runs.inbox import Request, check_replay, find_request
from a13n_service.runs.schedules.origin import origins
from a13n_service.runs.schedules.tables import ScheduleRow
from a13n_service.runs.schemas import Message, NewThread
from a13n_service.runs.tables import AttemptRow, InboxEntryRow, RunRow
from a13n_service.runs.threads import get_thread, require_open
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import (
    ExecutionAuthority,
    Principal,
    WorkspaceScope,
    execution_authority,
)
from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from . import inbox, main_thread
from .models import Claw, Steer, Worker

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
            .outerjoin(Worker, Worker.thread_id == RunRow.thread_id)
            .join(Claw, (Claw.main_thread_id == RunRow.thread_id) | (Claw.id == Worker.claw_id))
            .join(AttemptRow, AttemptRow.id == RunRow.current_attempt_id)
            .where(
                RunRow.id == run_id,
                AttemptRow.id == attempt_id,
                AttemptRow.lease_expires_at > await now(session),
                RunRow.status == "running",
                Claw.workspace_id == RunRow.workspace_id,
                Claw.service_account_id == RunRow.principal_id,
            )
        )
    ).one_or_none()
    if pair is not None:
        run, claw = pair
        role = "main" if run.thread_id == claw.main_thread_id else "worker"
        return Binding(claw, run, role, attempt_id)
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


async def worker_in(session: AsyncSession, claw: Claw, thread_id: str) -> Worker:
    worker = await session.scalar(
        select(Worker).where(Worker.claw_id == claw.id, Worker.thread_id == thread_id)
    )
    if worker is None:
        raise not_found("worker", thread_id)
    return worker


def assignment(thread_id: str, session_id: str, entry: InboxEntryRow) -> dict:
    return {
        "thread_id": thread_id,
        "session_id": session_id,
        "entry_id": entry.id,
        "entry_status": entry.status,
        "failure": entry.failure,
        "run_id": entry.assigned_run_id,
    }


async def assign(
    runtime: Runtime,
    binding: Binding,
    task: str,
    call_key: str,
    *,
    name: str | None = None,
    worker_id: str | None = None,
    model: str | None = None,
) -> dict:
    async with transaction(runtime.storage) as session:
        claw = await inbox.lock_claw(session, binding.claw.workspace_id, binding.claw.id)
        caller, actor = await fence(session, runtime, claw, binding)
        scope = WorkspaceScope(caller.organization_id, caller.workspace_id)
        await workspace_scope(session, actor, claw.workspace_id, "run")
        allowed = execution_authority(actor, scope)
        frozen = ExecutionAuthority.model_validate(caller.authority)
        authority = allowed.model_copy(update={"verbs": allowed.verbs & frozen.verbs})
        agent = await session.get_one(AgentRow, claw.agent_id)
        worker = await worker_in(session, claw, worker_id) if worker_id else None
        run_options = await main_thread.options(
            session, claw, agent, role="worker", model=worker.model if worker else model
        )
        message = Message(
            delivery="next_run",
            agent_id=agent.id,
            agent_revision_id=main_thread.selected_revision(claw, agent),
            options=run_options,
            payload=main_thread.payload(task),
        )
        title = name or "Worker"
        body = None
        if worker is None:
            body = NewThread(
                **message.model_dump(),
                session=main_thread.project_session(claw, f"{claw.name} · {title}"),
                environments=main_thread.project_mounts(claw),
                browser_binding=main_thread.project_browser(claw),
            )
            request = Request.of(call_key, "thread", claw.workspace_id, body)
        else:
            request = Request.of(call_key, "message", worker.thread_id, message)
        existing = await find_request(session, claw.workspace_id, actor.id, request.key)
        if existing is not None:
            # Replays never resubmit the native tool call.
            check_replay(existing, request)
            owner = await worker_in(session, claw, existing.thread_id)
            return assignment(owner.thread_id, owner.session_id, existing)
        if body is not None:
            thread, source = await submit.prepare_thread(
                session, runtime, actor, scope, body, request=request, authority=authority
            )
            session.add(
                Worker(
                    thread_id=thread.id,
                    claw_id=claw.id,
                    session_id=thread.session_id,
                    model=model,
                    name=title,
                )
            )
        else:
            assert worker is not None
            thread = await get_thread(
                session, claw.workspace_id, worker.thread_id, lock="acceptance", history=False
            )
            require_open(thread)
            source = await submit.prepare_message(
                session, runtime, actor, scope, thread, message, request, authority=authority
            )
        await accept(session, runtime, thread, explicit=source)
        assert source.entry is not None
        return assignment(thread.id, thread.session_id, source.entry)


async def steer(
    runtime: Runtime, binding: Binding, thread_id: str, text: str, call_key: str
) -> bool:
    async with transaction(runtime.storage) as session:
        claw = await inbox.lock_claw(session, binding.claw.workspace_id, binding.claw.id)
        await fence(session, runtime, claw, binding)
        await worker_in(session, claw, thread_id)
        thread = await get_thread(
            session, claw.workspace_id, thread_id, lock="acceptance", history=False
        )
        if thread.archived_at is not None or thread.current_run_id is None:
            return False
        run = await session.get_one(RunRow, thread.current_run_id)
        if run.status != "running" or run.principal_id != binding.claw.service_account_id:
            return False
        if await session.get(Steer, call_key) is None:
            pending = (
                await session.scalars(
                    select(Steer.id)
                    .where(
                        Steer.run_id == run.id,
                        Steer.consumed_at.is_(None),
                    )
                    .limit(16)
                )
            ).all()
            if len(pending) == 16:
                raise conflict("worker", thread_id, "steer_capacity")
            session.add(Steer(id=call_key, thread_id=thread.id, run_id=run.id, content=text))
        return True


async def take_steers(runtime: Runtime, binding: Binding) -> list[str]:
    async with transaction(runtime.storage) as session:
        claw = await inbox.lock_claw(session, binding.claw.workspace_id, binding.claw.id)
        await fence(session, runtime, claw, binding)
        rows = (
            await session.scalars(
                select(Steer)
                .where(
                    Steer.thread_id == binding.run.thread_id,
                    Steer.run_id == binding.run.id,
                    Steer.consumed_at.is_(None),
                )
                .order_by(Steer.created_at, Steer.id)
                .limit(16)
                .with_for_update()
            )
        ).all()
        if not rows:
            return []
        taken = await now(session)
        for row in rows:
            row.consumed_at = taken
        return [row.content for row in rows]


async def terminal_notices(runtime: Runtime, workspace_id: str, claw_id: str) -> None:
    async with short_session(runtime.storage) as session:
        worker_ids = (
            await session.scalars(select(Worker.thread_id).where(Worker.claw_id == claw_id))
        ).all()
    for thread_id in worker_ids:
        async with transaction(runtime.storage) as session:
            claw = await inbox.lock_claw(session, workspace_id, claw_id)
            worker = await worker_in(session, claw, thread_id)
            previous = (
                await session.get(RunRow, worker.last_notice_run_id)
                if worker.last_notice_run_id
                else None
            )
            query = (
                select(RunRow.id, RunRow.status, RunRow.created_at)
                .where(
                    RunRow.thread_id == thread_id,
                    RunRow.status.in_(("completed", "failed", "cancelled")),
                )
                .order_by(RunRow.created_at, RunRow.id)
                .limit(32)
            )
            if previous is not None:
                query = query.where(
                    tuple_(RunRow.created_at, RunRow.id) > (previous.created_at, previous.id)
                )
            for run_id, status, _ in (await session.execute(query)).all():
                summary = f"Worker {worker.name} {status}. Inspect its saved Run for details."
                await inbox.admit(
                    session,
                    claw,
                    inbox.Offer(
                        "worker_terminal",
                        "worker",
                        f"terminal:{run_id}",
                        {
                            "thread_id": thread_id,
                            "session_id": worker.session_id,
                            "run_id": run_id,
                            "status": status,
                        },
                        summary,
                        summary,
                    ),
                )
                await session.execute(
                    delete(Steer).where(Steer.thread_id == thread_id, Steer.run_id == run_id)
                )
                worker.last_notice_run_id = run_id
