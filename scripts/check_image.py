"""Smoke-test the same packaged console in the non-root container."""

import subprocess

from check_artifacts import check_authenticated, check_console


def check() -> None:
    container = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "-d",
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
    except Exception:
        subprocess.run(["docker", "logs", container], check=False)
        raise
    finally:
        subprocess.run(["docker", "stop", container], check=True)
    print("Non-root container console smoke test passed.")


if __name__ == "__main__":
    check()
