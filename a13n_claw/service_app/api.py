"""Workspace API for Claw's control plane and incoming messages."""

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal

import pydantic
from a13n_service.infra.cursors import keyset_page
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.environment_instances.tables import EnvironmentRow
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.resources.models.tables import ModelRow
from a13n_service.runs.browsers.tables import BrowserRow
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.runs.schedules import queries as native_schedules
from a13n_service.runs.schedules.schemas import Schedule as NativeSchedule
from a13n_service.runs.schedules.schemas import ScheduleQuery
from a13n_service.runs.schedules.tables import ScheduleRow as NativeScheduleRow
from a13n_service.runs.tables import RunRow, ThreadRow, UsageRecordRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.authorize import WorkspaceScope
from a13n_service.tenancy.requests import Actor, WorkspaceId
from a13n_service.tenancy.tables import GrantRow, PrincipalRow, WorkspaceRow
from a13n_service.usage import service as native_usage
from a13n_service.usage.schemas import ModelMetrics, UsageWindow
from fastapi import Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
        if (self.environment_id is None) == (self.environment_template_id is None):
            raise ValueError("Select one existing Environment or one Environment template")
        return self


class ClawView(Settings):
    model_config = ConfigDict(from_attributes=True)
    agent_id: str
    agent_revision_id: str | None
    environment_id: str
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
    browser: str | None
    automation_account: str
    cli: dict[str, project_cli.CLIStatus] | None
    cli_checked_at: datetime | None


class Overview(BaseModel):
    claw: ClawView
    unread: int
    read: int
    main: ThreadActivity | None
    project: ProjectStatus


class DeliveryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    item_id: str | None
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
    kind: str | None
    preview: str | None
    content: str | None
    source: dict[str, JsonValue] | None
    read_at: datetime | None


class InputMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=65536)
    source: dict[str, JsonValue] = Field(default_factory=dict, max_length=24)


def scope_claw(workspace_id: str, claw_id: str):
    return Claw.workspace_id == workspace_id, Claw.id == claw_id


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
            select(Claw).where(Claw.workspace_id == workspace_id),
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
        if environment_id is None:
            assert body.environment_template_id is not None
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
            memory_space_id=space_id,
            environment_id=environment_id,
            **body.model_dump(exclude={"environment_id", "environment_template_id"}),
        )
        session.add(claw)
        await session.flush()
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
        try:
            claw = await inbox.lock_claw(session, workspace_id, claw_id)
        except inbox.ClawNotFound:
            raise not_found("claw", claw_id) from None
        for key, value in body:
            setattr(claw, key, value)
        await inbox.prune_read(session, claw, await now(session))
        return ClawView.model_validate(claw)


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
        if claw.main_thread_id is not None:
            result = (
                (
                    await session.execute(
                        select(
                            ThreadRow.current_run_id,
                            ThreadRow.last_run_id,
                            RunRow.status.label("last_status"),
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
        environment = await session.scalar(
            select(EnvironmentRow.status).where(
                EnvironmentRow.workspace_id == workspace_id,
                EnvironmentRow.id == claw.environment_id,
            )
        )
        browser = None
        if claw.browser_id is not None:
            browser = (
                await session.scalar(
                    select(BrowserRow.status).where(
                        BrowserRow.workspace_id == workspace_id, BrowserRow.id == claw.browser_id
                    )
                )
                or "missing"
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
        return Overview(
            claw=ClawView.model_validate(claw),
            unread=unread,
            read=read,
            main=activity,
            project=ProjectStatus(
                environment=environment or "missing",
                browser=browser,
                automation_account=account_status,
                cli=claw.cli_observation,
                cli_checked_at=claw.cli_checked_at,
            ),
        )


class ModelUsageView(BaseModel):
    model_id: str | None
    model: str | None
    usage: ModelMetrics


@router.get("/claws/{claw_id}/usage", name="claw_usage")
async def usage(
    claw_id: ResourceId,
    workspace_id: WorkspaceId,
    runtime: CurrentRuntime,
    days: Literal["7", "30", "90"] = "7",
) -> list[ModelUsageView]:
    async with short_session(runtime.storage) as session:
        claw = await get_claw(session, workspace_id, claw_id)
        end = await now(session)
        window = UsageWindow(start=end - timedelta(days=int(days)), end=end)
        model_id = UsageRecordRow.model_id
        query = native_usage._model_query(workspace_id, window).add_columns(model_id, ModelRow.name)
        rows = (
            await session.execute(
                query.join(RunRow, RunRow.id == UsageRecordRow.run_id)
                .outerjoin(ModelRow, ModelRow.id == model_id)
                .where(
                    RunRow.principal_id == claw.service_account_id,
                    RunRow.options["labels"]["claw"].astext == claw.id,
                )
                .group_by(model_id, ModelRow.name)
                .order_by(func.count().desc(), model_id)
            )
        ).mappings()
        return [
            ModelUsageView(
                model_id=row["model_id"], model=row["name"], usage=native_usage._metrics(row)
            )
            for row in rows
        ]


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
        try:
            claw = await inbox.lock_claw(session, workspace_id, claw_id)
        except inbox.ClawNotFound:
            raise not_found("claw", claw_id) from None
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
    status: Literal["accepted", "full", "dropped"] | None = None,
    cursor: str | None = None,
    limit: PageSize = 30,
) -> DeliveryPage:
    async with short_session(runtime.storage) as session:
        await get_claw(session, workspace_id, claw_id)
        query = select(Delivery).where(
            Delivery.claw_id == claw_id,
            Delivery.created_at >= await now(session) - inbox.HISTORY_AGE,
        )
        if status is not None:
            query = query.where(Delivery.status == status)
        rows, next_cursor = await keyset_page(
            session,
            query,
            (Delivery.created_at, Delivery.id),
            kind="claw_delivery",
            owner=f"{claw_id}:{status or 'all'}",
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        return DeliveryPage(
            items=[DeliveryView.model_validate(row) for row in rows], next_cursor=next_cursor
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
                .outerjoin(
                    InboxItem,
                    and_(
                        InboxItem.claw_id == Delivery.claw_id,
                        InboxItem.id == Delivery.item_id,
                        or_(InboxItem.read_at.is_(None), InboxItem.read_at >= cutoff),
                    ),
                )
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
            **DeliveryView.model_validate(delivery).model_dump(),
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
            await schedules.find_schedule(session, claw, schedule_id)
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
        claw = await get_claw(session, workspace_id, claw_id)
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
    return await schedules.create_fresh(runtime, claw, body, f"{claw.id}:{actor.id}:{request_key}")


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
        claw = await get_claw(session, workspace_id, claw_id)
        await inbox.lock_claw(session, workspace_id, claw_id)
        schedule = await schedules.set_enabled(session, claw, schedule_id, body.enabled)
        return schedules.ScheduleView.model_validate(schedule)


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
        await schedules.find_schedule(session, claw, schedule_id)
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
        return {
            "items": [schedules.OccurrenceView.model_validate(row) for row in rows],
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


class ConnectorView(ConnectorInput):
    model_config = ConfigDict(from_attributes=True)
    id: str
    last_event_at: datetime | None
    created_at: datetime
    credential_mounted: bool = False


def connector_view(row: Connector) -> ConnectorView:
    view = ConnectorView.model_validate(row)
    mounted = connectors.secret_path(row.id)
    return view.model_copy(update={"credential_mounted": bool(mounted and mounted.is_file())})


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
    return {"items": [connector_view(row) for row in rows], "next_cursor": next_cursor}


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
    return connector_view(row)


@router.post("/claws/{claw_id}/connectors", status_code=201, name="claw_create_connector")
async def create_connector(
    claw_id: ResourceId,
    body: ConnectorInput,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> ConnectorView:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        claw = await get_claw(session, workspace_id, claw_id)
        if body.kind != "lark":
            await main_thread.account(session, runtime, scope, str(body.config["principal_id"]))
        await inbox.lock_claw(session, workspace_id, claw_id)
        if await session.scalar(
            select(Connector.id).where(Connector.claw_id == claw.id, Connector.name == body.name)
        ):
            raise conflict("connector", body.name, "name_in_use")
        row = Connector(id=inbox.new_id("con"), claw_id=claw.id, **body.model_dump())
        session.add(row)
        await session.flush()
    return connector_view(row)


@router.put("/claws/{claw_id}/connectors/{connector_id}", name="claw_update_connector")
async def update_connector(
    claw_id: ResourceId,
    connector_id: ResourceId,
    body: ConnectorInput,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
) -> ConnectorView:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "admin")
        claw = await get_claw(session, workspace_id, claw_id)
        await inbox.lock_claw(session, workspace_id, claw_id)
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
    return connector_view(row)


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
