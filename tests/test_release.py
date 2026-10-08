import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "prepare_release", Path(__file__).parents[1] / "scripts/prepare_release.py"
)
assert _spec is not None and _spec.loader is not None
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


@pytest.mark.parametrize("value,expected", [("0.0.1", "0.0.1"), ("1.2.3-rc.4", "1.2.3rc4")])
def test_release_version(value, expected):
    assert release.python_version(value) == expected


@pytest.mark.parametrize(
    "value", ["0.0.0", "v0.0.1", "01.0.0", "1.0", "1.2.3-rc.0", "1.2.3rc1", "1.2.3\n"]
)
def test_reject_invalid_versions(value):
    with pytest.raises(ValueError):
        release.python_version(value)


def test_prepare_changes_only_source_version(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nversion = "0.0.0"\nname = "a13n-claw"\n')
    release.prepare(path, "0.0.1")
    assert path.read_text() == '[project]\nversion = "0.0.1"\nname = "a13n-claw"\n'
    with pytest.raises(ValueError):
        release.prepare(path, "0.0.2")
