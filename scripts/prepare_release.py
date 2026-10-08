"""Inject a canonical release version into an ephemeral build checkout."""

import argparse
import re
from pathlib import Path

_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-rc\.([1-9][0-9]*))?")


def python_version(value: str) -> str:
    if not _PATTERN.fullmatch(value) or value == "0.0.0":
        raise ValueError(f"Invalid release version: {value!r}")
    return value.replace("-rc.", "rc")


def prepare(path: Path, value: str) -> None:
    normalized = python_version(value)
    source = path.read_text()
    updated, count = re.subn(
        r'^version = "0\.0\.0"$', f'version = "{normalized}"', source, flags=re.M
    )
    if count != 1:
        raise ValueError("Expected exactly one source version = 0.0.0")
    path.write_text(updated)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    args = parser.parse_args()
    prepare(Path("pyproject.toml"), args.version)


if __name__ == "__main__":
    main()
