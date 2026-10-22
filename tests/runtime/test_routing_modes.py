"""Startup routing changes serve new messages without migrating old obligations."""

import itertools

import pytest
import yaml
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest

from a13n_claw.runtime.lifecycle import open_service
from tests.helpers import message, settings, setup


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("old_mode", "new_mode"),
    list(itertools.permutations(("one_thread", "per_channel", "per_trigger"), 2)),
)
async def test_restart_uses_new_mode_without_draining_old_work(
    tmp_path, monkeypatch, old_mode, new_mode
):
    root, initial, transport = await setup(tmp_path, monkeypatch, old_mode)
    config = initial.settings
    (root / "claw.yaml").write_text(yaml.safe_dump(config.model_dump(mode="json")))
    async with open_service(root, adapters={"lark": transport}, background=False) as service:
        await service.host.mutate_configuration(
            relative_path="projects/channel.yaml",
            request=ResourceMutationRequest(
                content=yaml.safe_dump(
                    {
                        "schema_version": "1",
                        "kind": "project",
                        "id": "project-channel",
                        "name": "Channel",
                        "roots": [{"path": str(tmp_path / "work")}],
                        "defaults": {"agent": "agent-claw"},
                    }
                )
            ),
        )
        # More than one scan page: old-mode work must not starve fresh messages.
        for index in range(55):
            await service.ingress.receive(message(f"old-{index}"))
        old = service.store.rows("SELECT * FROM inbox ORDER BY created,id")
        attempt = service.store.begin_attempt("thread-old", "submit", [old[0]["id"]])
        service.store.finish_attempt(attempt, "unknown", evidence="Lost response")
        delivery = service.store.request_delivery(
            request_key="old-send",
            channel_id="room",
            destination={"chat_id": "chat", "reply_to": "msg-old-1"},
            thread_id=None,
            item_id=old[1]["id"],
            body={"text": "Old reply"},
        )
        service.store.settle(
            old[2]["id"], 1, "deferred", "Wait", dependency={"kind": "time", "at": 0}
        )
        retained = service.store.rows("SELECT * FROM inbox ORDER BY created,id")

    (root / "claw.yaml").write_text(yaml.safe_dump(settings(new_mode).model_dump(mode="json")))
    async with open_service(root, adapters={"lark": transport}, background=False) as service:
        assert service.settings.mode == service.store.instance()["mode"] == new_mode
        assert service.store.actionable() == []
        await service.ingress.receive(message("new"))
        await service.reconcile()
        fresh = service.store.rows("SELECT * FROM inbox WHERE event_id='new'")[0]
        assert fresh["mode"] == new_mode and fresh["thread_id"]
        admitted = service.store.rows(
            "SELECT a.* FROM attempts a JOIN attempt_items i ON i.attempt_id=a.id "
            "WHERE i.item_id=?",
            (fresh["id"],),
        )[0]
        assert admitted["status"] == "accepted"
        outcome = await service.host.wait_root_operation(admitted["receipt_id"], timeout_seconds=10)
        assert outcome.status == "completed"
        target = await service.host.get_thread(fresh["thread_id"])
        assert target.thread.configuration.project_id == (
            "project-claw-main" if new_mode == "one_thread" else "project-channel"
        )
        assert (
            service.store.rows("SELECT * FROM inbox WHERE event_id!='new' ORDER BY created,id")
            == retained
        )
        assert service.store.delivery(delivery["id"])["status"] == "pending"
        assert transport.sent == []
        assert (
            service.store.rows("SELECT status FROM attempts WHERE id=?", (attempt,))[0]["status"]
            == "unknown"
        )

    # Same-mode restart retains its normal pending-work recovery behavior.
    async with open_service(root, adapters={"lark": transport}, background=False) as service:
        assert service.store.instance()["mode"] == new_mode
        assert service.store.item(fresh["id"])["thread_id"] == fresh["thread_id"]
