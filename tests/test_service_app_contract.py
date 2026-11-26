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

from a13n_claw.service_app.api import ROUTERS
from a13n_claw.service_app.plugin import ClawCapability


def test_app_openapi_contract():
    app = FastAPI()
    for router in ROUTERS:
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


def test_scheduled_filters_decisions_and_denies_late_approval():
    async def exercise():
        ctx = cast(RunContext[AgentContext], None)  # Policies depend on the selected role only.
        scheduled = ClawCapability("scheduled")
        candidates = [
            ToolDefinition(name="claw_report"),
            ToolDefinition(name="ask_user_question"),
            ToolDefinition(name="client", kind="external"),
            ToolDefinition(name="mutate", kind="unapproved"),
        ]
        allowed = await scheduled.prepare_tools(ctx, candidates)
        assert [tool.name for tool in allowed] == ["claw_report"]
        requests = DeferredToolRequests(
            approvals=[ToolCallPart("dynamic_approval", {}, "approval")],
            calls=[ToolCallPart("dynamic_defer", {}, "call")],
        )
        results = await scheduled.handle_deferred_tool_calls(ctx, requests=requests)
        assert results is not None
        assert isinstance(results.approvals["approval"], ToolDenied)
        assert requests.remaining(results) is None
        main = ClawCapability("main")
        main_results = await main.handle_deferred_tool_calls(ctx, requests=requests)
        assert main_results is not None
        assert requests.remaining(main_results) is None

    asyncio.run(exercise())


@pytest.mark.parametrize("state", ["ready", "preparing"])
def test_main_cli_context_survives_refresh_while_captured_reference_is_active(monkeypatch, state):
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from a13n_claw.service_app import plugin

    async def run():
        connector = SimpleNamespace(
            kind="lark", config={"app_id": "synthetic-app", "domain": "https://open.larksuite.com"}
        )
        generation = SimpleNamespace(
            state=state,
            domain="https://open.larksuite.com",
            credential_id="ecred_fixture",
            refresh_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        bound = SimpleNamespace(
            role="main",
            claw=SimpleNamespace(id="claw", workspace_id="ws", worker_models=[]),
            run=SimpleNamespace(
                id="run",
                environment_mounts=[
                    {
                        "name": "workspace",
                        "session_configuration": {
                            "egress": {"secrets": [{"credential_id": "ecred_fixture"}]}
                        },
                    }
                ],
            ),
        )

        class Session:
            async def execute(self, query):
                compiled = query.compile()
                assert any(value == ["ready", "preparing"] for value in compiled.params.values())
                assert "IS NOT NULL" in str(compiled)
                return SimpleNamespace(all=lambda: [(connector, generation)])

        @asynccontextmanager
        async def short_session(_storage):
            yield Session()

        async def resolve(_session, _ctx):
            return bound

        async def unread(*_args, **_kwargs):
            return []

        monkeypatch.setattr(plugin, "_runtime", SimpleNamespace(storage=object()))
        monkeypatch.setattr(plugin, "short_session", short_session)
        monkeypatch.setattr(plugin, "resolve", resolve)
        monkeypatch.setattr(plugin.inbox, "unread_previews", unread)
        request = SimpleNamespace(messages=[ModelRequest(parts=[UserPromptPart("hello")])])
        await plugin.ClawCapability("main").before_model_request(
            SimpleNamespace(run_step=2), request
        )
        assert "LARKSUITE_CLI_APP_ID=synthetic-app" in str(request.messages)
        bound.run.environment_mounts[0]["session_configuration"]["egress"]["secrets"] = []
        request = SimpleNamespace(messages=[ModelRequest(parts=[UserPromptPart("hello")])])
        await plugin.ClawCapability("main").before_model_request(
            SimpleNamespace(run_step=2), request
        )
        assert "LARKSUITE_CLI_APP_ID" not in str(request.messages)

    asyncio.run(run())


def test_unified_schedule_rule_calculates_next_occurrences():
    from datetime import UTC, datetime, timedelta

    from a13n_service.runs.schedules.schemas import CronSchedule, OnceSchedule

    from a13n_claw.service_app.schedules import schedule_rule

    current = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    once = current + timedelta(hours=3)
    assert schedule_rule(OnceSchedule(type="once", run_at=once), current) == (None, "UTC", once)
    assert schedule_rule(CronSchedule(expression="0 * * * *", timezone="UTC"), current) == (
        "0 * * * *",
        "UTC",
        current + timedelta(hours=1),
    )
