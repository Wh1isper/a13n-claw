"""Business boundaries against the real embedded App, with a deterministic model only."""

from types import SimpleNamespace

import pytest
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import NewThreadDefaults
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from a13n_claw.bootstrap import initialize
from a13n_claw.business import BusinessStore
from a13n_claw.capability import messaging_integrations
from a13n_claw.host import open_host
from a13n_claw.service import ClawService
from a13n_claw.settings import ClawSettings
from a13n_claw.transport import InboundMessage, SendResult


def settings(mode="one_thread"):
    return ClawSettings.model_validate(
        {
            "mode": mode,
            "retry_seconds": 1,
            "connections": {"lark": {"app_id": "test-app", "bot_open_id": "bot"}},
            "channels": {
                "room": {
                    "connection": "lark",
                    "chat_id": "chat",
                    "allowed_senders": ["user"],
                    "shared_context_acknowledged": True,
                    "project_id": "project-channel" if mode != "one_thread" else None,
                }
            },
        }
    )


def message(event="event-1", **changes):
    return InboundMessage(
        platform="lark",
        connection="lark",
        event_id=event,
        message_id=f"msg-{event}",
        chat_id="chat",
        sender_id="user",
        text="Please reply",
        **changes,
    )


def adapter():
    sent = []

    async def send(destination, body, *, request_id):
        sent.append((destination, body, request_id))
        return SendResult("sent", "platform-message-id")

    async def download(*args):
        return b"attachment"

    return SimpleNamespace(send=send, download=download, sent=sent)


def streaming_model(function):
    async def stream(messages, info):
        response = await function(messages, info)
        for index, part in enumerate(response.parts):
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                yield {index: DeltaToolCall(name=part.tool_name, json_args=part.args_as_json_str())}

    return FunctionModel(stream_function=stream)


async def setup(tmp_path, monkeypatch, mode="one_thread", model=None):
    monkeypatch.setenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", "0")

    async def resolve(self, model_id, *, thread_id):
        return model or TestModel(call_tools=[], custom_output_text="Not broadcast")

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    root, workspace = tmp_path / "claw", tmp_path / "work"
    workspace.mkdir()
    await initialize(root, workspace, model_route="openai:gpt-4o")
    config = settings(mode)
    store = BusinessStore(root / "business.sqlite3", config)
    transport = adapter()
    service = ClawService(config, store, {"lark": transport})
    return root, service, transport


@pytest.mark.anyio
async def test_native_tools_read_reply_settle_and_deduplicate(tmp_path, monkeypatch):
    tool_results = []

    async def model(messages, info):
        results = [
            part for msg in messages for part in msg.parts if isinstance(part, ToolReturnPart)
        ]
        tool_results[:] = results
        if not results:
            return ModelResponse(parts=[ToolCallPart("claw_inbox_list", {})])
        last = results[-1]
        value = last.content
        assert "error" not in value, value
        if last.tool_name == "claw_inbox_list":
            return ModelResponse(
                parts=[ToolCallPart("claw_inbox_read", {"item_id": value["items"][0]["id"]})]
            )
        if last.tool_name == "claw_inbox_read":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "claw_send_message",
                        {
                            "item_id": value["id"],
                            "request_key": "test-reply",
                            "text": "A real tool reply",
                        },
                    )
                ]
            )
        if last.tool_name == "claw_send_message":
            item = next(r.content for r in results if r.tool_name == "claw_inbox_read")
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "claw_inbox_settle",
                        {
                            "item_id": item["id"],
                            "expected_version": item["version"],
                            "disposition": "handled",
                            "reason": "Reply confirmed",
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Private Main result")])

    root, service, transport = await setup(tmp_path, monkeypatch, model=streaming_model(model))
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await service.receive(message())
        await service.receive(message())
        await service.reconcile()
        attempts = service.store.rows("SELECT * FROM attempts")
        assert len(attempts) == 1
        operation = await host.wait_root_operation(attempts[0]["receipt_id"], timeout_seconds=20)
        assert operation.status == "completed", operation
        assert len(transport.sent) == 1
        assert transport.sent[0][1].text == "A real tool reply"
        assert service.store.rows("SELECT * FROM inbox")[0]["disposition"] == "handled"
        await service.reconcile()
        assert len(service.store.rows("SELECT * FROM attempts")) == 1
        assert len(tool_results) == 4
    reopened = BusinessStore(root / "business.sqlite3", service.settings)
    reopened.restart()
    assert reopened.rows("SELECT disposition FROM inbox")[0]["disposition"] == "handled"
    assert reopened.rows("SELECT status FROM deliveries")[0]["status"] == "sent"


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["one_thread", "per_channel", "per_trigger"])
async def test_routing_and_filtering(tmp_path, monkeypatch, mode):
    root, service, transport = await setup(tmp_path, monkeypatch, mode)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        if mode != "one_thread":
            await host.mutate_configuration(
                relative_path="projects/channel.yaml",
                request=ResourceMutationRequest(
                    content=(
                        "schema_version: '1'\nkind: project\nid: project-channel\n"
                        f"name: Channel\nroots:\n  - path: {tmp_path / 'work'}\n"
                    )
                ),
            )
        await service.receive(message("ignored", is_group=True))
        await service.reconcile()
        assert service.store.rows("SELECT * FROM targets") == []
        assert service.store.rows("SELECT * FROM inbox")[0]["body"] is None
        await service.receive(message("first", is_group=True, mentioned=True))
        await service.reconcile()
        first = service.store.rows("SELECT * FROM attempts")[0]
        await host.wait_root_operation(first["receipt_id"], timeout_seconds=20)
        await service.reconcile()
        await service.receive(message("second", is_group=True, mentioned=True))
        await service.reconcile()
        attempts = service.store.rows("SELECT * FROM attempts ORDER BY created")
        assert len(attempts) == 2
        if mode == "per_trigger":
            assert attempts[0]["thread_id"] != attempts[1]["thread_id"]
        else:
            assert attempts[0]["thread_id"] == attempts[1]["thread_id"]
        await host.wait_root_operation(attempts[1]["receipt_id"], timeout_seconds=20)
        assert transport.sent == []
        await service.receive(message("second", is_group=True, mentioned=True))
        assert len(service.store.rows("SELECT * FROM inbox")) == 3


def test_unknown_dispatch_cannot_be_settled_or_retried_by_new_operation(tmp_path):
    store = BusinessStore(tmp_path / "business.sqlite3", settings())
    row = store.accept(
        connection="lark",
        event_id="1",
        fingerprint="hash",
        channel_id="room",
        mode="one_thread",
        project_id="project-claw-main",
        body={"content": "test"},
    )
    attempt = store.begin_attempt("thread-1", "submit", [row["id"]])
    store.finish_attempt(attempt, "accepted", receipt_id="receipt-1")
    assert store.readable(row["id"], "receipt-1")["id"] == row["id"]
    store.restart()
    assert store.actionable() == []
    with pytest.raises(ValueError, match="reconciliation"):
        store.settle(row["id"], 1, "handled", "Pretend success", receipt_id="new-receipt")
    store.resolve_attempt(attempt, "retry", "Operator accepts possible repetition")
    assert len(store.actionable()) == 1
    assert len(store.rows("SELECT * FROM resolutions")) == 1


@pytest.mark.anyio
async def test_native_start_cannot_read_unknown_item_and_worker_cannot_publish(
    tmp_path, monkeypatch
):
    results = []

    async def model(messages, info):
        returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returned:
            return ModelResponse(parts=[ToolCallPart("claw_inbox_list", {})])
        results.append(returned[-1].content)
        return ModelResponse(parts=[TextPart("done")])

    root, service, _ = await setup(tmp_path, monkeypatch, model=streaming_model(model))
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await service.receive(message())
        main = await service.ensure_target("main", service.settings.main_project_id)
        item = service.store.rows("SELECT * FROM inbox")[0]
        service.store.assign_thread(item["id"], main)
        service.store.begin_attempt(main, "submit", [item["id"]])
        service.store.restart()
        native = await host.submit_thread(thread_id=main, prompt="Native followup")
        op = await host.wait_root_operation(native.receipt_id, timeout_seconds=20)
        assert op.status == "completed", op
        assert results[-1] == {"items": []}
        worker = await host.create_thread(
            coordinator_thread_id=main,
            defaults=NewThreadDefaults(project_id=service.settings.main_project_id),
        )
        receipt = await host.submit_thread(thread_id=worker.thread_id, prompt="Human worker input")
        op = await host.wait_root_operation(receipt.receipt_id, timeout_seconds=20)
        assert op.status == "completed", op
        assert "Only canonical Main" in results[-1]["error"]


@pytest.mark.anyio
async def test_retained_attachment_materialized_and_sent_by_native_tools(tmp_path, monkeypatch):
    import base64
    from pathlib import Path

    from a13n_claw.transport import AttachmentRef

    observed = {}

    async def model(messages, info):
        returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returned:
            return ModelResponse(parts=[ToolCallPart("claw_inbox_list", {})])
        last = returned[-1]
        value = last.content
        assert "error" not in value, value
        if last.tool_name == "claw_inbox_list":
            observed["item"] = value["items"][0]["id"]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "claw_inbox_file",
                        {
                            "item_id": observed["item"],
                            "index": 0,
                        },
                    )
                ]
            )
        if last.tool_name == "claw_inbox_file":
            observed["path"] = value["path"]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "claw_send_file",
                        {
                            "item_id": observed["item"],
                            "request_key": "attachment-reply",
                            "file_path": value["path"],
                        },
                    )
                ]
            )
        assert last.tool_name == "claw_send_file"
        assert value["status"] == "sent"
        return ModelResponse(parts=[TextPart("done")])

    root, service, transport = await setup(tmp_path, monkeypatch, model=streaming_model(model))
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await service.receive(
            message(
                attachments=(
                    AttachmentRef(
                        key="key",
                        name="../../input.txt",
                        kind="file",
                    ),
                )
            )
        )
        await service.reconcile()
        attempt = service.store.rows("SELECT * FROM attempts")[0]
        result = await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=20)
        assert result.status == "completed", result.model_dump_json()
        assert Path(observed["path"]).name == "input.txt"
        assert Path(observed["path"]).read_bytes() == b"attachment"
        assert len(transport.sent) == 1
        assert base64.b64decode(transport.sent[0][1].file.data) == b"attachment"
        service.settings = service.settings.model_copy(update={"channels": {}})
        with pytest.raises(ValueError):
            await service.inbox_file(attempt["thread_id"], "old-run", observed["item"], 0)


def enable_operator(service):
    binding = service.settings.channels["room"].model_copy(update={"operators": ("user",)})
    service.settings = service.settings.model_copy(update={"channels": {"room": binding}})


async def command(service, event, text, **changes):
    incoming = message(event, is_group=True).model_copy(update={"text": text, **changes})
    await service.receive(incoming)
    await service.reconcile()
    return incoming


@pytest.mark.anyio
async def test_im_commands_are_scoped_deduplicated_and_control_exact_operations(
    tmp_path, monkeypatch
):
    import asyncio
    import json

    entered = asyncio.Event()

    async def model(messages, info):
        entered.set()
        await asyncio.Event().wait()
        return ModelResponse(parts=[TextPart("unreachable")])

    root, service, transport = await setup(tmp_path, monkeypatch, model=streaming_model(model))
    enable_operator(service)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await command(service, "help", "/claw")
        assert service.store.rows("SELECT * FROM targets") == []
        assert "/claw cancel" in transport.sent[-1][1].text
        await service.receive(message("task"))
        await service.reconcile()
        item = service.store.rows("SELECT * FROM inbox WHERE event_id='task'")[0]
        first = service.store.rows("SELECT * FROM attempts")[0]
        await asyncio.wait_for(entered.wait(), 10)
        await command(service, "status", f"/claw status {item['id']}")
        status = json.loads(transport.sent[-1][1].text)
        assert status["operation"]["receipt"]["receipt_id"] == first["receipt_id"]
        await command(service, "stale", f"/claw cancel {item['id']} receipt-obsolete")
        assert "no longer active" in transport.sent[-1][1].text
        assert await host.active_root_operation(item["thread_id"]) is not None
        incoming = await command(
            service, "cancel", f"/claw cancel {item['id']} {first['receipt_id']}"
        )
        stopped = await host.wait_root_operation(first["receipt_id"], timeout_seconds=10)
        assert stopped.status == "cancelled"
        count = len(transport.sent)
        await service.receive(incoming)
        await service.reconcile()
        assert len(transport.sent) == count
        assert len(service.store.rows("SELECT * FROM attempts WHERE kind='control'")) == 1
        await command(service, "tasks", "/claw tasks")
        assert item["id"] in transport.sent[-1][1].text
        # A Channel binding, not a text-supplied ID, owns authority.
        other = service.settings.channels["room"].model_copy(update={"chat_id": "other"})
        service.settings = service.settings.model_copy(
            update={"channels": {**service.settings.channels, "other": other}}
        )
        await command(service, "cross", f"/claw status {item['id']}", chat_id="other")
        assert "outside this Channel" in transport.sent[-1][1].text
        room = service.settings.channels["room"].model_copy(update={"operators": ()})
        service.settings = service.settings.model_copy(update={"channels": {"room": room}})
        await command(service, "denied", "/claw tasks")
        assert (
            service.store.rows("SELECT body FROM inbox WHERE event_id='denied'")[0]["body"] is None
        )


@pytest.mark.anyio
async def test_exact_saved_dependency_recovery_and_unavailable_is_visible(tmp_path, monkeypatch):
    root, service, _ = await setup(tmp_path, monkeypatch)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        main = await service.ensure_target("main", service.settings.main_project_id)
        receipt = await host.submit_thread(thread_id=main, prompt="Do one thing")
        await host.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        await service.receive(message("dependency"))
        item = service.store.rows("SELECT * FROM inbox")[0]
        service.store.settle(
            item["id"],
            1,
            "deferred",
            "Wait exact work",
            dependency={
                "kind": "operation",
                "thread_id": main,
                "receipt_id": receipt.receipt_id,
            },
        )
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await service._dependencies()
        assert service.store.item(item["id"])["disposition"] == "pending"
        service.store.settle(
            item["id"],
            3,
            "deferred",
            "Missing receipt",
            dependency={
                "kind": "operation",
                "thread_id": main,
                "receipt_id": "missing",
            },
        )
        await service._dependencies()
        deferred = service.store.item(item["id"])
        assert deferred["disposition"] == "deferred"
        assert "operator review" in deferred["reason"]


@pytest.mark.anyio
async def test_target_creation_resolution_preserves_identity(tmp_path, monkeypatch):
    root, service, _ = await setup(tmp_path, monkeypatch)
    target = service.store.begin_target("main", service.settings.main_project_id)
    service.store.restart()
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        with pytest.raises(ValueError, match="reconciliation"):
            await service.ensure_target("main", service.settings.main_project_id)
        await service.resolve_target("main", "retry", "Verified no Thread; accept retry")
        thread_id = await service.ensure_target("main", service.settings.main_project_id)
        assert thread_id == target["thread_id"]
        service.store.target_failure("main", "Lost creation response")
        await service.resolve_target("main", "adopt", "Verified matching native Thread")
        assert service.store.instance()["main_thread_id"] == thread_id
        assert len(service.store.rows("SELECT * FROM resolutions")) == 2


@pytest.mark.anyio
async def test_im_question_answer_uses_exact_native_continuation(tmp_path, monkeypatch):
    import json

    async def model(messages, info):
        returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returned:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "ask_user_question",
                        {
                            "questions": [
                                {
                                    "header": "Choice",
                                    "question": "Which option?",
                                    "multiSelect": False,
                                    "options": [
                                        {"label": "One", "description": "First option"},
                                        {"label": "Two", "description": "Second option"},
                                    ],
                                }
                            ]
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Question answered")])

    root, service, transport = await setup(tmp_path, monkeypatch, model=streaming_model(model))
    enable_operator(service)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await service.receive(message())
        await service.reconcile()
        attempt = service.store.rows("SELECT * FROM attempts")[0]
        item = service.store.rows("SELECT * FROM inbox")[0]
        operation = await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=20)
        assert operation.status == "suspended", operation.model_dump_json()
        await command(service, "decisions", f"/claw decisions {item['id']}")
        pending = json.loads(transport.sent[-1][1].text)
        response = {
            "expected_continuation_id": pending["continuation_id"],
            "responses": [
                {
                    "kind": "question",
                    "request_id": pending["requests"][0]["request_id"],
                    "response": "One",
                }
            ],
        }
        incoming = await command(
            service, "answer", f"/claw answer {item['id']} {json.dumps(response)}"
        )
        receipt = json.loads(transport.sent[-1][1].text)
        resumed = await host.wait_root_operation(receipt["receipt_id"], timeout_seconds=20)
        assert resumed.status == "completed", resumed.model_dump_json()
        await service.receive(incoming)
        await service.reconcile()
        assert len(service.store.rows("SELECT * FROM attempts WHERE kind='control'")) == 1
        await command(service, "stale-answer", f"/claw answer {item['id']} {json.dumps(response)}")
        assert len(service.store.rows("SELECT * FROM attempts WHERE kind='control'")) == 1
        assert "continuation" in transport.sent[-1][1].text


@pytest.mark.anyio
async def test_rejected_host_image_does_not_block_other_tasks_or_deliveries(tmp_path, monkeypatch):
    import json

    from a13n_claw.transport import AttachmentRef

    root, service, transport = await setup(tmp_path, monkeypatch, "per_trigger")
    enable_operator(service)

    async def download(*args):
        return b'<svg xmlns="http://www.w3.org/2000/svg"/>'

    transport.download = download
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await host.mutate_configuration(
            relative_path="projects/channel.yaml",
            request=ResourceMutationRequest(
                content=(
                    "schema_version: '1'\nkind: project\nid: project-channel\n"
                    f"name: Channel\nroots:\n  - path: {tmp_path / 'work'}\n"
                )
            ),
        )
        await service.receive(
            message("svg", attachments=(AttachmentRef(key="svg", name="diagram.svg", kind="file"),))
        )
        await service.receive(message("other"))
        await service.receive(message("help").model_copy(update={"text": "/claw"}))
        await service.reconcile()
        attempts = service.store.rows("SELECT * FROM attempts WHERE kind='submit'")
        assert len(attempts) == 2
        for attempt in attempts:
            await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=20)
        svg = service.store.rows("SELECT body FROM inbox WHERE event_id='svg'")[0]
        assert "Host attachment unavailable: diagram.svg" in json.loads(svg["body"])["unavailable"]
        assert any("/claw cancel" in delivery[1].text for delivery in transport.sent)


@pytest.mark.anyio
async def test_ingress_commits_before_attachment_download(tmp_path, monkeypatch):
    import json

    from a13n_claw.transport import AttachmentRef

    _, service, transport = await setup(tmp_path, monkeypatch)
    calls = []

    async def download(*args):
        calls.append(args)
        return b"content"

    transport.download = download
    await service.receive(
        message(attachments=(AttachmentRef(key="file", name="a.txt", kind="file"),))
    )
    assert calls == []
    row = service.store.rows("SELECT * FROM inbox")[0]
    assert json.loads(row["body"])["attachments_pending"] is True
    await service.receive(
        message(attachments=(AttachmentRef(key="file", name="a.txt", kind="file"),))
    )
    assert len(service.store.rows("SELECT * FROM inbox")) == 1


@pytest.mark.anyio
async def test_operator_worker_followup_retains_main_attention_across_restart(
    tmp_path, monkeypatch
):
    import json

    root, service, _ = await setup(tmp_path, monkeypatch)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        main = await service.ensure_target("main", service.settings.main_project_id)
        worker = await host.create_thread(
            coordinator_thread_id=main,
            defaults=NewThreadDefaults(project_id=service.settings.main_project_id),
        )
        for text in ("First operator input", "Later operator followup"):
            receipt = await service.operator_input(worker.thread_id, text)
            await host.wait_root_operation(receipt.receipt_id, timeout_seconds=20)
        items = service.store.rows("SELECT * FROM inbox WHERE connection='operator'")
        assert len(items) == 2
        for item in items:
            body = json.loads(item["body"])
            assert body["operator_attention"]["outcome"] == "accepted"
            assert body["operator_attention"]["worker_thread_id"] == worker.thread_id
            with pytest.raises(ValueError, match="no external"):
                service.source(item)
    service.store.restart()
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await service.reconcile()
        attempts = service.store.rows("SELECT * FROM attempts")
        assert len(attempts) == 1
        assert attempts[0]["thread_id"] == main
        await host.wait_root_operation(attempts[0]["receipt_id"], timeout_seconds=20)
        assert len(service.store.rows("SELECT * FROM attempt_items")) == 2
        assert (
            await host.get_thread(worker.thread_id)
        ).thread.last_execution.execution_id == receipt.receipt_id


@pytest.mark.anyio
async def test_missing_required_capability_blocks_dispatch_without_inference(tmp_path, monkeypatch):
    import yaml

    root, service, _ = await setup(tmp_path, monkeypatch)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        source = await host.configuration_source(relative_path="agents/claw.yaml")
        agent = yaml.safe_load(source.content)
        agent["capabilities"] = []
        await host.mutate_configuration(
            relative_path="agents/claw.yaml",
            request=ResourceMutationRequest(content=yaml.safe_dump(agent)),
        )
        await service.receive(message())
        await service.reconcile()
        assert service.store.rows("SELECT * FROM attempts") == []
        item = service.store.rows("SELECT * FROM inbox")[0]
        assert "ClawMessaging" in item["reason"]
        assert item["disposition"] == "pending"
        assert await host.active_root_operation(item["thread_id"]) is None


@pytest.mark.anyio
async def test_native_start_after_project_change_cannot_read_original_inbox(tmp_path, monkeypatch):
    from a13n_harness_ui.surfaces import ThreadConfigurationMutationInput, ThreadConfigurationPatch

    captured = []
    item_id = None

    async def model(messages, info):
        returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returned:
            return ModelResponse(parts=[ToolCallPart("claw_inbox_read", {"item_id": item_id})])
        captured.append(returned[-1].content)
        return ModelResponse(parts=[TextPart("done")])

    root, service, _ = await setup(
        tmp_path, monkeypatch, "per_channel", model=streaming_model(model)
    )
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await host.mutate_configuration(
            relative_path="projects/channel.yaml",
            request=ResourceMutationRequest(
                content=(
                    "schema_version: '1'\nkind: project\nid: project-channel\n"
                    f"name: Channel\nroots:\n  - path: {tmp_path / 'work'}\n"
                )
            ),
        )
        main = await service.ensure_target("channel:room", "project-channel")
        await service.receive(message())
        item = service.store.rows("SELECT * FROM inbox")[0]
        item_id = item["id"]
        service.store.assign_thread(item_id, main)
        detail = await host.get_thread(main)
        await host.patch_thread_configuration(
            thread_id=main,
            mutation=ThreadConfigurationMutationInput(
                expected_version=detail.thread.configuration.version,
                patch=ThreadConfigurationPatch(project_id=None),
            ),
        )
        receipt = await host.submit_thread(thread_id=main, prompt="Inspect original Inbox")
        result = await host.wait_root_operation(receipt.receipt_id, timeout_seconds=20)
        assert result.status == "completed", result
        assert "Project" in captured[-1].get("error", ""), captured
