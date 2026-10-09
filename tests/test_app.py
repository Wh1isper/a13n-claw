from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from a13n_claw import app
from a13n_claw.cli import main


@pytest.fixture
def console(tmp_path, monkeypatch):
    directory = tmp_path / "console"
    directory.mkdir()
    (directory / "index.html").write_text("<!doctype html><title>Console</title>")
    (directory / "app.js").write_text("console.log('preview');")
    (tmp_path / "private.txt").write_text("not public")
    monkeypatch.setattr(app, "CONSOLE_DIRECTORY", directory)
    return directory


def test_static_console(console):
    with TestClient(app.create_app()) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Console" in response.text
        assert client.head("/").status_code == 200
        assert client.head("/").content == b""
        assert client.get("/app.js").text == "console.log('preview');"
        assert client.post("/").status_code == 405


@pytest.mark.parametrize(
    "path",
    [
        "/api/threads",
        "/healthz",
        "/docs",
        "/openapi.json",
        "/missing",
        "/assets/missing.js",
        "/%2e%2e/private.txt",
        "/..%2fprivate.txt",
    ],
)
def test_unknown_paths_are_not_html_or_private_files(console, path):
    with TestClient(app.create_app()) as client:
        response = client.get(path)
        assert response.status_code == 404
        assert "Console" not in response.text
        assert "not public" not in response.text


def test_missing_bundle_is_actionable(console):
    (console / "index.html").unlink()
    with pytest.raises(RuntimeError, match="make console-build"):
        app.create_app()


def test_serve_defaults(console):
    with patch("uvicorn.run") as run:
        main(["serve"])
    assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 8080}
    assert run.call_args.args[0].openapi_url is None


def test_serve_bind_options(console):
    with patch("uvicorn.run") as run:
        main(["serve", "--host", "0.0.0.0", "--port", "9000"])
    assert run.call_args.kwargs == {"host": "0.0.0.0", "port": 9000}


def test_serve_missing_bundle_reports_cli_error(console, capsys):
    (console / "index.html").unlink()
    with pytest.raises(SystemExit) as result:
        main(["serve"])
    assert result.value.code == 2
    assert "make console-build" in capsys.readouterr().err
