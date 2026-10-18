import subprocess
import sys
from pathlib import Path

import pytest

from a13n_claw.errors import ClawError
from a13n_claw.instance import InstanceLease, operator_token, resolve_workspace


def test_instance_lease_excludes_other_process_and_releases_on_close(tmp_path: Path):
    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; from a13n_claw.instance import InstanceLease; "
            f"lease = InstanceLease(Path({str(tmp_path)!r})); lease.acquire(); lease.close()"
        ),
    ]
    with InstanceLease(tmp_path):
        blocked = subprocess.run(command, capture_output=True, text=True)
        assert blocked.returncode != 0
        assert "Another server owns" in blocked.stderr
    assert subprocess.run(command, capture_output=True).returncode == 0


def test_operator_token_stable_and_not_stored_as_hash(tmp_path: Path):
    with InstanceLease(tmp_path):
        first = operator_token(tmp_path)
        assert operator_token(tmp_path) == first
        assert len(first) >= 32
        assert (tmp_path / "operator.token").read_text().strip() == first


def test_workspace_resolves_against_startup_not_data_root(tmp_path: Path):
    startup = tmp_path / "work"
    selected = startup / "selected"
    selected.mkdir(parents=True)
    data = tmp_path / "data"
    assert resolve_workspace(startup, None, data) == startup
    assert resolve_workspace(startup, Path("selected"), data) == selected
    with pytest.raises(ClawError, match="separate directories"):
        resolve_workspace(startup, None, startup / "data")


def test_operator_missing_file_and_offline_recovery(tmp_path):
    from a13n_claw.instance import reset_operator

    (tmp_path / "claw.yaml").write_text("schema_version: 1\n")
    with InstanceLease(tmp_path):
        first = operator_token(tmp_path)
        (tmp_path / "business.sqlite3").touch()
        (tmp_path / "operator.token").unlink()
        with pytest.raises(ClawError, match="missing"):
            operator_token(tmp_path)
        with pytest.raises(ClawError, match="Another server"):
            reset_operator(tmp_path)
    restored = reset_operator(tmp_path).read_text().strip()
    assert restored != first
    with InstanceLease(tmp_path):
        assert operator_token(tmp_path) == restored


def test_reset_rejects_legacy_root_without_replacing_token(tmp_path):
    from a13n_claw.instance import reset_operator

    (tmp_path / "claw.sqlite3").write_bytes(b"preserve legacy bytes")
    token = tmp_path / "operator.token"
    token.write_text("original-token\n")
    with pytest.raises(ValueError, match="standalone"):
        reset_operator(tmp_path)
    assert token.read_text() == "original-token\n"
    assert (tmp_path / "claw.sqlite3").read_bytes() == b"preserve legacy bytes"
