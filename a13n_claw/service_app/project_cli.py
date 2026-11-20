"""Provision the two official CLIs in the authorized Project Environment."""

import asyncio
import json
from typing import Literal

from a13n_harness.environment.commands import ArgvCommand, CommandLimits, CommandRequest
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.environment.retention import EnvironmentOutputPolicy

# Digests identify official release assets, not installer scripts or mutable release aliases.
INSTALL = """
set -eu
export LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1
install_dir="$HOME/.local/bin"
export PATH="$install_dir:$PATH"
if command -v gh >/dev/null && command -v lark-cli >/dev/null; then
    gh --version >/dev/null
    lark-cli --version >/dev/null
    exit 0
fi
case "$(uname -s):$(uname -m)" in
    Linux:x86_64)
        arch=amd64
        gh_sha=79e7ab5db738c48777b696e374f8b428f0647debfb424f44d2f426370407fe2f
        lark_sha=cd7363f731c9e5dd2becd909837922cffea8aad229d581a84eb13c6aa0ce6808
        ;;
    Linux:aarch64|Linux:arm64)
        arch=arm64
        gh_sha=b1278c75107423d818f1937fd92cafc9e5e9b606631568f68d7fb816f5885a02
        lark_sha=166e95ffaec34f82123f50316480250329884c09e71a6f92313047e2c5c3fa5d
        ;;
    *) echo 'Install gh and lark-cli for this platform.' >&2; exit 2 ;;
esac
mkdir -p "$install_dir"
tmp=$(mktemp -d "$install_dir/.claw-tools.XXXXXXXX")
trap 'rm -rf "$tmp"' EXIT
install_cli() {
    name=$1 url=$2 sha=$3 member=$4
    if command -v "$name" >/dev/null; then return; fi
    curl --proto '=https' --tlsv1.2 -fsSL --max-time 50 "$url" -o "$tmp/archive"
    printf '%s  %s\\n' "$sha" "$tmp/archive" | sha256sum --check --status
    tar -xOzf "$tmp/archive" "$member" > "$tmp/$name"
    chmod 755 "$tmp/$name"
    "$tmp/$name" --version >/dev/null
    mv -f "$tmp/$name" "$install_dir/$name"
}
gh_asset="gh_2.105.0_linux_$arch"
install_cli gh "https://github.com/cli/cli/releases/download/v2.105.0/$gh_asset.tar.gz" \
    "$gh_sha" "$gh_asset/bin/gh"
lark_release="https://github.com/larksuite/cli/releases/download/v1.0.100"
install_cli lark-cli "$lark_release/lark-cli-1.0.100-linux-$arch.tar.gz" "$lark_sha" lark-cli
"""


CLIStatus = Literal["verified", "not verified", "not installed", "unavailable"]


async def check_accounts(environment: BoundEnvironment) -> dict[str, CLIStatus]:
    github, lark = await asyncio.gather(
        _check(
            environment, "gh", "gh auth status --active --hostname github.com >/dev/null", False
        ),
        _check(environment, "lark-cli", "lark-cli auth status --json --verify", True),
    )
    return {"github": github, "lark": lark}


async def _check(
    environment: BoundEnvironment, binary: str, command: str, structured: bool
) -> CLIStatus:
    script = (
        'export PATH="$HOME/.local/bin:$PATH" GH_PROMPT_DISABLED=1\n'
        "export LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1\n"
        f"command -v {binary} >/dev/null || exit 127\n{command} 2>/dev/null"
    )
    try:
        result = await environment.shell.exec_captured(
            CommandRequest(
                command=ArgvCommand(executable="sh", arguments=("-c", script)),
                limits=CommandLimits(wall_time_seconds=12),
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=16384, max_output_bytes=16384, overflow="truncate"
                ),
            ),
            alias="workspace",
        )
    except EnvironmentError:
        return "unavailable"
    match result.status.exit_code:
        case 127:
            return "not installed"
        case None:
            return "unavailable"
        case 0:
            if not structured:
                return "verified"
            try:
                status = json.loads(result.output.stdout.inline or b"")
            except (ValueError, UnicodeError):
                return "unavailable"
            if isinstance(status, dict) and status.get("verified") is True:
                return "verified"
    return "not verified"


async def provision(environment: BoundEnvironment) -> bool:
    result = await environment.shell.exec_captured(
        CommandRequest(
            command=ArgvCommand(executable="sh", arguments=("-c", INSTALL)),
            limits=CommandLimits(wall_time_seconds=120),
            output_policy=EnvironmentOutputPolicy(
                max_inline_bytes=2048, max_output_bytes=2048, overflow="truncate"
            ),
        ),
        alias="workspace",
    )
    return result.status.exit_code == 0
