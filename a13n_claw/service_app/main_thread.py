"""Native Main admission and heartbeat. Inbox storage precedes every wake."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from a13n_logging import get_logger
from a13n_service.infra.db import now, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.resources.agents import service as agents
from a13n_service.resources.agents.schemas import (
    AgentConfig,
    AgentOverride,
    AgentRevisionCreate,
    PluginSelection,
)
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.environment_instances.access import require_usable
from a13n_service.resources.environment_instances.tables import EnvironmentRow
from a13n_service.resources.memories.schemas import SessionMemorySettings, SpaceBinding
from a13n_service.runs import submit
from a13n_service.runs.accept import accept, eligible, paused
from a13n_service.runs.browsers import lifecycle as browser_lifecycle
from a13n_service.runs.browsers.schemas import BrowserBinding
from a13n_service.runs.browsers.tables import BrowserBlockedRow, ThreadBrowserRow
from a13n_service.runs.environments.schemas import MountCreate
from a13n_service.runs.inbox import pending_entries
from a13n_service.runs.schemas import (
    Message,
    MessagePayload,
    NewThread,
    RunOptions,
    SessionCreate,
    SessionOrigin,
    TextPart,
)
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow
from a13n_service.runs.threads import get_thread
from a13n_service.tenancy.access import principal_for, workspace_scope
from a13n_service.tenancy.authorize import (
    ExecutionAuthority,
    Principal,
    WorkspaceScope,
    authorize,
    execution_authority,
)
from a13n_service.tenancy.tables import WorkspaceRow
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .inbox import ClawNotFound, lock_claw
from .models import APP_ID, Claw, Delivery, InboxItem

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)
PLUGIN_KEY = "a13n.claw"


async def account(
    session: AsyncSession, runtime: Runtime, scope: WorkspaceScope, account_id: str
) -> Principal:
    actor = await principal_for(session, runtime.access, account_id, confinement=scope)
    if actor.kind != "service_account" or actor.confinement != scope:
        raise conflict("service account", account_id, "workspace_account_required")
    authorize(actor, scope, "run")
    return actor


async def check_project(
    session: AsyncSession,
    scope: WorkspaceScope,
    actor: Principal,
    agent_id: str,
    environment_id: str | None,
    browser_id: str | None,
) -> AgentRow:
    agent = await session.scalar(
        select(AgentRow)
        .where(AgentRow.workspace_id == scope.workspace_id, AgentRow.id == agent_id)
        .with_for_update()
    )
    if agent is None:
        raise not_found("agent", agent_id)
    if agent.archived_at is not None or agent.default_revision_id is None:
        raise conflict("agent", agent_id, "agent_unavailable")
    await check_resources(session, scope, actor, environment_id, browser_id)
    return agent


async def check_resources(
    session: AsyncSession,
    scope: WorkspaceScope,
    actor: Principal,
    environment_id: str | None,
    browser_id: str | None,
) -> None:
    if environment_id is not None:
        environment = await session.scalar(
            select(EnvironmentRow).where(
                EnvironmentRow.workspace_id == scope.workspace_id,
                EnvironmentRow.id == environment_id,
            )
        )
        if environment is None:
            raise not_found("environment", environment_id)
        require_usable(environment, actor.id)
    if browser_id is not None:
        browser = await browser_lifecycle.find(session, scope.workspace_id, browser_id)
        browser_lifecycle.usable(browser)
        await browser_lifecycle.authorize_use(session, actor, browser, scope)


async def prepare_revision(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    scope: WorkspaceScope,
    agent: AgentRow,
    revision: AgentRevisionRow,
) -> str:
    if revision.config.get("summarize") is True:
        return revision.id
    config = AgentConfig.model_validate({**revision.config, "summarize": True})
    if agent.builtin:
        raise conflict("agent", agent.id, "managed_revision_requires_summary")
    published = await agents._publish_revision(
        session,
        actor,
        scope,
        agent,
        AgentRevisionCreate(config=config, make_default=False, note="Claw Project context summary"),
        registry=runtime.registry,
        plugins=runtime.plugins,
    )
    return published.id


def selected_revision(claw: Claw, agent: AgentRow) -> str:
    revision = claw.agent_revision_id or agent.default_revision_id
    if revision is None:
        raise conflict("agent", agent.id, "missing_default_revision")
    return revision


def payload(text: str) -> MessagePayload:
    return MessagePayload(content=(TextPart(type="text", text=text),))


def project_session(claw: Claw, title: str) -> SessionCreate:
    return SessionCreate(
        auto_recreate_lost_environment=False,
        title=title[:256],
        origin=SessionOrigin(application=APP_ID, name=claw.name, external_id=claw.id),
        memory=SessionMemorySettings(
            enabled=True, spaces=(SpaceBinding(name="project", space_id=claw.memory_space_id),)
        ),
    )


def project_mounts(claw: Claw) -> tuple[MountCreate, ...]:
    if claw.environment_id is None:
        raise conflict("claw", claw.id, "project_environment_required")
    return (MountCreate(name="workspace", environment_id=claw.environment_id),)


def project_browser(claw: Claw) -> BrowserBinding | None:
    if claw.browser_id is None:
        return None
    return BrowserBinding(browser_id=claw.browser_id, tab_access="global")


async def bind_project_browser(
    session: AsyncSession,
    claw: Claw,
    thread: ThreadRow,
    actor: Principal,
    scope: WorkspaceScope,
    authority: ExecutionAuthority,
) -> None:
    previous = await session.get(ThreadBrowserRow, thread.id)
    if claw.browser_id is not None:
        browser = await browser_lifecycle.find(
            session, scope.workspace_id, claw.browser_id, lock=True
        )
        browser_lifecycle.usable(browser)
        await browser_lifecycle.authorize_use(session, actor, browser, scope, authority=authority)
        binding = BrowserBinding(browser_id=browser.id, tab_access="global")
        if (
            previous is not None
            and previous.browser_id == binding.browser_id
            and previous.tab_access == binding.tab_access
        ):
            return
    else:
        binding = None
        if previous is None:
            return
    if previous is not None:
        await session.delete(previous)
        await session.flush()
    if binding is not None:
        session.add(
            ThreadBrowserRow(
                thread_id=thread.id, workspace_id=scope.workspace_id, **binding.model_dump()
            )
        )
    if previous is not None:
        await session.execute(
            update(BrowserBlockedRow)
            .where(BrowserBlockedRow.thread_id == thread.id)
            .values(suppressed=True)
        )
    await session.flush()


async def options(
    session: AsyncSession, claw: Claw, agent: AgentRow, *, role: str, model: str | None = None
) -> RunOptions:
    revision = await session.get_one(AgentRevisionRow, selected_revision(claw, agent))
    plugins = tuple(
        PluginSelection.model_validate(item)
        for item in revision.config.get("plugins", ())
        if item["instance_name"] != "claw"
    )
    selected = PluginSelection(
        instance_name="claw",
        plugin_key=PLUGIN_KEY,
        config={"role": role},
    )
    overrides = {"plugins": (selected, *plugins), "model": model}
    if claw.browser_id is not None:
        browser = revision.config.get("toolsets", {}).get("browser", {})
        overrides["toolsets"] = {"browser": {**browser, "enabled": True}}
    if role == "main":
        overrides["output_spec"] = {"alternatives": ["text", "builtin.finish_silently"]}
    else:
        overrides["client_tools"] = ()
        # The parent policy cannot govern an independently started child Run.
        overrides["subagents"] = {}
        output = revision.config.get("output_spec") or {}
        if any(
            isinstance(variant, dict) and variant.get("kind") == "client_tool"
            for variant in output.get("alternatives") or ()
        ):
            overrides["output_spec"] = {"alternatives": ["text", "builtin.finish_silently"]}
    return RunOptions(
        labels={"claw": claw.id, "claw_role": role},
        overrides=AgentOverride.model_validate(overrides),
    )


async def notify(
    runtime: Runtime, workspace_id: str, claw_id: str, *, delivery_id: str | None = None
) -> None:
    try:
        async with transaction(runtime.storage) as session:
            claw = await lock_claw(session, workspace_id, claw_id)
            await _wake(session, runtime, claw, delivery_id)
    except ClawNotFound:
        return
    except ServiceError as error:
        logger.warning(
            "Claw Main wake refused: %s",
            error.message,
            extra={"claw_id": claw_id, "error_code": error.code},
        )


async def _wake(
    session: AsyncSession, runtime: Runtime, claw: Claw, delivery_id: str | None
) -> None:
    if claw.environment_id is None:
        return
    state = await session.scalar(
        select(EnvironmentRow.status).where(
            EnvironmentRow.workspace_id == claw.workspace_id,
            EnvironmentRow.id == claw.environment_id,
        )
    )
    if state not in {"reserved", "creating", "ready", "stopped", "starting"}:
        return
    unread = await session.scalar(
        select(InboxItem.id)
        .where(InboxItem.claw_id == claw.id, InboxItem.read_at.is_(None))
        .limit(1)
    )
    thread: ThreadRow | None = None
    if claw.main_thread_id is not None:
        thread = await get_thread(
            session, claw.workspace_id, claw.main_thread_id, lock="acceptance", history=False
        )
        if thread.current_run_id is not None:
            active = await session.scalar(
                select(RunRow.id)
                .join(AttemptRow, AttemptRow.id == RunRow.current_attempt_id)
                .where(
                    RunRow.id == thread.current_run_id,
                    RunRow.status == "running",
                    RunRow.principal_id == claw.service_account_id,
                    RunRow.cancel_requested_at.is_(None),
                    AttemptRow.status.in_(("leased", "running")),
                    AttemptRow.lease_expires_at > await now(session),
                )
            )
            if active is not None:
                await steer_unread(session, claw, delivery_id, active)
            else:
                await link_delivery(session, claw, delivery_id, None)
            return
        last = await session.get(RunRow, thread.last_run_id) if thread.last_run_id else None
        if not eligible(thread, last) or (paused(last) and delivery_id is None):
            await link_delivery(session, claw, delivery_id, None)
            return
        if not paused(last) and await pending_entries(session, thread.id, limit=1):
            # Native eligible work always gets the next Run before an App wake.
            await link_delivery(session, claw, delivery_id, None)
            return
        idle_since = last.sealed_at if last is not None else thread.created_at
    else:
        idle_since = claw.created_at

    if unread is None and (
        not claw.heartbeat_seconds
        or idle_since is None
        or await now(session) < idle_since + timedelta(seconds=claw.heartbeat_seconds)
    ):
        return
    workspace = await session.get_one(WorkspaceRow, claw.workspace_id)
    scope = WorkspaceScope(workspace.organization_id, workspace.id)
    actor = await account(session, runtime, scope, claw.service_account_id)
    await workspace_scope(session, actor, workspace.id, "run")
    authority = execution_authority(actor, scope)
    agent = await session.scalar(
        select(AgentRow).where(AgentRow.id == claw.agent_id, AgentRow.workspace_id == workspace.id)
    )
    if agent is None:
        raise not_found("agent", claw.agent_id)
    run_options = await options(session, claw, agent, role="main")
    text = (
        "New Claw Inbox mail. List unread previews and open the items you choose."
        if unread
        else (
            "Claw heartbeat. Follow up on unfinished work if useful. "
            "If no user-facing reply is needed, finish_silently."
        )
    )
    if thread is None:
        thread, source = await submit.prepare_thread(
            session,
            runtime,
            actor,
            scope,
            NewThread(
                agent_id=agent.id,
                agent_revision_id=selected_revision(claw, agent),
                payload=payload(text),
                options=run_options,
                session=project_session(claw, f"{claw.name} · Main"),
                environments=project_mounts(claw),
                browser_binding=project_browser(claw),
            ),
            request=None,
            authority=authority,
        )
        claw.main_thread_id, claw.main_session_id = thread.id, thread.session_id
    else:
        source = await submit.prepare_message(
            session,
            runtime,
            actor,
            scope,
            thread,
            Message(
                agent_id=agent.id,
                agent_revision_id=selected_revision(claw, agent),
                payload=payload(text),
                options=run_options,
            ),
            None,
            authority=authority,
        )
    run = await accept(session, runtime, thread, explicit=source)
    await link_delivery(
        session,
        claw,
        delivery_id,
        run.id
        if run is not None and source.entry is not None and run.source_entry_id == source.entry.id
        else None,
    )


async def steer_unread(
    session: AsyncSession, claw: Claw, delivery_id: str | None, run_id: str
) -> None:
    # Address the hint to this Run now. The Harness model hook sees it at the
    # next model request; it never becomes queued input for another native Run.
    target = (
        select(Delivery.id)
        .join(InboxItem, InboxItem.id == Delivery.item_id)
        .where(Delivery.claw_id == claw.id, Delivery.run_id.is_(None), InboxItem.read_at.is_(None))
        .order_by(Delivery.created_at, Delivery.id)
        .limit(16)
    )
    if delivery_id is not None:
        target = target.where(Delivery.id == delivery_id)
    await session.execute(
        update(Delivery)
        .where(Delivery.id.in_(target))
        .values(thread_id=claw.main_thread_id, session_id=claw.main_session_id, run_id=run_id)
    )


async def link_delivery(
    session: AsyncSession, claw: Claw, delivery_id: str | None, run_id: str | None
) -> None:
    if delivery_id is not None:
        await session.execute(
            update(Delivery)
            .where(
                Delivery.claw_id == claw.id,
                Delivery.id == delivery_id,
                Delivery.run_id.is_(None),
            )
            .values(
                thread_id=claw.main_thread_id,
                session_id=claw.main_session_id,
                run_id=run_id,
            )
        )
