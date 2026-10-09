"""Authenticated application operations shared by the Console and API clients."""

import asyncio
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field, JsonValue

from a13n_claw import __version__
from a13n_claw.coordinator import Coordinator
from a13n_claw.domain import (
    MAX_ASSET_BYTES,
    ClawError,
    InputRequest,
    Principal,
    ResourceChange,
    ResourceId,
    RunRecord,
    Value,
)
from a13n_claw.instance import token_hash
from a13n_claw.runtime import Runtime
from a13n_claw.storage import Store


@dataclass(frozen=True)
class Services:
    store: Store
    runtime: Runtime
    coordinator: Coordinator
    workspace: Path


def services(request: Request) -> Services:
    value = request.app.state.services
    assert isinstance(value, Services)
    return value


async def authenticate(request: Request) -> Principal:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 4096:
        raise ClawError("unauthorized", "A Bearer access token is required", 401)
    # No cookies, URL credentials, permissive CORS, or browser token persistence.
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise ClawError("origin_forbidden", "Cross-origin API requests are not enabled", 403)
    return await asyncio.to_thread(services(request).store.authenticate, token_hash(token))


Actor = Annotated[Principal, Depends(authenticate)]
Application = Annotated[Services, Depends(services)]
router = APIRouter(prefix="/api")


class ThreadCreate(Value):
    title: str = Field(default="New conversation", min_length=1, max_length=500)
    profile_id: ResourceId | None = None


class ThreadEdit(Value):
    title: str = Field(min_length=1, max_length=500)
    profile_id: ResourceId
    expected_version: int = Field(ge=1)
    archived: bool = False


class ForkRequest(Value):
    checkpoint_id: str
    request_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    profile_id: ResourceId


class ResourceImport(Value):
    resources: list[ResourceChange] = Field(max_length=1000)


class SecretChange(Value):
    value: str | None = Field(default=None, max_length=65536)


class ClientChange(Value):
    principal: Principal


class ReviewRequest(Value):
    note: str = Field(min_length=1, max_length=10000)


class RecoveryRequest(Value):
    request_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=1_000_000)


class TargetOperation(Value):
    operation: Literal["inspect", "prepare", "stop", "remove"]


def project_run(run: RunRecord) -> dict[str, JsonValue]:
    # Executor fences are server-owned, not client control inputs.
    return run.model_dump(mode="json", exclude={"owner"})


@router.get("/instance")
async def instance(actor: Actor, app: Application):
    return {
        "version": __version__,
        "principal": actor,
        "dispatcher": "unavailable" if app.coordinator.failure else "ready",
        "error": app.coordinator.failure,
        "workspace": str(app.workspace) if actor.admin else None,
        "connectivity": "not_probed",
    }


@router.get("/resources")
async def resources(actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.resources, actor.id)


@router.post("/resources/import")
async def import_resources(body: ResourceImport, actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.import_resources, actor.id, body.resources)


@router.get("/profiles")
async def profiles(actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.available_profiles, actor.id)


@router.get("/credentials")
async def credentials(actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.credential_status, actor.id)


@router.put("/credentials/{name}", status_code=204)
async def set_credential(name: str, body: SecretChange, actor: Actor, app: Application):
    await asyncio.to_thread(app.store.save_credential, actor.id, name, body.value)


@router.get("/clients")
async def clients(actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.principals, actor.id)


@router.post("/clients", status_code=201)
async def create_client(body: ClientChange, actor: Actor, app: Application):
    token = secrets.token_urlsafe(32)
    await asyncio.to_thread(
        app.store.set_principal, actor.id, body.principal, token_hash(token), create_only=True
    )
    return {"principal": body.principal, "token": token}


@router.put("/clients/{client_id}", status_code=204)
async def update_client(client_id: str, body: ClientChange, actor: Actor, app: Application):
    if client_id != body.principal.id:
        raise ClawError("client_identity", "Client identity does not match the URL", 422)
    await asyncio.to_thread(app.store.set_principal, actor.id, body.principal, None)


@router.get("/threads")
async def threads(actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.threads, actor.id)


@router.post("/threads", status_code=201)
async def create_thread(body: ThreadCreate, actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.create_thread, actor.id, body.title, body.profile_id)


@router.post("/threads/fork", status_code=201)
async def fork_thread(body: ForkRequest, actor: Actor, app: Application):
    return await asyncio.to_thread(
        app.store.fork_thread,
        actor.id,
        body.checkpoint_id,
        body.request_id,
        body.title,
        body.profile_id,
    )


@router.get("/threads/{thread_id}")
async def thread(thread_id: str, actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.thread, actor.id, thread_id)


@router.put("/threads/{thread_id}")
async def update_thread(thread_id: str, body: ThreadEdit, actor: Actor, app: Application):
    return await asyncio.to_thread(
        app.store.update_thread,
        actor.id,
        thread_id,
        body.expected_version,
        title=body.title,
        profile_id=body.profile_id,
        archived=body.archived,
    )


@router.get("/threads/{thread_id}/history")
async def history(thread_id: str, actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.history, actor.id, thread_id)


@router.get("/threads/{thread_id}/inputs")
async def inputs(thread_id: str, actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.inputs, actor.id, thread_id)


@router.post("/threads/{thread_id}/inputs", status_code=202)
async def submit(thread_id: str, body: InputRequest, actor: Actor, app: Application):
    receipt = await asyncio.to_thread(
        app.store.admit, actor.id, thread_id, body, str(app.workspace)
    )
    app.coordinator.wake()
    return receipt


@router.get("/threads/{thread_id}/runs")
async def runs(thread_id: str, actor: Actor, app: Application):
    return [
        project_run(run) for run in await asyncio.to_thread(app.store.runs, actor.id, thread_id)
    ]


@router.get("/runs/{run_id}")
async def run(run_id: str, actor: Actor, app: Application):
    return project_run(await asyncio.to_thread(app.store.run, actor.id, run_id))


@router.get("/runs/{run_id}/observations")
async def observations(run_id: str, actor: Actor, app: Application):
    saved = await asyncio.to_thread(app.store.run, actor.id, run_id)
    return {
        "run": project_run(saved),
        "disposable": True,
        "events": list(app.coordinator.observations.get(run_id, [])),
    }


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str, actor: Actor, app: Application):
    saved = await asyncio.to_thread(app.store.cancel, actor.id, run_id)
    app.coordinator.wake()
    return project_run(saved)


@router.get("/runs/{run_id}/decisions")
async def decisions(run_id: str, actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.decisions, actor.id, run_id)


@router.post("/decisions/{decision_id}/answer")
async def answer(decision_id: str, body: dict[str, JsonValue], actor: Actor, app: Application):
    from a13n_claw.domain import canonical

    saved = await asyncio.to_thread(app.store.answer, actor.id, decision_id, canonical(body))
    app.coordinator.wake()
    return project_run(saved)


@router.get("/runs/{run_id}/children")
async def children(run_id: str, actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.delegations, actor.id, run_id)


@router.post("/runs/{run_id}/reconcile")
async def reconcile(run_id: str, body: ReviewRequest, actor: Actor, app: Application):
    return project_run(
        await asyncio.to_thread(app.store.reconcile_run, actor.id, run_id, body.note)
    )


@router.post("/runs/{run_id}/recover", status_code=202)
async def recover(run_id: str, body: RecoveryRequest, actor: Actor, app: Application):
    saved = await asyncio.to_thread(
        app.store.recover, actor.id, run_id, body.request_id, body.text, str(app.workspace)
    )
    app.coordinator.wake()
    return project_run(saved)


@router.post("/inputs/{input_id}/acknowledge")
async def acknowledge(input_id: str, body: ReviewRequest, actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.acknowledge_uncertainty, actor.id, input_id, body.note)


@router.post("/threads/{thread_id}/release-inputs", status_code=204)
async def release_inputs(thread_id: str, actor: Actor, app: Application):
    await asyncio.to_thread(app.store.release_blocked, actor.id, thread_id, str(app.workspace))
    app.coordinator.wake()


@router.get("/threads/{thread_id}/targets")
async def targets(thread_id: str, actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.targets, actor.id, thread_id)


@router.post("/targets/{target_id}/manage")
async def manage_target(target_id: str, body: TargetOperation, actor: Actor, app: Application):
    return await app.runtime.environments.manage(actor.id, target_id, body.operation)


@router.get("/threads/{thread_id}/files")
async def retained_files(thread_id: str, actor: Actor, app: Application):
    return await asyncio.to_thread(app.store.assets, actor.id, thread_id)


@router.post("/threads/{thread_id}/files", status_code=201)
async def upload_file(
    thread_id: str,
    request: Request,
    actor: Actor,
    app: Application,
    request_id: Annotated[str, Query(min_length=1, max_length=200)],
    name: Annotated[str, Query(min_length=1, max_length=255)],
):
    # Reject scope before reading a body. Bytes and metadata commit together.
    actor.require("submit", thread_id)
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_ASSET_BYTES:
            raise ClawError("asset_size", "Retained files are limited to 8 MiB each", 413)
    media_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    return await asyncio.to_thread(
        app.store.retain_asset, actor.id, thread_id, request_id, name, media_type, bytes(content)
    )


@router.get("/files/{file_id}/download")
async def download_file(file_id: str, actor: Actor, app: Application):
    asset, content = await asyncio.to_thread(app.store.asset_content, actor.id, file_id)
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(asset.name, safe='')}",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.get("/runs/{run_id}/files")
async def workspace_files(
    run_id: str,
    actor: Actor,
    app: Application,
    path: str = "/workspace",
    offset: Annotated[int, Query(ge=0)] = 0,
):
    async with app.runtime.environments.files(actor.id, run_id) as environment:
        return await environment.files.list(path, offset=offset, max_results=200)


@router.get("/runs/{run_id}/file-content")
async def workspace_content(
    run_id: str,
    actor: Actor,
    app: Application,
    path: str,
    line_offset: Annotated[int, Query(ge=0)] = 0,
):
    async with app.runtime.environments.files(actor.id, run_id) as environment:
        return await environment.files.read_text(path, line_offset=line_offset, line_limit=200)
