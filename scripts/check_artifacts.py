"""Install the wheel and rebuild the sdist outside the source checkout."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path


def check(dist: Path, expected: str) -> None:
    wheels = list(dist.glob("*.whl"))
    sources = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Expected one wheel and one source distribution")
    with tempfile.TemporaryDirectory(prefix="a13n-claw-artifacts-") as directory:
        root = Path(directory)
        subprocess.run(["uv", "venv", str(root / "venv"), "--python", "3.13"], check=True)
        executable = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            ["uv", "pip", "install", "--python", str(executable), str(wheels[0])], check=True
        )
        result = subprocess.run(
            [str(executable), "-m", "a13n_claw", "--version"],
            cwd=root,
            check=True,
            text=True,
            capture_output=True,
        )
        if result.stdout.strip() != f"a13n-claw {expected}":
            raise ValueError(f"Unexpected installed version: {result.stdout!r}")
        subprocess.run(
            ["uv", "build", str(sources[0]), "--wheel", "--out-dir", str(root / "rebuilt")],
            cwd=root,
            check=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.0.0")
    args = parser.parse_args()
    check(Path("dist").resolve(), args.version)
