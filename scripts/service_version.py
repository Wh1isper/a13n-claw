"""Resolve one Service version and export its hashed image requirements."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

IMAGE = "ghcr.io/converge-ai-labs/a13n-service"


def service_line(project: dict) -> tuple[SpecifierSet, tuple[int, int]]:
    dependencies = [Requirement(value) for value in project["project"]["dependencies"]]
    matches = [dep for dep in dependencies if dep.name.lower().replace("_", "-") == "a13n-service"]
    if len(matches) != 1 or matches[0].marker or matches[0].extras or matches[0].url:
        raise ValueError("Expected exactly one unconditional a13n-service dependency")
    specifiers = list(matches[0].specifier)
    lower = [s for s in specifiers if s.operator == ">="]
    upper = [s for s in specifiers if s.operator == "<"]
    if len(specifiers) != 2 or len(lower) != 1 or len(upper) != 1:
        raise ValueError("Service requires a bounded single minor compatibility line")
    try:
        low, high = Version(lower[0].version), Version(upper[0].version)
    except InvalidVersion as error:
        raise ValueError("Invalid Service compatibility bounds") from error
    if (
        len(low.release) != 3
        or len(high.release) != 3
        or low.is_prerelease
        or low.is_postrelease
        or low.is_devrelease
        or low.local
        or low.epoch
        or high.is_prerelease
        or high.is_postrelease
        or high.is_devrelease
        or high.local
        or high.epoch
        or high.release != (low.major, low.minor + 1, 0)
    ):
        raise ValueError("Service requires >=X.Y.M,<X.(Y+1).0")
    return matches[0].specifier, (low.major, low.minor)


def locked_version(lock: dict, spec: SpecifierSet) -> str:
    versions = [p["version"] for p in lock["package"] if p["name"] == "a13n-service"]
    if len(versions) != 1 or Version(versions[0]) not in spec:
        raise ValueError("Locked Service version is missing or outside the compatibility line")
    return versions[0]


def latest_version(releases: dict, spec: SpecifierSet) -> str:
    candidates = []
    for text, files in releases.items():
        try:
            value = Version(text)
        except InvalidVersion:
            continue
        if (
            len(value.release) != 3
            or value.epoch
            or value.is_prerelease
            or value.is_devrelease
            or value.is_postrelease
            or value.local
        ):
            continue
        if value in spec and any(not item.get("yanked", False) for item in files):
            candidates.append(value)
    if not candidates:
        raise ValueError("No stable non-yanked Service release satisfies the compatibility line")
    return str(max(candidates))


def docker_defaults(dockerfile: str) -> tuple[str, str]:
    image = re.search(r"^ARG SERVICE_IMAGE=(\S+)$", dockerfile, re.M)
    version = re.search(r"^ARG SERVICE_VERSION=(\S+)$", dockerfile, re.M)
    if image is None or version is None:
        raise ValueError("Dockerfile must declare Service image and version defaults")
    return image.group(1), version.group(1)


def validate_defaults(project: dict, lock: dict, dockerfile: str) -> str:
    spec, _ = service_line(project)
    version = locked_version(lock, spec)
    image, default = docker_defaults(dockerfile)
    if default != version or not re.fullmatch(
        rf"{re.escape(IMAGE)}:{re.escape(version)}(?:@sha256:[0-9a-f]{{64}})?", image
    ):
        raise ValueError("Docker defaults must match the locked Service version")
    return version


def resolve(mode: str, project: dict, lock: dict, releases: dict | None = None) -> str:
    spec, _ = service_line(project)
    if mode == "locked":
        return locked_version(lock, spec)
    if releases is None:
        with urlopen("https://pypi.org/pypi/a13n-service/json", timeout=30) as response:
            releases = json.load(response)["releases"]
    assert releases is not None
    return latest_version(releases, spec)


def export(mode: str, version: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if mode == "locked":
        subprocess.run(
            [
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--quiet",
                "--output-file",
                str(output),
            ],
            check=True,
        )
    else:
        with TemporaryDirectory(prefix="claw-service-constraints-") as temp:
            constraint = Path(temp) / "constraints.txt"
            constraint.write_text(f"a13n-service=={version}\n")
            subprocess.run(
                [
                    "uv",
                    "pip",
                    "compile",
                    "pyproject.toml",
                    "--constraint",
                    str(constraint),
                    "--quiet",
                    "--generate-hashes",
                    "--universal",
                    "--python-version",
                    "3.13",
                    "--no-emit-package",
                    "a13n-claw",
                    "--output-file",
                    str(output),
                ],
                check=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("locked", "latest"), default="locked")
    parser.add_argument("--output", type=Path, default=Path("build/image-requirements.txt"))
    parser.add_argument("--selection", type=Path, default=Path("build/service-selection.json"))
    args = parser.parse_args()
    project = tomllib.loads(Path("pyproject.toml").read_text())
    lock = tomllib.loads(Path("uv.lock").read_text())
    version = resolve(args.mode, project, lock)
    image = f"{IMAGE}:{version}"
    if args.mode == "locked":
        validate_defaults(project, lock, Path("deploy/docker/Dockerfile").read_text())
        image = Path("deploy/docker/service-image.lock").read_text().strip()
        if not re.fullmatch(
            rf"{re.escape(IMAGE)}:{re.escape(version)}@sha256:[0-9a-f]{{64}}", image
        ):
            raise ValueError("Locked Service image digest must match uv.lock")
    export(args.mode, version, args.output)
    args.selection.parent.mkdir(parents=True, exist_ok=True)
    args.selection.write_text(json.dumps({"version": version, "image": image}) + "\n")
    print(json.dumps({"version": version, "image": image}))


if __name__ == "__main__":
    main()
