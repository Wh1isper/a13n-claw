"""Project resource changes use the native App SDK atomically."""

import asyncio
from types import SimpleNamespace

import pytest
from a13n_service.infra.errors import ServiceError

from a13n_claw.service_app import project


def test_rebind_preserves_busy_project_and_uses_sdk_result(monkeypatch):
    async def run():
        claw = SimpleNamespace(
            id="claw",
            name="Project",
            main_thread_id="main",
            environment_id="old",
            browser_id="browser",
            cli_versions={"gh": "1"},
            cli_update_id="queued",
            cli_update_state="pending",
            cli_update_error=None,
        )
        calls = []
        busy = True

        class Session:
            async def execute(self, _query):
                calls.append("connector-update")

            async def flush(self):
                calls.append("flush")

        async def check(*_args, **_kwargs):
            pass

        async def update(*_args, **kwargs):
            calls.append(kwargs)
            if busy:
                raise ServiceError("conflict", "busy", {"reason": "thread_resources_busy"})
            return SimpleNamespace(environment_id="new", browser_id="browser")

        monkeypatch.setattr(project.app_projects, "check_resources", check)
        monkeypatch.setattr(project.schedules, "require_no_fresh_work", check)
        monkeypatch.setattr(project.egress, "revoke_for_rebind", check)
        monkeypatch.setattr(project.app_threads, "update_thread_resources", update)
        scope = SimpleNamespace(workspace_id="ws")
        account = SimpleNamespace(id="sa")
        with pytest.raises(ServiceError) as exc:
            await project.rebind(Session(), None, scope, account, claw, "new", "browser")
        assert exc.value.details["reason"] == "environment_rebind_requires_idle_threads"
        assert "pending native messages" in exc.value.message
        assert claw.environment_id == "old" and claw.cli_update_id == "queued"
        assert calls[0] == "connector-update" and calls[1]["environment_id"] == "new"
        busy = False
        await project.rebind(Session(), None, scope, account, claw, "new", "browser")
        assert claw.environment_id == "new" and claw.cli_update_id is None
        assert calls[-3] == "connector-update" and calls[-2]["environment_id"] == "new"
        assert calls[-1] == "flush"

    asyncio.run(run())


def test_browser_only_change_omits_environment_sdk_arguments(monkeypatch):
    async def run():
        claw = SimpleNamespace(
            id="claw", name="Project", main_thread_id="main", environment_id="env", browser_id="old"
        )
        recorded = {}

        class Session:
            async def flush(self):
                pass

        async def noop(*_args, **_kwargs):
            pass

        async def update(*_args, **kwargs):
            recorded.update(kwargs)
            return SimpleNamespace(environment_id="env", browser_id="new")

        monkeypatch.setattr(project.app_projects, "check_resources", noop)
        monkeypatch.setattr(project.schedules, "require_no_fresh_work", noop)
        monkeypatch.setattr(project.app_threads, "update_thread_resources", update)
        await project.rebind(
            Session(),
            None,
            SimpleNamespace(workspace_id="ws"),
            SimpleNamespace(id="sa"),
            claw,
            "env",
            "new",
        )
        assert recorded["browser_id"] == "new" and "environment_id" not in recorded
        assert claw.browser_id == "new"

    asyncio.run(run())
