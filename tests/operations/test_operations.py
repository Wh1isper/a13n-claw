"""Product configuration, quiescent snapshots and obligation-preserving retention."""

import asyncio
import os
import time
from types import SimpleNamespace

import httpx
import pytest
import yaml

from a13n_claw.app import create_app
from a13n_claw.configuration.bootstrap import initialize
from a13n_claw.configuration.models import read_settings
from a13n_claw.configuration.product import source
from a13n_claw.errors import ClawError
from a13n_claw.messaging.capability import messaging_integrations
from a13n_claw.operations.backup import backup, restore, verify
from a13n_claw.operations.maintenance import retain
from a13n_claw.runtime.host import open_host
from a13n_claw.runtime.lifecycle import open_service
from tests.helpers import message, setup


def test_backup_rejects_directory_enumeration_error(tmp_path, monkeypatch):
    root = tmp_path / "instance"
    root.mkdir()
    (root / "claw.yaml").write_text("{}")
    (root / "operator.token").write_text("test-token")
    hidden = root / "host" / "data"
    hidden.mkdir(parents=True)
    (hidden / "important.db").write_bytes(b"retained-host-data")
    scandir = os.scandir

    def denied(path):
        if os.fspath(path) == str(hidden):
            raise PermissionError("Directory enumeration denied")
        return scandir(path)

    # Deterministic on Windows and root-run CI as well as non-root POSIX.
    monkeypatch.setattr(os, "scandir", denied)
    snapshot = tmp_path / "snapshot"
    with pytest.raises(PermissionError, match="enumeration denied"):
        backup(root, snapshot, acknowledge_external_state=True)
    assert not (snapshot / "manifest.json").exists()


async def initialized(tmp_path, monkeypatch):
    monkeypatch.setenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", "0")
    root, workspace = tmp_path / "claw", tmp_path / "work"
    workspace.mkdir()
    await initialize(root, workspace)
    return root


@pytest.mark.anyio
async def test_product_api_save_reload_conflict_and_startup(tmp_path, monkeypatch):
    root = await initialized(tmp_path, monkeypatch)
    app = create_app(root=root, console=False)
    headers = {"Authorization": "Bearer " + (root / "operator.token").read_text().strip()}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://claw", headers=headers
        ) as client,
    ):
        original = (await client.get("/api/product-configuration")).json()
        native_host = app.state.service.host
        candidate = yaml.safe_load(original["content"])
        candidate["retry_seconds"] = 75
        content = "# Preserve this comment\r\n" + yaml.safe_dump(candidate).replace("\n", "\r\n")
        response = await client.put(
            "/api/product-configuration",
            json={
                "content": content,
                "expected_digest": original["digest"],
            },
        )
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["pending_reload"]
        assert saved["accepted"]["retry_seconds"] == 30
        assert (root / "claw.yaml").read_bytes() == content.encode()
        assert (
            await client.put(
                "/api/product-configuration",
                json={
                    "content": content,
                    "expected_digest": original["digest"],
                },
            )
        ).status_code == 409
        assert (
            await client.post(
                "/api/product-configuration/reload",
                json={
                    "expected_digest": original["digest"],
                },
            )
        ).status_code == 409
        loaded = await client.post(
            "/api/product-configuration/reload",
            json={
                "expected_digest": saved["digest"],
            },
        )
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["accepted"]["retry_seconds"] == 75
        assert not loaded.json()["pending_reload"]
        assert app.state.service.host is native_host
        for changes in (
            {"host_data": "elsewhere"},
            {"main_project_id": "missing"},
            {
                "connections": {"bad": {"app_id": "a", "bot_open_id": "b"}},
                "channels": {
                    "room": {
                        "connection": "bad",
                        "chat_id": "chat",
                        "project_id": "missing",
                        "allowed_senders": ["u"],
                        "shared_context_acknowledged": True,
                    }
                },
            },
        ):
            invalid = {**candidate, **changes}
            rejected = await client.put(
                "/api/product-configuration",
                json={
                    "content": yaml.safe_dump(invalid),
                    "expected_digest": saved["digest"],
                },
            )
            assert rejected.status_code == 409, rejected.text
            assert source(root / "claw.yaml")["digest"] == saved["digest"]
        diagnostic = await client.get("/api/diagnostics")
        assert diagnostic.status_code == 200
        assert "operator.token" not in diagnostic.text
        assert diagnostic.json()["missing_projects"] == []
        assert (await client.post("/api/retention", json={"older_than_days": 30})).json()[
            "pruned"
        ] == 0
    async with open_service(root, background=False) as service:
        assert service.settings.retry_seconds == 75
        assert not (await service.configuration.view())["pending_reload"]


@pytest.mark.anyio
async def test_reload_joins_listener_intake_and_survives_request_cancellation(
    tmp_path, monkeypatch
):
    root, service, transport = await setup(tmp_path, monkeypatch)
    (root / "claw.yaml").write_text(yaml.safe_dump(service.settings.model_dump(mode="json")))
    entered, release, started, closed = (asyncio.Event() for _ in range(4))

    async def run(receive):
        async with service.intake_lock:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                await release.wait()

    async def close():
        closed.set()

    async def new_run(receive):
        started.set()
        await asyncio.Future()

    async def new_close():
        pass

    transport.run, transport.close = run, close
    replacement = SimpleNamespace(run=new_run, close=new_close, status="listening")

    async def build(candidate):
        return {"lark": replacement}

    monkeypatch.setattr(service.configuration, "build", build)
    service.configuration.background = True
    service.configuration.start()
    await entered.wait()
    async with open_host(root, service.settings) as host:
        service.bind(host)
        content = yaml.safe_dump(
            service.settings.model_copy(update={"retry_seconds": 80}).model_dump(mode="json")
        )
        saved = await service.configuration.save(content, source(root / "claw.yaml")["digest"])
        reload_task = asyncio.create_task(service.configuration.reload(saved["digest"]))
        await asyncio.sleep(0.05)
        reload_task.cancel()
        await asyncio.sleep(0.05)
        assert not reload_task.done()
        assert not closed.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(reload_task, 5)
        await asyncio.wait_for(started.wait(), 5)
        assert service.settings.retry_seconds == 80
        assert service.adapters["lark"] is replacement
        assert closed.is_set()
        assert service.configuration.accepted_digest == saved["digest"]
        await service.configuration.stop()


@pytest.mark.anyio
async def test_failed_reload_preserves_adapters_and_generation(tmp_path, monkeypatch):
    root = await initialized(tmp_path, monkeypatch)
    async with open_service(root, background=False) as service:
        original = await service.configuration.view()
        (root / "claw.yaml").write_text("invalid: [\n")
        with pytest.raises(ValueError, match="Invalid product YAML"):
            await service.configuration.reload(source(root / "claw.yaml")["digest"])
        assert service.configuration.accepted_digest == original["digest"]
        assert service.configuration.error == "ValueError"
        assert service.settings.retry_seconds == 30


@pytest.mark.anyio
async def test_backup_restore_keeps_host_state_and_unknown_business_intent(tmp_path, monkeypatch):
    root = await initialized(tmp_path, monkeypatch)
    async with open_service(root, background=False) as service:
        main = service.store.instance()["main_thread_id"]
        attempt = service.store.begin_attempt(main, "wake", [])
        with pytest.raises(ClawError, match="owns"):
            backup(root, tmp_path / "live", acknowledge_external_state=True)
    snapshot = tmp_path / "snapshot"
    result = backup(root, snapshot, acknowledge_external_state=True)
    assert result["files"] > 3
    assert verify(snapshot)["format"] == 1
    with pytest.raises(FileExistsError):
        backup(root, snapshot, acknowledge_external_state=True)
    with pytest.raises(ValueError, match="empty root"):
        restore(root, snapshot, acknowledge_external_state=True)
    restored = tmp_path / "restored"
    restore(restored, snapshot, acknowledge_external_state=True)
    assert (root / "business.sqlite3").read_bytes() == (restored / "business.sqlite3").read_bytes()
    assert (root / "operator.token").read_bytes() == (restored / "operator.token").read_bytes()
    async with open_service(restored, background=False) as service:
        assert service.store.instance()["main_thread_id"] == main
        assert (await service.host.get_thread(main)).thread.thread_id == main
        row = service.store.rows("SELECT * FROM attempts WHERE id=?", (attempt,))[0]
        assert row["status"] == "unknown"
        assert len(service.store.rows("SELECT * FROM attempts")) == 1
    (snapshot / "instance" / "claw.yaml").write_text("corrupt")
    with pytest.raises(ValueError, match="checksum"):
        restore(tmp_path / "corrupt", snapshot, acknowledge_external_state=True)
    assert not (tmp_path / "corrupt").exists()


@pytest.mark.anyio
async def test_backup_boundaries_and_incomplete_restore_guard(tmp_path, monkeypatch):
    root = await initialized(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Acknowledge"):
        backup(root, tmp_path / "backup", acknowledge_external_state=False)
    with pytest.raises(ValueError, match="separate"):
        backup(root, root / "backup", acknowledge_external_state=True)
    settings = read_settings(root)
    (root / "claw.yaml").write_text(
        yaml.safe_dump(
            settings.model_copy(update={"host_data": str(tmp_path / "external")}).model_dump(
                mode="json"
            )
        )
    )
    with pytest.raises(ValueError, match="outside"):
        backup(root, tmp_path / "backup", acknowledge_external_state=True)
    (root / "restore.incomplete").touch()
    with pytest.raises(ValueError, match="Incomplete restore"):
        read_settings(root)


@pytest.mark.anyio
async def test_retention_keeps_unknown_effects_and_dedup_tombstones(tmp_path, monkeypatch):
    root, service, _ = await setup(tmp_path, monkeypatch)
    for event in ("settled", "pending", "unknown", "deferred", "delivery", "peer"):
        await service.ingress.receive(message(event))
    items = {row["event_id"]: row for row in service.store.rows("SELECT * FROM inbox")}
    for event in ("settled", "delivery"):
        row = items[event]
        service.store.settle(row["id"], 1, "handled", "Finished")
    attempt = service.store.begin_attempt(
        "thread-unresolved", "wake", [items["unknown"]["id"], items["peer"]["id"]]
    )
    service.store.finish_attempt(attempt, "unknown")
    service.store.settle(
        items["deferred"]["id"], 1, "deferred", "Later", dependency={"kind": "human"}
    )
    service.store.request_delivery(
        request_key="unknown-send",
        channel_id="room",
        destination={"chat_id": "chat"},
        thread_id=None,
        item_id=items["delivery"]["id"],
        body={"text": "retained"},
    )
    with service.store.transaction() as db:
        db.execute("UPDATE inbox SET created=?", (time.time() - 40 * 86400,))
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        preview = await retain(service, older_than_days=30, apply=False)
        assert preview["candidates"] == [items["settled"]["id"]]
        assert service.store.item(items["settled"]["id"])["body"] is not None
        assert (await retain(service, older_than_days=30, apply=True))["pruned"] == 1
        assert service.store.item(items["settled"]["id"])["body"] is None
        assert (await retain(service, older_than_days=30, apply=True))["pruned"] == 0
        await service.ingress.receive(message("settled"))
        assert len(service.store.rows("SELECT * FROM inbox")) == 6
        with pytest.raises(ValueError, match="pruned"):
            service.store.settle(items["settled"]["id"], 3, "pending", "Reopen")
        assert service.store.item(items["delivery"]["id"])["body"] is not None
        assert service.store.item(items["unknown"]["id"])["body"] is not None


@pytest.mark.anyio
async def test_reload_waits_for_admitted_send_then_revokes_next_send(tmp_path, monkeypatch):
    from a13n_claw.messaging.transport import SendResult

    root, service, transport = await setup(tmp_path, monkeypatch)
    (root / "claw.yaml").write_text(yaml.safe_dump(service.settings.model_dump(mode="json")))
    entered, release, closed = (asyncio.Event() for _ in range(3))

    async def send(*args, **kwargs):
        entered.set()
        await release.wait()
        assert not closed.is_set()
        return SendResult("sent", "fixture")

    async def close():
        closed.set()

    async def build(candidate):
        return {}

    transport.send, transport.close = send, close
    monkeypatch.setattr(service.configuration, "build", build)
    await service.ingress.receive(message())
    item = service.store.rows("SELECT * FROM inbox")[0]
    rows = [
        service.store.request_delivery(
            request_key=key,
            channel_id="room",
            destination={"chat_id": "chat", "reply_to": "msg-event-1"},
            thread_id=None,
            item_id=item["id"],
            body={"text": "Reply"},
        )
        for key in ("first", "second")
    ]
    async with open_host(root, service.settings) as host:
        service.bind(host)
        config = service.settings.model_dump(mode="json")
        config["channels"]["room"]["enabled"] = False
        saved = await service.configuration.save(
            yaml.safe_dump(config), source(root / "claw.yaml")["digest"]
        )
        sending = asyncio.create_task(service.delivery.send(rows[0]["id"]))
        await entered.wait()
        reloading = asyncio.create_task(service.configuration.reload(saved["digest"]))
        await asyncio.sleep(0.05)
        assert not reloading.done()
        assert not closed.is_set()
        release.set()
        assert (await sending)["status"] == "sent"
        await reloading
        assert closed.is_set()
        assert "blocked" in await service.delivery.send(rows[1]["id"])
        assert service.store.delivery(rows[1]["id"])["status"] == "pending"


@pytest.mark.anyio
async def test_connection_rebinding_cannot_reauthorize_retained_source(tmp_path, monkeypatch):
    root, service, _ = await setup(tmp_path, monkeypatch)
    await service.ingress.receive(message())
    item = service.store.rows("SELECT * FROM inbox")[0]
    original = service.settings
    changed = original.model_copy(
        update={
            "connections": {
                "lark": original.connections["lark"].model_copy(update={"app_id": "other-app"})
            }
        }
    )
    async with open_host(root, original) as host:
        service.bind(host)
        with pytest.raises(ValueError, match="new connection ID"):
            await service.configuration.validate(changed)
    # An out-of-band edit/restart still cannot retarget a retained source.
    service.settings = changed
    with pytest.raises(ValueError, match="identity changed"):
        service.ingress.source(item)


def test_product_save_rechecks_external_edit_before_replace(tmp_path, monkeypatch):
    from a13n_claw.configuration import product as product_configuration

    path = tmp_path / "claw.yaml"
    path.write_text("original")
    expected = source(path)["digest"]
    original_fsync = product_configuration.os.fsync

    def concurrent_edit(fd):
        original_fsync(fd)
        path.write_text("external edit")

    monkeypatch.setattr(product_configuration.os, "fsync", concurrent_edit)
    with pytest.raises(ValueError, match="changed"):
        product_configuration.save_source(path, "candidate", expected)
    assert path.read_text() == "external edit"
    assert list(tmp_path.glob(".claw-*")) == []


@pytest.mark.anyio
async def test_failed_restore_preserves_incomplete_marker_and_original(tmp_path, monkeypatch):
    from a13n_claw.operations import backup as backups

    root = await initialized(tmp_path, monkeypatch)
    snapshot = tmp_path / "snapshot"
    backup(root, snapshot, acknowledge_external_state=True)
    original = (root / "claw.yaml").read_bytes()
    restored = tmp_path / "failed-restore"

    def fail_copy(*args, **kwargs):
        raise OSError("Injected disk error")

    monkeypatch.setattr(backups.shutil, "copy2", fail_copy)
    with pytest.raises(OSError, match="disk error"):
        restore(restored, snapshot, acknowledge_external_state=True)
    assert (restored / "restore.incomplete").exists()
    with pytest.raises(ValueError, match="Incomplete restore"):
        read_settings(restored)
    assert (root / "claw.yaml").read_bytes() == original
    assert verify(snapshot)["format"] == 1


@pytest.mark.anyio
async def test_retention_tracks_request_snapshots_and_later_notification_obligations(
    tmp_path, monkeypatch
):
    from a13n_claw.adapters.github_store import GitHubStore
    from a13n_claw.operations.maintenance import prune, retention_candidates

    _, service, _ = await setup(tmp_path, monkeypatch)
    await service.ingress.receive(message("request"))
    item = service.store.rows("SELECT * FROM inbox")[0]
    service.store.settle(item["id"], item["version"], "handled", "Completed")
    store = GitHubStore(service.store, "lark", "account")
    notification = {"id": "notice", "updated_at": "first", "repository": "owner/repo"}
    store.stage(notification, 1, 0)
    store.prepared("notice:first", [("request", message("request").model_dump(mode="json"))])
    store.finish("notice:first", "captured", "pending")
    before = time.time() + 1
    assert retention_candidates(service.store, before) == []
    assert prune(service.store, before, [item["id"]]) == 0
    store.finish("notice:first", "captured", "read")
    store.stage({**notification, "updated_at": "second"}, 2, 0)
    assert retention_candidates(service.store, before) == []
    store.prepared("notice:second", [("request", message("request").model_dump(mode="json"))])
    store.finish("notice:second", "captured", "read")
    assert prune(service.store, before, [item["id"]]) == 1
    assert service.store.item(item["id"])["body"] is None
    assert store.messages("notice:first") == store.messages("notice:second") == []
    assert service.store.rows("SELECT * FROM github_requests")[0]["message"] is None
    assert len(service.store.rows("SELECT * FROM github_event_requests")) == 2
    # Re-discovery cannot restore a pruned request snapshot.
    store.prepared("notice:second", [("request", {"text": "must not resurrect"})])
    assert store.messages("notice:second") == []
