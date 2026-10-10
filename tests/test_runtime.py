import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from a13n_claw.coordinator import Coordinator
from a13n_claw.domain import InputRequest, ModelDefinition
from a13n_claw.runtime import Runtime, provider_model
from a13n_claw.storage import Store


async def terminal(store: Store, run_id: str):
    async with asyncio.timeout(10):
        while True:
            run = store.run("operator", run_id)
            if run.status in {"completed", "failed", "cancelled", "waiting"}:
                return run
            await asyncio.sleep(0.01)


@pytest.mark.anyio
@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
async def test_provider_clients_construct_and_close_without_network(provider):
    definition = ModelDefinition(provider=provider, model_name="test", credential_env="KEY")
    async with provider_model(definition, "not-a-live-key") as model:
        assert model.model_name == "test"


@pytest.mark.anyio
async def test_production_composition_reads_captured_skill_and_retains_no_credential(
    runtime_store: Store,
    tmp_path: Path,
):
    store = runtime_store
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "data"
    data_root.mkdir()
    store.save_credential("operator", "TEST_KEY", "first-secret-value")
    skill = store.save_resource(
        "operator",
        "skill",
        "guide",
        {
            "name": "guide",
            "description": "A guide",
            "instructions": "retained instructions",
            "files": {"references/note.txt": "supporting evidence"},
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
    thread = store.create_thread("operator", "Composition", "default")
    receipt = store.admit(
        "operator",
        thread.id,
        InputRequest(request_id="first", text="Read the guide"),
        str(workspace),
    )
    store.save_resource(
        "operator", "skill", "guide", {**skill.content, "instructions": "new instructions"}, 1
    )
    store.save_credential("operator", "TEST_KEY", "current-secret-value")
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        assert "view" in {tool.name for tool in info.function_tools}
        assert "shell_exec" not in {tool.name for tool in info.function_tools}
        if calls == 1:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": "/claw-skills/guide/SKILL.md"}),
                    tool_call_id="read-skill",
                )
            }
        else:
            returns = [
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            assert "retained instructions" in str(returns[-1].content)
            assert "new instructions" not in str(returns[-1].content)
            yield "Read captured skill"

    @asynccontextmanager
    async def models(definition, credential):
        assert credential == "current-secret-value"
        yield FunctionModel(stream_function=model)

    coordinator = Coordinator(store, str(workspace), Runtime(store, data_root, models=models))
    await coordinator.start()
    try:
        result = await terminal(store, receipt.run_id)
        assert result.status == "completed", result.error
        assert result.output == "Read captured skill"
        assert result.checkpoint_id is not None
        assert "secret-value" not in store.checkpoint(result.checkpoint_id).model_dump_json()
        assert "secret-value" not in result.composition.model_dump_json()
        assert "secret-value" not in str(store.resources("operator"))
        assert "secret-value" not in str(store.credential_status("operator"))
    finally:
        await coordinator.close()
    assert not list(data_root.glob("claw-skills-*"))


@pytest.mark.anyio
async def test_revoked_current_credential_prevents_following_tool(
    runtime_store: Store, tmp_path: Path
):
    store = runtime_store
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store.save_credential("operator", "TEST_KEY", "current-key")
    thread = store.create_thread("operator", "Revocation", "default")
    receipt = store.admit(
        "operator", thread.id, InputRequest(request_id="first", text="Read"), str(workspace)
    )

    async def model(messages, info):
        store.save_credential("operator", "TEST_KEY", "rotated-key")
        yield {
            0: DeltaToolCall(
                name="view", json_args='{"file_path":"/workspace/source.txt"}', tool_call_id="read"
            )
        }

    @asynccontextmanager
    async def models(definition, credential):
        yield FunctionModel(stream_function=model)

    coordinator = Coordinator(store, str(workspace), Runtime(store, tmp_path, models=models))
    await coordinator.start()
    try:
        result = await terminal(store, receipt.run_id)
        assert result.status == "failed"
        assert result.checkpoint_id is not None
        state = store.checkpoint(result.checkpoint_id)
        assert not any(
            isinstance(part, ToolReturnPart)
            for message in state.message_history
            for part in message.parts
        )
    finally:
        await coordinator.close()
