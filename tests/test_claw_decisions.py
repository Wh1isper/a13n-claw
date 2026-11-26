"""Claw Runs deny interactive decisions rather than entering a waiting state."""

import asyncio
from types import SimpleNamespace

import pytest
from a13n_harness import HarnessRunResult
from a13n_harness.capabilities.tool_review import ToolReviewPolicy, ToolRiskLevel
from a13n_harness.result import DeferredToolRequests as HarnessDeferredToolRequests
from a13n_harness.state import HarnessState
from a13n_harness.tools.metadata import HARNESS_TOOL_METADATA_KEY, HarnessToolMetadata
from a13n_harness.usage import RunUsageSummary
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.runs.dynamic_subagents import CapabilitySelection
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests, ToolDefinition, ToolDenied

from a13n_claw.service_app.main_thread import safe_revision_config
from a13n_claw.service_app.models import Claw
from a13n_claw.service_app.plugin import ClawCapability, ClawPlugin, Configuration
from a13n_claw.service_app.routes.claws import Settings, effective_settings


def test_safe_revision_denies_explicit_and_inherited_asks_and_review_fallbacks():
    config = AgentConfig(model="fixture").model_dump(mode="json")
    config["user_questions"] = True
    config["toolsets"]["files"]["tools"]["write"]["permission"] = "ask"
    config["toolsets"]["shell"]["tools"]["exec"]["permission"] = "review"
    config["reviewer"] = {
        "model": "fixture",
        "on_error": "allow",
        "on_flagged": "approval_required",
        "rules": {"shell.exec": {"on_flagged": "approval_required"}},
    }
    safe = safe_revision_config(config, summarize=True)
    assert safe.user_questions is False and safe.summarize is True
    assert safe.toolsets["files"].tools["write"].permission == "deny"
    assert safe.toolsets["agents"].tools["archive_agent"].permission == "deny"
    assert safe.toolsets["shell"].tools["exec"].permission == "review"
    assert safe.reviewer.on_flagged == safe.reviewer.on_error == "deny"
    assert safe.reviewer.rules["shell.exec"].on_flagged == "deny"


def test_enable_shell_review_without_authored_reviewer():
    config = AgentConfig(model="fixture").model_dump(mode="json")
    safe = safe_revision_config(
        config, summarize=True, shell_review={"enabled": True, "model": "review-model"}
    )
    assert safe.toolsets["shell"].tools["exec"].permission == "review"
    assert safe.reviewer is not None
    assert safe.reviewer.model == "review-model"
    assert safe.reviewer.on_flagged == safe.reviewer.on_error == "deny"


def test_shell_review_threshold_overrides_authored_shell_rule_only():
    config = AgentConfig(model="fixture").model_dump(mode="json")
    config["reviewer"] = {
        "model": "old-model",
        "rules": {
            "environment.shell_exec": {"risk_threshold": "extra_high"},
            "files.write": {"risk_threshold": "medium"},
        },
    }
    safe = safe_revision_config(
        config,
        summarize=True,
        shell_review={"enabled": True, "model": "review-model", "risk_threshold": "low"},
    )
    assert safe.reviewer is not None
    assert safe.reviewer.rules["environment.shell_exec"].risk_threshold == "low"
    policy = ToolReviewPolicy.model_validate(
        safe.reviewer.model_dump(include={"risk_threshold", "on_flagged", "rules"})
    )
    assert policy.decision_for("environment.shell_exec", ToolRiskLevel.LOW) == "deny"
    assert safe.reviewer.rules["files.write"].risk_threshold == "medium"
    assert safe.reviewer.on_flagged == safe.reviewer.on_error == "deny"


@pytest.mark.parametrize("permission", ["ask", "deny"])
def test_shell_review_does_not_grant_denied_execution(permission):
    config = AgentConfig(model="fixture").model_dump(mode="json")
    config["toolsets"]["shell"]["tools"]["exec"]["permission"] = permission
    safe = safe_revision_config(
        config, summarize=True, shell_review={"enabled": True, "model": "review-model"}
    )
    assert safe.toolsets["shell"].tools["exec"].permission == "deny"


@pytest.mark.parametrize("role", ["main", "scheduled", "guard"])
def test_claw_filters_and_denies_deferred_tools(role):
    async def run():
        capability = ClawCapability(role)
        tools = [
            SimpleNamespace(name="safe", kind="function"),
            SimpleNamespace(name="ask_user_question", kind="function"),
            SimpleNamespace(name="approval", kind="unapproved"),
            SimpleNamespace(name="external", kind="external"),
        ]
        assert await capability.prepare_tools(None, tools) == tools[:1]
        requests = DeferredToolRequests(
            approvals=[ToolCallPart("approval", tool_call_id="approval")],
            calls=[ToolCallPart("external", tool_call_id="external")],
        )
        result = await capability.handle_deferred_tool_calls(None, requests=requests)
        assert isinstance(result.approvals["approval"], ToolDenied)
        assert "denied" in str(result.calls["external"]).lower()

    asyncio.run(run())


def test_legacy_settings_preserve_new_fields_and_explicit_values_replace_them():
    claw = Claw(
        worker_agents=[{"agent_id": "agent_123", "alias": "worker"}],
        shell_review={"enabled": True, "model": "review-model", "risk_threshold": "low"},
    )
    legacy = effective_settings(Settings(name="renamed"), claw)
    assert [item.alias for item in legacy.worker_agents] == ["worker"]
    assert legacy.shell_review.enabled and legacy.shell_review.risk_threshold == "low"
    explicit = effective_settings(
        Settings(name="renamed", worker_agents=[], shell_review={"enabled": False}), claw
    )
    assert explicit.worker_agents == []
    assert not explicit.shell_review.enabled


def test_legacy_worker_tools_and_plaintext_reply_are_not_exposed():
    from a13n_claw.service_app.plugin import main_tools

    assert {
        "claw_assign",
        "claw_steer",
        "claw_workers",
        "claw_connector_reply",
        "claw_reply_connectors",
    }.isdisjoint(main_tools.tools)
    assert not ClawCapability("worker").get_toolset().tools
    assert "claw_report" in ClawCapability("scheduled").get_toolset().tools


def test_dynamic_spawn_cannot_remove_claw_guard():
    capability = ClawCapability("main")
    from a13n_harness.tools.metadata import ToolOutputPolicy

    metadata = HarnessToolMetadata(
        tool_id="subagents.spawn",
        effects=frozenset({"write"}),
        credential_audiences=(),
        idempotency="provider_key",
        output_policy=ToolOutputPolicy(
            max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate"
        ),
    )
    tool = ToolDefinition(name="spawn_subagent", metadata={HARNESS_TOOL_METADATA_KEY: metadata})
    call = ToolCallPart("spawn_subagent", tool_call_id="call")

    async def invoke(selection):
        args = {"selection": selection}
        return await capability.before_tool_execute(None, call=call, tool_def=tool, args=args)

    with pytest.raises(ModelRetry, match="retain the Claw guard"):
        asyncio.run(invoke(CapabilitySelection(plugins=())))
    with pytest.raises(ModelRetry, match="retain the Claw guard"):
        asyncio.run(invoke({"plugins": ["other"]}))
    for selection in (None, CapabilitySelection(), CapabilitySelection(plugins=("claw",))):
        assert asyncio.run(invoke(selection))["selection"] == selection


def test_native_spawn_tool_hook_rejects_guard_removal_before_callback():
    from a13n_service.runs.dynamic_subagents import SpawnCapability
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    invoked = []

    async def spawn(*args):
        invoked.append(args)
        raise AssertionError("guard was bypassed")

    model_calls = []

    def model(messages, info):
        model_calls.append(messages)
        if len(model_calls) > 1:
            return ModelResponse(parts=[TextPart("Denied")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "spawn_subagent",
                    {
                        "name": "child",
                        "instructions": "Work",
                        "prompt": "Work",
                        "selection": {"plugins": []},
                    },
                    "spawn-call",
                )
            ]
        )

    native = SpawnCapability(spawn, AgentConfig(model="fixture"))
    agent = Agent(
        FunctionModel(model),
        toolsets=[native.get_toolset()],
        capabilities=[ClawCapability("guard")],
    )
    result = asyncio.run(agent.run("test"))
    assert result.output == "Denied"
    assert not invoked


def test_unbound_inherited_main_plugin_hides_main_guidance_and_tools(monkeypatch):
    from a13n_claw.service_app import plugin

    monkeypatch.setattr(plugin, "_runtime", None)
    capability = ClawCapability("main")
    assert capability.get_instructions() == ""
    tools = [
        SimpleNamespace(name="claw_unread", kind="function"),
        SimpleNamespace(name="ordinary_tool", kind="function"),
    ]
    assert asyncio.run(capability.prepare_tools(None, tools)) == tools[1:]


@pytest.mark.parametrize("role", ["main", "scheduled", "guard"])
def test_claw_converts_unexpected_suspension_to_failure(role):
    async def run():
        plugin = ClawPlugin("claw", Configuration(role=role))
        suspended = HarnessRunResult(
            thread_id="thread_abc",
            run_id="run",
            status="suspended",
            output=None,
            state=HarnessState(schema_version="1", thread_id="thread_abc"),
            usage=RunUsageSummary(),
            suspend_reason="deferred",
            deferred=HarnessDeferredToolRequests(),
        )

        async def next_run(_exchange):
            return suspended

        result = await plugin.wrap_run(SimpleNamespace(), next_run)
        assert result.status == "failed"
        assert result.failure.code == "claw_decision_unavailable"
        assert result.deferred is None

    asyncio.run(run())


def test_explicit_worker_revision_publishes_sanitized_source_not_default(monkeypatch):
    from a13n_claw.service_app import main_thread

    async def run():
        source = AgentConfig(model="explicit-model").model_dump(mode="json")
        source["user_questions"] = True
        revision = SimpleNamespace(id="explicit", config=source)
        agent = SimpleNamespace(id="agent", builtin=False)
        actor = SimpleNamespace(id="operator")
        scope = SimpleNamespace(workspace_id="workspace")
        observed = []

        async def publish(*_args, **kwargs):
            observed.append(kwargs["mutate_config"](AgentConfig(model="different-default")))
            return SimpleNamespace(id="published")

        monkeypatch.setattr(main_thread.app_agents, "publish_derived_revision", publish)
        result = await main_thread.prepare_worker_revision(
            None, None, actor, scope, agent, revision, {}
        )
        assert result == "published"
        assert observed[0].model == "explicit-model"
        assert observed[0].user_questions is False

    asyncio.run(run())
