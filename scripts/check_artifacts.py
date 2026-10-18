"""Install and serve the wheel and rebuilt sdist outside the source checkout."""

import argparse
import json
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def check_console(base: str) -> None:
    deadline = time.monotonic() + 20
    while True:
        try:
            with urlopen(base, timeout=2) as response:
                assert response.status == 200
                assert response.headers.get_content_type() == "text/html"
                html = response.read().decode()
            break
        except (URLError, TimeoutError, ConnectionError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    assert "a13n Claw" in html
    assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
    assert any(path.endswith(".js") for path in assets), "Missing built JavaScript"
    assert any(path.endswith(".css") for path in assets), "Missing built CSS"
    for path in assets:
        with urlopen(base + path, timeout=5) as response:
            assert response.status == 200
            assert response.headers.get_content_type() != "text/html"
            assert response.read()
    for path, status in (
        ("/api/threads", 401),
        ("/assets/missing.js", 404),
        ("/%2e%2e/pyproject.toml", 404),
    ):
        try:
            urlopen(base + path, timeout=5).close()
        except HTTPError as error:
            assert error.code == status, (path, error.code)
        else:
            raise AssertionError(f"Expected {status}: {path}")


def check_authenticated(base: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    with urlopen(Request(base + "/api/status", headers=headers), timeout=5) as response:
        instance = json.load(response)
        assert instance["host"] == "open"
        assert instance["instance"]["main_thread_id"]
    with urlopen(Request(base + "/api/threads", headers=headers), timeout=5) as response:
        page = json.load(response)
        assert len(page["threads"]) == 1
        assert page["threads"][0]["role"] == "coordinator"


def check_wheel(wheel: Path, root: Path, expected: str) -> None:
    subprocess.run(["uv", "venv", str(root / "venv"), "--python", "3.13"], check=True)
    executable = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(["uv", "pip", "install", "--python", str(executable), str(wheel)], check=True)
    result = subprocess.run(
        [str(executable), "-m", "a13n_claw", "--version"],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )
    if result.stdout.strip() != f"a13n-claw {expected}":
        raise ValueError(f"Unexpected installed version: {result.stdout!r}")
    # Exercise package-owned migration assets from the installed wheel, including
    # the template needed by the development generator and repeated startup.
    subprocess.run(
        [
            str(executable),
            "-c",
            "from pathlib import Path; "
            "from a13n_claw.database.migration import DatabaseMigrator, MIGRATIONS_PATH; "
            "assert (MIGRATIONS_PATH / 'script.py.mako').is_file(); "
            "migrator = DatabaseMigrator(Path('migration-check.sqlite3')); "
            "migrator.upgrade(); migrator.upgrade()",
        ],
        cwd=root,
        check=True,
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    workspace = root / "workspace"
    workspace.mkdir()
    data = root / "data"
    environment = {**os.environ, "A13N_OFFICIAL_MODELS_AUTO_UPDATE": "0"}
    subprocess.run(
        [
            str(executable),
            "-m",
            "a13n_claw",
            "init",
            "--root",
            str(data),
            "--workspace",
            str(workspace),
        ],
        cwd=root,
        env=environment,
        check=True,
    )
    with (root / "serve.log").open("w+") as log:
        process = subprocess.Popen(
            [
                str(executable),
                "-m",
                "a13n_claw",
                "serve",
                "--port",
                str(port),
                "--root",
                str(data),
            ],
            cwd=root,
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            check_console(base)
            check_authenticated(base, (data / "operator.token").read_text().strip())
        except Exception:
            log.seek(0)
            print(log.read())
            raise
        finally:
            if os.name == "nt":
                # Windows virtualenv python.exe can be a launcher with a child interpreter.
                # Terminating only the launcher leaves SQLite open in that child.
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=True)
                process.wait(timeout=10)
            else:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def check(dist: Path, expected: str) -> None:
    wheels = list(dist.glob("*.whl"))
    sources = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Expected one wheel and one source distribution")
    with tempfile.TemporaryDirectory(prefix="a13n-claw-artifacts-") as directory:
        root = Path(directory)
        original = root / "original"
        original.mkdir()
        check_wheel(wheels[0], original, expected)
        subprocess.run(
            ["uv", "build", str(sources[0]), "--wheel", "--out-dir", str(root / "rebuilt")],
            cwd=root,
            check=True,
        )
        rebuilt = root / "from-sdist"
        rebuilt.mkdir()
        check_wheel(next((root / "rebuilt").glob("*.whl")), rebuilt, expected)
    print(
        "Wheel and rebuilt sdist: version, authenticated runtime, "
        "console, assets, and boundary checks passed."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.0.0")
    args = parser.parse_args()
    check(Path("dist").resolve(), args.version)
