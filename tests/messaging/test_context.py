"""Retained Lark context uses the same native caller and dispatch barriers as Inbox reads."""

import json

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart

from a13n_claw.messaging.capability import messaging_integrations
from a13n_claw.runtime.host import open_host
from tests.helpers import message, setup, streaming_model
from tests.messaging.test_feedback import project


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["one_thread", "per_channel", "per_trigger"])
async def test_context_is_bounded_currently_authorized_and_never_crosses_task(
    tmp_path, monkeypatch, mode
):
    context = []
    anchor_id = None

    async def model(messages, info):
        if anchor_id is None:
            return ModelResponse(parts=[TextPart("Earlier input")])
        results = [
            p
            for m in messages
            for p in m.parts
            if isinstance(p, ToolReturnPart) and p.tool_name == "claw_inbox_context"
        ]
        if not results:
            return ModelResponse(parts=[ToolCallPart("claw_inbox_context", {"item_id": anchor_id})])
        context.append(results[-1].content)
        return ModelResponse(parts=[TextPart("Context checked")])

    root, service, _ = await setup(tmp_path, monkeypatch, mode, streaming_model(model))
    binding = service.settings.channels["room"].model_copy(
        update={"allowed_senders": ("user", "revoked")}
    )
    service.settings = service.settings.model_copy(update={"channels": {"room": binding}})
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        if mode != "one_thread":
            await project(host, tmp_path)
        await service.ingress.receive(message("filtered", is_group=True))
        for event, changes in [
            ("reply", {}),
            ("other-topic", {"topic_id": "elsewhere"}),
            ("revoked", {"sender_id": "revoked"}),
            ("uncertain", {}),
        ]:
            incoming = message(event).model_copy(update={"text": event, **changes})
            await service.ingress.receive(incoming)
            await service.reconcile()
            attempt = service.store.rows("SELECT * FROM attempts ORDER BY created DESC")[0]
            await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=20)
            await service.reconcile()
            row = service.store.rows("SELECT * FROM inbox WHERE event_id=?", (event,))[0]
            service.store.settle(row["id"], row["version"], "handled", "Recorded")
        uncertain = service.store.rows("SELECT * FROM inbox WHERE event_id='uncertain'")[0]
        with service.store.transaction() as db:
            db.execute("UPDATE inbox SET disposition='pending' WHERE id=?", (uncertain["id"],))
        attempt = service.store.begin_attempt(uncertain["thread_id"], "submit", [uncertain["id"]])
        service.store.finish_attempt(attempt, "unknown", evidence="Lost admission response")
        service.settings = service.settings.model_copy(
            update={"channels": {"room": binding.model_copy(update={"allowed_senders": ("user",)})}}
        )
        await service.ingress.receive(message("anchor", reply_to="msg-reply"))
        anchor_id = service.store.rows("SELECT * FROM inbox WHERE event_id='anchor'")[0]["id"]
        await service.reconcile()
        attempt = service.store.rows("SELECT * FROM attempts ORDER BY created DESC")[0]
        result = await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=20)
        assert result.status == "completed", result.model_dump_json()
        assert len(context) == 1 and "error" not in context[0], context
        assert [row["text"] for row in context[0]["items"]] == (
            [] if mode == "per_trigger" else ["reply"]
        )
        assert context[0]["reply_available"] == (mode != "per_trigger")
        assert "filtered" not in json.dumps(context) and "uncertain" not in json.dumps(context)


def test_absent_reply_field_keeps_legacy_event_fingerprint():
    assert "reply_to" not in message().model_dump(mode="json")


@pytest.mark.anyio
@pytest.mark.parametrize("filtered", [False, True])
async def test_legacy_reply_redelivery_preserves_snapshot_and_conflicts(
    tmp_path, monkeypatch, filtered
):
    _, service, _ = await setup(tmp_path, monkeypatch)
    incoming = message(is_group=filtered)
    await service.ingress.receive(incoming)
    original = service.store.rows("SELECT * FROM inbox")[0]
    await service.ingress.receive(incoming.model_copy(update={"reply_to": "newly-observed-parent"}))
    assert service.store.item(original["id"]) == original
    with pytest.raises(ValueError, match="Conflicting"):
        await service.ingress.receive(
            incoming.model_copy(update={"reply_to": "parent", "text": "changed"})
        )
