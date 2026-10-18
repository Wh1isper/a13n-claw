"""Trusted operator API composing public Host operations and Claw business services."""

from __future__ import annotations

import secrets
from typing import Annotated, Literal
from urllib.parse import quote

from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.surfaces import (
    DecisionResponseBatch,
    NewThreadDefaults,
    ThreadConfigurationMutationInput,
    ThreadMetadataMutation,
)
from a13n_harness_ui.thread_files import AttachmentUpload
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field

from a13n_claw import __version__
from a13n_claw.service import ClawService, database
from a13n_claw.settings import ProductModel


def service(request: Request) -> ClawService:
    value = request.app.state.service
    assert isinstance(value, ClawService)
    return value


async def authenticate(request: Request) -> None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, request.app.state.token):
        raise HTTPException(401, "An operator Bearer token is required")
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Cross-origin API access is disabled")


Application = Annotated[ClawService, Depends(service)]
router = APIRouter(prefix="/api", dependencies=[Depends(authenticate)])


class ThreadCreate(ProductModel):
    title: str | None = None
    defaults: NewThreadDefaults | None = None
    coordinator_thread_id: str | None = None


class Input(ProductModel):
    text: str = Field(min_length=1, max_length=100_000)
    attachment_ids: tuple[str, ...] = ()


class ConfigurationWrite(ProductModel):
    relative_path: str
    content: str
    expected_digest: str | None  # null means a new source


class AttemptResolution(ProductModel):
    action: Literal["retry", "settle"]
    evidence: str = Field(min_length=1)


class DeliveryResolution(ProductModel):
    outcome: Literal["sent", "not_sent"]
    evidence: str = Field(min_length=1)
    retry: bool = False


class TargetResolution(ProductModel):
    key: str
    action: Literal["adopt", "retry"]
    evidence: str = Field(min_length=1)


class InboxChange(ProductModel):
    expected_version: int = Field(ge=1)
    disposition: Literal["handled", "ignored", "pending"]
    reason: str = Field(min_length=1)


@router.get("/status")
async def status(app: Application):
    return {
        "version": __version__,
        "instance": await database(app.store.instance),
        "reconciliation_error": app.last_error,
        "connection_errors": app.connection_errors,
        "host": "open",
        "connectivity": "not_probed",
        "connections": {
            key: {
                "platform": config.platform,
                "enabled": config.enabled,
                "status": app.adapters[key].status if key in app.adapters else "disabled",
                "interactions": ["text", "files", "mentions", "commands", "decision_json"],
            }
            for key, config in app.settings.connections.items()
        },
    }


@router.get("/threads")
async def threads(
    app: Application,
    cursor: str | None = None,
    project_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
):
    return await app.host.list_threads(cursor=cursor, limit=limit, project_id=project_id)


@router.post("/threads", status_code=201)
async def create_thread(body: ThreadCreate, app: Application):
    return await app.host.create_thread(
        title=body.title, defaults=body.defaults, coordinator_thread_id=body.coordinator_thread_id
    )


@router.get("/threads/{thread_id}")
async def thread(thread_id: str, app: Application):
    return await app.host.get_thread(thread_id)


@router.patch("/threads/{thread_id}/metadata")
async def thread_metadata(thread_id: str, body: ThreadMetadataMutation, app: Application):
    return await app.host.update_thread_metadata(thread_id=thread_id, mutation=body)


@router.patch("/threads/{thread_id}/configuration")
async def thread_configuration(
    thread_id: str, body: ThreadConfigurationMutationInput, app: Application
):
    return await app.host.patch_thread_configuration(thread_id=thread_id, mutation=body)


@router.get("/threads/{thread_id}/history")
async def history(
    thread_id: str,
    app: Application,
    cursor: str | None = None,
    expected_continuation_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
):
    return await app.host.get_thread_transcript(
        thread_id=thread_id,
        cursor=cursor,
        limit=limit,
        expected_continuation_id=expected_continuation_id,
    )


@router.post("/threads/{thread_id}/inputs", status_code=202)
async def submit(thread_id: str, body: Input, app: Application):
    # Direct operator input retains Host admission semantics, not a second durable input queue.
    return await app.operator_input(thread_id, body.text, body.attachment_ids)


@router.get("/threads/{thread_id}/active")
async def active(thread_id: str, app: Application):
    return await app.host.active_root_operation(thread_id)


@router.get("/operations/{receipt_id}")
async def operation(receipt_id: str, app: Application):
    return await app.host.get_root_operation(receipt_id)


@router.post("/operations/{receipt_id}/steer")
async def steer(receipt_id: str, body: Input, app: Application):
    operation = await app.host.get_root_operation(receipt_id)
    return await app.operator_input(
        operation.receipt.thread_id, body.text, body.attachment_ids, receipt_id=receipt_id
    )


@router.post("/operations/{receipt_id}/cancel")
async def cancel(receipt_id: str, app: Application):
    return await app.host.cancel_root_operation(receipt_id)


@router.get("/threads/{thread_id}/decisions")
async def decisions(thread_id: str, app: Application, expected_continuation_id: str | None = None):
    return await app.host.thread_decisions(
        thread_id=thread_id, expected_continuation_id=expected_continuation_id
    )


@router.post("/threads/{thread_id}/decisions", status_code=202)
async def answer(thread_id: str, body: DecisionResponseBatch, app: Application):
    return await app.host.respond_decisions(thread_id=thread_id, response=body)


@router.get("/configuration")
async def configuration(app: Application):
    return await app.host.configuration_sources()


@router.get("/configuration/source")
async def configuration_source(app: Application, path: str):
    return await app.host.configuration_source(relative_path=path)


@router.put("/configuration/source")
async def write_configuration(body: ConfigurationWrite, app: Application):
    # Claw serializes its editors and checks the observed source. Native Host mutation
    # validates/accepts the candidate; it does not expose an OS-wide compare-and-swap.
    async with app.lock:
        catalog = await app.host.configuration_sources()
        existing = next((s for s in catalog.sources if s.relative_path == body.relative_path), None)
        if existing is None:
            if body.expected_digest is not None:
                raise ValueError("Configuration source no longer exists")
        else:
            source = await app.host.configuration_source(relative_path=body.relative_path)
            if source.source_digest != body.expected_digest:
                raise ValueError("Configuration source changed; refetch and preserve your draft")
        return await app.host.mutate_configuration(
            relative_path=body.relative_path, request=ResourceMutationRequest(content=body.content)
        )


@router.post("/configuration/reload")
async def reload_configuration(app: Application):
    async with app.lock:
        await app.host.reload_configuration()
        return await app.host.configuration_sources()


@router.get("/projects")
async def projects(app: Application):
    return await app.host.projects()


@router.get("/targets")
async def targets(app: Application):
    return await database(app.store.rows, "SELECT * FROM targets ORDER BY key")


@router.post("/targets/resolve")
async def resolve_target(body: TargetResolution, app: Application):
    return await app.resolve_target(body.key, body.action, body.evidence)


@router.get("/channels")
async def channels(app: Application):
    return app.settings.channels


@router.get("/inbox")
async def inbox(
    app: Application,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
):
    return await database(
        app.store.rows,
        "SELECT id,channel_id,thread_id,disposition,version,reason,blocked,"
        "next_check,dependency,created "
        "FROM inbox ORDER BY created,id LIMIT ? OFFSET ?",
        (limit, offset),
    )


@router.get("/inbox/{item_id}")
async def inbox_item(item_id: str, app: Application):
    import json

    row = await database(app.store.item, item_id)
    if row["body"] is not None:
        body = json.loads(row["body"])
        body["files"] = [{"name": f["name"]} for f in body["files"]]
        row["body"] = body
    return row


@router.patch("/inbox/{item_id}")
async def update_inbox(item_id: str, body: InboxChange, app: Application):
    async with app.lock:
        result = await database(
            app.store.settle, item_id, body.expected_version, body.disposition, body.reason
        )
        app.wake.set()
        return {key: result[key] for key in ("id", "disposition", "version", "reason")}


@router.get("/attempts")
async def attempts(app: Application, offset: int = Query(default=0, ge=0)):
    return await database(
        app.store.rows, "SELECT * FROM attempts ORDER BY created DESC LIMIT 100 OFFSET ?", (offset,)
    )


@router.post("/attempts/{attempt_id}/resolve")
async def resolve_attempt(attempt_id: str, body: AttemptResolution, app: Application):
    async with app.lock:
        await database(app.store.resolve_attempt, attempt_id, body.action, body.evidence)
        app.wake.set()
        return {"resolved": attempt_id}


@router.get("/deliveries")
async def deliveries(app: Application, offset: int = Query(default=0, ge=0)):
    return await database(
        app.store.rows,
        "SELECT * FROM deliveries ORDER BY created DESC LIMIT 100 OFFSET ?",
        (offset,),
    )


@router.post("/deliveries/{delivery_id}/resolve")
async def resolve_delivery(delivery_id: str, body: DeliveryResolution, app: Application):
    async with app.lock:
        result = await database(
            app.store.resolve_delivery, delivery_id, body.outcome, body.evidence, retry=body.retry
        )
        app.wake.set()
        return result


@router.post("/threads/{thread_id}/attachments", status_code=201)
async def upload(thread_id: str, request: Request, app: Application, name: str):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(413, "Attachments are limited to 10 MiB")
    return await app.host.stage_thread_attachment(
        thread_id=thread_id,
        upload=AttachmentUpload(
            name=name, data=bytes(content), media_type=request.headers.get("content-type")
        ),
    )


@router.get("/threads/{thread_id}/attachments/{attachment_id}")
async def download(thread_id: str, attachment_id: str, app: Application):
    attachment, content = await app.host.read_thread_attachment(
        thread_id=thread_id, attachment_id=attachment_id
    )
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(attachment.name, safe=""),
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )
