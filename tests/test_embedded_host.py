"""Acceptance against the published Harness UI package with isolated instance roots."""

from pathlib import Path

import pytest
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import NewThreadDefaults
from pydantic_ai.models.test import TestModel

from a13n_claw.bootstrap import initialize
from a13n_claw.capability import messaging_integrations
from a13n_claw.host import open_host
from a13n_claw.settings import ClawSettings, instance_root, read_settings


@pytest.fixture
def isolated_host(monkeypatch):
    monkeypatch.setenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", "0")

    async def resolve(self, model_id, *, thread_id):
        return TestModel(custom_output_text="Embedded reply", call_tools=[])

    # Test-only provider replacement. Production uses the native configured resolver.
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)


@pytest.mark.anyio
async def test_native_lifecycle_configuration_and_saved_continuation(
    tmp_path, monkeypatch, isolated_host
):
    root = tmp_path / "claw"
    workspace = tmp_path / "work"
    workspace.mkdir()
    personal = tmp_path / "personal-data"
    monkeypatch.setenv("A13N_HARNESS_UI_DATA_ROOT", str(personal))
    settings = await initialize(root, workspace, model_route="openai:gpt-4o")
    assert read_settings(root) == settings
    assert not personal.exists()
    source = root / "host/projects/main.yaml"
    original = source.read_bytes()
    async with open_host(root, settings, integrations=messaging_integrations()) as host:
        main = await host.create_thread(
            defaults=NewThreadDefaults(project_id=settings.main_project_id),
            coordinator=True,
            title="Main",
        )
        worker = await host.create_thread(
            defaults=NewThreadDefaults(project_id=settings.main_project_id),
            coordinator_thread_id=main.thread_id,
        )
        assert worker.coordinator_thread_id == main.thread_id
        receipt = await host.submit_thread(thread_id=main.thread_id, prompt="Hello from IM")
        operation = await host.wait_root_operation(receipt.receipt_id, timeout_seconds=20)
        assert operation.status == "completed", operation
        detail = await host.get_thread(main.thread_id)
        assert detail.continuation_id is not None
        await host.mutate_configuration(
            relative_path="projects/main.yaml",
            request=ResourceMutationRequest(
                content=original.decode().replace("Claw Main", "Renamed")
            ),
        )
        assert any(p.name == "Renamed" for p in await host.projects())
    async with open_host(root, settings, integrations=messaging_integrations()) as reopened:
        detail2 = await reopened.get_thread(main.thread_id)
        assert detail2.continuation_id == detail.continuation_id
        assert (
            await reopened.get_thread(worker.thread_id)
        ).thread.coordinator_thread_id == main.thread_id
        assert await reopened.active_root_operation(main.thread_id) is None
        with pytest.raises(HarnessUiError):
            await reopened.get_root_operation(receipt.receipt_id)
    assert not personal.exists()
    with pytest.raises(ValueError, match="already exist"):
        await initialize(root, workspace)
    assert b"Renamed" in source.read_bytes()


@pytest.mark.anyio
async def test_fresh_setup_rejects_legacy_data_without_modifying_it(tmp_path, isolated_host):
    root = tmp_path / "claw"
    root.mkdir()
    legacy = root / "claw.sqlite3"
    legacy.write_bytes(b"legacy fixture")
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(ValueError, match="standalone"):
        await initialize(root, work)
    assert legacy.read_bytes() == b"legacy fixture"
    assert list(root.iterdir()) == [legacy]


def test_default_directory_and_strict_product_settings(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAW_ROOT", raising=False)
    assert instance_root() == Path("~/.a13n-claw").expanduser().resolve()
    monkeypatch.setenv("CLAW_ROOT", str(tmp_path))
    assert instance_root() == tmp_path
    assert ClawSettings().mode == "one_thread"
    with pytest.raises(ValueError):
        ClawSettings.model_validate({"models": {"alternate_catalog": {}}})
