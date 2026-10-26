"""Trusted operator API composing public Host operations and Claw business services."""

from __future__ import annotations

from urllib.parse import quote

from a13n_harness_ui.configuration_inspection import ThreadConfigurationInspection
from a13n_harness_ui.surfaces import (
    DecisionResponseBatch,
    NewThreadDefaults,
    RootOperationView,
    ThreadConfigurationMutationInput,
    ThreadDetail,
    ThreadMetadataMutation,
    ThreadPage,
    ToolExecutionView,
    TranscriptPage,
)
from a13n_harness_ui.thread_files import AttachmentUpload
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import Field

from a13n_claw.api.dependencies import Application
from a13n_claw.api.observation import thread_events
from a13n_claw.configuration.models import ProductModel

router = APIRouter()


class ThreadCreate(ProductModel):
    title: str | None = None
    defaults: NewThreadDefaults | None = None
    coordinator_thread_id: str | None = None


class Input(ProductModel):
    text: str = Field(min_length=1, max_length=100_000)
    attachment_ids: tuple[str, ...] = ()


@router.get("/threads")
async def threads(
    app: Application,
    cursor: str | None = None,
    project_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    query: str | None = None,
    include_archived: bool = False,
) -> ThreadPage:
    return await app.host.list_threads(
        cursor=cursor,
        limit=limit,
        project_id=project_id,
        query=query,
        include_archived=include_archived,
    )


@router.post("/threads", status_code=201)
async def create_thread(body: ThreadCreate, app: Application):
    return await app.host.create_thread(
        title=body.title, defaults=body.defaults, coordinator_thread_id=body.coordinator_thread_id
    )


@router.get("/threads/{thread_id}")
async def thread(thread_id: str, app: Application) -> ThreadDetail:
    return await app.host.get_thread(thread_id)


@router.post("/threads/{thread_id}/fork", status_code=201)
async def fork(thread_id: str, app: Application):
    return await app.host.fork_thread(thread_id)


@router.get("/threads/{thread_id}/configuration")
async def inspect_configuration(thread_id: str, app: Application) -> ThreadConfigurationInspection:
    return await app.host.inspect_thread_configuration(thread_id)


@router.get("/threads/{thread_id}/events")
async def events(thread_id: str, app: Application):
    # Check the target before sending headers. Watch still validates its own cutover.
    detail = await app.host.get_thread(thread_id)
    if detail.thread.parent_thread_id is not None:
        raise HTTPException(409, "Observation requires a root Thread")
    return StreamingResponse(
        thread_events(app.host, thread_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


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
) -> TranscriptPage:
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
async def active(thread_id: str, app: Application) -> RootOperationView | None:
    return await app.host.active_root_operation(thread_id)


@router.get("/threads/{thread_id}/tool-executions")
async def tool_executions(
    thread_id: str,
    app: Application,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> tuple[ToolExecutionView, ...]:
    return await app.host.tool_executions(thread_id, offset=offset, limit=limit)


@router.post("/threads/{thread_id}/tool-executions/{execution_id}/cancel")
async def cancel_tool_execution(thread_id: str, execution_id: str, app: Application):
    return await app.host.cancel_tool_execution(thread_id, execution_id)


@router.get("/operations/{receipt_id}")
async def operation(receipt_id: str, app: Application) -> RootOperationView:
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
    async with app.lock:
        return await app.scheduler.respond(thread_id, body)


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
