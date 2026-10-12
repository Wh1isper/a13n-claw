"""Headless publication and CLI behavior over real application boundaries."""

import io
import json
import sqlite3
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient
from test_api import authorize, completed, models

from a13n_claw.app import create_app
from a13n_claw.cli import main
from a13n_claw.configuration import (
    Configuration,
    ConfigurationApply,
    apply_configuration,
    dump_configuration,
    export_configuration,
    load_configuration,
)
from a13n_claw.domain import ClawError
from a13n_claw.instance import InstanceLease
from a13n_claw.management import STARTER, Client
from a13n_claw.storage import Store


def configuration():
    data = yaml.safe_load(STARTER)
    data["models"]["primary"]["model_name"] = "fixture"
    data["models"]["primary"]["credential_env"] = "TEST_KEY"
    return Configuration.model_validate(data)


def test_document_export_edit_conflict_and_retry(runtime_store):
    store = runtime_store
    original = export_configuration(store, "operator")
    edited = Configuration.model_validate(
        original.model_dump(mode="json")
        | {
            "environments": {"local": {"kind": "local", "files": "write"}},
        }
    )
    request = ConfigurationApply(request_id="first", configuration=edited)
    dry = apply_configuration(store, "operator", request, validate_only=True)
    assert not dry["applied"] and export_configuration(store, "operator") == original
    saved = apply_configuration(store, "operator", request)
    assert saved["applied"]
    assert apply_configuration(Store(store.path), "operator", request) == saved
    assert export_configuration(store, "operator").versions["environment/local"] == 2
    with pytest.raises(ClawError, match="Resource changed"):
        apply_configuration(store, "operator", request.model_copy(update={"request_id": "other"}))
    with pytest.raises(ClawError, match="identity was reused"):
        apply_configuration(
            store, "operator", request.model_copy(update={"configuration": original})
        )


def test_publication_rolls_back_resources_channels_and_receipt(runtime_store):
    store = runtime_store
    original = export_configuration(store, "operator")
    data = original.model_dump(mode="json")
    data["channels"] = {
        "chat": {
            "platform": "http",
            "account": "test",
            "channel": "chat",
            "ingress_actor_id": "missing",
            "allowed_senders": ["human"],
        }
    }
    request = ConfigurationApply(
        request_id="invalid", configuration=Configuration.model_validate(data)
    )
    with pytest.raises(ClawError):
        apply_configuration(store, "operator", request)
    assert export_configuration(store, "operator") == original
    with store._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM configuration_applications").fetchone()[0] == 0
    data["channels"]["chat"]["ingress_actor_id"] = "operator"
    result = apply_configuration(
        store,
        "operator",
        request.model_copy(update={"configuration": Configuration.model_validate(data)}),
    )
    assert result["versions"]["channel/chat"] == 1
    assert export_configuration(store, "operator").channels["chat"].channel == "chat"


def test_publication_checks_references_and_preserves_captured_resources(runtime_store):
    store = runtime_store
    original = export_configuration(store, "operator")
    data = original.model_dump(mode="json")
    data["profiles"]["default"]["model_id"] = "missing"
    with pytest.raises(ClawError, match="Missing model"):
        apply_configuration(
            store,
            "operator",
            ConfigurationApply(
                request_id="missing", configuration=Configuration.model_validate(data)
            ),
        )
    assert export_configuration(store, "operator") == original
    data = original.model_dump(mode="json")
    data["retired"] = ["model/model"]
    with pytest.raises(ClawError, match="retired"):
        apply_configuration(
            store,
            "operator",
            ConfigurationApply(
                request_id="retired", configuration=Configuration.model_validate(data)
            ),
        )
    assert export_configuration(store, "operator") == original


def test_roundtrip_yaml_and_ambiguous_keys(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(dump_configuration(configuration()), encoding="utf-8")
    assert load_configuration(path) == configuration()
    for text in ("format: 1\nformat: 1\n", "models: {42: {}}", "models: !python/object {}"):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ClawError):
            load_configuration(path)


def test_offline_cli_bootstrap_secret_export_and_lease(tmp_path, monkeypatch, capsys):
    path, root = tmp_path / "config.yaml", tmp_path / "data"
    common = ["--config", str(path), "--data-root", str(root), "--json"]
    main([*common, "config", "init"])
    assert path.is_file()
    main([*common, "config", "validate"])
    main([*common, "config", "apply", "--offline"])
    first = Store(root / "claw.sqlite3")
    assert first.coordination.mode() == "one_thread"
    main([*common, "config", "apply", "--offline"])
    assert len(first.resources("operator")) == 4
    monkeypatch.setattr("sys.stdin", io.StringIO("fixture-secret\n"))
    main([*common, "credentials", "set", "OPENAI_API_KEY", "--stdin", "--offline"])
    assert first.resolve_credential("OPENAI_API_KEY") == "fixture-secret"
    main([*common, "main", "init", "--profile", "default", "--offline"])
    main_id = first.coordination.view("operator")["main_id"]
    main([*common, "main", "init", "--profile", "default", "--offline"])
    assert first.coordination.view("operator")["main_id"] == main_id
    exported = tmp_path / "export.yaml"
    main([*common, "config", "export", "--offline", "--output", str(exported)])
    assert load_configuration(exported).versions["model/primary"] == 1
    assert "fixture-secret" not in exported.read_text() + capsys.readouterr().out
    with InstanceLease(root), pytest.raises(SystemExit) as error:
        main([*common, "config", "apply", "--offline"])
    assert error.value.code == 2
    assert "instance_owned" in capsys.readouterr().err


def test_default_config_initialization_keeps_data_private(tmp_path, monkeypatch):
    import os
    import stat

    if os.name == "nt":
        pytest.skip("POSIX directory permissions; Windows uses account ACLs")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAW_CONFIG", raising=False)
    monkeypatch.delenv("CLAW_DATA_ROOT", raising=False)
    previous = os.umask(0o022)
    try:
        main(["config", "init"])
        main(["config", "apply", "--offline"])
        monkeypatch.setattr("sys.stdin", io.StringIO("private-credential-fixture"))
        main(["credentials", "set", "OPENAI_API_KEY", "--stdin", "--offline"])
    finally:
        os.umask(previous)
    root = tmp_path / ".a13n-claw"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert Store(root / "claw.sqlite3").resolve_credential("OPENAI_API_KEY") == (
        "private-credential-fixture"
    )


def test_retired_defaults_export_and_publication(runtime_store, tmp_path):
    store = runtime_store
    store.save_resource(
        "operator", "defaults", "instance", {"profile_id": "missing"}, 0, retired=True
    )
    exported = export_configuration(store, "operator")
    assert "defaults/instance" in exported.retired
    path = tmp_path / "retired.yaml"
    path.write_text(dump_configuration(exported), encoding="utf-8")
    apply_configuration(
        store,
        "operator",
        ConfigurationApply(request_id="retired-defaults", configuration=load_configuration(path)),
    )
    retained = export_configuration(store, "operator")
    assert retained.defaults.profile_id == "missing"
    assert "defaults/instance" in retained.retired
    assert retained.versions["defaults/instance"] == 2


def test_receipt_failure_rolls_back_entire_publication(runtime_store):
    store = runtime_store
    original = export_configuration(store, "operator")
    data = original.model_dump(mode="json")
    data["channels"] = {
        "chat": {
            "platform": "http",
            "account": "test",
            "channel": "chat",
            "ingress_actor_id": "operator",
            "allowed_senders": ["human"],
        }
    }
    with store._transaction() as db:
        audit_count = db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        db.execute(
            "CREATE TRIGGER fail_configuration_receipt BEFORE INSERT ON configuration_applications "
            "BEGIN SELECT RAISE(ABORT, 'fixture receipt failure'); END"
        )
    request = ConfigurationApply(
        request_id="receipt", configuration=Configuration.model_validate(data)
    )
    with pytest.raises(sqlite3.IntegrityError, match="fixture receipt failure"):
        apply_configuration(store, "operator", request)
    assert export_configuration(store, "operator") == original
    with store._transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == audit_count
        assert db.execute("SELECT COUNT(*) FROM configuration_applications").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_configuration_receipt")
    assert apply_configuration(store, "operator", request)["applied"]


def test_configuration_requires_current_admin_even_for_receipt_replay(console, tmp_path):
    from a13n_claw.domain import Principal
    from a13n_claw.instance import token_hash

    with TestClient(create_app(console=False)) as api:
        authorize(api, tmp_path)
        store = Store(tmp_path / "data" / "claw.sqlite3")
        store.set_principal("operator", Principal(id="editor", admin=True), token_hash("editor"))
        headers = {"Authorization": "Bearer editor"}
        request = ConfigurationApply(request_id="access", configuration=configuration())
        body = request.model_dump(mode="json")
        assert api.post("/api/configuration/apply", json=body, headers=headers).status_code == 200
        assert api.get("/api/configuration", headers=headers).status_code == 200
        for update in ({"admin": False}, {"enabled": False}):
            actor = store.principal("editor").model_copy(update=update)
            store.set_principal("operator", actor, None)
            assert api.get("/api/configuration", headers=headers).status_code == 403
            for validate_only in (False, True):
                assert (
                    api.post(
                        "/api/configuration/apply",
                        json=body,
                        headers=headers,
                        params={"validate_only": validate_only},
                    ).status_code
                    == 403
                )
        assert export_configuration(store, "operator").versions["model/primary"] == 1


def test_cli_timeout_never_retries_or_falls_back_offline(tmp_path, monkeypatch, capsys):
    import httpx

    path, root = tmp_path / "config.yaml", tmp_path / "data"
    path.write_text(dump_configuration(configuration()), encoding="utf-8")
    monkeypatch.setenv("CLAW_TOKEN", "fixture-client-token")
    with (
        patch("httpx.Client.request", side_effect=httpx.ReadTimeout("response lost")) as request,
        pytest.raises(SystemExit) as error,
    ):
        main(
            [
                "config",
                "apply",
                "--config",
                str(path),
                "--data-root",
                str(root),
                "--url",
                "http://127.0.0.1:8123",
            ]
        )
    assert error.value.code == 2
    assert request.call_count == 1
    assert "transport_unknown" in capsys.readouterr().err
    assert not root.exists()


def test_fresh_default_and_existing_mode_survive_restart(console, tmp_path):
    with TestClient(create_app(console=False)) as client:
        authorize(client, tmp_path)
        assert client.get("/api/instance").json()["conversation_mode"] == "one_thread"
        assert client.get("/").status_code == 404
    with TestClient(create_app(mode="per_channel", console=False)) as client:
        authorize(client, tmp_path)
        assert client.get("/api/instance").json()["conversation_mode"] == "per_channel"
    with TestClient(create_app(console=False)) as client:
        authorize(client, tmp_path)
        assert client.get("/api/instance").json()["conversation_mode"] == "per_channel"


def test_v3_migration_preserves_mode_and_threads(runtime_store):
    store = runtime_store
    thread = store.create_thread("operator", "Retained", "default")
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TABLE configuration_applications")
        db.execute("PRAGMA user_version=3")
    upgraded = Store(store.path, initial_mode="one_thread")
    assert upgraded.coordination.mode() == "per_channel"
    assert upgraded.thread("operator", thread.id).active


def test_cli_error_does_not_print_secret_values(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text("models:\n  main:\n    api_key: private-value\n", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        main(["config", "validate", "--config", str(path)])
    assert error.value.code == 2
    assert "private-value" not in capsys.readouterr().err
    path.write_text("key: [private-value\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["config", "validate", "--config", str(path)])
    assert "private-value" not in capsys.readouterr().err


def test_config_launch_paths_precedence_and_no_implicit_apply(console, tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        dump_configuration(
            configuration().model_copy(
                update={
                    "server": Configuration.model_validate(
                        {
                            "server": {
                                "workspace": "workspace",
                                "data_root": "other-data",
                                "port": 8123,
                                "console": False,
                            }
                        }
                    ).server
                }
            )
        )
    )
    monkeypatch.delenv("CLAW_DATA_ROOT")
    monkeypatch.delenv("CLAW_WORKSPACE")
    with patch("uvicorn.run") as run:
        main(["serve", "--config", str(path), "--port", "9123"])
    assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 9123}
    with TestClient(run.call_args.args[0]) as client:
        token = (tmp_path / "other-data" / "operator.token").read_text().strip()
        client.headers["Authorization"] = f"Bearer {token}"
        assert client.get("/api/resources").json() == []
        assert client.get("/api/instance").json()["workspace"] == str(tmp_path / "workspace")


def test_live_cli_configuration_work_and_stale_controls(console, tmp_path, monkeypatch, capsys):
    path = tmp_path / "config.yaml"
    path.write_text(dump_configuration(configuration()), encoding="utf-8")
    common = ["--config", str(path), "--json"]
    with TestClient(create_app(models=models, console=False)) as api:
        authorize(api, tmp_path)

        # Exercise the actual CLI parser, transport adapter, API and native runtime.
        # The loopback subprocess test separately covers real HTTP transport.
        def attach(self, args, config):
            self.http = api

        monkeypatch.setattr(Client, "__init__", attach)
        monkeypatch.setattr(Client, "close", lambda self: None)
        main([*common, "config", "apply"])
        assert json.loads(capsys.readouterr().out)["applied"]
        monkeypatch.setattr("sys.stdin", io.StringIO("provider-secret-fixture"))
        main([*common, "credentials", "set", "TEST_KEY", "--stdin"])
        capsys.readouterr()
        main([*common, "main", "init", "--profile", "default"])
        main_id = json.loads(capsys.readouterr().out)["id"]
        main([*common, "main", "show"])
        version = json.loads(capsys.readouterr().out)["version"]
        main([*common, "main", "pause", "--version", str(version)])
        capsys.readouterr()
        with pytest.raises(SystemExit):
            main([*common, "main", "resume", "--version", str(version)])
        assert "version_conflict" in capsys.readouterr().err
        main([*common, "threads", "send", main_id, "--request-id", "input-1", "--text", "Hello"])
        receipt = json.loads(capsys.readouterr().out)
        result = completed(api, receipt["run_id"])
        main([*common, "runs", "show", result["id"]])
        assert json.loads(capsys.readouterr().out)["output"] == result["output"]
        main([*common, "threads", "send", main_id, "--request-id", "input-1", "--text", "Hello"])
        assert json.loads(capsys.readouterr().out)["id"] == receipt["id"]
        main([*common, "workers"])
        assert json.loads(capsys.readouterr().out) == []
        for command in (
            ["status"],
            ["inbox", "list"],
            ["channels", "list"],
            ["deliveries", "list"],
            ["threads", "history", main_id],
        ):
            main([*common, *command])
            json.loads(capsys.readouterr().out)


@pytest.mark.anyio
async def test_subprocess_cli_over_http_failure_recovery_and_restart(console, tmp_path):
    import asyncio
    import os
    import subprocess
    import sys

    import httpx
    from test_network_runtime import listening

    path = tmp_path / "cli.yaml"
    root = tmp_path / "data"
    path.write_text(dump_configuration(configuration()), encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("CLAW_")}
    env.pop("TEST_KEY", None)

    async def cli(*arguments, stdin=None):
        result = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "a13n_claw",
                "--config",
                str(path),
                "--data-root",
                str(root),
                "--json",
                *arguments,
            ],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "provider-secret-fixture" not in result.stdout + result.stderr
        return json.loads(result.stdout)

    await cli("config", "apply", "--offline")
    main_thread = await cli("main", "init", "--profile", "default", "--offline")
    app = create_app(data_root=root, workspace=tmp_path / "workspace", models=models, console=False)
    async with listening(app) as url:
        connection = ["--url", url, "--token-file", str(root / "operator.token")]
        status = await cli("status", *connection)
        assert status["conversation_mode"] == "one_thread"
        receipt = await cli(
            "threads",
            "send",
            main_thread["id"],
            "--text",
            "Run without credential",
            "--request-id",
            "failure",
            *connection,
        )
        token = (root / "operator.token").read_text().strip()
        async with httpx.AsyncClient(
            base_url=url, headers={"Authorization": f"Bearer {token}"}
        ) as http:

            async def terminal(run_id):
                async with asyncio.timeout(10):
                    while True:
                        response = await http.get(f"/api/runs/{run_id}")
                        response.raise_for_status()
                        run = response.json()
                        if run["status"] in {"failed", "completed", "cancelled"}:
                            return run
                        await asyncio.sleep(0.02)

            failed = await terminal(receipt["run_id"])
            assert failed["status"] == "failed"
            await cli(
                "credentials",
                "set",
                "TEST_KEY",
                "--stdin",
                *connection,
                stdin="provider-secret-fixture\n",
            )
            await cli(
                "runs",
                "reconcile",
                failed["id"],
                "--note",
                "Missing credential; no external action occurred",
                *connection,
            )
            recovered = await cli(
                "runs",
                "recover",
                failed["id"],
                "--request-id",
                "recovery",
                "--text",
                "Continue after configuring credential",
                *connection,
            )
            done = await terminal(recovered["id"])
            assert done["status"] == "completed"
            assert done["output"] == "Saved through the real application boundary"
            assert (await http.get("/")).status_code == 404
        inspected = await cli("runs", "show", done["id"], *connection)
        assert inspected["checkpoint_id"] == done["checkpoint_id"]
    async with listening(
        create_app(data_root=root, workspace=tmp_path / "workspace", models=models, console=False)
    ) as url:
        connection = ["--url", url, "--token-file", str(root / "operator.token")]
        retained = await cli("runs", "show", done["id"], *connection)
        assert retained["output"] == done["output"]
        retried = await cli(
            "runs",
            "recover",
            failed["id"],
            "--request-id",
            "recovery",
            "--text",
            "Continue after configuring credential",
            *connection,
        )
        assert retried["id"] == done["id"]
