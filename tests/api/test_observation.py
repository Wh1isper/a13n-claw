"""Operator SSE uses native watches; disconnect and overflow never settle execution."""

import asyncio
import json
import socket
from contextlib import aclosing, asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from a13n_harness_ui.app import HarnessUiApp
from a13n_harness_ui.errors import LivePresentationError
from a13n_harness_ui.live import HarnessUiLiveHub, LiveCursor
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from ag_ui.core import CustomEvent
from pydantic_ai.messages import ModelResponse, TextPart

from a13n_claw.api.observation import thread_events
from a13n_claw.app import create_app
from a13n_claw.configuration.bootstrap import initialize
from tests.helpers import streaming_model


@asynccontextmanager
async def listener(app):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{sock.getsockname()[1]}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)


async def next_frame(lines):
    kind = None
    async with asyncio.timeout(10):
        async for line in lines:
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                return kind, json.loads(line[6:])
    raise AssertionError("SSE closed without a frame")


@pytest.mark.anyio
async def test_native_sse_auth_disconnect_reconnect_and_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", "0")
    entered, release = asyncio.Event(), asyncio.Event()
    watches = set()
    original_watch = HarnessUiApp.watch_thread

    @asynccontextmanager
    async def watched(self, *, root_thread_id):
        marker = object()
        async with original_watch(self, root_thread_id=root_thread_id) as watch:
            watches.add(marker)
            try:
                yield watch
            finally:
                watches.remove(marker)

    async def model(messages, info):
        entered.set()
        await release.wait()
        return ModelResponse(parts=[TextPart("Retained SSE result")])

    async def resolve(self, model_id, *, thread_id):
        return streaming_model(model)

    monkeypatch.setattr(HarnessUiApp, "watch_thread", watched)
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    root, work = tmp_path / "claw", tmp_path / "work"
    work.mkdir()
    await initialize(root, work, model_route="openai:gpt-4o")
    app = create_app(root=root, console=False)
    headers = {"Authorization": "Bearer " + (root / "operator.token").read_text().strip()}
    async with (
        app.router.lifespan_context(app),
        listener(app) as url,
        httpx.AsyncClient(base_url=url, timeout=10) as client,
    ):
        service = app.state.service
        main = service.store.instance()["main_thread_id"]
        path = f"/api/threads/{main}/events"
        assert (await client.get(path)).status_code == 401
        assert (
            await client.get(path, headers={**headers, "Origin": "http://other"})
        ).status_code == 403
        assert (await client.get("/api/threads/missing/events", headers=headers)).status_code == 409
        async with client.stream("GET", path, headers=headers) as response:
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = response.aiter_lines()
            kind, snapshot = await next_frame(lines)
            assert kind == "snapshot"
            epoch = snapshot["epoch"]
            assert snapshot["root_operation"] is None
            reply = await client.post(
                f"/api/threads/{main}/inputs", headers=headers, json={"text": "Observe"}
            )
            receipt = reply.json()["receipt_id"]
            await asyncio.wait_for(entered.wait(), 10)
            kind, event = await next_frame(lines)
            assert kind == "observation"
            assert event["root_thread_id"] == main
            assert event["epoch"] == epoch
        # Closing a presentation subscription releases it without cancelling the Run.
        async with asyncio.timeout(5):
            while watches:
                await asyncio.sleep(0.01)
        assert (await service.host.active_root_operation(main)) is not None
        release.set()
        assert (
            await service.host.wait_root_operation(receipt, timeout_seconds=10)
        ).status == "completed"
        async with client.stream(
            "GET", path, headers={**headers, "Last-Event-ID": "obsolete"}
        ) as response:
            kind, snapshot = await next_frame(response.aiter_lines())
            assert kind == "snapshot" and snapshot["epoch"] == epoch
        assert (
            "Retained SSE result"
            in (await client.get(f"/api/threads/{main}/history", headers=headers)).text
        )
    async with app.router.lifespan_context(app):
        async with aclosing(thread_events(app.state.service.host, main)) as stream:
            snapshot = json.loads((await anext(stream)).split("data: ", 1)[1])
            assert snapshot["epoch"] != epoch
            assert snapshot["root_operation"] is None
        assert not watches


@pytest.mark.anyio
async def test_bounded_native_subscription_gap_requires_refetch():
    hub = HarnessUiLiveHub(subscriber_buffer_size=1)
    closed = False

    @asynccontextmanager
    async def watch_thread(*, root_thread_id):
        nonlocal closed
        async with hub.subscribe(root_thread_id=root_thread_id) as events:
            try:
                yield SimpleNamespace(snapshot=events.cursor, root_stream=None, events=events)
            finally:
                closed = True

    host = SimpleNamespace(watch_thread=watch_thread)
    async with aclosing(thread_events(host, "thread-main")) as stream:
        assert (await anext(stream)).startswith("event: snapshot")
        for i in range(2):
            await hub.publish(
                run_kind="root",
                root_thread_id="thread-main",
                parent_thread_id=None,
                thread_id="thread-main",
                run_id="run-1",
                events=[CustomEvent(name="fixture", value=i)],
            )
        assert "live_cursor_expired" in await anext(stream)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    assert closed
    await hub.close()


@pytest.mark.anyio
async def test_snapshot_race_and_host_close_request_refetch():
    @asynccontextmanager
    async def changed(*, root_thread_id):
        raise LivePresentationError("Changed", code="live_snapshot_changed")
        yield  # pragma: no cover

    assert [
        event async for event in thread_events(SimpleNamespace(watch_thread=changed), "main")
    ] == ['event: refetch\ndata: {"reason": "live_snapshot_changed"}\n\n']

    async def empty():
        return
        yield  # pragma: no cover

    @asynccontextmanager
    async def ended(*, root_thread_id):
        yield SimpleNamespace(
            snapshot=LiveCursor(epoch="closed", sequence=0), root_stream=None, events=empty()
        )

    frames = [event async for event in thread_events(SimpleNamespace(watch_thread=ended), "main")]
    assert frames[-1] == 'event: refetch\ndata: {"reason":"host_stream_closed"}\n\n'
