"""Smoke-test the same packaged console in the non-root container."""

import subprocess

from check_artifacts import check_console


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
        address = subprocess.check_output(
            ["docker", "port", container, "8080/tcp"],
            text=True,
        ).strip()
        check_console(f"http://{address}")
    except Exception:
        subprocess.run(["docker", "logs", container], check=False)
        raise
    finally:
        subprocess.run(["docker", "stop", container], check=True)
    print("Non-root container console smoke test passed.")


if __name__ == "__main__":
    check()
