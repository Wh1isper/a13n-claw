import subprocess
import sys
from pathlib import Path

import pytest

from a13n_claw.domain import ClawError
from a13n_claw.instance import InstanceLease, operator_token, resolve_workspace, token_hash


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
        assert token_hash(first) != first


def test_workspace_resolves_against_startup_not_data_root(tmp_path: Path):
    startup = tmp_path / "work"
    selected = startup / "selected"
    selected.mkdir(parents=True)
    data = tmp_path / "data"
    assert resolve_workspace(startup, None, data) == startup
    assert resolve_workspace(startup, Path("selected"), data) == selected
    with pytest.raises(ClawError, match="separate directories"):
        resolve_workspace(startup, None, startup / "data")


def test_operator_mismatch_missing_file_and_offline_recovery(tmp_path):
    from a13n_claw.instance import reset_operator
    from a13n_claw.storage import Store

    with InstanceLease(tmp_path):
        first = operator_token(tmp_path)
        store = Store(tmp_path / "claw.sqlite3")
        store.bootstrap(token_hash(first))
        with pytest.raises(ClawError, match="does not match"):
            store.bootstrap(token_hash("changed"))
        (tmp_path / "operator.token").unlink()
        with pytest.raises(ClawError, match="missing"):
            operator_token(tmp_path)
        with pytest.raises(ClawError, match="Another server"):
            reset_operator(tmp_path)
    restored = reset_operator(tmp_path).read_text().strip()
    assert restored != first
    assert store.authenticate(token_hash(restored)).admin
    with pytest.raises(ClawError, match="Invalid"):
        store.authenticate(token_hash(first))
