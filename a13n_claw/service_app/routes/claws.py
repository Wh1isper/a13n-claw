"""Claw claws routes."""

from datetime import datetime, timedelta
from typing import Annotated, Literal

import pydantic
from a13n_service.app_sdk import threads as app_threads
from a13n_service.app_sdk.usage import AppModelUsageQuery, app_models
from a13n_service.infra.cursors import keyset_page
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.environment_instances.tables import EnvironmentRow
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.resources.models.tables import ModelRow
from a13n_service.resources.providers.tables import BrowserProviderRow
from a13n_service.runs.browsers.tables import BrowserRow
from a13n_service.runs.environments.schemas import EnvironmentFailure
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.runs.schemas import Failure
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.authorize import WorkspaceScope
from a13n_service.tenancy.requests import Actor, WorkspaceId
from a13n_service.tenancy.tables import GrantRow, PrincipalRow, WorkspaceRow
from a13n_service.usage.schemas import ModelMetrics
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import (
    connectors,
    egress,
    inbox,
    main_thread,
    project,
    project_cli,
)
from ..models import (
    APP_ID,
    Claw,
    InboxItem,
    claw_context,
)
from .common import Name, PageSize, ResourceId, get_claw, lock_active_claw, prepare_claw_admission

router = app_router(APP_ID)


class WorkerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")]
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]


class WorkerAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: ResourceId
    alias: Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")]
    description: Annotated[str, StringConstraints(max_length=4096)] | None = None


class ShellReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    model: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    risk_threshold: Literal["low", "medium", "high", "extra_high"] = "high"
    instruction: Annotated[str, StringConstraints(max_length=32768)] | None = None

    @pydantic.model_validator(mode="after")
    def require_model(self):
        if self.enabled and self.model is None:
            raise ValueError("A Workspace Model is required when Shell review is enabled")
        return self


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    heartbeat_seconds: int = Field(default=300, ge=0, le=86400)
    unread_limit: int = Field(default=500, ge=1, le=10000)
    read_limit: int = Field(default=3000, ge=1, le=10000)
    worker_models: list[WorkerModel] = Field(default_factory=list, max_length=20)
    worker_agents: list[WorkerAgent] = Field(default_factory=list, max_length=20)
    shell_review: ShellReview = Field(default_factory=ShellReview)

    @pydantic.model_validator(mode="after")
    def distinct_delegates(self):
        aliases = [item.alias for item in self.worker_agents]
        if len(aliases) != len(set(aliases)):
            raise ValueError("Worker Agent aliases must be distinct")
        models = [item.model for item in self.worker_models]
        if len(models) != len(set(models)):
            raise ValueError("Worker Models must be distinct")
        return self


class CreateClaw(Settings):
    agent_id: ResourceId
    environment_id: ResourceId | None = None
    environment_template_id: ResourceId | None = None
    browser_id: ResourceId | None = None
    browser_provider_id: ResourceId | None = None
    service_account_id: ResourceId

    @pydantic.model_validator(mode="after")
    def select_environment(self):
        if self.environment_id is not None and self.environment_template_id is not None:
            raise ValueError("Select an existing Environment or an Environment template, not both")
        if self.browser_id is not None and self.browser_provider_id is not None:
            raise ValueError("Select an existing Browser or a Browser provider, not both")
        return self


class ClawView(Settings):
    model_config = ConfigDict(from_attributes=True)
    agent_id: str
    agent_revision_id: str | None
    environment_id: str | None
    browser_id: str | None
    service_account_id: str
    id: str
    workspace_id: str
    memory_space_id: str
    main_thread_id: str | None
    main_session_id: str | None
    created_at: datetime


class ThreadActivity(BaseModel):
    current_run_id: str | None
    last_run_id: str | None
    last_status: str | None


class ProjectStatus(BaseModel):
    environment: str
    environment_name: str | None
    environment_failure: EnvironmentFailure | None
    browser_name: str | None
    browser_failure: str | None
    browser: str | None
    automation_account: str
    cli_versions: dict[str, str] | None
    cli_update_state: project_cli.UpdateState | None
    cli_update_error: str | None


class Overview(BaseModel):
    claw: ClawView
    unread: int
    read: int
    main: ThreadActivity | None
    last_failure: Failure | None
    agent_revision_outdated: bool
    project: ProjectStatus


@router.get("/claws", name="claw_list_claws")
async def list_claws(
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    cursor: str | None = None,
    limit: PageSize = 50,
) -> dict[str, list[ClawView] | str | None]:
    async with short_session(runtime.storage) as session:
        rows, next_cursor = await keyset_page(
            session,
            select(Claw).where(Claw.workspace_id == workspace_id, Claw.archived_at.is_(None)),
            (Claw.created_at, Claw.id),
            kind="claw",
            owner=workspace_id,
            cursor=cursor,
            limit=limit,
            newest_first=False,
        )
        return {"items": [ClawView.model_validate(row) for row in rows], "next_cursor": next_cursor}


class ResourceChoice(BaseModel):
    id: str
    name: str


class ResourceChoices(BaseModel):
    items: list[ResourceChoice]
    next_cursor: str | None


@router.get("/project-resources/{kind}", name="claw_project_resources")
async def project_resources(
    kind: Literal[
        "agent", "account", "environment", "template", "browser", "browser_provider", "model"
    ],
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    account_id: ResourceId | None = None,
    cursor: str | None = None,
    limit: PageSize = 50,
) -> ResourceChoices:
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "admin")
        match kind:
            case "agent":
                table = AgentRow
                identifier = AgentRow.id
                filters = (
                    AgentRow.workspace_id == workspace_id,
                    AgentRow.archived_at.is_(None),
                    AgentRow.default_revision_id.is_not(None),
                    or_(
                        AgentRow.source == "custom",
                        select(AgentRevisionRow.id)
                        .where(
                            AgentRevisionRow.id == AgentRow.default_revision_id,
                            AgentRevisionRow.config["summarize"].astext == "true",
                        )
                        .exists(),
                    ),
                )
            case "account":
                table = PrincipalRow
                identifier = PrincipalRow.id
                filters = (
                    PrincipalRow.home_workspace_id == workspace_id,
                    PrincipalRow.status == "active",
                    select(GrantRow.id)
                    .where(
                        GrantRow.principal_id == PrincipalRow.id,
                        GrantRow.workspace_id == workspace_id,
                        GrantRow.role.in_(("runner", "builder", "admin")),
                    )
                    .exists(),
                )
            case "environment":
                table = EnvironmentRow
                identifier = EnvironmentRow.id
                filters = (
                    EnvironmentRow.workspace_id == workspace_id,
                    EnvironmentRow.status.not_in(("disabled", "deleting", "deleted", "lost")),
                    EnvironmentRow.failure["permanent"].astext.is_distinct_from("true"),
                    or_(
                        EnvironmentRow.owner_principal_id.is_(None),
                        EnvironmentRow.kind == "external",
                        EnvironmentRow.owner_principal_id == account_id,
                    ),
                )
            case "template":
                table = EnvironmentTemplateRow
                identifier = EnvironmentTemplateRow.id
                filters = (
                    EnvironmentTemplateRow.workspace_id == workspace_id,
                    EnvironmentTemplateRow.enabled,
                )
            case "browser":
                table = BrowserRow
                identifier = BrowserRow.id
                filters = (
                    BrowserRow.workspace_id == workspace_id,
                    BrowserRow.status.not_in(("deleting", "deleted", "lost")),
                )
            case "browser_provider":
                table = BrowserProviderRow
                identifier = BrowserProviderRow.id
                filters = (
                    BrowserProviderRow.workspace_id == workspace_id,
                    BrowserProviderRow.enabled,
                )
            case "model":
                table = ModelRow
                identifier = ModelRow.id
                filters = (ModelRow.workspace_id == workspace_id, ModelRow.enabled)
        rows, next_cursor = await keyset_page(
            session,
            select(table).where(*filters),
            (table.created_at, identifier),
            kind=f"claw_resource_{kind}",
            owner=f"{workspace_id}:{account_id or ''}",
            cursor=cursor,
            limit=limit,
            newest_first=False,
        )
        return ResourceChoices(
            items=[
                ResourceChoice(id=row.key if isinstance(row, ModelRow) else row.id, name=row.name)
                for row in rows
            ],
            next_cursor=next_cursor,
        )


async def prepare_worker_selections(
    session: AsyncSession,
    runtime: CurrentRuntime,
    actor: Actor,
    scope: WorkspaceScope,
    body: Settings,
) -> dict[str, str]:
    selected = {item.agent_id for item in body.worker_agents}
    revisions: dict[str, str] = {}
    for agent_id in sorted(selected):
        agent = await main_thread.current_agent(session, scope.workspace_id, agent_id)
        assert agent.default_revision_id is not None
        revision = await session.get_one(AgentRevisionRow, agent.default_revision_id)
        revisions[agent_id] = await main_thread.prepare_worker_revision(
            session, runtime, actor, scope, agent, revision, body.shell_review.model_dump()
        )
    keys = {item.model for item in body.worker_models}
    if body.shell_review.enabled:
        assert body.shell_review.model is not None
        keys.add(body.shell_review.model)
    if keys:
        existing = set(
            (
                await session.scalars(
                    select(ModelRow.key).where(
                        ModelRow.workspace_id == scope.workspace_id,
                        ModelRow.enabled,
                        ModelRow.key.in_(keys),
                    )
                )
            ).all()
        )
        if missing := keys - existing:
            raise not_found("model", sorted(missing)[0])
    return revisions


@router.post("/claws", status_code=201, name="claw_create_claw")
async def create_claw(
    body: CreateClaw,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> ClawView:
    claw_id = inbox.new_id("clw")
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        await app_threads.prepare_admission(
            session,
            runtime,
            workspace_id,
            body.service_account_id,
            app_id=APP_ID,
            app_context=claw_context(claw_id),
        )
        account = await main_thread.automation_account(
            session, runtime, scope, body.service_account_id
        )
        agent = await main_thread.check_project(
            session, runtime, scope, account, body.agent_id, body.environment_id, body.browser_id
        )
        assert agent.default_revision_id is not None
        revision = await session.get_one(AgentRevisionRow, agent.default_revision_id)
        environment_id = body.environment_id
        if body.environment_template_id is not None:
            environment_id = await project.reserve_environment(
                session, runtime, account, scope, body.environment_template_id, body.name, claw_id
            )
        browser_id = body.browser_id
        if body.browser_provider_id is not None:
            browser_id = await project.reserve_browser(
                session, runtime, account, scope, body.browser_provider_id, body.name, claw_id
            )
        worker_revisions = await prepare_worker_selections(session, runtime, actor, scope, body)
        revision_id = await main_thread.prepare_revision(
            session,
            runtime,
            actor,
            scope,
            agent,
            revision,
            shell_review=body.shell_review.model_dump(),
        )
        space_id = await project.create_space(session, runtime, actor, scope, claw_id, body.name)
        claw = Claw(
            id=claw_id,
            workspace_id=workspace_id,
            agent_revision_id=revision_id,
            source_agent_revision_id=revision.id,
            worker_agent_revisions=worker_revisions,
            memory_space_id=space_id,
            environment_id=environment_id,
            browser_id=browser_id,
            **body.model_dump(
                exclude={
                    "environment_id",
                    "environment_template_id",
                    "browser_id",
                    "browser_provider_id",
                }
            ),
        )
        session.add(claw)
        await session.flush()
        return ClawView.model_validate(claw)


@router.delete("/claws/{claw_id}", status_code=204, name="claw_archive")
async def archive_claw(
    claw_id: ResourceId, workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime
) -> None:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        await egress.invalidate_claw(session, claw.id)
        claw.archived_at = await now(session)
    await connectors.connectors_changed(runtime, scope.organization_id)


@router.post("/claws/{claw_id}/agent-revision/sync", name="claw_sync_agent_revision")
async def sync_agent_revision(
    claw_id: ResourceId, workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime
) -> ClawView:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        await prepare_claw_admission(session, runtime, workspace_id, claw_id)
        claw = await lock_active_claw(session, workspace_id, claw_id)
        agent = await main_thread.current_agent(session, workspace_id, claw.agent_id)
        assert agent.default_revision_id is not None
        pinned = (
            await session.get(AgentRevisionRow, claw.agent_revision_id)
            if claw.agent_revision_id
            else None
        )
        if (
            pinned is None
            or not main_thread.revision_is_safe(pinned, claw.shell_review)
            or not main_thread.source_matches(claw, agent.default_revision_id)
        ):
            revision = await session.get_one(AgentRevisionRow, agent.default_revision_id)
            claw.agent_revision_id = await main_thread.prepare_revision(
                session, runtime, actor, scope, agent, revision, shell_review=claw.shell_review
            )
        claw.source_agent_revision_id = agent.default_revision_id
        return ClawView.model_validate(claw)


@router.put("/claws/{claw_id}/settings", name="claw_update_settings")
async def update_settings(
    claw_id: ResourceId,
    body: Settings,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> ClawView:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        await prepare_claw_admission(session, runtime, workspace_id, claw_id)
        claw = await lock_active_claw(session, workspace_id, claw_id)
        worker_revisions = await prepare_worker_selections(session, runtime, actor, scope, body)
        if claw.shell_review != body.shell_review.model_dump():
            agent = await main_thread.current_agent(session, workspace_id, claw.agent_id)
            revision_id = claw.source_agent_revision_id or agent.default_revision_id
            assert revision_id is not None
            revision = await session.get_one(AgentRevisionRow, revision_id)
            claw.agent_revision_id = await main_thread.prepare_revision(
                session,
                runtime,
                actor,
                scope,
                agent,
                revision,
                shell_review=body.shell_review.model_dump(),
            )
        claw.worker_agent_revisions = worker_revisions
        for key, value in body.model_dump().items():
            setattr(claw, key, value)
        await inbox.prune_read(session, claw, await now(session))
        return ClawView.model_validate(claw)


class ProjectBindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: ResourceId | None = None
    environment_template_id: ResourceId | None = None
    browser_id: ResourceId | None = None
    browser_provider_id: ResourceId | None = None

    @pydantic.model_validator(mode="after")
    def valid(self):
        if "environment_id" in self.model_fields_set and self.environment_id is None:
            raise ValueError("Select an Environment to replace the current one")
        if self.environment_id is not None and self.environment_template_id is not None:
            raise ValueError("Select an existing Environment or an Environment template, not both")
        if self.browser_provider_id is not None and "browser_id" in self.model_fields_set:
            raise ValueError("Select an existing Browser or a Browser provider, not both")
        return self


@router.patch("/claws/{claw_id}/project", name="claw_update_project")
async def update_project(
    claw_id: ResourceId,
    body: ProjectBindings,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> ClawView:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        await prepare_claw_admission(session, runtime, workspace_id, claw_id)
        claw = await lock_active_claw(session, workspace_id, claw_id)
        account = await main_thread.automation_account(
            session, runtime, scope, claw.service_account_id
        )
        environment_id = body.environment_id or claw.environment_id
        browser_id = body.browser_id if "browser_id" in body.model_fields_set else claw.browser_id
        await project.rebind(
            session,
            runtime,
            scope,
            account,
            claw,
            environment_id,
            browser_id,
            environment_template_id=body.environment_template_id,
            browser_provider_id=body.browser_provider_id,
        )
        result = ClawView.model_validate(claw)
    if body.environment_id is not None or body.environment_template_id is not None:
        await main_thread.notify(runtime, workspace_id, claw_id)
    return result


@router.get("/claws/{claw_id}", name="claw_overview")
async def overview(
    claw_id: ResourceId, workspace_id: WorkspaceId, runtime: CurrentRuntime
) -> Overview:
    async with short_session(runtime.storage) as session:
        claw = await get_claw(session, workspace_id, claw_id)
        cutoff = await now(session) - inbox.HISTORY_AGE
        unread, read = (
            await session.execute(
                select(
                    func.count().filter(InboxItem.read_at.is_(None)),
                    func.count().filter(InboxItem.read_at >= cutoff),
                ).where(InboxItem.claw_id == claw.id)
            )
        ).one()
        activity = None
        last_failure = None
        if claw.main_thread_id is not None:
            try:
                result = await app_threads.get_thread_activity(
                    session, workspace_id, claw.main_thread_id
                )
            except ServiceError as error:
                if error.code != "not_found":
                    raise
            else:
                activity = ThreadActivity(
                    current_run_id=result.current_run_id,
                    last_run_id=result.last_run_id,
                    last_status=result.last_status,
                )
                last_failure = result.last_failure if result.last_status == "failed" else None
        environment = (
            (
                await session.execute(
                    select(
                        EnvironmentRow.status, EnvironmentRow.name, EnvironmentRow.failure
                    ).where(
                        EnvironmentRow.workspace_id == workspace_id,
                        EnvironmentRow.id == claw.environment_id,
                    )
                )
            ).one_or_none()
            if claw.environment_id is not None
            else None
        )
        browser = (
            (
                await session.execute(
                    select(BrowserRow.status, BrowserRow.name, BrowserRow.failure).where(
                        BrowserRow.workspace_id == workspace_id,
                        BrowserRow.id == claw.browser_id,
                    )
                )
            ).one_or_none()
            if claw.browser_id is not None
            else None
        )
        organization_id = await session.scalar(
            select(WorkspaceRow.organization_id).where(WorkspaceRow.id == workspace_id)
        )
        account_status = "unavailable"
        if organization_id is not None:
            try:
                await main_thread.automation_account(
                    session,
                    runtime,
                    WorkspaceScope(organization_id, workspace_id),
                    claw.service_account_id,
                )
            except ServiceError:
                pass
            else:
                account_status = "active"
        default_revision = await session.scalar(
            select(AgentRow.default_revision_id).where(
                AgentRow.id == claw.agent_id,
                AgentRow.workspace_id == workspace_id,
                AgentRow.archived_at.is_(None),
            )
        )
        pinned_revision = (
            await session.get(AgentRevisionRow, claw.agent_revision_id)
            if claw.agent_revision_id
            else None
        )
        return Overview(
            claw=ClawView.model_validate(claw),
            unread=unread,
            read=read,
            main=activity,
            last_failure=last_failure,
            agent_revision_outdated=default_revision is not None
            and (
                pinned_revision is None
                or not main_thread.revision_is_safe(pinned_revision, claw.shell_review)
                or not main_thread.source_matches(claw, default_revision)
            ),
            project=ProjectStatus(
                environment=environment.status
                if environment
                else ("not configured" if claw.environment_id is None else "missing"),
                environment_name=environment.name if environment else None,
                environment_failure=environment.failure if environment else None,
                browser_failure=browser.failure if browser else None,
                browser_name=browser.name if browser else None,
                browser=browser.status if browser else ("missing" if claw.browser_id else None),
                automation_account=account_status,
                cli_versions=claw.cli_versions,
                cli_update_state=claw.cli_update_state,
                cli_update_error=claw.cli_update_error,
            ),
        )


@router.post("/claws/{claw_id}/project/cli-update", status_code=202, name="claw_update_cli")
async def update_cli(
    claw_id: ResourceId, workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime
) -> dict[str, str]:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "admin")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        if claw.environment_id is None:
            raise conflict("claw", claw.id, "environment_required")
        if claw.cli_update_state != "queued":
            claw.cli_update_id = inbox.new_id("cup")
            claw.cli_update_state = "queued"
            claw.cli_update_error = None
    return {"state": "queued"}


class ModelUsageView(BaseModel):
    model_id: str | None
    model: str | None
    usage: ModelMetrics


@router.get("/claws/{claw_id}/usage", name="claw_usage")
async def usage(
    claw_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    actor: Actor,
    days: Literal["7", "30", "90"] = "7",
) -> list[ModelUsageView]:
    async with short_session(runtime.storage) as session:
        claw = await get_claw(session, workspace_id, claw_id)
        end = await now(session)
        query = AppModelUsageQuery(
            start=end - timedelta(days=int(days)),
            end=end,
            principal_id=claw.service_account_id,
            run_labels={"claw": claw.id},
            limit=100,
        )
    groups = []
    while True:
        page = await app_models(runtime.storage, actor, workspace_id, query)
        groups.extend(page.items)
        if page.next_cursor is None:
            break
        query = query.model_copy(update={"cursor": page.next_cursor})
    keys = {group.model for group in groups if group.model is not None}
    async with short_session(runtime.storage) as session:
        model_ids = (
            dict(
                (
                    await session.execute(
                        select(ModelRow.key, ModelRow.id).where(
                            ModelRow.workspace_id == workspace_id, ModelRow.key.in_(keys)
                        )
                    )
                )
                .tuples()
                .all()
            )
            if keys
            else {}
        )
    views = [
        ModelUsageView(
            model_id=model_ids.get(group.model) if group.model else None,
            model=group.name,
            usage=group.usage,
        )
        for group in groups
    ]
    return sorted(views, key=lambda view: (-view.usage.requests, view.model_id or ""))


@router.get("/claws/{claw_id}/workers", name="claw_list_workers")
async def list_workers(
    claw_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    cursor: str | None = None,
    limit: PageSize = 50,
) -> dict:
    async with short_session(runtime.storage) as session:
        claw = await get_claw(session, workspace_id, claw_id)
        if claw.main_session_id is None:
            return {"items": [], "next_cursor": None}
        page = await app_threads.list_child_threads(
            session, workspace_id, session_id=claw.main_session_id, cursor=cursor, limit=limit
        )
        return {
            "items": [
                {
                    "thread_id": row.id,
                    "session_id": claw.main_session_id,
                    "name": row.subagent or "Worker",
                    "activity": {
                        "id": row.id,
                        "current_run_id": row.current_run_id,
                        "last_run_id": row.last_run_id,
                        "last_status": row.last_status,
                    },
                }
                for row in page.items
            ],
            "next_cursor": page.next_cursor,
        }
