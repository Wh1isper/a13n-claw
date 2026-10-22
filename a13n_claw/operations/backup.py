"""Offline, checksummed Instance snapshots without interpreting either owner's database."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from a13n_claw import __version__
from a13n_claw.configuration.models import read_settings, selected_path
from a13n_claw.operations.instance import InstanceLease

_MARKER = "restore.incomplete"


def inventory(root: Path) -> tuple[dict[str, str], list[str]]:
    files: dict[str, str] = {}
    directories = []

    def fail_enumeration(error: OSError) -> None:
        raise error

    for parent, dirs, names in os.walk(root, onerror=fail_enumeration):
        base = Path(parent)
        for name in dirs + names:
            path = base / name
            if path.is_symlink():
                raise ValueError(
                    "Snapshot requires regular files; back up symlink targets separately"
                )
        directories.extend((base / name).relative_to(root).as_posix() for name in dirs)
        for name in names:
            path = base / name
            relative = path.relative_to(root).as_posix()
            if relative == "server.lock":
                continue
            if not path.is_file():
                raise ValueError("Snapshot contains a non-regular file")
            with path.open("rb") as stream:
                files[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
    return files, sorted(directories)


def contained_host(root: Path) -> None:
    settings = read_settings(root)
    for value in (settings.host_configuration, settings.host_data):
        if not selected_path(root, value).is_relative_to(root.resolve()):
            raise ValueError(
                "Host configuration/data outside the Instance requires an operator-managed "
                "coordinated snapshot; this command covers self-contained roots"
            )


def backup(root: Path, destination: Path, *, acknowledge_external_state: bool) -> dict[str, Any]:
    if not acknowledge_external_state:
        raise ValueError("Acknowledge separate backup/quiescence of workspaces and external state")
    root, destination = root.resolve(), destination.expanduser().resolve()
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError("Backup destination must be separate from the Instance")
    with InstanceLease(root):
        contained_host(root)
        files, directories = inventory(root)
        destination.mkdir(mode=0o700, parents=False, exist_ok=False)
        payload = destination / "instance"
        payload.mkdir(mode=0o700)
        for directory in directories:
            (payload / directory).mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in files:
            shutil.copy2(root / name, payload / name)
        if inventory(payload) != (files, directories) or inventory(root) != (files, directories):
            raise ValueError(
                "Snapshot sources changed; incomplete backup was retained for inspection"
            )
        manifest = {
            "format": 1,
            "version": __version__,
            "created": time.time(),
            "source_root": str(root),
            "files": files,
            "directories": directories,
            "external_state": "Working directories, external memory/Skills, executors, browser "
            "volumes, environment credentials and remote systems require separate backup",
        }
        with (destination / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        return {
            "backup": str(destination),
            "files": len(files),
            "external_state": manifest["external_state"],
        }


def verify(snapshot: Path) -> dict[str, Any]:
    snapshot = snapshot.expanduser().resolve()
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1:
        raise ValueError("Unsupported backup format")
    payload = snapshot / "instance"
    if payload.is_symlink() or not payload.is_dir():
        raise ValueError("Invalid backup payload")
    files, directories = inventory(payload)
    if files != manifest.get("files") or directories != manifest.get("directories"):
        raise ValueError("Backup checksum or file inventory mismatch")
    if "claw.yaml" not in files or "operator.token" not in files or _MARKER in files:
        raise ValueError("Incomplete Instance backup")
    return manifest


def restore(root: Path, snapshot: Path, *, acknowledge_external_state: bool) -> dict[str, Any]:
    if not acknowledge_external_state:
        raise ValueError(
            "Acknowledge that restore cannot undo external effects and may repeat work"
        )
    root, snapshot = root.resolve(), snapshot.expanduser().resolve()
    if root.is_relative_to(snapshot) or snapshot.is_relative_to(root):
        raise ValueError("Restore destination must be separate from the backup")
    manifest = verify(snapshot)
    payload = snapshot / "instance"
    with InstanceLease(root):
        if any(path.name != "server.lock" for path in root.iterdir()):
            raise ValueError(
                "Restore requires an empty root; existing Instance data is never replaced"
            )
        marker = root / _MARKER
        marker.write_text("Incomplete restore; do not start this Instance\n", encoding="utf-8")
        # Enumerate actual verified paths rather than using untrusted manifest paths for writes.
        files, directories = inventory(payload)
        for directory in directories:
            (root / directory).mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in files:
            shutil.copy2(payload / name, root / name)
        actual, restored_directories = inventory(root)
        actual.pop(_MARKER)
        if actual != manifest["files"] or restored_directories != manifest["directories"]:
            raise ValueError("Restored inventory mismatch; incomplete marker blocks startup")
        # Absolute bootstrap paths intentionally keep their original meaning. A different
        # destination needs explicit operator editing before opening the Host.
        marker.unlink()
        return {
            "root": str(root),
            "source_root": manifest["source_root"],
            "files": len(files),
            "next": "Reconcile external effects and paths before starting one owner; "
            "restored unknown attempts remain blocked",
        }
