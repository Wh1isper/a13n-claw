"""Native Main admission and heartbeat. Inbox storage precedes every wake."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from a13n_logging import get_logger
from a13n_service.app_sdk import agents as app_agents
from a13n_service.app_sdk import projects as app_projects
from a13n_service.app_sdk import threads as app_threads
from a13n_service.infra.db import now, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.resources.agents.schemas import (
    AgentConfig,
    AgentOverride,
    PluginSelection,
)
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.agents.toolsets import catalog
from a13n_service.resources.environment_instances.tables import EnvironmentRow
from a13n_service.resources.memories.schemas import SessionMemorySettings, SpaceBinding
from a13n_service.runs.accept import eligible, paused
from a13n_service.runs.browsers.schemas import BrowserBinding
from a13n_service.runs.environments.schemas import MountCreate
from a13n_service.runs.inbox import Request, pending_entries
from a13n_service.runs.schemas import (
    Message,
    MessagePayload,
    NewThread,
    RunOptions,
    SessionCreate,
    SessionOrigin,
    TextPart,
)
from a13n_service.runs.tables import AttemptRow, InboxEntryRow, RunRow, ThreadRow
from a13n_service.runs.threads import get_thread
from a13n_service.tenancy.access import principal_for
from a13n_service.tenancy.authorize import (
    Principal,
    WorkspaceScope,
    authorize,
    execution_authority,
)
from a13n_service.tenancy.tables import WorkspaceRow
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import egress
from .inbox import ClawNotFound, lock_claw
from .models import APP_ID, Claw, Delivery, InboxItem, claw_context

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


async def automation_account(
    session: AsyncSession, runtime: Runtime, scope: WorkspaceScope, account_id: str
) -> Principal:
    actor = await account(session, runtime, scope, account_id)
    if execution_authority(actor, scope).verbs != {"read", "run", "write"}:
        raise conflict("service account", account_id, "claw_builder_required")
    return actor


async def check_project(
    session: AsyncSession,
    runtime: Runtime,
    scope: WorkspaceScope,
    actor: Principal,
    agent_id: str,
    environment_id: str | None,
    browser_id: str | None,
) -> AgentRow:
    agent = await current_agent(session, scope.workspace_id, agent_id)
    await app_projects.check_resources(
        session,
        runtime,
        scope.workspace_id,
        principal_id=actor.id,
        environment_id=environment_id,
        browser_id=browser_id,
    )
    return agent


async def current_agent(session: AsyncSession, workspace_id: str, agent_id: str) -> AgentRow:
    agent = await session.scalar(
        select(AgentRow)
        .where(AgentRow.workspace_id == workspace_id, AgentRow.id == agent_id)
        .with_for_update()
    )
    if agent is None:
        raise not_found("agent", agent_id)
    if agent.archived_at is not None or agent.default_revision_id is None:
        raise conflict("agent", agent_id, "agent_unavailable")
    return agent


def safe_revision_config(
    source: dict, *, summarize: bool, shell_review: dict | None = None, guard: bool = False
) -> AgentConfig:
    """Compile a revision that cannot request an interactive tool decision."""
    original = AgentConfig.model_validate(source)
    data = original.model_dump(mode="json")
    data["user_questions"] = False
    if summarize:
        data["summarize"] = True
    defaults = {
        (group.key, tool.key): tool.default_permission
        for group in catalog(frozenset()).items
        for tool in group.tools
    }
    for group_name, group in data["toolsets"].items():
        for tool_name, tool in group["tools"].items():
            permission = tool["permission"]
            if permission == "ask" or (
                permission == "inherit" and defaults.get((group_name, tool_name)) == "ask"
            ):
                tool["permission"] = "deny"
    for connection in data["connection_tools"]:
        if connection["permission"] == "ask":
            connection["permission"] = "deny"
        for name, permission in connection["permissions"].items():
            if permission == "ask":
                connection["permissions"][name] = "deny"
    if shell_review and shell_review.get("enabled"):
        if not shell_review.get("model"):
            raise ValueError("Shell review requires a model")
        reviewer = data.get("reviewer") or {}
        reviewer.update(
            model=shell_review["model"],
            risk_threshold=shell_review.get("risk_threshold", "high"),
            shell_instruction=shell_review.get("instruction"),
        )
        reviewer.setdefault("rules", {}).setdefault("environment.shell_exec", {})[
            "risk_threshold"
        ] = shell_review.get("risk_threshold", "high")
        data["reviewer"] = reviewer
        shell_exec = data["toolsets"]["shell"]["tools"]["exec"]
        if shell_exec["permission"] != "deny":
            shell_exec["permission"] = "review"
    if reviewer := data.get("reviewer"):
        reviewer["on_flagged"] = "deny"
        reviewer["on_error"] = "deny"
        for rule in reviewer.get("rules", {}).values():
            if rule.get("on_flagged") == "approval_required":
                rule["on_flagged"] = "deny"
    if any(p["instance_name"] == "claw" and p["plugin_key"] != PLUGIN_KEY for p in data["plugins"]):
        raise ValueError("Agent has an unrelated plugin occupying the Claw instance name")
    if guard:
        data["plugins"] = [p for p in data["plugins"] if p["plugin_key"] != PLUGIN_KEY]
        data["plugins"].append(
            {"instance_name": "claw", "plugin_key": PLUGIN_KEY, "config": {"role": "guard"}}
        )
    return AgentConfig.model_validate(data)


def revision_is_safe(revision: AgentRevisionRow, shell_review: dict) -> bool:
    return AgentConfig.model_validate(revision.config) == safe_revision_config(
        revision.config, summarize=True, shell_review=shell_review
    )


async def prepare_revision(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    scope: WorkspaceScope,
    agent: AgentRow,
    revision: AgentRevisionRow,
    *,
    shell_review: dict | None = None,
) -> str:
    config = safe_revision_config(revision.config, summarize=True, shell_review=shell_review)
    if config == AgentConfig.model_validate(revision.config):
        return revision.id
    if agent.builtin:
        raise conflict("agent", agent.id, "managed_revision_requires_safe_configuration")
    # Preserve the selected immutable source configuration, not the current
    # default: the SDK publishes and validates the supplied transformation.
    published = await app_agents.publish_derived_revision(
        session,
        runtime,
        scope.workspace_id,
        app_id=APP_ID,
        actor_id=actor.id,
        agent_id=agent.id,
        mutate_config=lambda _default: config,
        note="Claw Project context summary",
    )
    return published.id


async def prepare_worker_revision(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    scope: WorkspaceScope,
    agent: AgentRow,
    revision: AgentRevisionRow,
    shell_review: dict,
    *,
    path: frozenset[str] = frozenset(),
) -> str:
    """Pin every reachable named child to a non-interactive revision."""
    if revision.id in path:
        raise conflict("agent", agent.id, "cyclic_worker_graph")
    original = AgentConfig.model_validate(revision.config)
    config = safe_revision_config(
        revision.config, summarize=False, shell_review=shell_review, guard=not agent.builtin
    )
    named = {}
    for alias, edge in sorted(config.subagents.named.items()):
        child = await current_agent(session, scope.workspace_id, edge.agent_id)
        child_revision = await session.get(
            AgentRevisionRow, edge.revision_id or child.default_revision_id
        )
        if child_revision is None or child_revision.agent_id != child.id:
            raise not_found("agent revision", edge.revision_id or child.id)
        child_id = await prepare_worker_revision(
            session,
            runtime,
            actor,
            scope,
            child,
            child_revision,
            shell_review,
            path=path | {revision.id},
        )
        named[alias] = edge.model_copy(update={"revision_id": child_id})
    config = config.model_copy(
        update={"subagents": config.subagents.model_copy(update={"named": named})}
    )
    if agent.builtin:
        if config != original or original.client_tools or original.plugins:
            raise conflict("agent", agent.id, "builtin_worker_requires_safe_configuration")
        return revision.id
    if config == original:
        return revision.id
    published = await app_agents.publish_derived_revision(
        session,
        runtime,
        scope.workspace_id,
        app_id=APP_ID,
        actor_id=actor.id,
        agent_id=agent.id,
        mutate_config=lambda _default: config,
        note="Claw safe Worker revision",
    )
    return published.id


async def source_matches(session: AsyncSession, claw: Claw, default_id: str) -> bool:
    if claw.source_agent_revision_id is not None:
        return claw.source_agent_revision_id == default_id
    if claw.agent_revision_id is None or claw.agent_revision_id == default_id:
        return True
    # Existing rows predate the source pointer. A generated summary revision
    # still represents the current default if composition gives the same config.
    pinned = await session.get(AgentRevisionRow, claw.agent_revision_id)
    if pinned is None or pinned.note != "Claw Project context summary":
        return False
    default = await session.get_one(AgentRevisionRow, default_id)
    return AgentConfig.model_validate(pinned.config) == safe_revision_config(
        default.config, summarize=True
    )


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


def project_mounts(
    claw: Claw, configuration: app_threads.SessionConfiguration | None = None
) -> tuple[MountCreate, ...]:
    if claw.environment_id is None:
        raise conflict("claw", claw.id, "project_environment_required")
    return (
        MountCreate(
            name="workspace",
            environment_id=claw.environment_id,
            session_configuration=configuration,
        ),
    )


def project_browser(claw: Claw) -> BrowserBinding | None:
    if claw.browser_id is None:
        return None
    return BrowserBinding(browser_id=claw.browser_id, tab_access="global")


async def options(
    session: AsyncSession, claw: Claw, agent: AgentRow, *, role: str, model: str | None = None
) -> RunOptions:
    revision = await session.get_one(AgentRevisionRow, selected_revision(claw, agent))
    if not revision_is_safe(revision, claw.shell_review):
        raise conflict("claw", claw.id, "safe_revision_sync_required")
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
        named = {}
        for entry in claw.worker_agents:
            agent_id = entry["agent_id"]
            alias = entry["alias"]
            if not isinstance(agent_id, str) or not isinstance(alias, str):
                raise conflict("claw", claw.id, "invalid_worker_agent")
            revision_id = claw.worker_agent_revisions.get(agent_id)
            if revision_id is None:
                raise conflict("claw", claw.id, "worker_revision_unavailable")
            named[alias] = {
                "agent_id": agent_id,
                "revision_id": revision_id,
                "description": entry.get("description"),
                "environment": {"mode": "shared"},
            }
        overrides["subagents"] = {
            "mode": "async",
            "named": named,
            "dynamic": {
                "enabled": bool(claw.worker_models),
                "allowed_models": [item["model"] for item in claw.worker_models],
            },
        }
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
    runtime: Runtime,
    workspace_id: str,
    claw_id: str,
    *,
    delivery_id: str | None = None,
    completed_run_id: str | None = None,
    unread_only: bool = False,
) -> None:
    try:
        async with transaction(runtime.storage) as session:
            # The execution account is immutable. Read it without locking Claw,
            # then take Service admission locks in organization → Claw → Thread order.
            account_id = await session.scalar(
                select(Claw.service_account_id).where(
                    Claw.workspace_id == workspace_id,
                    Claw.id == claw_id,
                    Claw.archived_at.is_(None),
                    Claw.environment_id.is_not(None),
                )
            )
            if account_id is None:
                return
            admission = await app_threads.prepare_admission(
                session,
                runtime,
                workspace_id,
                account_id,
                app_id=APP_ID,
                app_context=claw_context(claw_id),
            )
            claw = await lock_claw(session, workspace_id, claw_id)
            workspace = await session.get_one(WorkspaceRow, workspace_id)
            await automation_account(
                session,
                runtime,
                WorkspaceScope(workspace.organization_id, workspace_id),
                account_id,
            )
            await _wake(
                session,
                runtime,
                claw,
                admission,
                delivery_id,
                completed_run_id=completed_run_id,
                unread_only=unread_only,
            )
    except ClawNotFound:
        return
    except ServiceError as error:
        logger.warning(
            "Claw Main wake refused: %s",
            error.message,
            extra={"claw_id": claw_id, "error_code": error.code},
        )


async def _wake(
    session: AsyncSession,
    runtime: Runtime,
    claw: Claw,
    admission: app_threads.Admission,
    delivery_id: str | None,
    *,
    completed_run_id: str | None,
    unread_only: bool,
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
        .order_by(InboxItem.created_at, InboxItem.id)
        .limit(1)
    )
    if unread is None and (unread_only or completed_run_id is not None):
        return
    thread: ThreadRow | None = None
    last: RunRow | None = None
    if claw.main_thread_id is not None:
        thread = await get_thread(
            session, claw.workspace_id, claw.main_thread_id, lock="acceptance", history=False
        )
        if completed_run_id is not None and (
            thread.last_run_id != completed_run_id or thread.current_run_id is not None
        ):
            return
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
        if completed_run_id is not None:
            if (
                last is None
                or last.status != "completed"
                or last.principal_id != claw.service_account_id
            ):
                return
            source_key = await session.scalar(
                select(InboxEntryRow.request_key).where(InboxEntryRow.id == last.source_entry_id)
            )
            if source_key and source_key.startswith("claw:seal:"):
                # A Main that ignored its previous seal wake must not drive a
                # tight loop. New unread mail or a read during that Run counts
                # as progress; a sweep can still retry unchanged unread mail.
                progress = await session.scalar(
                    select(InboxItem.id)
                    .where(
                        InboxItem.claw_id == claw.id,
                        or_(
                            (InboxItem.created_at >= last.created_at) & InboxItem.read_at.is_(None),
                            InboxItem.read_at >= last.created_at,
                        ),
                    )
                    .limit(1)
                )
                if progress is None:
                    return
        if not eligible(thread, last) or (paused(last) and (delivery_id is None or unread_only)):
            await link_delivery(session, claw, delivery_id, None)
            return
        if not paused(last) and await pending_entries(session, thread.id, limit=1):
            # Native eligible work always gets the next Run before an App wake.
            await link_delivery(session, claw, delivery_id, None)
            return
        idle_since = last.sealed_at if last is not None else thread.created_at
    else:
        if completed_run_id is not None:
            return
        idle_since = claw.created_at

    if unread is None and (
        not claw.heartbeat_seconds
        or idle_since is None
        or await now(session) < idle_since + timedelta(seconds=claw.heartbeat_seconds)
    ):
        return
    workspace = await session.get_one(WorkspaceRow, claw.workspace_id)
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
    message = Message(
        delivery="next_run",
        agent_id=agent.id,
        agent_revision_id=selected_revision(claw, agent),
        payload=payload(text),
        options=run_options,
    )
    if thread is not None:
        # One stable notice for this idle slot and work item. Include the native
        # message fingerprint so a changed Project/Agent capture can be admitted.
        digest = Request.of("", "message", thread.id, message).digest
        wake = "seal" if completed_run_id is not None else "wake"
        anchor = delivery_id or unread or "heartbeat"
        key = f"claw:{wake}:{last.id if last else thread.id}:{anchor}:{digest}"
        receipt, _ = await app_threads.submit_message_in_transaction(
            session, runtime, admission, claw.workspace_id, thread.id, message, request_key=key
        )
        if receipt.entry.status == "failed":
            # A rejected wake is not an admission. Roll it back so a repaired
            # resource can use the same key; the original Inbox item stays unread.
            failure = receipt.entry.failure
            raise ServiceError(
                "conflict",
                failure.message if failure else "Main wake rejected",
            )
        run_id = (
            receipt.run.id
            if receipt.run is not None and receipt.run.source_entry_id == receipt.entry.id
            else None
        )
        await link_delivery(session, claw, delivery_id, run_id)
        return

    revision = await session.get_one(AgentRevisionRow, selected_revision(claw, agent))
    default_config = revision.config.get("default_session_configuration")
    base = (
        app_threads.SessionConfiguration.model_validate(default_config) if default_config else None
    )
    body = NewThread(
        **message.model_dump(),
        session=project_session(claw, f"{claw.name} · Main"),
        environments=project_mounts(claw, await egress.initial_configuration(session, claw, base)),
        browser_binding=project_browser(claw),
    )
    created_thread, receipt = await app_threads.create_thread_in_transaction(
        session, runtime, admission, workspace.id, body, request_key=f"claw:first:{claw.id}"
    )
    claw.main_thread_id, claw.main_session_id = created_thread.id, created_thread.session_id
    await link_delivery(session, claw, delivery_id, receipt.entry.assigned_run_id)


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
