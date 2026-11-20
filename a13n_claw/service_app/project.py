"""Join the native Project reservations to Claw's creation transaction."""

from a13n_service.infra.db import now
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.memories.schemas import MemoryCreate, OrganizationPolicy
from a13n_service.resources.memories.service import insert_memory
from a13n_service.resources.memories.tables import MemorySpaceRow
from a13n_service.resources.rows import audit_row
from a13n_service.runs.environments.access import audit
from a13n_service.runs.environments.desktop.delivery import suppress as suppress_desktop
from a13n_service.runs.environments.lifecycle import begin, reserve
from a13n_service.runs.environments.mounts import mount_environments
from a13n_service.runs.environments.tables import ThreadEnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import ThreadRow
from a13n_service.tenancy.authorize import Principal, WorkspaceScope, execution_authority
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import main_thread, schedules
from .models import Claw, Worker


async def reserve_environment(
    session: AsyncSession,
    runtime: Runtime,
    account: Principal,
    scope: WorkspaceScope,
    template_id: str,
    name: str,
) -> str:
    row = await reserve(
        session,
        account,
        scope,
        template_id,
        limit=runtime.settings.environments.managed_count,
        name=name[:128],
    )
    # Native maintenance dispatches the operation after this transaction commits.
    await begin(session, row, "creating")
    await session.flush()
    audit(session, account, row, "create")
    return row.id


async def create_space(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    scope: WorkspaceScope,
    claw_id: str,
    name: str,
) -> str:
    memory = await insert_memory(
        session,
        actor,
        scope,
        MemoryCreate(name=claw_id),
        settings=runtime.settings.memory,
    )
    space = MemorySpaceRow(
        id=new_object_id("mspace"),
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        memory_id=memory.id,
        name=claw_id,
        description=f"Project memory for {name}",
        labels={},
        organization=OrganizationPolicy().model_dump(mode="json", exclude_none=True),
        created_by_id=actor.id,
        updated_by_id=actor.id,
    )
    session.add(space)
    audit_row(session, actor, space, "create")
    return space.id


async def rebind(
    session: AsyncSession,
    runtime: Runtime,
    scope: WorkspaceScope,
    account: Principal,
    claw: Claw,
    environment_id: str | None,
    browser_id: str | None,
    *,
    environment_template_id: str | None = None,
) -> None:
    """Update desired resources before any later native admission locks the Thread."""
    environment_changed = (
        environment_template_id is not None or environment_id != claw.environment_id
    )
    browser_changed = browser_id != claw.browser_id
    if not (environment_changed or browser_changed):
        return
    await main_thread.check_resources(
        session,
        scope,
        account,
        environment_id if environment_changed and environment_template_id is None else None,
        browser_id if browser_changed else None,
    )
    if environment_changed or browser_id is not None:
        await schedules.require_no_fresh_work(session, claw)
    owned_worker = select(Worker.thread_id).where(Worker.claw_id == claw.id)
    threads = await session.scalars(
        select(ThreadRow)
        .where(
            ThreadRow.workspace_id == scope.workspace_id,
            ThreadRow.archived_at.is_(None),
            or_(ThreadRow.id == claw.main_thread_id, ThreadRow.id.in_(owned_worker)),
        )
        .order_by(ThreadRow.id)
        .with_for_update()
    )
    claw.browser_id = browser_id
    if environment_changed:
        claw.cli_observation = None
        claw.cli_checked_at = None
    authority = execution_authority(account, scope)
    threads = threads.all()
    if browser_changed:
        for thread in threads:
            await main_thread.bind_project_browser(session, claw, thread, account, scope, authority)
    if environment_template_id is not None:
        # Native fresh admission locks its Thread before reserving Workspace capacity.
        environment_id = await reserve_environment(
            session, runtime, account, scope, environment_template_id, claw.name
        )
    claw.environment_id = environment_id
    for thread in threads:
        if environment_changed:
            old = await session.get(ThreadEnvironmentRow, (thread.id, "workspace"))
            if old is not None:
                await suppress_desktop(session, thread.id, mount_name="workspace")
                await session.delete(old)
                await session.flush()
            await mount_environments(
                session, thread, main_thread.project_mounts(claw), principal=account
            )
        thread.updated_at = await now(session)
    await session.flush()
