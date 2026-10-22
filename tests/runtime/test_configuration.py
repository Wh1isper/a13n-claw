"""Native configuration changes affect future composition, not captured execution."""

import asyncio

import pytest
import yaml
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from pydantic_ai.messages import ModelResponse, TextPart

from a13n_claw.messaging.capability import messaging_integrations
from a13n_claw.runtime.host import open_host
from tests.helpers import message, setup, streaming_model


@pytest.mark.anyio
async def test_dispatch_uses_captured_capability_then_next_configuration(tmp_path, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def model(messages, info):
        entered.set()
        await asyncio.wait_for(release.wait(), 20)
        return ModelResponse(parts=[TextPart("No implicit settlement")])

    root, service, _ = await setup(tmp_path, monkeypatch, model=streaming_model(model))
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await service.ingress.receive(message("first"))
        await service.reconcile()
        first = service.store.rows("SELECT * FROM attempts")[0]
        try:
            await asyncio.wait_for(entered.wait(), 10)
            agent = yaml.safe_load((root / "host/agents/claw.yaml").read_text())
            agent["capabilities"] = [
                entry for entry in agent["capabilities"] if entry["capability"] != "ClawMessaging"
            ]
            await host.mutate_configuration(
                relative_path="agents/claw.yaml",
                request=ResourceMutationRequest(content=yaml.safe_dump(agent)),
            )
            configuration = await host.inspect_thread_configuration(first["thread_id"])
            assert configuration.captured is not None
            assert "ClawMessaging" in configuration.captured.capability_ids
            assert "ClawMessaging" not in configuration.next_capability_ids
            await service.ingress.receive(message("second"))
            await service.reconcile()
            steering = service.store.rows("SELECT * FROM attempts WHERE kind='steer'")
            assert len(steering) == 1
            assert steering[0]["status"] == "accepted"
            assert steering[0]["receipt_id"] == first["receipt_id"]
        finally:
            release.set()
        outcome = await host.wait_root_operation(first["receipt_id"], timeout_seconds=10)
        assert outcome.status == "completed", outcome
        await service.reconcile()
        await asyncio.sleep(service.settings.retry_seconds)
        await service.reconcile()
        assert len(service.store.rows("SELECT * FROM attempts")) == 2
        items = service.store.rows("SELECT * FROM inbox")
        assert all(item["disposition"] == "pending" for item in items)
        assert all("lacks required ClawMessaging" in item["reason"] for item in items)
