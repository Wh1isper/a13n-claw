"""Real Harness capabilities and loopback HTTP delivery, without paid providers."""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, Request
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from test_network_runtime import listening
from test_one_thread import configure, receive, running_main

from a13n_claw.attention import ChannelPolicy, DeliveryRequest
from a13n_claw.coordinator import Coordinator
from a13n_claw.messaging import DeliveryDispatcher
from a13n_claw.runtime import Runtime


@pytest.mark.anyio
async def test_native_main_drain_workers_inbox_and_real_http_egress(runtime_store, tmp_path):
    store = runtime_store
    main, messaging = configure(store)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store.save_credential("operator", "TEST_KEY", "offline")
    store.save_resource(
        "operator",
        "model",
        "worker-model",
        {"provider": "openai", "model_name": "worker", "credential_env": "TEST_KEY"},
        0,
    )
    store.save_resource(
        "operator", "profile", "worker", {"model_id": "worker-model", "environment_id": "local"}, 0
    )
    calls, sent = [], []
    main_runs = 0
    receiver = FastAPI()

    @receiver.post("/messages")
    async def deliver(request: Request):
        body = await request.json()
        assert request.headers["idempotency-key"] == body["delivery_id"]
        sent.append(body)
        return {"accepted": True}

    @asynccontextmanager
    async def models(definition, credential):
        nonlocal main_runs
        is_main = definition.model_name != "worker"
        if is_main:
            main_runs += 1
        number = main_runs
        step = 0
        attention = []
        external = None

        async def model(messages, info):
            nonlocal step, external, attention
            names = {tool.name for tool in info.function_tools}
            assert "inspect_threads" in names and "send_thread_message" in names
            returns = [
                part
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            if not is_main:
                assert "send_message" not in names and "create_worker" not in names
                if step == 0:
                    tool, args = (
                        "send_thread_message",
                        {"thread_id": main.id, "text": "Worker result saved"},
                    )
                else:
                    yield "Worker complete"
                    return
            elif number == 1:
                # Critical: success without disposition is not completion of the Inbox.
                yield "I think everything is finished"
                return
            elif number == 2:
                assert "send_message" in names and "create_worker" in names
                if step == 0:
                    tool, args = "list_inbox", {}
                elif step == 1:
                    external = next(
                        part.content[0]
                        for part in reversed(returns)
                        if part.tool_name == "list_inbox"
                    )
                    assert "External body" in external["payload"]["text"]
                    tool, args = (
                        "create_worker",
                        {"title": "Research", "profile_id": "worker", "text": "Do bounded work"},
                    )
                elif step == 2:
                    tool, args = (
                        "send_message",
                        {"channel_id": "room", "text": "Explicit external reply"},
                    )
                elif step == 3:
                    assert external
                    tool, args = (
                        "update_inbox",
                        {
                            "item_id": external["id"],
                            "change": {
                                "expected_version": external["version"],
                                "disposition": "handled",
                                "note": "Reply saved",
                            },
                        },
                    )
                else:
                    yield "Main Console answer is not a broadcast"
                    return
            else:
                if step == 0:
                    tool, args = "list_attention", {}
                elif step == 1:
                    attention = next(
                        part.content
                        for part in reversed(returns)
                        if part.tool_name == "list_attention"
                    )
                    tool, args = "inspect_threads", {}
                elif attention:
                    item = attention.pop(0)
                    tool, args = (
                        "update_attention",
                        {
                            "item_id": item["id"],
                            "change": {
                                "expected_version": item["version"],
                                "disposition": "handled",
                                "note": "Reviewed saved worker state",
                            },
                        },
                    )
                else:
                    yield "Attention reconciled"
                    return
            calls.append(tool)
            step += 1
            yield {
                0: DeltaToolCall(
                    name=tool,
                    json_args=json.dumps(args),
                    tool_call_id=f"call-{is_main}-{number}-{step}",
                )
            }

        yield FunctionModel(stream_function=model)

    async with listening(receiver) as base:
        policy = ChannelPolicy.model_validate(messaging.channels("operator")[0]["policy"])
        messaging.save_channel(
            "operator", "room", policy.model_copy(update={"delivery_url": base + "/messages"}), 1
        )
        receive(messaging)
        # Saved ingress predates the dispatcher; no live event or wake is supplied.
        coordinator = Coordinator(store, str(workspace), Runtime(store, tmp_path, models=models))
        delivery = DeliveryDispatcher(messaging)
        await coordinator.start()
        delivery.start()
        try:
            async with asyncio.timeout(15):
                while True:
                    state = await asyncio.to_thread(store.coordination.view, "operator")
                    runs = await asyncio.to_thread(store.runs, "operator", main.id)
                    items = await asyncio.to_thread(store.coordination.inbox, "operator")
                    if (
                        len(runs) >= 3
                        and runs[-1].status == "completed"
                        and sent
                        and state["counts"].get("pending", 0) == 0
                        and any(item["kind"] == "worker_outcome" for item in items)
                    ):
                        break
                    assert coordinator.failure is None
                    assert not [run for run in runs if run.status == "failed"], runs
                    await asyncio.sleep(0.05)
            assert len(sent) == 1 and sent[0]["text"] == "Explicit external reply"
            assert len(state["workers"]) == 1
            assert {
                "create_worker",
                "send_thread_message",
                "list_inbox",
                "update_inbox",
                "send_message",
                "list_attention",
                "update_attention",
            } <= set(calls)
            assert messaging.deliveries("operator")[0]["status"] == "sent"
            assert store.runs("operator", main.id)[0].output == "I think everything is finished"
        finally:
            await coordinator.close()
            await delivery.close()


@pytest.mark.anyio
async def test_real_http_ambiguous_failure_retains_intent_without_retry(runtime_store):
    from fastapi.responses import Response

    store = runtime_store
    _, messaging, run = running_main(store)
    receiver = FastAPI()
    attempts = []

    @receiver.post("/messages")
    async def deliver(request: Request):
        attempts.append(await request.json())
        # Receiver may have committed before its error. A 5xx is not proof of no effect.
        return Response(status_code=503)

    async with listening(receiver) as base:
        policy = ChannelPolicy.model_validate(messaging.channels("operator")[0]["policy"])
        messaging.save_channel(
            "operator", "room", policy.model_copy(update={"delivery_url": base + "/messages"}), 1
        )
        messaging.send(
            run.run_id, "owner", DeliveryRequest(request_id="send", channel_id="room", text="Once")
        )
        dispatcher = DeliveryDispatcher(messaging)
        dispatcher.start()
        try:
            async with asyncio.timeout(5):
                while messaging.deliveries("operator")[0]["status"] != "unknown":
                    await asyncio.sleep(0.02)
            await asyncio.sleep(0.5)
            assert len(attempts) == 1
        finally:
            await dispatcher.close()
