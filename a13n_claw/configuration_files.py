"""One file authority shared by CLI, Web and startup; SQLite is its loaded projection."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import JsonValue, ValidationError
from ruamel.yaml import YAML

from a13n_claw.attention import ChannelPolicy
from a13n_claw.configuration import (
    GROUPS,
    Configuration,
    ServerSettings,
    configuration_issues,
    dump_configuration,
    export_configuration,
    load_projection,
    parse_configuration,
    parse_yaml,
)
from a13n_claw.domain import ClawError, ResourceChange
from a13n_claw.instance import InstanceLease
from a13n_claw.storage import Store


def revision(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def patch_yaml(text: str, data: dict) -> str:
    """Preserve comments and untouched nodes when adapting older resource operations."""
    codec = YAML()
    codec.preserve_quotes = True
    tree = codec.load(text)

    def sync(target, source):
        for key in list(target):
            if key not in source:
                del target[key]
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                sync(target[key], value)
            elif target.get(key) != value or key not in target:
                target[key] = value

    sync(tree, data)
    output = io.StringIO()
    codec.dump(tree, output)
    return output.getvalue()


class ConfigurationFile:
    def __init__(self, store: Store, path: Path, *, launch: ServerSettings | None = None):
        self.store = store
        self.path = path.expanduser().resolve()
        self.launch = launch
        self._lock = threading.RLock()

    @contextmanager
    def _owned(self, actor_id: str):
        # Check before exposing paths or touching the filesystem, including on retries.
        self.store.principal(actor_id).require("admin")
        with self._lock, InstanceLease(self.path.parent, filename=f".{self.path.name}.lock"):
            yield

    def _source(self) -> tuple[str, str]:
        try:
            content = self.path.read_bytes()
        except FileNotFoundError:
            return "", "missing"
        if len(content) > 2_000_000:
            raise ClawError("configuration_size", "Configuration exceeds 2 MB", 422)
        try:
            return content.decode("utf-8"), revision(content)
        except UnicodeError as error:
            raise ClawError(
                "configuration_encoding", "Configuration must use UTF-8", 422
            ) from error

    def _active(self) -> dict | None:
        with self.store._connection() as db:
            row = db.execute("SELECT * FROM configuration_state WHERE id=1").fetchone()
            return dict(row) if row else None

    def _status(self) -> dict[str, JsonValue]:
        text, source_revision = self._source()
        active = self._active()
        active_revision = (
            active["revision"] if active and active["path"] == str(self.path) else None
        )
        issues: list[JsonValue] = []
        document = None
        restart: list[JsonValue] = []
        try:
            parsed = parse_configuration(text)
            # Return authored fields, not hundreds of normalized defaults to write back.
            authored = parse_yaml(text)
            assert isinstance(authored, dict)
            document = authored
            if self.launch is not None:
                previous = self.launch.model_dump()
                restart = [
                    key
                    for key, value in parsed.server.model_dump().items()
                    if value != previous[key]
                ]
        except (ValidationError, ClawError) as error:
            issues = configuration_issues(error)
        return {
            "path": str(self.path),
            "text": text,
            "revision": source_revision,
            "active_revision": active_revision,
            "pending": source_revision != active_revision,
            "document": document,
            "issues": issues,
            "restart_required": restart,
        }

    def status(self, actor_id: str) -> dict[str, JsonValue]:
        with self._owned(actor_id):
            return self._status()

    def preview(self, actor_id: str, text: str) -> dict[str, JsonValue]:
        with self._owned(actor_id):
            try:
                document = parse_configuration(text)
                changes = load_projection(
                    self.store,
                    actor_id,
                    document,
                    self.path,
                    revision(text.encode()),
                    validate_only=True,
                )
            except (ValidationError, ClawError) as error:
                return {"valid": False, "issues": configuration_issues(error), "changes": []}
            return {"valid": True, "issues": [], "changes": changes}

    def _replace(self, text: str, expected: str) -> None:
        if self._source()[1] != expected:
            raise ClawError(
                "configuration_conflict", "Configuration file changed; keep your draft and compare"
            )
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(dir=self.path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(text.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            if self.path.exists():
                temporary.chmod(stat.S_IMODE(self.path.stat().st_mode))
            # External editors do not take our lock. Detect edits during validation/write.
            if self._source()[1] != expected:
                raise ClawError(
                    "configuration_conflict",
                    "Configuration file changed; keep your draft and compare",
                )
            os.replace(temporary, self.path)
            if os.name != "nt":
                descriptor = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except OSError as error:
            raise ClawError(
                "configuration_write",
                "File save could not be confirmed; inspect the source before retrying",
            ) from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self, actor_id: str, text: str, expected_revision: str) -> dict[str, JsonValue]:
        with self._owned(actor_id):
            current = self._source()[1]
            if current == revision(text.encode()):
                return self._status()
            if current != expected_revision:
                raise ClawError(
                    "configuration_conflict",
                    "Configuration file changed; keep your draft and compare",
                )
            document = parse_configuration(text)
            load_projection(
                self.store,
                actor_id,
                document,
                self.path,
                revision(text.encode()),
                validate_only=True,
            )
            self._replace(text, expected_revision)
            return self._status()

    def _reload(
        self, actor_id: str, expected_revision: str, *, dry_run: bool = False
    ) -> dict[str, JsonValue]:
        text, current = self._source()
        if current != expected_revision:
            raise ClawError(
                "configuration_conflict", "Configuration file changed; inspect it before reloading"
            )
        document = parse_configuration(text)
        changes = load_projection(
            self.store, actor_id, document, self.path, current, validate_only=dry_run
        )
        return {**self._status(), "changes": changes, "applied": not dry_run}

    def reload(
        self, actor_id: str, expected_revision: str, *, dry_run: bool = False
    ) -> dict[str, JsonValue]:
        with self._owned(actor_id):
            return self._reload(actor_id, expected_revision, dry_run=dry_run)

    def initialize(self, *, migrate: bool = False) -> None:
        with self._owned("operator"):
            text, current = self._source()
            if current == "missing":
                with self.store._connection() as db:
                    has_state = bool(
                        db.execute(
                            "SELECT 1 FROM resources UNION ALL SELECT 1 FROM channels LIMIT 1"
                        ).fetchone()
                    )
                if has_state and not migrate:
                    raise ClawError(
                        "configuration_missing",
                        "Configuration file missing for an existing Instance; "
                        "run config migrate --offline",
                    )
                text = dump_configuration(
                    export_configuration(self.store, "operator") if migrate else Configuration()
                )
                self._replace(text, "missing")
            elif migrate:
                raise ClawError(
                    "configuration_exists", "Migration requires a new configuration file"
                )
            self._reload("operator", revision(text.encode()))

    def _editable(self) -> tuple[str, str, dict]:
        text, current = self._source()
        active = self._active()
        if not active or active["path"] != str(self.path) or active["revision"] != current:
            raise ClawError(
                "configuration_pending",
                "The file has unloaded changes; review and reload before editing resources",
            )
        data = parse_yaml(text)
        assert isinstance(data, dict)
        return text, current, data

    def update_resources(self, actor_id: str, changes: list[ResourceChange]):
        """Compatibility API still writes the file, never an independent DB catalog."""
        with self._owned(actor_id):
            text, current, data = self._editable()
            if len({(item.kind, item.id) for item in changes}) != len(changes):
                raise ClawError("duplicate_resource", "A resource can only be edited once", 422)
            saved = {(r.kind, r.id): r for r in self.store.resources(actor_id)}
            groups = {value: key for key, value in GROUPS.items()}
            retired = list(data.get("retired", []))
            for item in changes:
                prior = saved.get((item.kind, item.id))
                if item.expected_version != (prior.version if prior else 0):
                    raise ClawError(
                        "version_conflict", "Resource changed; keep your draft and refresh"
                    )
                if item.kind == "defaults":
                    data["defaults"] = item.content
                else:
                    data.setdefault(groups[item.kind], {})[item.id] = item.content
                key = f"{item.kind}/{item.id}"
                retired = [value for value in retired if value != key]
                if item.retired:
                    retired.append(key)
            if retired:
                data["retired"] = retired
            else:
                data.pop("retired", None)
            candidate = patch_yaml(text, data)
            document = parse_configuration(candidate)
            new_revision = revision(candidate.encode())
            load_projection(
                self.store, actor_id, document, self.path, new_revision, validate_only=True
            )
            self._replace(candidate, current)
            self._reload(actor_id, new_revision)
            saved = {(r.kind, r.id): r for r in self.store.resources(actor_id)}
            return [saved[item.kind, item.id] for item in changes]

    def update_channel(
        self, actor_id: str, identity: str, policy: ChannelPolicy, expected_version: int
    ):
        with self._owned(actor_id):
            text, current, data = self._editable()
            with self.store._connection() as db:
                row = db.execute("SELECT version FROM channels WHERE id=?", (identity,)).fetchone()
                if expected_version != (row[0] if row else 0):
                    raise ClawError(
                        "version_conflict", "Channel changed; keep your draft and refresh"
                    )
            data.setdefault("channels", {})[identity] = policy.model_dump(mode="json")
            candidate = patch_yaml(text, data)
            document = parse_configuration(candidate)
            new_revision = revision(candidate.encode())
            load_projection(
                self.store, actor_id, document, self.path, new_revision, validate_only=True
            )
            self._replace(candidate, current)
            self._reload(actor_id, new_revision)
            with self.store._connection() as db:
                row = db.execute(
                    "SELECT version,policy FROM channels WHERE id=?", (identity,)
                ).fetchone()
                return {"id": identity, "version": row[0], "policy": json.loads(row[1])}
