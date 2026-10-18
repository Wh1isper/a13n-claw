"""Smoke-test the non-root runtime and configuration across container replacement."""

import json
import subprocess
from urllib.request import Request, urlopen
from uuid import uuid4

from check_artifacts import check_authenticated, check_console

CONFIGURATION = """# Retain authored source across container replacement.
schema_version: '1'
kind: model
id: model-persisted
name: Container test
route: openai:container-test
authentication:
  kind: api_key
  env: TEST_API_KEY
"""


def check_configuration(base: str, token: str, *, restarted: bool) -> None:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if not restarted:
        request = Request(
            base + "/api/configuration/source",
            headers=headers,
            method="PUT",
            data=json.dumps(
                {
                    "relative_path": "models/persisted.yaml",
                    "content": CONFIGURATION,
                    "expected_digest": None,
                }
            ).encode(),
        )
        with urlopen(request, timeout=5) as response:
            assert response.status == 200
    with urlopen(
        Request(base + "/api/configuration/source?path=models/persisted.yaml", headers=headers),
        timeout=5,
    ) as response:
        assert json.load(response)["content"] == CONFIGURATION


def check_container(volume: str, *, restarted: bool) -> str:
    container = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "-v",
            f"{volume}:/home/claw/data",
            "-e",
            "A13N_OFFICIAL_MODELS_AUTO_UPDATE=0",
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
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{volume}:/home/claw/data",
                "-e",
                "A13N_OFFICIAL_MODELS_AUTO_UPDATE=0",
                "a13n-claw:check",
                "init",
                "--workspace",
                "/home/claw/workspace",
            ],
            check=True,
        )
        token = check_container(volume, restarted=False)
        assert check_container(volume, restarted=True) == token
    finally:
        subprocess.run(["docker", "volume", "rm", volume], check=True)
    print("Non-root runtime, console, and configuration persistence checks passed.")


if __name__ == "__main__":
    check()
