"""Opt-in packaged persistent executor and native Host integration, no external accounts."""

import asyncio
import json
import os
import secrets
import shlex
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import yaml
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.surfaces import NewThreadDefaults
from a13n_harness_ui.thread_files import AttachmentUpload
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from test_business_service import setup, streaming_model

from a13n_claw.bootstrap import executor_resources
from a13n_claw.capability import messaging_integrations
from a13n_claw.host import open_host

REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.mark.anyio
@pytest.mark.parametrize("host_docker", [False, True], ids=["volumes", "host-docker"])
@pytest.mark.skipif(os.environ.get("CLAW_EXECUTOR_TEST") != "1", reason="Opt-in Docker executor")
async def test_compose_executor_native_host_browser_and_persistence(
    tmp_path, monkeypatch, host_docker
):
    name = "claw-test-" + uuid4().hex[:12]
    token = secrets.token_urlsafe(32)
    env = {**os.environ, "CLAW_EXECUTOR_TOKEN": token, "CLAW_BROWSER_PORT": "0", "CLAW_PORT": "0"}
    override = tmp_path / "compose.yaml"
    override.write_text(yaml.safe_dump({"services": {"executor": {"ports": ["127.0.0.1::8888"]}}}))
    base = [
        "docker",
        "compose",
        "-p",
        name,
        "-f",
        str(REPOSITORY / "deploy/docker/compose.yaml"),
        "-f",
        str(override),
    ]

    def compose(*args):
        result = subprocess.run([*base, *args], env=env, text=True, capture_output=True)
        if result.returncode:
            logs = subprocess.run(
                [*base, "logs", "--tail", "30"], env=env, text=True, capture_output=True
            )
            pytest.fail(result.stderr + logs.stdout + logs.stderr)
        return result.stdout

    execution_gid = 1000
    host_workspace = tmp_path / "nested-work"
    if host_docker:
        host_workspace.mkdir()
        host_workspace.chmod(0o777)  # Disposable bind directory also works on CI's UID 1001.
        execution_gid = Path("/var/run/docker.sock").stat().st_gid
        env.update(CLAW_HOST_WORKSPACE=str(host_workspace), CLAW_DOCKER_GID=str(execution_gid))
        base.extend(["-f", str(REPOSITORY / "deploy/docker/compose.host-docker.yaml")])

    results = []
    actions = []
    browser_cli = "agent-browser --session claw-test --cdp 9222"
    skills_root = shlex.quote(str(host_workspace) if host_docker else "/workspace")
    script = (
        f"set -eu; test $(id -u) = 1000; test $(id -g) = {execution_gid}; "
        'test -z "${CLAW_EXECUTOR_TOKEN:-}"; '
        "command -v uv pnpm cargo rustfmt gh git-lfs rclone anycap lark-cli; "
        "cargo clippy --version; "
        f"test -f {skills_root}/.agents/skills/anycap-cli/SKILL.md; "
        f"test -f {skills_root}/.agents/skills/lark-im/SKILL.md; "
        "test $(cat /workspace/input.txt) = attachment-proof; "
        "printf persisted > /workspace/proof; printf home > $HOME/proof; "
        "printf cache > $XDG_CACHE_HOME/proof; printf download > /workspace/downloads/proof; "
        "curl -fsS http://127.0.0.1:9222/json/version; "
        f"{browser_cli} open http://127.0.0.1:6080/vnc.html; "
        'echo \'localStorage.setItem("claw-test","persisted-profile")\' | '
        f"{browser_cli} eval --stdin; "
        f"{browser_cli} get title; "
        "echo executor-checked"
    )

    if host_docker:
        script += (
            f"; cd {shlex.quote(str(host_workspace))}; printf nested > nested-input; "
            'docker run --rm --network none --user 1000:1000 -v "$PWD:/proof" '
            "--entrypoint sh a13n-claw-executor:local "
            "-c 'test $(cat /proof/nested-input) = nested; printf mapped > /proof/nested-output'"
        )

    async def model(messages, info):
        returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if returned:
            results.append(returned[-1].content)
        if len(returned) < len(actions):
            tool, arguments = actions[len(returned)]
            names = {t.name for t in info.function_tools}
            assert {"copy", "claw_send_message", "claw_schedules", "list_threads"} <= names, names
            assert tool in names, (tool, names)
            return ModelResponse(parts=[ToolCallPart(tool, arguments)])
        return ModelResponse(parts=[TextPart("Executor checked")])

    root, service, _ = await setup(tmp_path, monkeypatch, model=streaming_model(model))
    monkeypatch.setenv("CLAW_EXECUTOR_TOKEN", token)
    try:
        await asyncio.to_thread(
            compose,
            "run",
            "--rm",
            "--no-deps",
            "claw",
            "init",
            "--workspace",
            "/home/claw/workspace",
            "--executor",
            "http://executor:8888",
        )
        await asyncio.to_thread(compose, "up", "-d", "--wait", "--wait-timeout", "90")
        status = json.loads(
            await asyncio.to_thread(compose, "exec", "-T", "claw", "a13n-claw", "status")
        )
        assert status["instance"]["mode"] == "one_thread"
        endpoint = (
            "http://" + (await asyncio.to_thread(compose, "port", "executor", "8888")).strip()
        )
        async with httpx.AsyncClient() as client:
            for headers in ({}, {"Authorization": "Bearer wrong"}):
                denied = await client.post(endpoint + "/eip/control", headers=headers, json={})
                assert denied.status_code == 401
        device = yaml.safe_load((REPOSITORY / "deploy/docker/device.yaml").read_text())
        device["transport"]["configuration"]["endpoint"] = endpoint
        project = (REPOSITORY / "deploy/docker/project.yaml").read_text()
        async with open_host(
            root, service.settings, integrations=messaging_integrations(service)
        ) as host:
            service.bind(host)
            resources = executor_resources(endpoint)
            await host.mutate_configuration(
                relative_path="extensions/claw-files.yaml",
                request=ResourceMutationRequest(
                    content=yaml.safe_dump(resources["extensions/claw-files.yaml"])
                ),
            )
            await host.mutate_configuration(
                relative_path="devices/executor.yaml",
                request=ResourceMutationRequest(content=yaml.safe_dump(device)),
            )
            await host.mutate_configuration(
                relative_path="projects/main.yaml", request=ResourceMutationRequest(content=project)
            )
            info = await host.device_info("device-claw-executor")
            assert "claw-executor" in info.model_dump_json()
            thread = await host.create_thread(
                defaults=NewThreadDefaults(project_id="project-claw-main")
            )
            attachment = await host.stage_thread_attachment(
                thread_id=thread.thread_id,
                upload=AttachmentUpload(name="input.txt", data=b"attachment-proof"),
            )
            directory = root / "host/data/threads" / thread.thread_id
            actions.extend(
                [
                    (
                        "copy",
                        {
                            "pairs": [
                                {
                                    "src": str(
                                        directory
                                        / "attachments"
                                        / attachment.attachment_id
                                        / "content"
                                    ),
                                    "dst": "/environment/executor/workspace/input.txt",
                                }
                            ]
                        },
                    ),
                    ("shell_exec", {"command": script, "alias": "executor"}),
                    ("view", {"file_path": "/environment/executor/workspace/input.txt"}),
                    (
                        "copy",
                        {
                            "pairs": [
                                {
                                    "src": "/environment/executor/workspace/input.txt",
                                    "dst": str(directory / "tmp/output.txt"),
                                }
                            ]
                        },
                    ),
                ]
            )
            receipt = await host.submit_thread(
                thread_id=thread.thread_id,
                prompt="Check executor",
                attachment_ids=(attachment.attachment_id,),
            )
            outcome = await host.wait_root_operation(receipt.receipt_id, timeout_seconds=60)
            assert outcome.status == "completed", outcome
            assert len(results) == 4 and all(r.get("ok") for r in results), results
            assert results[1]["status"]["exit_code"] == 0, json.dumps(results[1])
            # Native cancellation must end a live remote process, retaining its files.
            actions[:] = [
                (
                    "shell_exec",
                    {
                        "command": "echo $$ > /workspace/cancel.pid; exec sleep 300",
                        "alias": "executor",
                    },
                )
            ]
            cancellation_thread = await host.create_thread(
                defaults=NewThreadDefaults(project_id="project-claw-main")
            )
            pending = await host.submit_thread(
                thread_id=cancellation_thread.thread_id, prompt="Start cancellable executor work"
            )
            # Observe startup independently; do not wait for the shell to return.
            for _ in range(100):
                started = await asyncio.to_thread(
                    compose,
                    "exec",
                    "-T",
                    "executor",
                    "sh",
                    "-c",
                    "test ! -f /workspace/cancel.pid || cat /workspace/cancel.pid",
                )
                if started.strip():
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail("Native shell process did not start")
            await host.cancel_root_operation(pending.receipt_id)
            cancelled = await host.wait_root_operation(pending.receipt_id, timeout_seconds=20)
            assert cancelled.status == "cancelled", cancelled
            stopped = await asyncio.to_thread(
                compose,
                "exec",
                "-T",
                "executor",
                "sh",
                "-c",
                'set -e; test -f /workspace/cancel.pid; if kill -0 "$(cat /workspace/cancel.pid)" '
                "2>/dev/null; then exit 1; fi; "
                "echo cancelled",
            )
            assert stopped.strip() == "cancelled"
        if host_docker:
            assert (host_workspace / "nested-output").read_text() == "mapped"
        assert results and "executor-checked" in json.dumps(results), results
        assert len(results) == 4 and all(r.get("ok") for r in results), results
        assert (directory / "tmp/output.txt").read_bytes() == b"attachment-proof"
        assert not (tmp_path / "work/proof").exists()
        # Recreate containers, retaining only the deployment's volumes.
        await asyncio.to_thread(compose, "down")
        await asyncio.to_thread(compose, "up", "-d", "--wait", "--wait-timeout", "90")
        proof = await asyncio.to_thread(
            compose,
            "exec",
            "-T",
            "--user",
            "1000:1000",
            "executor",
            "sh",
            "-c",
            "cat /workspace/proof /home/sandbox/proof "
            "/home/sandbox/.cache/proof /workspace/downloads/proof",
        )
        assert proof == "persistedhomecachedownload"
        browser = await asyncio.to_thread(
            compose, "exec", "-T", "executor", "curl", "-fsS", "http://127.0.0.1:6080/vnc.html"
        )
        assert "noVNC" in browser
        profile = await asyncio.to_thread(
            compose,
            "exec",
            "-T",
            "--user",
            "1000:1000",
            "executor",
            "sh",
            "-c",
            f"{browser_cli} open http://127.0.0.1:6080/vnc.html; "
            f"echo 'localStorage.getItem(\"claw-test\")' | {browser_cli} eval --stdin",
        )
        assert "persisted-profile" in profile
        # Kill/recreate the browser to cover stale Chromium locks after a crash.
        await asyncio.to_thread(compose, "kill", "-s", "SIGKILL", "browser")
        await asyncio.to_thread(compose, "up", "-d", "--wait", "--wait-timeout", "90", "browser")
    finally:
        await asyncio.to_thread(compose, "down", "--volumes", "--remove-orphans")
