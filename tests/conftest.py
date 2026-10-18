import asyncio

import pytest

from a13n_claw.bootstrap import initialize


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
    root = tmp_path / "claw"
    monkeypatch.setenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", "0")
    asyncio.run(initialize(root, workspace))
    monkeypatch.setattr(app, "CONSOLE_DIRECTORY", directory)
    monkeypatch.setenv("CLAW_ROOT", str(root))
    return directory
