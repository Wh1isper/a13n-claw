"""Release compatibility and image selection contracts."""

from pathlib import Path
from tomllib import loads

import pytest
from packaging.specifiers import SpecifierSet

from scripts import prepare_release, service_version


def project(requirement="a13n-service>=0.6.0,<0.7.0"):
    return {"project": {"dependencies": [requirement]}}


def test_release_version_matches_minor_line(tmp_path):
    source = Path("pyproject.toml").read_text()
    for release, expected in (("0.6.0", "0.6.0"), ("0.6.0-rc.1", "0.6.0rc1")):
        path = tmp_path / "pyproject.toml"
        path.write_text(source)
        prepare_release.prepare(path, release)
        assert loads(path.read_text())["project"]["version"] == expected
    for release in ("0.2.0", "0.7.0", "0.0.0", "0.6.0-rc.0"):
        path = tmp_path / "pyproject.toml"
        path.write_text(source)
        with pytest.raises(ValueError):
            prepare_release.prepare(path, release)
        assert path.read_text() == source


@pytest.mark.parametrize(
    "requirement",
    [
        "a13n-service==0.6.0",
        "a13n-service>=0.6.0",
        "a13n-service>=0.6.0,<0.8.0",
        "a13n-service>=0.6.0,<0.7.0,!=0.6.2",
        "a13n-service>=0.6.0rc1,<0.7.0",
    ],
)
def test_rejects_ambiguous_or_unbounded_service_range(requirement):
    with pytest.raises(ValueError):
        service_version.service_line(project(requirement))


def test_resolves_latest_non_yanked_stable_and_locked():
    spec, line = service_version.service_line(project())
    releases = {
        "0.6.0": [{"yanked": False}],
        "0.6.2": [{"yanked": True}],
        "0.6.3rc1": [{"yanked": False}],
        "0.6.4": [{"yanked": True}, {"yanked": False}],
        "0.6.5": [],
        "0.6.9.dev1": [{"yanked": False}],
        "0.7.0": [{"yanked": False}],
        "invalid": [{"yanked": False}],
    }
    lock = {"package": [{"name": "a13n-service", "version": "0.6.0"}]}
    assert line == (0, 6)
    assert service_version.latest_version(releases, spec) == "0.6.4"
    assert service_version.resolve("locked", project(), lock) == "0.6.0"
    assert service_version.resolve("latest", project(), lock, releases) == "0.6.4"
    with pytest.raises(ValueError):
        service_version.latest_version({"0.6.2": [{"yanked": True}]}, spec)
    with pytest.raises(ValueError):
        service_version.locked_version(
            {"package": [{"name": "a13n-service", "version": "0.7.0"}]}, spec
        )


def test_docker_defaults_match_lock():
    lock = {"package": [{"name": "a13n-service", "version": "0.6.0"}]}
    docker = (
        "ARG SERVICE_IMAGE=ghcr.io/converge-ai-labs/a13n-service:0.6.0\nARG SERVICE_VERSION=0.6.0\n"
    )
    assert service_version.validate_defaults(project(), lock, docker) == "0.6.0"
    for bad in (
        docker.replace("SERVICE_VERSION=0.6.0", "SERVICE_VERSION=0.6.2"),
        docker.replace(":0.6.0", ":0.6.2"),
    ):
        with pytest.raises(ValueError):
            service_version.validate_defaults(project(), lock, bad)
    assert "0.6.0" in SpecifierSet(str(service_version.service_line(project())[0]))
