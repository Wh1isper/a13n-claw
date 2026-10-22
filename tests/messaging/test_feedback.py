"""Feedback and selected final output use native Host execution and durable deliveries."""

import json

import pytest
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart

from a13n_claw.configuration.models import ClawSettings
from a13n_claw.messaging.capability import messaging_integrations
from a13n_claw.messaging.transport import SendResult
from a13n_claw.runtime.host import open_host
from tests.helpers import command, message, setup, streaming_model


def enable(service, **changes):
    binding = service.settings.channels["room"].model_copy(update=changes)
    service.settings = service.settings.model_copy(update={"channels": {"room": binding}})


async def project(host, tmp_path):
    await host.mutate_configuration(
        relative_path="projects/channel.yaml",
        request=ResourceMutationRequest(
            content=(
                "schema_version: '1'\nkind: project\nid: project-channel\nname: Channel\n"
                f"roots:\n  - path: {tmp_path / 'work'}\n"
            )
        ),
    )


@pytest.mark.anyio
async def test_feedback_unknown_does_not_block_work_or_replay_and_main_stays_private(
    tmp_path, monkeypatch
):
    root, service, transport = await setup(tmp_path, monkeypatch)
    enable(service, progress_updates=True, reactions=True)
    sent = []

    async def unknown(destination, body, *, request_id):
        sent.append(body)
        return SendResult("unknown", "Lost response")

    transport.send = unknown
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await service.ingress.receive(message())
        item = service.store.rows("SELECT * FROM inbox")[0]
        # Send feedback before dispatch; its unknown result must not inhibit admission.
        for row in service.store.rows("SELECT * FROM deliveries"):
            await service.delivery.send(row["id"])
        assert len(service.store.actionable()) == 1
        await service.reconcile()
        attempt = service.store.rows("SELECT * FROM attempts")[0]
        await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=20)
        await service.reconcile()
        count = len(sent)
        service.store.restart()
        await service.ingress.receive(message())
        await service.feedback.status(item, "retained")
        for row in service.store.rows("SELECT * FROM deliveries"):
            await service.delivery.send(row["id"])
        assert len(sent) == count
        assert all(body.text != "Not broadcast" for body in sent)
        assert {body.reaction for body in sent if body.reaction} == {"OnIt", "DONE"}


@pytest.mark.anyio
@pytest.mark.parametrize("restart", [False, True])
async def test_routed_final_is_exact_once_and_settles_without_model_replay(
    tmp_path, monkeypatch, restart
):
    root, service, transport = await setup(tmp_path, monkeypatch, "per_trigger")
    enable(service, final_reply=True, progress_updates=True)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await project(host, tmp_path)
        await service.ingress.receive(message())
        await service.reconcile()
        attempt = service.store.rows("SELECT * FROM attempts")[0]
        await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=20)
        if not restart:
            await service.reconcile()
    if restart:
        service.store.restart()
        async with open_host(
            root, service.settings, integrations=messaging_integrations(service)
        ) as host:
            service.bind(host)
            await service.reconcile()
    assert [body.text for _, body, _ in transport.sent].count("Not broadcast") == 1
    assert service.store.rows("SELECT * FROM inbox")[0]["disposition"] == "handled"
    assert service.store.actionable() == []
    assert len(service.store.rows("SELECT * FROM attempts")) == 1
    assert all(destination.reply_to == "msg-event-1" for destination, _, _ in transport.sent)


def test_one_thread_rejects_final_broadcast_policy():
    from tests.helpers import settings

    config = settings().model_dump()
    config["channels"]["room"]["final_reply"] = True
    with pytest.raises(ValueError, match="private"):
        ClawSettings.model_validate(config)


@pytest.mark.anyio
async def test_progress_revocation_blocks_unsent_feedback(tmp_path, monkeypatch):
    root, service, transport = await setup(tmp_path, monkeypatch)
    enable(service, progress_updates=True)
    await service.ingress.receive(message())
    delivery = service.store.rows("SELECT * FROM deliveries")[0]
    enable(service, progress_updates=False)
    result = await service.delivery.send(delivery["id"])
    assert result["blocked"] and transport.sent == []


@pytest.mark.anyio
async def test_native_question_prompt_and_im_answer_keep_original_task_correlation(
    tmp_path, monkeypatch
):
    async def model(messages, info):
        if not any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts):
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
                                        {"label": "One", "description": "First"},
                                        {"label": "Two", "description": "Second"},
                                    ],
                                }
                            ]
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Question answered")])

    root, service, transport = await setup(
        tmp_path, monkeypatch, "per_trigger", streaming_model(model)
    )
    enable(service, final_reply=True, decision_prompts=True, operators=("user",))
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await project(host, tmp_path)
        await service.ingress.receive(message())
        await service.reconcile()
        item = service.store.rows("SELECT * FROM inbox")[0]
        attempt = service.store.rows("SELECT * FROM attempts")[0]
        operation = await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=20)
        assert operation.status == "suspended"
        await service.reconcile()
        prompts = [body.text for _, body, _ in transport.sent if "/claw answer" in body.text]
        assert len(prompts) == 1 and "Which option?" in prompts[0]
        pending = await host.thread_decisions(thread_id=item["thread_id"])
        response = {
            "expected_continuation_id": pending.continuation_id,
            "responses": [
                {
                    "kind": "question",
                    "request_id": pending.requests[0].request_id,
                    "response": "One",
                }
            ],
        }
        await command(service, "answer", f"/claw answer {item['id']} {json.dumps(response)}")
        resumed = service.store.rows("SELECT * FROM attempts WHERE kind='continue'")[0]
        assert resumed["receipt_id"] != attempt["receipt_id"]
        await host.wait_root_operation(resumed["receipt_id"], timeout_seconds=20)
        await service.reconcile()
        assert [body.text for _, body, _ in transport.sent].count("Question answered") == 1
        assert service.store.item(item["id"])["disposition"] == "handled"
        await command(service, "stale", f"/claw answer {item['id']} {json.dumps(response)}")
        assert len(service.store.rows("SELECT * FROM attempts WHERE kind='continue'")) == 1


@pytest.mark.anyio
async def test_topic_reply_inherits_root_but_explicit_topic_policy_wins(tmp_path, monkeypatch):
    _, service, _ = await setup(tmp_path, monkeypatch)
    reply = message(topic_id="topic")
    await service.ingress.receive(reply)
    retained = service.store.rows("SELECT * FROM inbox")[0]
    assert retained["channel_id"] == "room" and retained["body"] is not None
    assert service.ingress.source(retained)[0].topic_id == "topic"
    root = service.settings.channels["room"]
    topic = root.model_copy(update={"topic_id": "topic", "enabled": False})
    service.settings = service.settings.model_copy(
        update={"channels": {"room": root, "topic": topic}}
    )
    assert service.ingress.binding(reply)[0] == "topic"
    with pytest.raises(ValueError, match="binding"):
        service.ingress.source(retained)
    await service.ingress.receive(message("disabled-topic", topic_id="topic"))
    ignored = service.store.rows("SELECT * FROM inbox WHERE event_id='disabled-topic'")[0]
    assert ignored["body"] is None and ignored["channel_id"] == "topic"


@pytest.mark.anyio
async def test_completed_task_can_continue_in_reply_topic_with_new_final(tmp_path, monkeypatch):
    root, service, transport = await setup(tmp_path, monkeypatch, "per_trigger")
    enable(service, final_reply=True, operators=("user",))
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await project(host, tmp_path)
        await service.ingress.receive(message())
        await service.reconcile()
        attempt = service.store.rows("SELECT * FROM attempts")[0]
        await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=20)
        await service.reconcile()
        item = service.store.rows("SELECT * FROM inbox")[0]
        assert item["disposition"] == "handled"
        continuation = message("followup", topic_id="msg-event-1").model_copy(
            update={"text": f"/claw continue {item['id']} More details"}
        )
        await service.ingress.receive(continuation)
        await service.reconcile()
        followup = service.store.rows("SELECT * FROM attempts WHERE kind='continue'")[0]
        await host.wait_root_operation(followup["receipt_id"], timeout_seconds=20)
        await service.reconcile()
        assert [body.text for _, body, _ in transport.sent].count("Not broadcast") == 2
        assert service.store.item(item["id"])["disposition"] == "handled"
        assert followup["thread_id"] == item["thread_id"]


@pytest.mark.anyio
async def test_continuation_intents_are_atomic_when_target_blocked(tmp_path, monkeypatch):
    _, service, _ = await setup(tmp_path, monkeypatch)
    enable(service, operators=("user",))
    await service.ingress.receive(message())
    target = service.store.rows("SELECT * FROM inbox")[0]
    original = service.store.begin_attempt("thread", "submit", [target["id"]])
    await service.ingress.receive(message("control").model_copy(update={"text": "/claw help"}))
    control = service.store.rows("SELECT * FROM inbox WHERE event_id='control'")[0]
    with pytest.raises(ValueError, match="reconciliation"):
        service.store.begin_continuation("thread", control["id"], target["id"])
    assert len(service.store.rows("SELECT * FROM attempts")) == 1
    assert service.store.item(control["id"])["blocked"] is None
    assert service.store.item(target["id"])["blocked"] == original
