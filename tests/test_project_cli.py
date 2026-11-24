"""Keep connector secrets out of scripts and update only verified release assets."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from a13n_claw.service_app import project_cli
from a13n_claw.service_app.feishu import reply_key


def test_lark_reply_key_is_stable_and_within_provider_limit():
    assert reply_key("event") == "claw-lark-event"
    first = reply_key("a" * 100)
    assert len(first) == 50 and first == reply_key("a" * 100)
    assert first != reply_key("b" * 100)


def result(code=0, stdout=b""):
    return SimpleNamespace(
        status=SimpleNamespace(exit_code=code),
        output=SimpleNamespace(stdout=SimpleNamespace(inline=stdout)),
    )


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
        if script == project_cli.PROBE_CLI:
            return result(0 if arguments[0] in installed else 127)
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
        assert outcome.error == "gh: download, verification or installation failed"
        assert outcome.failed and outcome.versions == {"gh": "2.0.0", "lark-cli": "1.5.0"}
        assert observed == ["gh", "lark-cli"]
        failed.clear()
        assert (await project_cli.provision(environment, update=True)).versions["gh"] == "2.1.0"

    asyncio.run(run())


def test_provision_reuses_installed_binaries_even_if_version_is_unrecognized(monkeypatch):
    async def command(_environment, script, arguments=(), **_kwargs):
        if script == project_cli.RUN_CLI:
            return result(1) if arguments[0] == "gh" else result(0, b"unexpected version output")
        assert script == project_cli.PROBE_CLI
        return result(0)

    async def latest(*_args):
        pytest.fail("installed binaries must not trigger a release lookup")

    monkeypatch.setattr(project_cli, "command", command)
    monkeypatch.setattr(project_cli, "latest", latest)
    outcome = asyncio.run(project_cli.provision(SimpleNamespace()))
    assert not outcome.failed
    assert outcome.versions == {"gh": "installed", "lark-cli": "installed"}
