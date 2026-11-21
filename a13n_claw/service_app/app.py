"""Installed App declaration and bounded organization maintenance."""

from contextlib import AsyncExitStack, asynccontextmanager

from a13n_logging import get_logger
from a13n_service.app_definitions import AppDefinition, OrganizationSweep
from a13n_service.app_plugins import Frontend
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.tables import WorkspaceRow
from fastapi import FastAPI
from sqlalchemy import select

from . import api, connectors, inbox, main_thread, plugin, schedules, workers
from .models import APP_ID, Claw, database

logger = get_logger(__name__)
_listeners: dict[Storage, connectors.Listeners] = {}


async def startup(runtime: Runtime, resources: AsyncExitStack) -> None:
    await resources.enter_async_context(plugin.local_runtime(runtime))


@asynccontextmanager
async def control_lifespan(app: FastAPI):
    # Service only mounts the App router in all/control, after installing its final Runtime.
    runtime = app.state.runtime
    listeners = connectors.Listeners(runtime)
    _listeners[runtime.storage] = listeners
    try:
        yield
    finally:
        _listeners.pop(runtime.storage)
        await listeners.close()


api.router.lifespan_context = control_lifespan


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
                if archived_at is not None:
                    continue
                await workers.terminal_notices(runtime, workspace_id, claw_id)
                await schedules.sweep(runtime, workspace_id, claw_id)
                await schedules.retry_fresh(runtime, workspace_id, claw_id)
                await main_thread.notify(runtime, workspace_id, claw_id)
            except (ServiceError, inbox.ClawNotFound) as error:
                logger.warning(
                    "Claw sweep skipped",
                    extra={"claw_id": claw_id, "error_type": type(error).__name__},
                )
        after = rows[-1].id


def definition() -> AppDefinition:
    return AppDefinition(
        app_id=APP_ID,
        name="Claw",
        database=database,
        factory_keys=frozenset({main_thread.PLUGIN_KEY}),
        routers=(api.router,),
        frontend=Frontend(
            "a13n_claw.service_app", "static", entry="index.js", styles=("index.css",)
        ),
        startup=startup,
        sweeps=(OrganizationSweep("claw", every=15, timeout=60, run=sweep),),
    )
