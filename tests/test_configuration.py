import pytest
from pydantic import ValidationError

from a13n_claw.domain import (
    ClawError,
    InputRequest,
    MCPDefinition,
    ProfileDefinition,
    ResourceChange,
    SkillDefinition,
)
from a13n_claw.storage import Store


@pytest.mark.parametrize(
    "agent",
    [
        {"model": "openai:uncontrolled"},
        {
            "capabilities": [
                {"MCP": {"url": "https://example.com/mcp", "authorization_token": "secret"}}
            ]
        },
        {"model_settings": {"extra_headers": {"Authorization": "secret"}}},
        {"output_schema": {"type": "object"}},
        {"unrecognized": True},
    ],
)
def test_profile_cannot_embed_infrastructure(agent):
    with pytest.raises(ValidationError):
        ProfileDefinition(model_id="model", environment_id="local", agent=agent)


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", "SKILL.md", "a/../../b", "a\\b", "a:b", "a//b"]
)
def test_skill_paths_cannot_escape_snapshot(name):
    with pytest.raises(ValidationError):
        SkillDefinition(name="test", description="Test", instructions="Read", files={name: "bad"})


def test_mcp_only_accepts_secret_references():
    with pytest.raises(ValidationError):
        MCPDefinition(url="https://token@example.com/mcp")
    with pytest.raises(ValidationError):
        MCPDefinition(url="https://example.com/mcp", header_env={"Authorization": "Bearer secret"})
    with pytest.raises(ValidationError):
        MCPDefinition(url="https://example.com/mcp", header_env={"X-A": "KEY", "x-a": "KEY"})
    assert MCPDefinition(url="https://example.com/mcp", header_env={"Authorization": "MCP_AUTH"})


def test_configuration_import_is_atomic_and_version_checked(runtime_store: Store):
    before = runtime_store.resources("operator")
    changes = [
        ResourceChange(kind="environment", id="new", expected_version=0, content={"kind": "local"}),
        ResourceChange(
            kind="environment", id="local", expected_version=0, content={"kind": "local"}
        ),
    ]
    with pytest.raises(ClawError, match="Resource changed"):
        runtime_store.import_resources("operator", changes)
    assert runtime_store.resources("operator") == before
    imported = runtime_store.import_resources("operator", [changes[0]])
    assert imported[0].version == 1


def test_default_change_and_skill_edit_do_not_rewrite_captured_work(runtime_store: Store):
    store = runtime_store
    store.save_resource("operator", "defaults", "instance", {"profile_id": "default"}, 0)
    skill = store.save_resource(
        "operator",
        "skill",
        "guide",
        {
            "name": "guide",
            "description": "Guide",
            "instructions": "original",
        },
        0,
    )
    store.save_resource(
        "operator",
        "profile",
        "default",
        {
            "model_id": "model",
            "environment_id": "local",
            "skills": ["guide"],
        },
        1,
    )
    thread = store.create_thread("operator", "Uses default")
    receipt = store.admit(
        "operator", thread.id, InputRequest(request_id="r1", text="Run"), "/shared"
    )
    store.save_resource(
        "operator",
        "skill",
        "guide",
        {
            **skill.content,
            "instructions": "changed",
        },
        skill.version,
    )
    store.save_resource("operator", "defaults", "instance", {"profile_id": None}, 1)
    assert store.thread("operator", thread.id).profile_id == "default"
    assert (
        store.run("operator", receipt.run_id).composition.skills[0].content["instructions"]
        == "original"
    )
    with pytest.raises(ClawError, match="Select a Profile"):
        store.create_thread("operator", "No default")
