"""File authority, revision preconditions and independently committed runtime loading."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml
from fastapi.testclient import TestClient
from test_api import authorize

from a13n_claw.app import create_app
from a13n_claw.configuration import Configuration, ServerSettings, export_configuration
from a13n_claw.configuration_files import ConfigurationFile, revision
from a13n_claw.domain import ClawError, InputRequest, Principal, ResourceChange
from a13n_claw.instance import token_hash
from a13n_claw.storage import Store


def migrated(store, tmp_path):
    source = ConfigurationFile(store, tmp_path / "config.yaml", launch=ServerSettings())
    source.initialize(migrate=True)
    return source


def test_save_load_comments_noop_and_captured_definitions(runtime_store, tmp_path):
    store = runtime_store
    source = migrated(store, tmp_path)
    original = source.status("operator")
    thread = store.create_thread("operator", "Captured", "default")
    receipt = store.admit(
        "operator", thread.id, InputRequest(request_id="first", text="Hi"), str(tmp_path)
    )
    captured = store.run("operator", receipt.run_id).composition
    versions = [(r.id, r.version) for r in store.resources("operator")]
    candidate = "# Operator's explanation\n" + original["text"].replace(
        "model_name: test", "model_name: changed"
    )
    preview = source.preview("operator", candidate)
    assert preview["valid"] and preview["changes"] == [
        {"resource": "model/model", "action": "update"}
    ]
    assert source.status("operator") == original
    saved = source.save("operator", candidate, original["revision"])
    assert saved["pending"] and saved["active_revision"] == original["revision"]
    assert [(r.id, r.version) for r in store.resources("operator")] == versions
    assert source.save("operator", candidate, original["revision"]) == saved
    loaded = source.reload("operator", saved["revision"])
    assert not loaded["pending"]
    assert source.reload("operator", saved["revision"])["changes"] == []
    assert store.run("operator", receipt.run_id).composition == captured
    model = next(r for r in store.resources("operator") if r.kind == "model")
    source.update_resources(
        "operator",
        [
            ResourceChange(
                kind="model",
                id="model",
                content={**model.content, "model_name": "next"},
                expected_version=model.version,
            )
        ],
    )
    assert source.path.read_text().startswith("# Operator's explanation\n")
    assert export_configuration(store, "operator").models["model"].model_name == "next"


def test_conflicting_writes_and_external_edits_preserve_source(runtime_store, tmp_path):
    source = migrated(runtime_store, tmp_path)
    initial = source.status("operator")
    candidates = [initial["text"] + f"# editor {i}\n" for i in range(2)]

    def save(text):
        try:
            return source.save("operator", text, initial["revision"])
        except ClawError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, candidates))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "configuration_conflict" in results
    winner = source.path.read_text()
    with pytest.raises(ClawError, match="unloaded changes"):
        source.update_resources("operator", [])
    with pytest.raises(ClawError, match="changed"):
        source.reload("operator", initial["revision"])
    assert source.path.read_text() == winner


def test_invalid_reload_and_saved_file_load_failure_are_separate(runtime_store, tmp_path):
    store = runtime_store
    source = migrated(store, tmp_path)
    before = source.status("operator")
    original = export_configuration(store, "operator")
    invalid = before["text"].replace("model_id: model", "model_id: missing")
    assert not source.preview("operator", invalid)["valid"]
    with pytest.raises(ClawError, match="Missing model"):
        source.save("operator", invalid, before["revision"])
    assert source.status("operator") == before
    candidate = before["text"].replace("model_name: test", "model_name: changed")
    saved = source.save("operator", candidate, before["revision"])
    with store._transaction() as db:
        db.execute(
            "CREATE TRIGGER fail_load BEFORE INSERT ON configuration_state "
            "BEGIN SELECT RAISE(ABORT, 'fixture load failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="fixture load failure"):
        source.reload("operator", saved["revision"])
    assert source.status("operator")["pending"]
    assert source.path.read_text() == candidate
    assert export_configuration(store, "operator") == original
    source.path.write_text("profiles: [invalid YAML\n", encoding="utf-8")
    broken = source.status("operator")
    assert broken["text"] and broken["issues"] and broken["pending"]
    with pytest.raises(ClawError):
        source.reload("operator", broken["revision"])
    repaired = source.save("operator", candidate, broken["revision"])
    with store._transaction() as db:
        db.execute("DROP TRIGGER fail_load")
    assert not source.reload("operator", repaired["revision"])["pending"]


def test_complete_catalog_omission_retires_and_disables(runtime_store, tmp_path):
    source = migrated(runtime_store, tmp_path)
    initial = source.status("operator")
    data = yaml.safe_load(initial["text"])
    data["channels"] = {
        "chat": {
            "platform": "http",
            "account": "test",
            "channel": "chat",
            "ingress_actor_id": "operator",
            "allowed_senders": ["human"],
        }
    }
    text = yaml.safe_dump(data)
    saved = source.save("operator", text, initial["revision"])
    source.reload("operator", saved["revision"])
    saved = source.save("operator", "format: 1\n", saved["revision"])
    source.reload("operator", saved["revision"])
    assert all(r.retired for r in runtime_store.resources("operator"))
    with runtime_store._connection() as db:
        channel = db.execute("SELECT policy,version FROM channels WHERE id='chat'").fetchone()
        assert not yaml.safe_load(channel[0])["enabled"] and channel[1] == 2
    assert source.reload("operator", saved["revision"])["changes"] == []


def test_missing_existing_catalog_requires_migration(runtime_store, tmp_path):
    source = ConfigurationFile(runtime_store, tmp_path / "config.yaml")
    with pytest.raises(ClawError, match="migrate --offline"):
        source.initialize()
    assert not source.path.exists()
    source.initialize(migrate=True)
    with pytest.raises(ClawError, match="new configuration file"):
        source.initialize(migrate=True)
    assert not source.status("operator")["pending"]


def test_retired_defaults_survive_migration(runtime_store, tmp_path):
    runtime_store.save_resource(
        "operator", "defaults", "instance", {"profile_id": "missing"}, 0, retired=True
    )
    source = migrated(runtime_store, tmp_path)
    assert "defaults/instance" in source.status("operator")["document"]["retired"]
    assert source.reload("operator", source.status("operator")["revision"])["changes"] == []


def test_launch_changes_remain_restart_required_after_reload(runtime_store, tmp_path):
    source = migrated(runtime_store, tmp_path)
    original = source.status("operator")
    saved = source.save(
        "operator", original["text"] + "server:\n  port: 9001\n", original["revision"]
    )
    loaded = source.reload("operator", saved["revision"])
    assert not loaded["pending"] and loaded["restart_required"] == ["port"]


def test_http_file_access_rechecks_admin_and_preserves_secret_boundary(console, tmp_path):
    with TestClient(create_app(console=False)) as api:
        authorize(api, tmp_path)
        store = Store(tmp_path / "data" / "claw.sqlite3")
        store.set_principal("operator", Principal(id="editor", admin=True), token_hash("editor"))
        headers = {"Authorization": "Bearer editor"}
        original = api.get("/api/configuration", headers=headers).json()
        candidate = "# from Web\nformat: 1\n"
        body = {"text": candidate, "expected_revision": original["revision"]}
        assert api.post(
            "/api/configuration/validate", json={"text": candidate}, headers=headers
        ).json()["valid"]
        saved = api.put("/api/configuration", json=body, headers=headers).json()
        assert saved["pending"]
        assert api.post(
            "/api/configuration/reload",
            json={"expected_revision": saved["revision"]},
            headers=headers,
        ).json()["applied"]
        store.save_credential("operator", "KEY", "secret-not-in-file")
        assert "secret-not-in-file" not in api.get("/api/configuration").text
        for update in ({"admin": False}, {"enabled": False}):
            actor = store.principal("editor").model_copy(update=update)
            store.set_principal("operator", actor, None)
            assert api.get("/api/configuration", headers=headers).status_code == 403
            assert api.put("/api/configuration", json=body, headers=headers).status_code == 403
            assert (
                api.post(
                    "/api/configuration/reload",
                    json={"expected_revision": saved["revision"]},
                    headers=headers,
                ).status_code
                == 403
            )


def test_invalid_startup_does_not_fall_back_to_loaded_state(console, tmp_path):
    with TestClient(create_app(console=False)):
        pass
    path = tmp_path / "config.yaml"
    path.write_text("profiles: [broken\n", encoding="utf-8")
    with pytest.raises(ClawError):
        create_app(console=False)


def test_exact_bytes_revision_includes_line_endings_and_comments(runtime_store, tmp_path):
    source = migrated(runtime_store, tmp_path)
    text = source.path.read_text()
    source.path.write_bytes(text.replace("\n", "\r\n").encode())
    assert source.status("operator")["revision"] == revision(source.path.read_bytes())
    assert source.status("operator")["pending"]
    assert Configuration.model_validate(yaml.safe_load(text)) == export_configuration(
        runtime_store, "operator"
    )


def test_reopening_preserves_loaded_configuration_and_history(runtime_store, tmp_path):
    store = runtime_store
    thread = store.create_thread("operator", "Retained Thread", "default")
    source = migrated(store, tmp_path)
    before = source.status("operator")
    reopened = Store(store.path)
    source = ConfigurationFile(reopened, source.path)
    assert source.status("operator") == before
    assert reopened.thread("operator", thread.id).title == "Retained Thread"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_offline_cli_can_inspect_export_and_repair_invalid_source(tmp_path, capsys, newline):
    import json

    from a13n_claw.cli import main

    source, root = tmp_path / "config.yaml", tmp_path / "data"
    common = ["--config", str(source), "--data-root", str(root), "--json"]
    main([*common, "config", "init"])
    main([*common, "config", "reload", "--offline"])
    valid = source.read_text().replace("\n", newline)
    broken = f"profiles: [broken{newline}"
    source.write_bytes(broken.encode("utf-8"))
    capsys.readouterr()
    main([*common, "config", "status", "--offline"])
    state = json.loads(capsys.readouterr().out)
    assert state["issues"] and state["text"] == broken
    exported = tmp_path / "broken-copy.yaml"
    main([*common, "config", "export", "--offline", "--output", str(exported)])
    assert exported.read_bytes() == source.read_bytes()
    candidate = tmp_path / "replacement.yaml"
    candidate.write_bytes(valid.encode("utf-8"))
    main(
        [
            *common,
            "config",
            "save",
            "--offline",
            "--file",
            str(candidate),
            "--revision",
            state["revision"],
        ]
    )
    assert source.read_bytes() == candidate.read_bytes()
