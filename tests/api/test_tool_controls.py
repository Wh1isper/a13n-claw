"""Retained native shell controls through authenticated Claw HTTP boundaries."""

import asyncio
import sys

import httpx
import pytest
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from pydantic_ai.messages import ModelResponse, ToolCallPart

from a13n_claw.app import create_app
from a13n_claw.configuration.bootstrap import initialize
from tests.helpers import streaming_model


@pytest.mark.anyio
async def test_api_cancels_exact_retained_tool_after_run_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", "0")
    ready = tmp_path / "work" / "shell-ready"
    shell_prefix = "& " if sys.platform == "win32" else ""

    async def model(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "shell_exec",
                    {
                        "command": (
                            f'{shell_prefix}"{sys.executable}" -c "import pathlib, time; '
                            "pathlib.Path('shell-ready').touch(); time.sleep(60)\""
                        ),
                        "cwd": str(ready.parent),
                    },
                )
            ]
        )

    async def resolve(self, model_id, *, thread_id):
        return streaming_model(model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    root, workspace = tmp_path / "claw", tmp_path / "work"
    workspace.mkdir()
    await initialize(root, workspace, model_route="openai:gpt-4o")
    app = create_app(root=root, console=False)
    token = (root / "operator.token").read_text().strip()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://claw",
            headers={"Authorization": f"Bearer {token}"},
        ) as client,
    ):
        created = await client.post(
            "/api/threads", json={"defaults": {"project_id": "project-claw-main"}}
        )
        assert created.status_code == 201, created.text
        thread = created.json()["thread_id"]
        other = (await client.post("/api/threads", json={})).json()["thread_id"]
        submitted = await client.post(f"/api/threads/{thread}/inputs", json={"text": "Start shell"})
        assert submitted.status_code == 202, submitted.text
        receipt = submitted.json()["receipt_id"]
        path = f"/api/threads/{thread}/tool-executions"
        async with asyncio.timeout(20):
            while True:
                executions = (await client.get(path)).json()
                if executions and executions[0]["status"] == "running" and ready.exists():
                    break
                await asyncio.sleep(0.05)
        execution_id = executions[0]["execution_id"]
        assert executions[0]["tool_name"] == "shell_exec"
        assert (await client.get(path + "?offset=1&limit=1")).json() == []
        await client.post(f"/api/operations/{receipt}/cancel")
        stopped = await app.state.service.host.wait_root_operation(receipt, timeout_seconds=10)
        assert stopped.status == "cancelled"
        assert (await client.get(path)).json()[0]["status"] == "running"
        wrong = await client.post(f"/api/threads/{other}/tool-executions/{execution_id}/cancel")
        assert wrong.status_code == 409, wrong.text
        assert (await client.get(path)).json()[0]["status"] == "running"
        cancelled = await client.post(path + f"/{execution_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        async with asyncio.timeout(15):
            while (await client.get(path)).json()[0]["status"] in {"queued", "running"}:
                await asyncio.sleep(0.05)
        assert (await client.get(path)).json()[0]["status"] == "cancelled"
