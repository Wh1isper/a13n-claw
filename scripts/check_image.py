"""Smoke-test the non-root runtime and configuration across container replacement."""

import json
import subprocess
from urllib.request import Request, urlopen
from uuid import uuid4

from check_artifacts import check_authenticated, check_console

CONFIGURATION = """# Retain authored source across container replacement.
format: 1
models:
  persisted:
    provider: openai
    model_name: container-test
    credential_env: TEST_API_KEY
"""


def check_configuration(base: str, token: str, *, restarted: bool) -> None:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with urlopen(Request(base + "/api/configuration", headers=headers), timeout=5) as response:
        source = json.load(response)
    assert source["path"] == "/home/claw/data/config.yaml"
    if restarted:
        assert source["text"] == CONFIGURATION
        assert not source["pending"]
    else:
        request = Request(
            base + "/api/configuration",
            headers=headers,
            method="PUT",
            data=json.dumps(
                {"text": CONFIGURATION, "expected_revision": source["revision"]}
            ).encode(),
        )
        with urlopen(request, timeout=5) as response:
            assert json.load(response)["pending"]
    with urlopen(Request(base + "/api/resources", headers=headers), timeout=5) as response:
        resources = json.load(response)
    if restarted:
        assert len(resources) == 1
        assert resources[0]["id"] == "persisted"
        assert resources[0]["content"]["model_name"] == "container-test"
    else:
        assert resources == [], "Saving must not publish the runtime projection"


def check_container(volume: str, *, restarted: bool) -> str:
    container = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "-v",
            f"{volume}:/home/claw/data",
            "-p",
            "127.0.0.1::8080",
            "a13n-claw:check",
            "serve",
            "--host",
            "0.0.0.0",
        ],
        text=True,
    ).strip()
    try:
        uid = subprocess.check_output(["docker", "exec", container, "id", "-u"], text=True)
        assert uid.strip() == "10001"
        mode = subprocess.check_output(
            ["docker", "exec", container, "stat", "-c", "%a", "/home/claw/data"], text=True
        ).strip()
        assert mode == "700", "Runtime data must not be readable by other container users"
        address = subprocess.check_output(
            ["docker", "port", container, "8080/tcp"],
            text=True,
        ).strip()
        base = f"http://{address}"
        check_console(base)
        token = subprocess.check_output(
            ["docker", "exec", container, "cat", "/home/claw/data/operator.token"], text=True
        ).strip()
        check_authenticated(base, token)
        check_configuration(base, token, restarted=restarted)
        return token
    except Exception:
        subprocess.run(["docker", "logs", container], check=False)
        raise
    finally:
        subprocess.run(["docker", "stop", container], check=True)


def check() -> None:
    volume = f"a13n-claw-check-{uuid4().hex}"
    subprocess.run(["docker", "volume", "create", volume], check=True)
    try:
        token = check_container(volume, restarted=False)
        assert check_container(volume, restarted=True) == token
    finally:
        subprocess.run(["docker", "volume", "rm", volume], check=True)
    print("Non-root runtime, console, and configuration persistence checks passed.")


if __name__ == "__main__":
    check()
