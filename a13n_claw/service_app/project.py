"""Join the native Project reservations to Claw's creation transaction."""

from a13n_service.infra.ids import new_object_id
from a13n_service.resources.memories.schemas import MemoryCreate, OrganizationPolicy
from a13n_service.resources.memories.service import insert_memory
from a13n_service.resources.memories.tables import MemorySpaceRow
from a13n_service.resources.rows import audit_row
from a13n_service.runs.environments.access import audit
from a13n_service.runs.environments.lifecycle import begin, reserve
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import Principal, WorkspaceScope
from sqlalchemy.ext.asyncio import AsyncSession


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
