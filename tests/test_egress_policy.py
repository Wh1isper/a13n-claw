"""Connector Sessions expose only brokered secrets for the intended hosts."""

import pytest

from a13n_claw.service_app.egress import Binding, session_configuration


def test_controlled_public_policy_uses_only_secret_references():
    config = session_configuration(
        (
            Binding("github", "ecred_" + "a" * 20),
            Binding("lark", "ecred_" + "b" * 20, "https://open.feishu.cn"),
        )
    )
    assert config.egress_mode == "controlled"
    assert config.egress is not None and config.egress.destinations.mode == "public"
    refs = {ref.env: ref for ref in config.credential_references}
    assert set(refs) == {"GH_TOKEN", "LARKSUITE_CLI_TENANT_ACCESS_TOKEN"}
    assert set(refs["GH_TOKEN"].inject_hosts) == {
        "api.github.com",
        "github.com",
        "uploads.github.com",
    }
    assert refs["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"].inject_hosts == ("open.feishu.cn",)
    assert "value" not in str(config.model_dump())


def test_policy_rejects_duplicate_platform_or_unrecognized_lark_host():
    github = Binding("github", "ecred_" + "a" * 20)
    with pytest.raises(ValueError, match="one primary"):
        session_configuration((github, github))
    with pytest.raises(ValueError, match="region"):
        session_configuration((Binding("lark", "ecred_" + "b" * 20, "https://attacker.test"),))


def test_lark_region_isolation():
    config = session_configuration(
        (Binding("lark", "ecred_" + "c" * 20, "https://open.larksuite.com"),)
    )
    assert config.credential_references[0].inject_hosts == ("open.larksuite.com",)


def test_lark_token_exchange_returns_provider_ttl_without_exposing_secret():
    import asyncio

    import httpx

    from a13n_claw.service_app.feishu import LarkAdapter, LarkSettings

    async def run():
        seen = []

        def reply(request):
            seen.append(request)
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "synthetic-token", "expire": 7200}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            adapter = LarkAdapter(
                "connector",
                LarkSettings(app_id="app", bot_open_id="bot", senders=("user",)),
                "synthetic-secret",
                client=client,
            )
            assert await adapter.exchange_token() == ("synthetic-token", 7200)
        assert len(seen) == 1 and seen[0].url.host == "open.feishu.cn"
        assert b"synthetic-secret" in seen[0].content

    asyncio.run(run())


@pytest.mark.parametrize("lifetime", [None, 0, -1, True, "7200"])
def test_lark_exchange_rejects_unusable_ttl(lifetime):
    import asyncio

    import httpx

    from a13n_claw.service_app.feishu import LarkAdapter, LarkSettings

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={"code": 0, "tenant_access_token": "synthetic", "expire": lifetime},
                )
            )
        ) as client:
            adapter = LarkAdapter(
                "connector",
                LarkSettings(app_id="app", bot_open_id="bot", senders=("user",)),
                "secret",
                client=client,
            )
            with pytest.raises(ValueError, match="lifetime"):
                await adapter.exchange_token()

    asyncio.run(run())


def test_first_mount_accepts_reference_policy_without_credentials_in_payload():
    from types import SimpleNamespace

    from a13n_claw.service_app.main_thread import project_mounts

    config = session_configuration((Binding("github", "ecred_" + "a" * 20),))
    (mount,) = project_mounts(SimpleNamespace(environment_id="env_" + "d" * 20), config)
    assert mount.session_configuration == config
    assert mount.session_configuration.credential_references[0].env == "GH_TOKEN"


def test_token_refresh_targets_75_minutes_with_expiry_margin():
    from a13n_claw.service_app.egress import token_refresh_delay

    assert token_refresh_delay(7200) == 4500
    assert token_refresh_delay(3600) == 2700
    assert token_refresh_delay(1800) == 900
    assert token_refresh_delay(1) == 1
    with pytest.raises(ValueError):
        token_refresh_delay(0)


def test_transactional_generation_uses_exact_hosts_and_controlled_public_initialization(
    monkeypatch,
):
    import asyncio
    from types import SimpleNamespace

    from a13n_service.app_sdk import egress as sdk_egress
    from a13n_service.app_sdk import threads as sdk_threads

    from a13n_claw.service_app import egress

    async def run():
        calls = []
        session, runtime = object(), object()
        claw = SimpleNamespace(workspace_id="ws", service_account_id="sa", main_thread_id="thread")
        generation = SimpleNamespace(
            id="gen", kind="github", domain=None, state="preparing", credential_id=None
        )

        async def create(*args, **kwargs):
            calls.append(("create", args, kwargs))
            return SimpleNamespace(id="ecred_" + "a" * 20)

        async def bind(*args, **kwargs):
            calls.append(("bind", args, kwargs))

        monkeypatch.setattr(sdk_egress, "create_app_credential_in_transaction", create)
        monkeypatch.setattr(sdk_threads, "update_app_egress_secrets", bind)
        await egress.commit_generation(session, runtime, claw, generation, "synthetic", None)
        assert generation.state == "ready"
        assert calls[0][1][0] is session and calls[1][1][0] is session
        assert calls[0][2]["allowed_hosts"] == egress.GITHUB_HOSTS
        reference = calls[1][2]["set_secrets"][0]
        assert reference.env == "GH_TOKEN" and reference.inject_hosts == egress.GITHUB_HOSTS
        assert calls[1][2]["initial_destinations"].mode == "public"

    asyncio.run(run())


@pytest.mark.parametrize("replacement", [None, "new-generation"])
@pytest.mark.parametrize("state", ["revoke_pending", "unbinding"])
def test_revoke_does_not_remove_newer_generation_reference(monkeypatch, replacement, state):
    import asyncio
    from types import SimpleNamespace

    from a13n_service.app_sdk import egress as sdk_egress
    from a13n_service.app_sdk import threads as sdk_threads

    from a13n_claw.service_app import egress

    async def run():
        calls = []

        class Session:
            async def scalar(self, _query):
                return replacement

        async def revoke(*_args, **kwargs):
            calls.append(("revoke", kwargs["credential_id"]))

        async def unbind(*_args, **kwargs):
            calls.append(("unbind", kwargs["remove_envs"]))

        monkeypatch.setattr(sdk_egress, "revoke_app_credential_in_transaction", revoke)
        monkeypatch.setattr(sdk_threads, "update_app_egress_secrets", unbind)
        claw = SimpleNamespace(
            id="claw", workspace_id="ws", service_account_id="sa", main_thread_id="thread"
        )
        generation = SimpleNamespace(
            id="old", kind="github", credential_id="old-credential", state=state
        )
        await egress.revoke_generation(Session(), object(), claw, generation)
        assert generation.state == "revoked"
        assert (("revoke", "old-credential") in calls) is (state == "revoke_pending")
        assert (("unbind", ("GH_TOKEN",)) in calls) is (replacement is None)

    asyncio.run(run())


def test_background_revoke_commits_before_thread_unbinding(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from a13n_service.app_sdk import egress as sdk_egress
    from a13n_service.app_sdk import threads as sdk_threads

    from a13n_claw.service_app import egress
    from a13n_claw.service_app.models import Claw, ConnectorEgress

    async def run():
        claw = SimpleNamespace(id="claw", workspace_id="ws", service_account_id="sa")
        generation = SimpleNamespace(
            claw_id="claw", credential_id="credential", state="revoke_pending", failure="old"
        )
        calls = []

        class Session:
            async def get(self, model, _id, **_kwargs):
                return {Claw: claw, ConnectorEgress: generation}[model]

            async def scalar(self, _query):
                return claw

        @asynccontextmanager
        async def session(_storage):
            yield Session()
            calls.append("commit")

        async def admission(*_args, **_kwargs):
            pass

        async def revoke(*_args, **_kwargs):
            calls.append("revoke")

        async def unbind(*_args, **_kwargs):
            calls.append("unbind")

        monkeypatch.setattr(egress, "transaction", session)
        monkeypatch.setattr(egress, "short_session", session)
        monkeypatch.setattr(sdk_threads, "prepare_admission", admission)
        monkeypatch.setattr(sdk_egress, "revoke_app_credential_in_transaction", revoke)
        monkeypatch.setattr(egress, "revoke_generation", unbind)
        await egress.process_generation(SimpleNamespace(storage=object()), "ws", "claw", "gen")
        assert generation.state == "unbinding" and generation.failure is None
        assert calls == ["commit", "revoke", "commit"]

    asyncio.run(run())


def test_first_mount_preserves_selected_agent_policy_and_rejects_widening():
    from a13n_service.app_sdk.threads import SessionConfiguration

    hosts = ["api.github.com", "github.com", "uploads.github.com", "example.com"]
    base = SessionConfiguration.model_validate(
        {
            "working_directory": "/workspace/project",
            "commands": True,
            "egress_mode": "controlled",
            "egress": {"destinations": {"mode": "allowlist", "hosts": hosts}},
        }
    )
    result = session_configuration((Binding("github", "ecred_" + "a" * 20),), base)
    assert result.working_directory == base.working_directory
    assert result.commands == base.commands
    assert result.egress.destinations == base.egress.destinations
    assert len(result.credential_references) == 1
    blocked = SessionConfiguration.model_validate(
        {
            "egress_mode": "controlled",
            "egress": {"destinations": {"mode": "allowlist", "hosts": ["example.com"]}},
        }
    )
    with pytest.raises(ValueError, match="allowed destinations"):
        session_configuration((Binding("github", "ecred_" + "a" * 20),), blocked)


def test_enterprise_host_does_not_include_public_github_hosts():
    config = session_configuration((Binding("github", "ecred_" + "a" * 20, "git.example.com"),))
    assert config.credential_references[0].inject_hosts == ("git.example.com",)


def test_existing_connector_environment_name_is_not_overwritten():

    original = "ecred_" + "a" * 20
    base = session_configuration((Binding("github", original),))
    with pytest.raises(ValueError, match="already in use"):
        session_configuration((Binding("github", "ecred_" + "b" * 20),), base)
    assert session_configuration((Binding("github", original),), base) == base


def test_initial_configuration_maps_policy_conflict_to_service_error():
    import asyncio
    from types import SimpleNamespace

    from a13n_service.app_sdk.threads import SessionConfiguration
    from a13n_service.infra.errors import ServiceError

    from a13n_claw.service_app.egress import initial_configuration

    class Session:
        async def scalars(self, _query):
            return [SimpleNamespace(kind="github", credential_id="ecred_" + "a" * 20, domain=None)]

    async def run():
        base = SessionConfiguration.model_validate({"egress_mode": "deny"})
        with pytest.raises(ServiceError) as error:
            await initial_configuration(Session(), SimpleNamespace(id="claw"), base)
        assert error.value.details["reason"] == "connector_egress_policy_conflict"

    asyncio.run(run())


@pytest.mark.parametrize("kind,domain", [("github", None), ("lark", "https://open.larksuite.com")])
def test_ready_generation_rotates_same_reference_without_rebinding(monkeypatch, kind, domain):
    import asyncio
    from types import SimpleNamespace

    from a13n_service.app_sdk import egress as sdk_egress
    from a13n_service.app_sdk import threads as sdk_threads

    from a13n_claw.service_app.egress import commit_generation

    async def run():
        calls = []
        generation = SimpleNamespace(
            id="gen", kind=kind, domain=domain, state="preparing", credential_id="ecred_existing"
        )
        claw = SimpleNamespace(workspace_id="ws", service_account_id="sa", main_thread_id="busy")

        async def rotate(*_args, **kwargs):
            calls.append(kwargs["credential_id"])

        async def no_bind(*_args, **_kwargs):
            pytest.fail("rotation must not rebind a busy Thread")

        monkeypatch.setattr(sdk_egress, "rotate_app_credential_in_transaction", rotate)
        monkeypatch.setattr(sdk_threads, "update_app_egress_secrets", no_bind)
        await commit_generation(object(), object(), claw, generation, "replacement", None)
        assert calls == ["ecred_existing"] and generation.state == "ready"

    asyncio.run(run())
