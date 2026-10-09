"""Loopback integration: real provider client, MCP transport and local shell.

No FunctionModel, mocked HTTP client, external credentials or paid service is used.
"""

import asyncio
import json
import socket
from contextlib import asynccontextmanager

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from mcp.server.mcpserver import MCPServer
from test_runtime import terminal

from a13n_claw.coordinator import Coordinator
from a13n_claw.domain import InputRequest
from a13n_claw.runtime import Runtime


@asynccontextmanager
async def listening(app):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        raise AssertionError("Test HTTP server stopped before startup")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{sock.getsockname()[1]}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)


@pytest.mark.anyio
async def test_provider_mcp_and_shell_over_real_transports(runtime_store, tmp_path):
    mcp = MCPServer("acceptance")
    calls = []
    headers = []
    requests = []

    @mcp.tool()
    def echo(text: str) -> str:
        calls.append(text)
        return "MCP accepted: " + text

    mcp_app = mcp.streamable_http_app(stateless_http=True, json_response=True)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/service", mcp_app)

    @app.middleware("http")
    async def capture_header(request, call_next):
        if request.url.path.startswith("/service/"):
            headers.append(request.headers.get("x-fixture-key"))
        return await call_next(request)

    @app.post("/v1/chat/completions")
    async def completion(request: Request):
        assert request.headers["authorization"] == "Bearer loopback-provider-key"
        body = await request.json()
        requests.append(body)
        assert body["stream"] is True
        names = [tool["function"]["name"] for tool in body["tools"]]
        index = len(requests)
        if index == 1:
            name = next(name for name in names if name.endswith("echo"))
            arguments = {"text": "loopback"}
        elif index == 2:
            assert "MCP accepted: loopback" in json.dumps(body["messages"])
            name = "shell_exec"
            assert name in names
            arguments = {"command": "echo network-shell-accepted", "cwd": "/workspace"}
        else:
            assert "network-shell-accepted" in json.dumps(body["messages"])
            name = None
            arguments = {}
        delta = (
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"call-{index}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ]
            }
            if name
            else {"content": "Provider, MCP and shell completed"}
        )

        async def stream():
            for chunk, finish in [(delta, None), ({}, "tool_calls" if name else "stop")]:
                payload = {
                    "id": f"response-{index}",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture",
                    "choices": [{"index": 0, "delta": chunk, "finish_reason": finish}],
                }
                yield f"data: {json.dumps(payload)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    store = runtime_store
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    async with listening(app) as base:
        store.save_resource(
            "operator",
            "model",
            "model",
            {
                "provider": "openai",
                "model_name": "fixture",
                "credential_env": "TEST_KEY",
                "base_url": base + "/v1",
            },
            1,
        )
        store.save_resource(
            "operator",
            "environment",
            "local",
            {"kind": "local", "shell": True},
            1,
        )
        store.save_resource(
            "operator",
            "mcp",
            "echo",
            {
                "url": base + "/service/mcp",
                "header_env": {"x-fixture-key": "MCP_KEY"},
                "allowed_tools": ["echo"],
            },
            0,
        )
        store.save_resource(
            "operator",
            "profile",
            "default",
            {"model_id": "model", "environment_id": "local", "mcp_servers": ["echo"]},
            1,
        )
        store.save_credential("operator", "TEST_KEY", "loopback-provider-key")
        store.save_credential("operator", "MCP_KEY", "loopback-mcp-key")
        thread = store.create_thread("operator", "Real transports", "default")
        receipt = store.admit(
            "operator",
            thread.id,
            InputRequest(request_id="network", text="Exercise transports"),
            str(workspace),
        )
        coordinator = Coordinator(store, str(workspace), Runtime(store, tmp_path))
        await coordinator.start()
        try:
            result = await terminal(store, receipt.run_id)
            assert result.status == "completed", result.error
            assert result.output == "Provider, MCP and shell completed"
            assert calls == ["loopback"]
            assert headers and set(headers) == {"loopback-mcp-key"}
            assert len(requests) == 3
            state = store.checkpoint(result.checkpoint_id).model_dump_json()
            assert "loopback-provider-key" not in state
            assert "loopback-mcp-key" not in state
        finally:
            await coordinator.close()
