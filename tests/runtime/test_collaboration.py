"""Native worker wakes racing product drain retain exact Host ownership."""

import asyncio

import pytest
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart

from a13n_claw.messaging.capability import messaging_integrations
from a13n_claw.runtime.host import open_host
from tests.helpers import message, setup, streaming_model


@pytest.mark.anyio
@pytest.mark.parametrize("report", [False, True], ids=["auto-followup", "worker-report"])
async def test_native_worker_wake_wins_drain_race_without_replaying_unknown(
    tmp_path, monkeypatch, report
):
    release_worker = asyncio.Event()
    native_started = asyncio.Event()
    release_main = asyncio.Event()
    worker_created = {}
    visible = []
    main_calls = 0
    report_results = []

    async def main_model(messages, info):
        nonlocal main_calls
        main_calls += 1
        results = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if main_calls == 1:
            return ModelResponse(parts=[ToolCallPart("create_thread", {"prompt": "Worker task"})])
        if main_calls == 2:
            worker_created.update(results[-1].content)
            return ModelResponse(parts=[TextPart("Worker requested; wait for its result")])
        if main_calls == 3:
            native_started.set()
            await asyncio.wait_for(release_main.wait(), 20)
            return ModelResponse(parts=[ToolCallPart("claw_inbox_list", {})])
        last = results[-1]
        assert "error" not in last.content, last.content
        if last.tool_name == "claw_inbox_list":
            visible.extend(last.content["items"])
            item = visible[0]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "claw_inbox_settle",
                        {
                            "item_id": item["id"],
                            "expected_version": item["version"],
                            "disposition": "handled",
                            "reason": "Inspected native worker result",
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Business attention settled")])

    async def worker_model(messages, info):
        await asyncio.wait_for(release_worker.wait(), 20)
        results = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if report and not results:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "send_thread_message",
                        {
                            "thread_id": main,
                            "message": "Worker result available",
                        },
                    )
                ]
            )
        if results:
            report_results.append(results[-1].content)
        return ModelResponse(parts=[TextPart("Worker finished")])

    root, service, transport = await setup(tmp_path, monkeypatch)
    main = None

    async def resolve(self, model_id, *, thread_id):
        return streaming_model(main_model if thread_id == main else worker_model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        main = await service.ensure_target("main", service.settings.main_project_id)
        bootstrap = await host.submit_thread(thread_id=main, prompt="Create one worker")
        initial = await host.wait_root_operation(bootstrap.receipt_id, timeout_seconds=20)
        assert initial.status == "completed", initial.model_dump_json()
        assert worker_created["ok"], worker_created
        worker = await host.get_thread(worker_created["thread_id"])
        assert worker.thread.coordinator_thread_id == main
        assert (await host.get_thread(main)).thread.auto_followup

        await service.ingress.receive(message("unknown"))
        unknown = service.store.rows("SELECT * FROM inbox WHERE event_id='unknown'")[0]
        service.store.assign_thread(unknown["id"], main)
        blocked = service.store.begin_attempt(main, "submit", [unknown["id"]])
        service.store.finish_attempt(blocked, "unknown", evidence="Earlier receipt lost")
        await service.ingress.receive(message("eligible"))
        eligible = service.store.rows("SELECT * FROM inbox WHERE event_id='eligible'")[0]

        submit = host.submit_thread

        async def native_wins(**kwargs):
            # Product already observed idle and retained intent. A real native worker
            # message or lifecycle notice now wins Host admission before its call.
            release_worker.set()
            await asyncio.wait_for(native_started.wait(), 20)
            return await submit(**kwargs)

        monkeypatch.setattr(host, "submit_thread", native_wins)
        try:
            await service.reconcile()
            attempts = service.store.rows("SELECT * FROM attempts WHERE id<>?", (blocked,))
            assert len(attempts) == 1
            assert attempts[0]["status"] == "rejected"
            assert attempts[0]["evidence"] == "thread_run_active"
            active = await host.active_root_operation(main)
            assert active is not None
            await asyncio.sleep(service.settings.retry_seconds)
            await service.reconcile()
            attempts = service.store.rows("SELECT * FROM attempts WHERE kind='steer'")
            assert len(attempts) == 1
            assert attempts[0]["receipt_id"] == active.receipt.receipt_id
            assert attempts[0]["status"] == "accepted"
        finally:
            release_worker.set()
            release_main.set()
        completed = await host.wait_root_operation(active.receipt.receipt_id, timeout_seconds=20)
        assert completed.status == "completed", completed.model_dump_json()
        worker_result = await host.wait_root_operation(
            worker_created["receipt"]["receipt_id"], timeout_seconds=20
        )
        assert worker_result.status == "completed"
        if report:
            assert report_results and report_results[0]["ok"]
        await service.reconcile()
        assert [item["id"] for item in visible] == [eligible["id"]]
        assert service.store.item(eligible["id"])["disposition"] == "handled"
        assert service.store.item(unknown["id"])["blocked"] == blocked
        assert (
            service.store.rows("SELECT status FROM attempts WHERE id=?", (blocked,))[0]["status"]
            == "unknown"
        )
        assert transport.sent == []
        threads = await host.list_threads(project_id=service.settings.main_project_id)
        assert [t.thread_id for t in threads.threads if t.coordinator_thread_id == main] == [
            worker_created["thread_id"]
        ]
