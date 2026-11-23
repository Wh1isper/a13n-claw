"""Exercise the installed Service contract without network credentials."""

import asyncio
import sys
from typing import cast

import pytest
from a13n_harness import AgentContext
from a13n_service.app import build_app
from a13n_service.settings import Settings
from fastapi import FastAPI
from pydantic_ai import RunContext
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests, ToolDefinition, ToolDenied

from a13n_claw.service_app.api import router
from a13n_claw.service_app.plugin import ClawCapability


def test_app_openapi_contract():
    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]
    assert "/api/v1/apps/a13n.claw/claws" in paths
    assert "/api/v1/apps/a13n.claw/claws/{claw_id}/deliveries/{delivery_id}" in paths


@pytest.mark.skipif(sys.platform != "linux", reason="Service runtime has Linux providers")
def test_routes_compose_with_native_service():
    # Construction validates operation names and all OpenAPI schemas and mounts the native API.
    app = build_app(role="control", settings=Settings())
    paths = app.openapi()["paths"]
    assert "/api/v1/schedules" in paths
    assert "/api/v1/apps/a13n.claw/claws" in paths
    assert "/api/v1/apps/a13n.claw/claws/{claw_id}/deliveries/{delivery_id}" in paths


def test_worker_filters_decisions_and_denies_late_approval():
    async def exercise():
        ctx = cast(RunContext[AgentContext], None)  # Policies depend on the selected role only.
        worker = ClawCapability("worker")
        candidates = [
            ToolDefinition(name="claw_report"),
            ToolDefinition(name="ask_user_question"),
            ToolDefinition(name="client", kind="external"),
            ToolDefinition(name="mutate", kind="unapproved"),
        ]
        allowed = await worker.prepare_tools(ctx, candidates)
        assert [tool.name for tool in allowed] == ["claw_report"]
        requests = DeferredToolRequests(
            approvals=[ToolCallPart("dynamic_approval", {}, "approval")],
            calls=[ToolCallPart("dynamic_defer", {}, "call")],
        )
        results = await worker.handle_deferred_tool_calls(ctx, requests=requests)
        assert results is not None
        assert isinstance(results.approvals["approval"], ToolDenied)
        assert requests.remaining(results) is None
        main = ClawCapability("main")
        main_results = await main.handle_deferred_tool_calls(ctx, requests=requests)
        assert main_results is not None
        assert requests.remaining(main_results) is None

    asyncio.run(exercise())
