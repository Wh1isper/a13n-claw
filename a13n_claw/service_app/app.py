"""Installed App declaration and bounded organization maintenance."""

from contextlib import AsyncExitStack, asynccontextmanager

from a13n_logging import get_logger
from a13n_service.app_definitions import AppDefinition, OrganizationSweep, RunSealSubscription
from a13n_service.app_plugins import Frontend
from a13n_service.app_sdk.hooks import EnvironmentHookContext, EnvironmentLifecycleHook
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.seal_events import RunSealEvent
from a13n_service.tenancy.tables import WorkspaceRow
from fastapi import FastAPI
from sqlalchemy import select

from . import (
    api,
    github_webhook,
    inbox,
    lark_retry,
    main_thread,
    plugin,
    project_cli,
    schedules,
    supervisor,
)
from .models import APP_ID, Claw, Occurrence, Schedule, database

logger = get_logger(__name__)
_listeners: dict[Storage, supervisor.Listeners] = {}


async def startup(runtime: Runtime, resources: AsyncExitStack) -> None:
    await resources.enter_async_context(plugin.local_runtime(runtime))


@asynccontextmanager
async def control_lifespan(app: FastAPI):
    # Service only mounts the App router in all/control, after installing its final Runtime.
    runtime = app.state.runtime
    listeners = supervisor.Listeners(runtime)
    _listeners[runtime.storage] = listeners
    try:
        yield
    finally:
        _listeners.pop(runtime.storage)
        await listeners.close()


api.claws.router.lifespan_context = control_lifespan


async def sweep(runtime: Runtime, organization_id: str) -> None:
    if (listeners := _listeners.get(runtime.storage)) is not None:
        await listeners.reconcile(organization_id)
    after = ""
    while True:
        async with short_session(runtime.storage) as session:
            rows = (
                await session.execute(
                    select(Claw.workspace_id, Claw.id, Claw.archived_at)
                    .join(WorkspaceRow, WorkspaceRow.id == Claw.workspace_id)
                    .where(
                        WorkspaceRow.organization_id == organization_id,
                        WorkspaceRow.archived_at.is_(None),
                        Claw.id > after,
                    )
                    .order_by(Claw.id)
                    .limit(50)
                )
            ).all()
        if not rows:
            return
        for workspace_id, claw_id, archived_at in rows:
            try:
                await inbox.cleanup(runtime.storage, workspace_id, claw_id)
                if listeners is not None:
                    listeners.queue_egress(workspace_id, claw_id)
                if archived_at is not None:
                    await schedules.reconcile_archived(runtime, workspace_id, claw_id)
                    continue
                await schedules.sweep(runtime, workspace_id, claw_id)
                if listeners is not None:
                    listeners.queue_cards(workspace_id, claw_id)
                await main_thread.notify(runtime, workspace_id, claw_id)
            except (ServiceError, inbox.ClawNotFound) as error:
                logger.warning(
                    "Claw sweep skipped",
                    extra={"claw_id": claw_id, "error_type": type(error).__name__},
                )
        after = rows[-1].id


async def on_environment_created(context: EnvironmentHookContext) -> None:
    runtime = plugin.current_runtime()
    claw_id = (context.app_context or {}).get("claw_id")
    if runtime is None or context.owner_app_id != APP_ID or claw_id is None:
        return
    async with short_session(runtime.storage) as session:
        claw = await session.scalar(
            select(Claw).where(
                Claw.id == claw_id,
                Claw.workspace_id == context.workspace_id,
                Claw.archived_at.is_(None),
                Claw.environment_id == context.environment_id,
            )
        )
        if claw is None or plugin.cli_is_current(claw):
            return
    outcome = await project_cli.provision(context.execution, update=False, install_timeout=18)
    if not outcome.versions and outcome.failed:
        return
    async with transaction(runtime.storage) as session:
        try:
            claw = await inbox.lock_claw(session, context.workspace_id, claw_id)
        except inbox.ClawNotFound:
            return
        if claw.environment_id == context.environment_id and not (
            project_cli.CLIS.keys() <= (claw.cli_versions or {}).keys()
        ):
            claw.cli_versions = outcome.versions


async def on_run_seal(runtime: Runtime, event: RunSealEvent) -> None:
    if event.app_id != APP_ID or (claw_id := (event.app_context or {}).get("claw_id")) is None:
        return
    role = event.labels.get("claw_role")
    try:
        if role == "main" and event.status == "failed":
            await lark_retry.discover(runtime, event.workspace_id, claw_id)
        elif role == "scheduled":
            async with transaction(runtime.storage) as session:
                claw = await inbox.lock_claw(
                    session, event.workspace_id, claw_id, include_archived=True
                )
                if claw.service_account_id != event.principal_id:
                    return
                row = await session.scalar(
                    select(Occurrence)
                    .join(Schedule, Schedule.id == Occurrence.schedule_id)
                    .where(
                        Schedule.claw_id == claw.id,
                        Schedule.target == "fresh",
                        Occurrence.target == "fresh",
                        Occurrence.run_id == event.run_id,
                        Occurrence.thread_id == event.thread_id,
                        Occurrence.session_id == event.session_id,
                        Occurrence.status == "processing",
                    )
                    .with_for_update()
                )
                if row is not None:
                    row.status = event.status
        elif role == "main" and event.status == "completed":
            await main_thread.notify(
                runtime,
                event.workspace_id,
                claw_id,
                completed_run_id=event.run_id,
            )
    except inbox.ClawNotFound:
        return


def definition() -> AppDefinition:
    return AppDefinition(
        app_id=APP_ID,
        name="Claw",
        database=database,
        factory_keys=frozenset({main_thread.PLUGIN_KEY}),
        routers=api.ROUTERS,
        webhook_routers=(github_webhook.router,),
        frontend=Frontend(
            "a13n_claw.service_app", "static", entry="index.js", styles=("index.css",)
        ),
        startup=startup,
        run_seals=(RunSealSubscription(run=on_run_seal),),
        environment_hooks=(
            EnvironmentLifecycleHook(
                "after_create", on_environment_created, timeout=30.0, scope="app"
            ),
        ),
        sweeps=(OrganizationSweep("claw", every=15, timeout=60, run=sweep),),
    )
