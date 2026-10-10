import asyncio
import os
from pathlib import Path

import pytest
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.context import AgentContext
from a13n_harness.environment import EnvironmentError, EnvironmentMount
from pydantic_ai import RunContext, Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from a13n_claw.domain import ClawError, InputRequest
from a13n_claw.environments import EnvironmentManager
from a13n_claw.storage import Store


def admitted(store: Store, workspace: Path, thread_id: str | None = None, request: str = "first"):
    thread_id = thread_id or store.create_thread("operator", "Workspace", "default").id
    receipt = store.admit(
        "operator", thread_id, InputRequest(request_id=request, text="work"), str(workspace)
    )
    return store.claim(receipt.run_id, request, str(workspace))


async def exercise_mount(mount: EnvironmentMount, *, write: bool):
    async def probe(ctx: RunContext[AgentContext]) -> str:
        value = await ctx.deps.environment.files.read_text("/workspace/source.txt")
        assert value.text == "shared content"
        if write:
            await ctx.deps.environment.files.write_text(
                "/workspace/result.txt", "saved", mode="upsert"
            )
        else:
            with pytest.raises(EnvironmentError):
                await ctx.deps.environment.files.write_text(
                    "/workspace/result.txt", "no", mode="upsert"
                )
        return "verified"

    async def model(messages, info):
        if any(
            isinstance(message, ModelRequest)
            and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        ):
            yield "done"
        else:
            yield {0: DeltaToolCall(name="probe", json_args="{}", tool_call_id="probe-call")}

    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(Capability(id="probe", tools=[Tool(probe)]),),
    )
    result = await executable.run("test", environment=mount)
    assert result.status == "completed", result.failure


@pytest.mark.anyio
async def test_local_fresh_access_read_policy_and_shared_workspace(
    runtime_store: Store, tmp_path: Path
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.txt").write_text("shared content")
    run = admitted(runtime_store, workspace)
    manager = EnvironmentManager(runtime_store)
    async with manager.acquire(run) as mount:
        assert mount.environment.recover_on_unavailable is False
        await exercise_mount(mount, write=False)
        target = runtime_store.targets("operator", run.thread_id)[0]
        with pytest.raises(ClawError, match="active target use"):
            await manager.manage("operator", target.id, "remove")
    assert not (workspace / "result.txt").exists()
    assert (await manager.manage("operator", target.id, "remove")).status == "removed"
    assert (workspace / "source.txt").exists()


@pytest.mark.anyio
async def test_local_write_and_model_change_reuse_generation(runtime_store: Store, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.txt").write_text("shared content")
    runtime_store.save_resource(
        "operator", "environment", "local", {"kind": "local", "files": "write"}, 1
    )
    first = admitted(runtime_store, workspace)
    manager = EnvironmentManager(runtime_store)
    async with manager.acquire(first) as mount:
        await exercise_mount(mount, write=True)
    original = runtime_store.targets("operator", first.thread_id)[0]
    runtime_store.finish_without_checkpoint(first.id, "first", status="cancelled", error="test")
    runtime_store.save_resource(
        "operator",
        "model",
        "model",
        {
            "provider": "openai",
            "model_name": "new-model",
            "credential_env": "KEY",
        },
        1,
    )
    second = admitted(runtime_store, workspace, first.thread_id, "second")
    async with EnvironmentManager(runtime_store).acquire(second) as second_mount:
        assert second_mount.environment is not mount.environment
        await exercise_mount(second_mount, write=True)
    assert runtime_store.targets("operator", first.thread_id)[0].id == original.id
    assert len(runtime_store.targets("operator", first.thread_id)) == 1
    assert (workspace / "result.txt").read_text() == "saved"


@pytest.mark.anyio
@pytest.mark.skipif(
    not os.environ.get("CLAW_TEST_DOCKER_IMAGE"), reason="Requires an explicit Docker test image"
)
async def test_real_docker_target_reuse_restart_stop_and_remove(
    runtime_store: Store, tmp_path: Path
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.txt").write_text("shared content")
    runtime_store.save_resource(
        "operator",
        "environment",
        "local",
        {
            "kind": "docker",
            "image": os.environ["CLAW_TEST_DOCKER_IMAGE"],
            "files": "write",
            "shell": True,
            "retention": "stop",
        },
        1,
    )
    first = admitted(runtime_store, workspace)
    manager = EnvironmentManager(runtime_store)
    target_id = None
    try:
        async with manager.acquire(first) as mount:
            target = runtime_store.targets("operator", first.thread_id)[0]
            target_id = target.id
            await exercise_mount(mount, write=True)
            physical_id = mount.environment.descriptor.backing_identity
        assert runtime_store.target("operator", target_id).status == "stopped"
        runtime_store.finish_without_checkpoint(first.id, "first", status="cancelled", error="test")
        second = admitted(runtime_store, workspace, first.thread_id, "second")
        # Reconstruct the manager and Store, as after server restart.
        manager = EnvironmentManager(Store(runtime_store.path))
        async with manager.acquire(second) as second_mount:
            assert second_mount.environment.descriptor.backing_identity == physical_id
            await exercise_mount(second_mount, write=True)
        assert len(runtime_store.targets("operator", first.thread_id)) == 1
    finally:
        # Remove only targets created by this test, including preparation failures.
        for target in runtime_store.targets("operator", first.thread_id):
            await manager.manage("operator", target.id, "remove")
    assert (workspace / "result.txt").read_text() == "saved"
    assert runtime_store.targets("operator", first.thread_id)[0].status == "removed"


@pytest.mark.anyio
async def test_cancelling_prepare_keeps_target_lock_until_mutation_settles(
    runtime_store: Store, tmp_path: Path, monkeypatch
):
    from a13n_claw.environments import create_adapter

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run = admitted(runtime_store, workspace)
    entered = asyncio.Event()
    release = asyncio.Event()
    settled = asyncio.Event()

    async def factory(target):
        environment = await create_adapter(target)
        original = environment.prepare

        async def prepare():
            entered.set()
            await release.wait()
            result = await original()
            settled.set()
            return result

        monkeypatch.setattr(environment, "prepare", prepare)
        return environment

    manager = EnvironmentManager(runtime_store, factory)

    async def use():
        async with manager.acquire(run):
            pytest.fail("Cancelled acquisition must not expose operational access")

    task = asyncio.create_task(use())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        target = runtime_store.targets("operator", run.thread_id)[0]
        task.cancel()
        await asyncio.sleep(0)
        assert manager.locks[target.id].locked()
        assert not task.done() and not settled.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    assert settled.is_set()
    assert not manager.locks[target.id].locked()
    assert runtime_store.target("operator", target.id).status == "unavailable"
    assert (await manager.manage("operator", target.id, "remove")).status == "removed"
