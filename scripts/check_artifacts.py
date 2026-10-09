"""Install and serve the wheel and rebuilt sdist outside the source checkout."""

import argparse
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


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
    for path in ("/api/threads", "/assets/missing.js", "/%2e%2e/pyproject.toml"):
        try:
            urlopen(base + path, timeout=5).close()
        except HTTPError as error:
            assert error.code == 404, (path, error.code)
        else:
            raise AssertionError(f"Expected 404: {path}")


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
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (root / "serve.log").open("w+") as log:
        process = subprocess.Popen(
            [str(executable), "-m", "a13n_claw", "serve", "--port", str(port)],
            cwd=root,
            stdout=log,
            stderr=log,
        )
        try:
            check_console(f"http://127.0.0.1:{port}")
        except Exception:
            log.seek(0)
            print(log.read())
            raise
        finally:
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
    print("Wheel and rebuilt sdist: version, console, assets, and 404 checks passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.0.0")
    args = parser.parse_args()
    check(Path("dist").resolve(), args.version)
