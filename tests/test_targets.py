"""Lifecycle fault coverage without a daemon; execution uses published Harness APIs."""

import asyncio
import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from a13n_harness.environment import EnvironmentState
from a13n_harness.environment.errors import EnvironmentProviderError
from docker.errors import ImageNotFound, NotFound
from test_environments import admitted

from a13n_claw.domain import ClawError, TargetRecord, digest
from a13n_claw.environments import EnvironmentManager, target_definition
from a13n_claw.targets import DockerTarget, create_target


@pytest.fixture
def legacy_record():
    fixture = json.loads(Path(__file__).with_name("harness_08_docker.json").read_text())
    return TargetRecord(
        id="target-legacy",
        thread_id="thread-legacy",
        definition=fixture["definition"],
        generation=fixture["generation"],
        provider_state=fixture["provider_state"],
        status="ready",
        operation_id=None,
        error=None,
        updated_at="2026-10-10T00:00:00Z",
    )


@pytest.fixture
def docker_client(monkeypatch):
    client = MagicMock()
    containers = {}
    client.images.get.return_value = SimpleNamespace(id="sha256:image")

    def lookup(selector):
        if selector not in containers:
            raise NotFound("missing")
        return containers[selector]

    def create(image, **kwargs):
        container = MagicMock()
        container.id = "created-container"
        container.labels = kwargs["labels"]
        container.status = "created"
        container.start.side_effect = lambda: setattr(container, "status", "running")
        container.stop.side_effect = lambda **kw: setattr(container, "status", "exited")
        container.exec_run.return_value = SimpleNamespace(exit_code=0)

        def remove(**kw):
            containers.clear()

        container.remove.side_effect = remove
        containers[container.id] = containers[kwargs["name"]] = container
        return container

    client.containers.get.side_effect = lookup
    client.containers.create.side_effect = create
    monkeypatch.setattr("a13n_claw.targets.docker.DockerClient", lambda **kw: client)
    return client, containers


def test_published_08_generation_and_state_remain_compatible(runtime_store, legacy_record):
    runtime_store.save_resource(
        "operator",
        "environment",
        "local",
        {"kind": "docker", "image": "python:3.13-slim-bookworm", "files": "write"},
        1,
    )
    # Preserve the Linux daemon path recorded by 0.8, even on a Windows test host.
    run = admitted(runtime_store, PurePosixPath("/claw-test-workspace"))
    assert target_definition(run) == legacy_record.definition
    assert digest(target_definition(run)) == legacy_record.generation
    target = DockerTarget(legacy_record)
    connector = target.source().connector  # Actual 0.10 provider validates the old reference.
    assert connector.state == EnvironmentState.model_validate(legacy_record.provider_state)
    assert connector.environment_id == legacy_record.id


@pytest.mark.parametrize("change", ["owner", "configuration"])
def test_docker_rejects_state_from_other_owner_or_recipe(legacy_record, change):
    record = legacy_record.model_copy(deep=True)
    if change == "owner":
        record = record.model_copy(update={"id": "target-other"})
    else:
        record.definition["configuration"]["image"] = "other-image"
    with pytest.raises((ClawError, EnvironmentProviderError)):
        DockerTarget(record)


def test_docker_reconciles_lost_and_stale_ids_without_creating(legacy_record, docker_client):
    client, containers = docker_client
    target = DockerTarget(legacy_record.model_copy(update={"provider_state": None}))
    assert target._operate("prepare") == "running"
    original = target.state
    client.containers.create.reset_mock()
    for state in (None, legacy_record.provider_state):
        restored = DockerTarget(legacy_record.model_copy(update={"provider_state": state}))
        assert restored._operate("inspect") == "running"
        assert restored.state == original
    client.containers.create.assert_not_called()
    assert containers["created-container"].labels == target.labels
    assert client.close.call_count == 3


def test_docker_refuses_foreign_named_container(legacy_record, docker_client):
    client, containers = docker_client
    foreign = MagicMock(labels={"a13n.environment": "someone-else"})
    containers["a13n-" + legacy_record.id] = foreign
    target = DockerTarget(legacy_record)
    for action in ("inspect", "prepare", "stop", "remove"):
        with pytest.raises(ClawError, match="ownership"):
            target._operate(action)
    client.containers.create.assert_not_called()
    foreign.start.assert_not_called()
    foreign.stop.assert_not_called()
    foreign.remove.assert_not_called()


def test_ambiguous_create_is_inspected_not_duplicated(legacy_record, docker_client):
    client, containers = docker_client
    create = client.containers.create.side_effect

    def lost_response(*args, **kwargs):
        container = create(*args, **kwargs)
        container.exec_run.return_value = SimpleNamespace(exit_code=1)
        raise TimeoutError("creation response lost")

    client.containers.create.side_effect = lost_response
    target = DockerTarget(legacy_record.model_copy(update={"provider_state": None}))
    with pytest.raises(TimeoutError):
        target._operate("prepare")
    assert target.state is None
    restored = DockerTarget(legacy_record.model_copy(update={"provider_state": None}))
    assert restored._operate("inspect") == "stopped"
    assert restored.state is not None
    with pytest.raises(ClawError, match="initialization"):
        restored._operate("prepare")
    # Existing, incompletely initialized containers require operator action, not replay.
    assert client.containers.create.call_count == 1
    assert containers["created-container"].exec_run.call_count == 1
    assert restored._operate("remove") == "absent"
    assert restored.state is None and not containers


def test_docker_stop_remove_and_missing_image_pull(legacy_record, docker_client):
    client, containers = docker_client
    client.images.get.side_effect = ImageNotFound("missing")
    client.images.pull.return_value = SimpleNamespace(id="sha256:pulled")
    target = DockerTarget(legacy_record.model_copy(update={"provider_state": None}))
    assert target._operate("prepare") == "running"
    client.images.pull.assert_called_once_with("python:3.13-slim-bookworm")
    state = target.state
    fixed = target.source()
    container = containers["created-container"]
    assert target._operate("stop") == "stopped"
    container.stop.assert_called_once_with(timeout=10)
    assert target.state == state
    assert target._operate("remove") == "absent"
    assert target.state is None
    assert fixed.state == state  # Issued selections never follow later lifecycle changes.
    assert target._operate("remove") == "absent"
    assert client.containers.create.call_count == 1


@pytest.mark.anyio
async def test_overlapping_access_does_not_prepare_or_stop_early(
    runtime_store, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_store.save_resource(
        "operator",
        "environment",
        "local",
        {"kind": "local", "retention": "stop"},
        1,
    )
    run = admitted(runtime_store, workspace)
    calls = []

    async def factory(record):
        target = await create_target(record)
        prepare = target.prepare

        async def tracked_prepare():
            calls.append("prepare")
            await prepare()

        async def tracked_stop():
            calls.append("stop")

        monkeypatch.setattr(target, "prepare", tracked_prepare)
        monkeypatch.setattr(target, "stop", tracked_stop)
        return target

    manager = EnvironmentManager(runtime_store, factory)
    async with manager.acquire(run) as mount:
        connector = await mount.source.ensure_ready()
        first = await connector.open()
        second = await connector.open()
        try:
            assert first.execution_id != second.execution_id
        finally:
            await asyncio.gather(first.close(), second.close())
        async with manager.acquire(run):
            assert calls == ["prepare"]
        assert calls == ["prepare"]
    assert calls == ["prepare", "stop"]
