"""Background Inbox recovery uses retained state, without another arrival or callback."""

import asyncio

import pytest
import yaml
from pydantic_ai.messages import ModelResponse, TextPart

from a13n_claw.runtime.lifecycle import open_service
from tests.helpers import message, setup, streaming_model


@pytest.mark.anyio
async def test_startup_and_periodic_drain_revisit_unsettled_completion(tmp_path, monkeypatch):
    calls = []

    async def model(messages, info):
        calls.append(messages)
        return ModelResponse(parts=[TextPart("Finished without settling the business item")])

    root, original, transport = await setup(tmp_path, monkeypatch, model=streaming_model(model))
    config = original.settings.model_copy(update={"reconcile_seconds": 0.1})
    (root / "claw.yaml").write_text(
        yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8"
    )
    await original.ingress.receive(message("retained-before-startup"))
    item_id = original.store.rows("SELECT id FROM inbox")[0]["id"]
    assert original.store.rows("SELECT * FROM attempts") == []

    # A fresh service has no arrival wake or callback from the original process.
    async with open_service(root, adapters={"lark": transport}) as service:
        assert service is not original
        async with asyncio.timeout(20):
            while True:
                attempts = service.store.rows("SELECT * FROM attempts ORDER BY created")
                if len(attempts) >= 2 and attempts[1]["receipt_id"]:
                    break
                await asyncio.sleep(0.05)
        assert len(attempts) == 2
        first, second = attempts
        assert first["status"] == "observed"
        assert first["thread_id"] == second["thread_id"]
        assert first["receipt_id"] != second["receipt_id"]
        assert second["created"] - first["created"] >= config.retry_seconds
        outcome = await service.host.wait_root_operation(second["receipt_id"], timeout_seconds=10)
        assert outcome.status == "completed", outcome
        assert len(calls) == 2
        assert service.store.item(item_id)["disposition"] == "pending"
        assert transport.sent == []
        # Stop further checks only after proving periodic fallback and backoff.
        service.stopping = True
        service.wake.set()
