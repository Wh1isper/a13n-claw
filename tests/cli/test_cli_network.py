"""Real CLI/server subprocesses and production OpenAI client against a loopback provider."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse


def test_operator_cli_does_not_import_server_runtime(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import a13n_claw.cli; "
            "assert 'a13n_harness_ui.app' not in sys.modules; "
            "assert 'lark_oapi' not in sys.modules",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=True)
    else:
        process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@contextmanager
def provider():
    app = FastAPI()
    calls = []

    @app.post("/v1/chat/completions")
    async def completion(request: Request):
        body = await request.json()
        calls.append(body)
        assert request.headers["authorization"] == "Bearer loopback-fixture-key"
        assert body["stream"] is True

        async def events():
            for delta, finish in (
                ({"role": "assistant", "content": "Loopback provider reply"}, None),
                ({}, "stop"),
            ):
                chunk = {
                    "id": "chatcmpl-fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                yield "data: " + json.dumps(chunk) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.02)
        yield f"http://127.0.0.1:{port}/v1", calls
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()


def test_cli_provider_request_saved_history_and_restart(tmp_path):
    root, workspace = tmp_path / "claw", tmp_path / "work"
    workspace.mkdir()
    environment = {
        **os.environ,
        "A13N_OFFICIAL_MODELS_AUTO_UPDATE": "0",
        "CLAW_MODEL_API_KEY": "loopback-fixture-key",
    }
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    def cli(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "a13n_claw", "--root", str(root), "--url", base, *arguments],
            env=environment,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    @contextmanager
    def running():
        with (tmp_path / "server.log").open("a+") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "a13n_claw",
                    "serve",
                    "--root",
                    str(root),
                    "--port",
                    str(port),
                    "--no-console",
                ],
                env=environment,
                cwd=tmp_path,
                stdout=log,
                stderr=log,
            )
            try:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        response = httpx.get(base + "/api/status", timeout=1)
                        if response.status_code == 401:
                            break
                    except httpx.HTTPError:
                        pass
                    if process.poll() is not None or time.monotonic() >= deadline:
                        log.seek(0)
                        raise AssertionError(log.read())
                    time.sleep(0.05)
                yield
            finally:
                stop_process(process)

    with provider() as (provider_url, calls):
        cli(
            "init",
            "--workspace",
            str(workspace),
            "--model",
            "openai-chat:gpt-4o",
            "--model-base-url",
            provider_url,
        )
        with running():
            main = cli("status")["instance"]["main_thread_id"]
            receipt = cli("threads", "send", main, "--text", "Human loopback input")["receipt_id"]
            deadline = time.monotonic() + 20
            while True:
                operation = cli("operations", "show", receipt)
                if operation["status"] in {"completed", "failed", "cancelled", "suspended"}:
                    break
                assert time.monotonic() < deadline
                time.sleep(0.05)
            assert operation["status"] == "completed", operation
            assert "Loopback provider reply" in json.dumps(cli("threads", "history", main))
            assert len(calls) == 1
            candidate = tmp_path / "schedule.json"
            candidate.write_text(
                json.dumps(
                    {
                        "name": "Periodic workspace check",
                        "prompt": "Scheduled loopback input",
                        "project_id": "project-claw-main",
                        "notification": "never",
                        "timing": {"kind": "interval", "seconds": 86400},
                    }
                )
            )
            catalog = cli(
                "schedules", "put", "check", "--file", str(candidate), "--expected-digest", "new"
            )
            assert "check" in catalog["catalog"]["schedules"]
            occurrence = cli("schedules", "trigger", "check", "--request-key", "cli-fire")
            repeated = cli("schedules", "trigger", "check", "--request-key", "cli-fire")
            assert occurrence["id"] == repeated["id"]
            deadline = time.monotonic() + 20
            while True:
                occurrence = cli("occurrences", "show", occurrence["id"])
                if occurrence["status"] == "completed":
                    break
                assert time.monotonic() < deadline, occurrence
                time.sleep(0.05)
            assert occurrence["result"] == "Loopback provider reply"
            assert len(calls) == 2
        with running():
            assert cli("status")["instance"]["main_thread_id"] == main
            assert "Loopback provider reply" in json.dumps(cli("threads", "history", main))
            assert cli("occurrences", "show", occurrence["id"])["status"] == "completed"
            assert len(cli("occurrences", "list", "--schedule", "check")) == 1
            assert len(calls) == 2
