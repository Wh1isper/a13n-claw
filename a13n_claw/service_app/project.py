"""Join the native Project reservations to Claw's creation transaction."""

from typing import TypedDict

from a13n_service.app_sdk import projects as app_projects
from a13n_service.app_sdk import threads as app_threads
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import Principal, WorkspaceScope
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from . import egress
from .models import APP_ID, Claw, Connector, claw_context


async def reserve_environment(
    session: AsyncSession,
    runtime: Runtime,
    account: Principal,
    scope: WorkspaceScope,
    template_id: str,
    name: str,
    claw_id: str,
) -> str:
    return await app_projects.reserve_environment(
        session,
        runtime,
        scope.workspace_id,
        app_id=APP_ID,
        app_context=claw_context(claw_id),
        principal_id=account.id,
        template_id=template_id,
        name=name[:128],
    )


async def reserve_browser(
    session: AsyncSession,
    runtime: Runtime,
    account: Principal,
    scope: WorkspaceScope,
    provider_id: str,
    name: str,
    claw_id: str,
) -> str:
    return await app_projects.reserve_browser(
        session,
        runtime,
        scope.workspace_id,
        app_id=APP_ID,
        app_context=claw_context(claw_id),
        principal_id=account.id,
        provider_id=provider_id,
        name=name[:128],
    )


async def create_space(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    scope: WorkspaceScope,
    claw_id: str,
    name: str,
) -> str:
    return await app_projects.create_memory_space(
        session,
        runtime,
        scope.workspace_id,
        app_id=APP_ID,
        app_context=claw_context(claw_id),
        actor_id=actor.id,
        name=claw_id,
        description=f"Project memory for {name}",
    )


class _ResourceChanges(TypedDict, total=False):
    environment_id: str | None
    environment_template_id: str | None
    browser_id: str | None
    template_name: str | None


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
    browser_provider_id: str | None = None,
) -> None:
    """Update the native shared tree atomically with the Claw Project."""
    environment_changed = (
        environment_template_id is not None or environment_id != claw.environment_id
    )
    browser_changed = browser_provider_id is not None or browser_id != claw.browser_id
    if not (environment_changed or browser_changed):
        return
    await app_projects.check_resources(
        session,
        runtime,
        scope.workspace_id,
        principal_id=account.id,
        environment_id=environment_id
        if environment_changed and environment_template_id is None
        else None,
        browser_id=browser_id if browser_changed and browser_provider_id is None else None,
    )
    if environment_changed:
        await egress.revoke_for_rebind(session, runtime, claw)
        await session.execute(
            update(Connector)
            .where(Connector.claw_id == claw.id, Connector.egress_primary.is_(True))
            .values(egress_primary=False)
        )
    if browser_provider_id is not None:
        browser_id = await reserve_browser(
            session, runtime, account, scope, browser_provider_id, claw.name, claw.id
        )
    if claw.main_thread_id is not None:
        kwargs: _ResourceChanges = {}
        if environment_template_id is not None:
            kwargs["environment_template_id"] = environment_template_id
            kwargs["template_name"] = claw.name[:128]
        elif environment_changed:
            kwargs["environment_id"] = environment_id
        if browser_changed:
            kwargs["browser_id"] = browser_id
        try:
            result = await app_threads.update_thread_resources(
                session,
                runtime,
                scope.workspace_id,
                app_id=APP_ID,
                principal_id=account.id,
                root_thread_id=claw.main_thread_id,
                **kwargs,
            )
        except ServiceError as error:
            if error.details and error.details.get("reason") == "thread_resources_busy":
                raise ServiceError(
                    "conflict",
                    "Finish or cancel active Main and shared Worker Runs, clear pending native "
                    "messages, then retry the Environment change.",
                    {
                        "kind": "project",
                        "id": claw.id,
                        "reason": "environment_rebind_requires_idle_threads",
                    },
                ) from error
            raise
        environment_id, browser_id = result.environment_id, result.browser_id
    elif environment_template_id is not None:
        environment_id = await reserve_environment(
            session, runtime, account, scope, environment_template_id, claw.name, claw.id
        )
    if environment_changed:
        claw.cli_versions = None
        claw.cli_update_id = None
        claw.cli_update_state = None
        claw.cli_update_error = None
    claw.environment_id = environment_id
    claw.browser_id = browser_id
    await session.flush()
