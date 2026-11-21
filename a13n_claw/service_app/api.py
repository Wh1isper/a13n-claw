"""Workspace API for Claw's control plane and incoming messages."""

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal

import httpx
import pydantic
from a13n_service.infra.cursors import keyset_page
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.environment_instances.tables import EnvironmentRow
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.resources.models.tables import ModelRow
from a13n_service.runs.browsers.tables import BrowserRow
from a13n_service.runs.environments.schemas import EnvironmentFailure
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.runs.schedules import queries as native_schedules
from a13n_service.runs.schedules.schemas import Schedule as NativeSchedule
from a13n_service.runs.schedules.schemas import ScheduleQuery
from a13n_service.runs.schedules.tables import ScheduleRow as NativeScheduleRow
from a13n_service.runs.schemas import Failure
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.authorize import WorkspaceScope
from a13n_service.tenancy.requests import Actor, WorkspaceId
from a13n_service.tenancy.tables import GrantRow, PrincipalRow, WorkspaceRow
from a13n_service.usage import service as native_usage
from a13n_service.usage.schemas import AppModelUsageQuery, ModelMetrics
from fastapi import Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import with_expression

from . import connectors, inbox, main_thread, project, project_cli, schedules
from .feishu import LarkSettings
from .models import APP_ID, Claw, Connector, Delivery, InboxItem, Occurrence, Worker

router = app_router(APP_ID)

ResourceId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
PageSize = Annotated[int, Query(ge=1, le=100)]


class WorkerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")]
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    heartbeat_seconds: int = Field(default=300, ge=0, le=86400)
    unread_limit: int = Field(default=500, ge=1, le=10000)
    read_limit: int = Field(default=3000, ge=1, le=10000)
    worker_models: list[WorkerModel] = Field(default_factory=list, max_length=20)


class CreateClaw(Settings):
    agent_id: ResourceId
    environment_id: ResourceId | None = None
    environment_template_id: ResourceId | None = None
    browser_id: ResourceId | None = None
    service_account_id: ResourceId

    @pydantic.model_validator(mode="after")
    def select_environment(self):
        if self.environment_id is not None and self.environment_template_id is not None:
            raise ValueError("Select an existing Environment or an Environment template, not both")
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
    fresh_schedule_count: int
    unauthorized_reply_connectors: int
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


class DeliveryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    item_id: str | None
    kind: str | None = None
    preview: str | None = None
    read_at: datetime | None = None
    origin: str
    status: str
    thread_id: str | None
    session_id: str | None
    run_id: str | None
    created_at: datetime


class DeliveryPage(BaseModel):
    items: list[DeliveryView]
    next_cursor: str | None


class DeliveryDetail(DeliveryView):
    content: str | None
    source: dict[str, JsonValue] | None


class InputMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=65536)
    source: dict[str, JsonValue] = Field(default_factory=dict, max_length=24)


def scope_claw(workspace_id: str, claw_id: str):
    return Claw.workspace_id == workspace_id, Claw.id == claw_id, Claw.archived_at.is_(None)


async def get_claw(session: AsyncSession, workspace_id: str, claw_id: str) -> Claw:
    claw = await session.scalar(select(Claw).where(*scope_claw(workspace_id, claw_id)))
    if claw is None:
        raise not_found("claw", claw_id)
    return claw


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
    kind: Literal["agent", "account", "environment", "template", "browser", "model"],
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
        account = await main_thread.account(session, runtime, scope, body.service_account_id)
        agent = await main_thread.check_project(
            session, scope, account, body.agent_id, body.environment_id, body.browser_id
        )
        assert agent.default_revision_id is not None
        revision = await session.get_one(AgentRevisionRow, agent.default_revision_id)
        environment_id = body.environment_id
        if body.environment_template_id is not None:
            environment_id = await project.reserve_environment(
                session, runtime, account, scope, body.environment_template_id, body.name
            )
        revision_id = await main_thread.prepare_revision(
            session, runtime, actor, scope, agent, revision
        )
        space_id = await project.create_space(session, runtime, actor, scope, claw_id, body.name)
        claw = Claw(
            id=claw_id,
            workspace_id=workspace_id,
            agent_revision_id=revision_id,
            source_agent_revision_id=revision.id,
            memory_space_id=space_id,
            environment_id=environment_id,
            **body.model_dump(exclude={"environment_id", "environment_template_id"}),
        )
        session.add(claw)
        await session.flush()
        return ClawView.model_validate(claw)


async def lock_active_claw(session: AsyncSession, workspace_id: str, claw_id: str) -> Claw:
    try:
        return await inbox.lock_claw(session, workspace_id, claw_id)
    except inbox.ClawNotFound:
        raise not_found("claw", claw_id) from None


@router.delete("/claws/{claw_id}", status_code=204, name="claw_archive")
async def archive_claw(
    claw_id: ResourceId, workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime
) -> None:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        await schedules.require_no_fresh_work(
            session, claw, reason="archive_fresh_schedules_before_archiving"
        )
        claw.archived_at = await now(session)
    await connectors.lark_changed(runtime, scope.organization_id)


@router.post("/claws/{claw_id}/agent-revision/sync", name="claw_sync_agent_revision")
async def sync_agent_revision(
    claw_id: ResourceId, workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime
) -> ClawView:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        agent = await main_thread.current_agent(session, workspace_id, claw.agent_id)
        assert agent.default_revision_id is not None
        if claw.agent_revision_id is None or not await main_thread.source_matches(
            session, claw, agent.default_revision_id
        ):
            revision = await session.get_one(AgentRevisionRow, agent.default_revision_id)
            claw.agent_revision_id = await main_thread.prepare_revision(
                session, runtime, actor, scope, agent, revision
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
        await workspace_scope(session, actor, workspace_id, "admin")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        for key, value in body:
            setattr(claw, key, value)
        await inbox.prune_read(session, claw, await now(session))
        return ClawView.model_validate(claw)


class ProjectBindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: ResourceId | None = None
    environment_template_id: ResourceId | None = None
    browser_id: ResourceId | None = None

    @pydantic.model_validator(mode="after")
    def valid(self):
        if "environment_id" in self.model_fields_set and self.environment_id is None:
            raise ValueError("Select an Environment to replace the current one")
        if self.environment_id is not None and self.environment_template_id is not None:
            raise ValueError("Select an existing Environment or an Environment template, not both")
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
        claw = await lock_active_claw(session, workspace_id, claw_id)
        account = await main_thread.account(session, runtime, scope, claw.service_account_id)
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
            result = (
                (
                    await session.execute(
                        select(
                            ThreadRow.current_run_id,
                            ThreadRow.last_run_id,
                            RunRow.status.label("last_status"),
                            RunRow.failure,
                        )
                        .outerjoin(RunRow, RunRow.id == ThreadRow.last_run_id)
                        .where(
                            ThreadRow.workspace_id == workspace_id,
                            ThreadRow.id == claw.main_thread_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if result is not None:
                activity = ThreadActivity.model_validate(result)
                last_failure = result["failure"] if result["last_status"] == "failed" else None
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
                await main_thread.account(
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
        unauthorized = await session.scalar(
            select(func.count(Connector.id)).where(
                Connector.claw_id == claw.id,
                Connector.enabled,
                Connector.reply_reauthorization_required,
            )
        )
        return Overview(
            claw=ClawView.model_validate(claw),
            unread=unread,
            read=read,
            main=activity,
            last_failure=last_failure,
            agent_revision_outdated=default_revision is not None
            and not await main_thread.source_matches(session, claw, default_revision),
            project=ProjectStatus(
                environment=environment.status
                if environment
                else ("not configured" if claw.environment_id is None else "missing"),
                environment_name=environment.name if environment else None,
                environment_failure=environment.failure if environment else None,
                browser_failure=browser.failure if browser else None,
                fresh_schedule_count=await schedules.fresh_schedule_count(session, claw),
                unauthorized_reply_connectors=unauthorized or 0,
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
        page = await native_usage.app_models(runtime.storage, actor, workspace_id, query)
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


@router.post("/claws/{claw_id}/input", status_code=201, name="claw_receive")
async def receive(
    claw_id: ResourceId,
    body: InputMessage,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    response: Response,
    request_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> inbox.Admission:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "run")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        result = await inbox.admit(
            session,
            claw,
            inbox.Offer(
                "user",
                "input",
                f"input:{actor.id}:{sha256(request_key.encode()).hexdigest()}",
                {**body.source, "principal_id": actor.id},
                body.content,
                body.content[:200],
            ),
        )
    if result.status == "full":
        raise conflict("claw", claw_id, "inbox_full", delivery_id=result.delivery_id)
    if result.status == "duplicate":
        response.status_code = 200
    else:
        await main_thread.notify(runtime, workspace_id, claw_id, delivery_id=result.delivery_id)
    return result


@router.get("/claws/{claw_id}/deliveries", name="claw_deliveries")
async def deliveries(
    claw_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    status: Literal["accepted", "duplicate", "full", "dropped"] | None = None,
    inbox_state: Literal["unread", "read", "expired"] | None = None,
    cursor: str | None = None,
    limit: PageSize = 30,
) -> DeliveryPage:
    async with short_session(runtime.storage) as session:
        await get_claw(session, workspace_id, claw_id)
        cutoff = await now(session) - inbox.HISTORY_AGE
        query = (
            select(Delivery)
            .outerjoin(InboxItem, retained_item(cutoff))
            .where(Delivery.claw_id == claw_id, Delivery.created_at >= cutoff)
            .options(
                with_expression(Delivery.kind, InboxItem.kind),
                with_expression(Delivery.preview, InboxItem.preview),
                with_expression(Delivery.read_at, InboxItem.read_at),
            )
        )
        if inbox_state is not None:
            if inbox_state == "expired":
                query = query.where(Delivery.item_id.is_not(None), InboxItem.id.is_(None))
            else:
                query = query.where(
                    InboxItem.id.is_not(None),
                    InboxItem.read_at.is_(None)
                    if inbox_state == "unread"
                    else InboxItem.read_at.is_not(None),
                )
        if status is not None:
            query = query.where(Delivery.status == status)
        rows, next_cursor = await keyset_page(
            session,
            query,
            (Delivery.created_at, Delivery.id),
            kind="claw_delivery",
            owner=f"{claw_id}:{status or 'all'}:{inbox_state or 'all'}",
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        return DeliveryPage(
            items=[DeliveryView.model_validate(row) for row in rows], next_cursor=next_cursor
        )


def still_retained(cutoff: datetime):
    return or_(InboxItem.read_at.is_(None), InboxItem.read_at >= cutoff)


def retained_item(cutoff: datetime):
    return and_(
        InboxItem.claw_id == Delivery.claw_id,
        InboxItem.id == Delivery.item_id,
        still_retained(cutoff),
    )


@router.get("/claws/{claw_id}/deliveries/{delivery_id}", name="claw_delivery_detail")
async def delivery_detail(
    claw_id: ResourceId,
    delivery_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
) -> DeliveryDetail:
    async with short_session(runtime.storage) as session:
        cutoff = await now(session) - inbox.HISTORY_AGE
        result = (
            await session.execute(
                select(Delivery, InboxItem)
                .join(Claw, Claw.id == Delivery.claw_id)
                .outerjoin(InboxItem, retained_item(cutoff))
                .where(
                    *scope_claw(workspace_id, claw_id),
                    Delivery.id == delivery_id,
                    Delivery.created_at >= cutoff,
                )
            )
        ).one_or_none()
        if result is None:
            raise not_found("delivery", delivery_id)
        delivery, item = result
        return DeliveryDetail(
            **DeliveryView.model_validate(delivery).model_dump(
                exclude={"kind", "preview", "read_at"}
            ),
            kind=item.kind if item else None,
            preview=item.preview if item else None,
            content=item.content if item else None,
            source=item.source if item else None,
            read_at=item.read_at if item else None,
        )


@router.get("/claws/{claw_id}/workers", name="claw_list_workers")
async def list_workers(
    claw_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    cursor: str | None = None,
    limit: PageSize = 50,
) -> dict:
    async with short_session(runtime.storage) as session:
        await get_claw(session, workspace_id, claw_id)
        rows, next_cursor = await keyset_page(
            session,
            select(Worker).where(Worker.claw_id == claw_id),
            (Worker.created_at, Worker.thread_id),
            kind="claw_worker",
            owner=claw_id,
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        thread_activity = (
            (
                await session.execute(
                    select(
                        ThreadRow.id,
                        ThreadRow.current_run_id,
                        ThreadRow.last_run_id,
                        RunRow.status.label("last_status"),
                    )
                    .outerjoin(RunRow, RunRow.id == ThreadRow.last_run_id)
                    .where(
                        ThreadRow.workspace_id == workspace_id,
                        ThreadRow.id.in_([row.thread_id for row in rows]),
                    )
                )
            ).all()
            if rows
            else []
        )
        by_id = {row.id: dict(row._mapping) for row in thread_activity}
        return {
            "items": [
                {
                    "thread_id": row.thread_id,
                    "session_id": row.session_id,
                    "name": row.name,
                    "model": row.model,
                    "activity": by_id.get(row.thread_id),
                }
                for row in rows
            ],
            "next_cursor": next_cursor,
        }


@router.get("/claws/{claw_id}/schedules", name="claw_list_schedules")
async def list_schedules(
    claw_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    cursor: str | None = None,
    limit: PageSize = 50,
) -> dict:
    async with short_session(runtime.storage) as session:
        claw = await get_claw(session, workspace_id, claw_id)
        rows, next_cursor = await schedules.list_schedules(session, claw, cursor, limit)
        return {
            "items": [schedules.ScheduleView.model_validate(row) for row in rows],
            "next_cursor": next_cursor,
        }


@router.get("/claws/{claw_id}/schedules/{schedule_id}", name="claw_schedule_detail")
async def schedule_detail(
    claw_id: ResourceId, schedule_id: ResourceId, workspace_id: WorkspaceId, runtime: CurrentRuntime
) -> schedules.ScheduleView:
    async with short_session(runtime.storage) as session:
        claw = await get_claw(session, workspace_id, claw_id)
        return schedules.ScheduleView.model_validate(
            await schedules.find_schedule(session, claw, schedule_id, include_deleted=True)
        )


@router.post("/claws/{claw_id}/schedules", status_code=201, name="claw_create_schedule")
async def create_schedule(
    claw_id: ResourceId,
    body: schedules.ScheduleInput,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> schedules.ScheduleView:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        return schedules.ScheduleView.model_validate(await schedules.create(session, claw, body))


@router.get("/claws/{claw_id}/fresh-schedules", name="claw_list_fresh_schedules")
async def list_fresh_schedules(
    claw_id: ResourceId,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    cursor: str | None = None,
    limit: PageSize = 30,
) -> dict:
    async with short_session(runtime.storage) as session:
        claw = await get_claw(session, workspace_id, claw_id)
    page = await native_schedules.list_schedules(
        runtime.storage,
        actor,
        workspace_id,
        ScheduleQuery(
            cursor=cursor,
            limit=limit,
            label=[f"claw:{claw.id}"],
            principal_id=claw.service_account_id,
        ),
    )
    async with short_session(runtime.storage) as session:
        next_times = (
            dict(
                (
                    await session.execute(
                        select(NativeScheduleRow.id, NativeScheduleRow.next_fire_at).where(
                            NativeScheduleRow.workspace_id == workspace_id,
                            NativeScheduleRow.id.in_([row.id for row in page.items]),
                        )
                    )
                )
                .tuples()
                .all()
            )
            if page.items
            else {}
        )
    return {
        "items": [
            dict(id=row.id, name=row.name, state=row.state, next_at=next_times.get(row.id))
            for row in page.items
        ],
        "next_cursor": page.next_cursor,
    }


@router.post("/claws/{claw_id}/fresh-schedules", status_code=201, name="claw_create_fresh_schedule")
async def create_fresh_schedule(
    claw_id: ResourceId,
    body: schedules.ScheduleInput,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    request_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> NativeSchedule:
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
        claw = await get_claw(session, workspace_id, claw_id)
    try:
        return await schedules.create_fresh(
            runtime, claw, body, f"{claw.id}:{actor.id}:{request_key}"
        )
    except inbox.ClawNotFound:
        raise not_found("claw", claw_id) from None


class ScheduleState(BaseModel):
    enabled: bool


@router.patch("/claws/{claw_id}/schedules/{schedule_id}", name="claw_set_schedule_state")
async def set_schedule_state(
    claw_id: ResourceId,
    schedule_id: ResourceId,
    body: ScheduleState,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> schedules.ScheduleView:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        schedule = await schedules.set_enabled(session, claw, schedule_id, body.enabled)
        return schedules.ScheduleView.model_validate(schedule)


@router.put("/claws/{claw_id}/schedules/{schedule_id}", name="claw_edit_schedule")
async def edit_schedule(
    claw_id: ResourceId,
    schedule_id: ResourceId,
    body: schedules.ScheduleEdit,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> schedules.ScheduleView:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        return schedules.ScheduleView.model_validate(
            await schedules.edit(session, claw, schedule_id, body)
        )


@router.delete(
    "/claws/{claw_id}/schedules/{schedule_id}", status_code=204, name="claw_delete_schedule"
)
async def delete_schedule(
    claw_id: ResourceId,
    schedule_id: ResourceId,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> None:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        await schedules.remove(session, claw, schedule_id)


@router.get("/claws/{claw_id}/schedules/{schedule_id}/occurrences", name="claw_occurrences")
async def occurrences(
    claw_id: ResourceId,
    schedule_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    cursor: str | None = None,
    limit: PageSize = 50,
) -> dict:
    async with short_session(runtime.storage) as session:
        claw = await get_claw(session, workspace_id, claw_id)
        await schedules.find_schedule(session, claw, schedule_id, include_deleted=True)
        rows, next_cursor = await keyset_page(
            session,
            select(Occurrence).where(Occurrence.schedule_id == schedule_id),
            (Occurrence.scheduled_at, Occurrence.id),
            kind="claw_occurrence",
            owner=schedule_id,
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        item_ids = {row.item_id for row in rows if row.item_id is not None}
        links = (
            dict(
                (
                    await session.execute(
                        select(Delivery.item_id, Delivery.id).where(
                            Delivery.claw_id == claw.id,
                            Delivery.item_id.in_(item_ids),
                            Delivery.status == "accepted",
                            Delivery.created_at >= await now(session) - inbox.HISTORY_AGE,
                        )
                    )
                )
                .tuples()
                .all()
            )
            if item_ids
            else {}
        )
        return {
            "items": [
                schedules.OccurrenceView.model_validate(row).model_copy(
                    update={"delivery_id": links.get(row.item_id)}
                )
                for row in rows
            ],
            "next_cursor": next_cursor,
        }


class ConnectorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    kind: Literal["lark", "github"]
    config: dict[str, JsonValue]
    enabled: bool = True

    @pydantic.model_validator(mode="after")
    def validate_config(self):
        schema = LarkSettings if self.kind == "lark" else connectors.ExternalSettings
        self.config = schema.model_validate(self.config).model_dump(mode="json")
        return self


class ConnectorWrite(ConnectorInput):
    credential: pydantic.SecretStr | None = None
    reply_environment_id: ResourceId | None = None

    @pydantic.field_validator("credential")
    @classmethod
    def valid_credential(cls, secret: pydantic.SecretStr | None) -> pydantic.SecretStr | None:
        if secret is not None:
            value = secret.get_secret_value()
            if (
                not 1 <= len(value) <= 8192
                or not value.isascii()
                or not all(33 <= ord(char) <= 126 for char in value)
            ):
                raise ValueError("Enter a non-empty platform credential without spaces")
        return secret


class ConnectorView(ConnectorInput):
    model_config = ConfigDict(from_attributes=True)
    id: str
    last_event_at: datetime | None
    created_at: datetime
    credential_configured: bool = False
    reply_environment_id: str | None = None
    reply_reauthorization_required: bool = False
    listener_status: str | None = None


async def connector_view(runtime: CurrentRuntime, row: Connector) -> ConnectorView:
    view = ConnectorView.model_validate(row)
    return view.model_copy(
        update={
            "credential_configured": row.credential is not None,
            "listener_status": await connectors.listener_status(runtime, row.id)
            if row.enabled and row.kind == "lark" and row.credential is not None
            else None,
        }
    )


@router.get("/claws/{claw_id}/connectors", name="claw_list_connectors")
async def list_connectors(
    claw_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    cursor: str | None = None,
    limit: PageSize = 50,
) -> dict:
    async with short_session(runtime.storage) as session:
        await get_claw(session, workspace_id, claw_id)
        rows, next_cursor = await keyset_page(
            session,
            select(Connector).where(Connector.claw_id == claw_id),
            (Connector.created_at, Connector.id),
            kind="claw_connector",
            owner=claw_id,
            cursor=cursor,
            limit=limit,
            newest_first=False,
        )
    return {
        "items": [await connector_view(runtime, row) for row in rows],
        "next_cursor": next_cursor,
    }


@router.get("/claws/{claw_id}/connectors/{connector_id}", name="claw_connector_detail")
async def connector_detail(
    claw_id: ResourceId,
    connector_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
) -> ConnectorView:
    async with short_session(runtime.storage) as session:
        await get_claw(session, workspace_id, claw_id)
        row = await connectors.find(session, claw_id, connector_id)
    return await connector_view(runtime, row)


def check_reply_environment(claw: Claw, body: ConnectorWrite) -> None:
    if body.reply_environment_id is not None and body.reply_environment_id != claw.environment_id:
        raise conflict("claw", claw.id, "reply_environment_changed")


@router.post("/claws/{claw_id}/connectors", status_code=201, name="claw_create_connector")
async def create_connector(
    claw_id: ResourceId,
    body: ConnectorWrite,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> ConnectorView:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        claw = await get_claw(session, workspace_id, claw_id)
        if body.kind != "lark":
            await main_thread.account(session, runtime, scope, str(body.config["principal_id"]))
        claw = await lock_active_claw(session, workspace_id, claw_id)
        check_reply_environment(claw, body)
        if await session.scalar(
            select(Connector.id).where(Connector.claw_id == claw.id, Connector.name == body.name)
        ):
            raise conflict("connector", body.name, "name_in_use")
        row = Connector(
            id=inbox.new_id("con"), claw_id=claw.id, **body.model_dump(exclude={"credential"})
        )
        if body.credential is not None:
            row.credential = connectors.protect(
                runtime, scope.organization_id, row.id, body.credential.get_secret_value()
            )
        session.add(row)
        await session.flush()
    if row.kind == "lark":
        await connectors.lark_changed(runtime, scope.organization_id)
    return await connector_view(runtime, row)


@router.put("/claws/{claw_id}/connectors/{connector_id}", name="claw_update_connector")
async def update_connector(
    claw_id: ResourceId,
    connector_id: ResourceId,
    body: ConnectorWrite,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> ConnectorView:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        claw = await lock_active_claw(session, workspace_id, claw_id)
        check_reply_environment(claw, body)
        row = await connectors.find(session, claw.id, connector_id)
        if row.kind != body.kind:
            raise conflict("connector", connector_id, "connector_kind_is_immutable")
        if body.kind != "lark":
            await main_thread.account(session, runtime, scope, str(body.config["principal_id"]))
        if row.name != body.name and await session.scalar(
            select(Connector.id).where(
                Connector.claw_id == claw.id,
                Connector.name == body.name,
            )
        ):
            raise conflict("connector", body.name, "name_in_use")
        row.name, row.enabled, row.config = body.name, body.enabled, body.config
        if "reply_environment_id" in body.model_fields_set:
            row.reply_environment_id = body.reply_environment_id
            row.reply_reauthorization_required = False
        if "credential" in body.model_fields_set:
            row.credential = (
                connectors.protect(
                    runtime, scope.organization_id, row.id, body.credential.get_secret_value()
                )
                if body.credential is not None
                else None
            )
    if row.kind == "lark":
        await connectors.lark_changed(runtime, scope.organization_id)
    return await connector_view(runtime, row)


@router.delete(
    "/claws/{claw_id}/connectors/{connector_id}", status_code=204, name="claw_delete_connector"
)
async def delete_connector(
    claw_id: ResourceId,
    connector_id: ResourceId,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> None:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        await lock_active_claw(session, workspace_id, claw_id)
        row = await connectors.find(session, claw_id, connector_id)
        await session.delete(row)
    if row.kind == "lark":
        await connectors.lark_changed(runtime, scope.organization_id)


class ConnectorCheck(BaseModel):
    state: Literal["not configured", "failed", "verified"]
    reason: str | None = None
    http_status: int | None = None


@router.post(
    "/claws/{claw_id}/connectors/{connector_id}/check",
    name="claw_check_connector",
    response_model_exclude_none=True,
)
async def check_connector(
    claw_id: ResourceId,
    connector_id: ResourceId,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> ConnectorCheck:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        await get_claw(session, workspace_id, claw_id)
        row = await connectors.find(session, claw_id, connector_id)
        try:
            token = connectors.credential(runtime, scope.organization_id, row)
        except ServiceError:
            return ConnectorCheck(state="failed", reason="credential_unavailable")
    if token is None:
        return ConnectorCheck(state="not configured")
    try:
        await connectors.check(row.kind, row.id, row.config, token)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if row.kind == "github" and status in (403, 404):
            reason = "repository_unavailable"
        elif status in (400, 401, 403):
            reason = "credentials_rejected"
        else:
            reason = "provider_http_error"
        return ConnectorCheck(state="failed", reason=reason, http_status=status)
    except httpx.RequestError:
        return ConnectorCheck(state="failed", reason="provider_unreachable")
    except ValueError:
        return ConnectorCheck(
            state="failed",
            reason="check_region_and_credentials" if row.kind == "lark" else "invalid_repository",
        )
    return ConnectorCheck(state="verified")


@router.post(
    "/claws/{claw_id}/connectors/{connector_id}/input",
    status_code=201,
    name="claw_receive_connector",
)
async def receive_connector(
    claw_id: ResourceId,
    connector_id: ResourceId,
    body: connectors.ExternalMessage,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    response: Response,
) -> inbox.Admission:
    try:
        result = await connectors.offer_external(
            runtime, actor, workspace_id, claw_id, connector_id, body
        )
    except inbox.ClawNotFound:
        raise not_found("claw", claw_id) from None
    if result.status == "full":
        raise conflict("claw", claw_id, "inbox_full", delivery_id=result.delivery_id)
    if result.status == "duplicate":
        response.status_code = 200
    return result
