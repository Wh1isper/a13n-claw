"""Keep connector secrets out of scripts and update only verified release assets."""

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import httpx
import pytest
from a13n_harness.environment.commands import ShellCommand
from pydantic_ai import ModelRetry

from a13n_claw.service_app import connectors, project_cli
from a13n_claw.service_app.plugin import reply_arguments


def result(code=0, stdout=b""):
    return SimpleNamespace(
        status=SimpleNamespace(exit_code=code),
        output=SimpleNamespace(stdout=SimpleNamespace(inline=stdout)),
    )


@dataclass
class FixtureShell:
    requests: list

    async def exec_captured(self, request, *, alias):
        assert alias == "workspace"
        self.requests.append(request)
        return result()


def test_reply_uses_only_selected_platform_environment():
    async def run():
        requests = []
        environment = SimpleNamespace(shell=FixtureShell(requests))
        token = "synthetic-github-token"
        config = {"principal_id": "account", "channels": ["team/repo"], "senders": ["operator"]}
        with pytest.raises(ModelRetry):
            reply_arguments("github", config, "other/repo#2", "text", "issue_comment", "text")
        args = reply_arguments(
            "github", config, "team/repo#2", "quotes ' and $HOME", "issue_comment", "text"
        )
        variables = await connectors.command_environment("github", "con-example", config, token)
        await project_cli.command(environment, project_cli.REPLY_CLI, args, variables=variables)
        (request,) = requests
        assert isinstance(request.command, ShellCommand)
        assert token not in request.command.script
        assert request.environment.set["GH_TOKEN"] == token
        assert "LARKSUITE_CLI_TENANT_ACCESS_TOKEN" not in request.environment.set
        await project_cli.command(environment, project_cli.RUN_CLI, ("gh", "--version"))
        assert "GH_TOKEN" not in requests[-1].environment.set

    asyncio.run(run())


def test_lark_reply_uses_bot_access_token_not_app_secret(monkeypatch):
    async def token(self):
        assert self.secret == "synthetic-app-secret"
        return "synthetic-access-token"

    monkeypatch.setattr(connectors.LarkAdapter, "access_token", token)
    config = {"app_id": "cli_test", "bot_open_id": "ou_bot", "senders": ["ou_operator"]}
    variables = asyncio.run(
        connectors.command_environment("lark", "con-fixture", config, "synthetic-app-secret")
    )
    assert variables["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] == "synthetic-access-token"
    assert variables["LARKSUITE_CLI_STRICT_MODE"] == "bot"
    assert "synthetic-app-secret" not in variables.values() and "GH_TOKEN" not in variables
    args = reply_arguments("lark", config, "om_message", "hello", "lark_reply", "text")
    assert args[-3:] == ("--as", "bot", "--text=hello")


def test_latest_uses_expected_official_assets_and_digest():
    digest = "a" * 64

    def github(request):
        assert str(request.url) == "https://api.github.com/repos/larksuite/cli/releases/latest"
        return httpx.Response(
            200,
            json={
                "tag_name": "v1.2.3",
                "assets": [
                    {
                        "name": "lark-cli-1.2.3-linux-arm64.tar.gz",
                        "digest": "sha256:" + digest,
                        "browser_download_url": "https://wrong.example/asset",
                    }
                ],
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(github)) as client:
            assert await project_cli.latest(client, "lark-cli", "arm64") == (
                "1.2.3",
                "https://github.com/larksuite/cli/releases/download/v1.2.3/lark-cli-1.2.3-linux-arm64.tar.gz",
                digest,
                "lark-cli",
            )
            with pytest.raises(ValueError, match="SHA256"):
                await project_cli.latest(client, "lark-cli", "amd64")

    asyncio.run(run())


def test_provision_installs_missing_and_updates_only_on_request(monkeypatch):
    installed = {"gh": "2.0.0"}
    observed = []
    failed = set()

    async def command(_environment, script, arguments=(), **_kwargs):
        if script == project_cli.RUN_CLI:
            name = arguments[0]
            return (
                result(0, f"{name} version {installed[name]}\n".encode())
                if name in installed
                else result(127)
            )
        if script == project_cli.INSTALL:
            name, _, _, _, version = arguments
            if name in failed:
                return result(2)
            installed[name] = version
            return result()
        return result(0, b"Linux:x86_64")

    async def latest(_client, name, arch):
        assert arch == "amd64"
        observed.append(name)
        return ("2.1.0" if name == "gh" else "1.5.0", "fixture-url", "a" * 64, name)

    monkeypatch.setattr(project_cli, "command", command)
    monkeypatch.setattr(project_cli, "latest", latest)

    async def run():
        environment = SimpleNamespace()
        assert await project_cli.provision(environment) == project_cli.Maintenance(
            {"gh": "2.0.0", "lark-cli": "1.5.0"}, False
        )
        assert observed == ["lark-cli"]
        observed.clear()
        assert not (await project_cli.provision(environment)).failed
        assert observed == []
        failed.add("gh")
        outcome = await project_cli.provision(environment, update=True)
        assert outcome.failed and outcome.versions == {"gh": "2.0.0", "lark-cli": "1.5.0"}
        assert observed == ["gh", "lark-cli"]
        failed.clear()
        assert (await project_cli.provision(environment, update=True)).versions["gh"] == "2.1.0"

    asyncio.run(run())
