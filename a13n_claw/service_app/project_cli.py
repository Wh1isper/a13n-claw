"""Provision official CLIs in a leased Project Environment."""

import re
from dataclasses import dataclass
from shlex import join
from typing import Literal

import httpx
from a13n_harness.environment.commands import (
    CommandEnvironment,
    CommandRequest,
    ShellCommand,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.environment.retention import EnvironmentOutputPolicy

CLIS = {"gh": "cli/cli", "lark-cli": "larksuite/cli"}
VERSION = r"[0-9]+\.[0-9]+\.[0-9]+"
NOTIFIERS = {"LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1", "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1"}
RESOLVE = 'binary=$1; shift; export PATH="$HOME/.local/bin:$PATH"'
RUN_CLI = RESOLVE + '\nexec "$binary" "$@"'
REPLY_CLI = RESOLVE + '\nexec "$binary" "$@" 2>/dev/null'
INSTALL = r"""
set -eu
name=$1 url=$2 digest=$3 member=$4 version=$5
install_dir="$HOME/.local/bin"
mkdir -p "$install_dir"
tmp=$(mktemp -d "$install_dir/.claw-tools.XXXXXXXX")
trap 'rm -rf "$tmp"' EXIT
curl --proto '=https' --proto-redir '=https' -fsSL --max-time 45 "$url" -o "$tmp/archive"
printf '%s  %s\n' "$digest" "$tmp/archive" | sha256sum --check --status
tar -xOzf "$tmp/archive" "$member" > "$tmp/$name"
chmod 755 "$tmp/$name"
reported=$("$tmp/$name" --version)
case "$reported" in
    "$name version $version"|"$name version $version "*) ;;
    *) exit 2 ;;
esac
mv -f "$tmp/$name" "$install_dir/$name"
"""


@dataclass(frozen=True, slots=True)
class Maintenance:
    versions: dict[str, str]
    failed: bool


async def command(
    environment: BoundEnvironment,
    script: str,
    arguments: tuple[str, ...] = (),
    *,
    variables: dict[str, str] | None = None,
    timeout: int = 8,
):
    return await environment.shell.exec_captured(
        CommandRequest(
            command=ShellCommand(
                profile_id="default",
                script=join(
                    (
                        "timeout",
                        "-k",
                        "2s",
                        f"{timeout}s",
                        "sh",
                        "-c",
                        script,
                        "claw-cli",
                        *arguments,
                    )
                ),
            ),
            environment=CommandEnvironment(set={**NOTIFIERS, **(variables or {})}),
            output_policy=EnvironmentOutputPolicy(
                max_inline_bytes=8192, max_output_bytes=8192, overflow="truncate"
            ),
        ),
        alias="workspace",
    )


async def latest(client: httpx.AsyncClient, name: str, arch: str) -> tuple[str, str, str, str]:
    repo = CLIS[name]
    result = await client.get(f"https://api.github.com/repos/{repo}/releases/latest")
    result.raise_for_status()
    info = result.json()
    tag = info["tag_name"]
    if not isinstance(tag, str) or not re.fullmatch("v" + VERSION, tag):
        raise ValueError("Invalid release version")
    version = tag[1:]
    if name == "gh":
        stem = f"gh_{version}_linux_{arch}"
        asset, member = stem + ".tar.gz", stem + "/bin/gh"
    else:
        asset, member = f"lark-cli-{version}-linux-{arch}.tar.gz", "lark-cli"
    for record in info["assets"]:
        if record["name"] == asset:
            digest = record.get("digest")
            if isinstance(digest, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                return (
                    version,
                    f"https://github.com/{repo}/releases/download/{tag}/{asset}",
                    digest[7:],
                    member,
                )
    raise ValueError("Release asset has no SHA256 digest")


async def provision(environment: BoundEnvironment, *, update: bool = False) -> Maintenance:
    versions: dict[str, str] = {}
    for name in CLIS:
        result = await command(environment, RUN_CLI, (name, "--version"))
        if result.status.exit_code == 0:
            text = (result.output.stdout.inline or b"").decode(errors="replace")
            if match := re.match(rf"{re.escape(name)} version ({VERSION})(?=\s|$)", text):
                versions[name] = match[1]
    if len(versions) == len(CLIS) and not update:
        return Maintenance(versions, False)
    platform = await command(environment, 'printf "%s:%s" "$(uname -s)" "$(uname -m)"')
    arch = {b"Linux:x86_64": "amd64", b"Linux:aarch64": "arm64", b"Linux:arm64": "arm64"}.get(
        platform.output.stdout.inline or b""
    )
    if platform.status.exit_code != 0 or arch is None:
        return Maintenance(versions, True)
    failed = False
    async with httpx.AsyncClient(timeout=8) as client:
        for name in CLIS:
            if name in versions and not update:
                continue
            try:
                version, url, digest, member = await latest(client, name, arch)
                if versions.get(name) == version:
                    continue
                result = await command(
                    environment, INSTALL, (name, url, digest, member, version), timeout=65
                )
                if result.status.exit_code == 0:
                    versions[name] = version
                else:
                    failed = True
            except (httpx.HTTPError, KeyError, TypeError, ValueError, EnvironmentError):
                failed = True
    return Maintenance(versions, failed)


UpdateState = Literal["queued", "succeeded", "failed"]
