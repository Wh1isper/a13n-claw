"""One process owns a local application data root for its entire server lifetime."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from a13n_claw.errors import ClawError


class InstanceLease:
    """Kernel-backed exclusion, released on process death without stale lock deletion."""

    def __init__(self, data_root: Path, *, filename: str = "server.lock"):
        self.data_root = data_root.resolve()
        self.filename = filename
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        if self._file is not None:
            raise ClawError("instance_owned", "This lease has already been acquired")
        self.data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = (self.data_root / self.filename).open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise ClawError("instance_owned", "Another server owns this data root") from exc
        self._file = handle

    def close(self) -> None:
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self) -> InstanceLease:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def operator_token(data_root: Path) -> str:
    """Call under the Instance lease; never put the token in logs or configuration exports."""
    path = data_root / "operator.token"
    if path.exists():
        token = path.read_text().strip()
        if len(token) < 32:
            raise ClawError("operator_token_invalid", "Operator token file is invalid")
        return token
    if (data_root / "business.sqlite3").exists():
        raise ClawError(
            "operator_token_missing",
            "Operator token is missing; stop the server and run reset-operator",
        )
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(token + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return token


def reset_operator(data_root: Path) -> Path:
    """Explicit offline recovery also revokes the old operator credential."""
    from a13n_claw.configuration.models import read_settings

    with InstanceLease(data_root):
        # Reject legacy or uninitialized roots before changing credentials.
        read_settings(data_root)
        token = secrets.token_urlsafe(32)
        path = data_root / "operator.token"
        # The token file is the sole authority; replacement revokes the old token.
        with NamedTemporaryFile(mode="w", dir=data_root, delete=False) as temporary:
            temporary.write(token + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary.name, path)
        return path


def resolve_workspace(startup: Path, configured: Path | None, data_root: Path) -> Path:
    selected = configured if configured is not None else startup
    workspace = (selected if selected.is_absolute() else startup / selected).resolve()
    data = data_root.resolve()
    if workspace == data or workspace.is_relative_to(data) or data.is_relative_to(workspace):
        raise ClawError(
            "workspace_data_overlap", "Workspace and application data must be separate directories"
        )
    if not workspace.is_dir():
        raise ClawError("workspace_missing", "Configured workspace directory does not exist")
    return workspace
