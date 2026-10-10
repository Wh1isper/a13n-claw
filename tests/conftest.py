from pathlib import Path

import pytest

from a13n_claw.storage import Store


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def runtime_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "runtime.sqlite3")
    store.bootstrap("operator-token-hash")
    store.save_resource(
        "operator",
        "model",
        "model",
        {
            "provider": "openai",
            "model_name": "test",
            "credential_env": "TEST_KEY",
        },
        0,
    )
    store.save_resource("operator", "environment", "local", {"kind": "local"}, 0)
    store.save_resource(
        "operator",
        "profile",
        "default",
        {
            "model_id": "model",
            "environment_id": "local",
        },
        0,
    )
    return store


@pytest.fixture
def console(tmp_path, monkeypatch):
    from a13n_claw import app

    directory = tmp_path / "console"
    directory.mkdir()
    (directory / "index.html").write_text("<!doctype html><title>Console</title>")
    (directory / "app.js").write_text("console.log('preview');")
    (tmp_path / "private.txt").write_text("not public")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(app, "CONSOLE_DIRECTORY", directory)
    monkeypatch.setenv("CLAW_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("CLAW_WORKSPACE", str(workspace))
    return directory
