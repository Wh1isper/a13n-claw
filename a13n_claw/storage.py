"""SQLite application boundaries, committed before any external execution.

Methods are synchronous, short transactions. The async application calls them in a
worker thread; no transaction is exposed to a caller or held across an await.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from a13n_harness import DeferredToolResume, HarnessState
from pydantic import JsonValue, TypeAdapter

from a13n_claw.decisions import REQUESTS, RESULTS, validate_answer, validate_wait
from a13n_claw.domain import (
    MAX_ASSET_BYTES,
    TERMINAL,
    AssetRecord,
    ClawError,
    Composition,
    DelegationRecord,
    EnvironmentVariable,
    InputReceipt,
    InputRequest,
    InstanceDefaults,
    Principal,
    ProfileDefinition,
    Resource,
    ResourceChange,
    RunRecord,
    SkillDefinition,
    TargetRecord,
    ThreadRecord,
    canonical,
    digest,
    new_id,
    now,
    validate_resource,
)

_SCHEMA = """
CREATE TABLE resources (
    kind TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
    content TEXT NOT NULL, retired INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(kind, id)
);
CREATE TABLE credentials (
    name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE principals (
    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, content TEXT NOT NULL
);
CREATE TABLE threads (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, profile_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, checkpoint_id TEXT,
    parent_thread_id TEXT REFERENCES threads(id), parent_run_id TEXT,
    fork_checkpoint_id TEXT REFERENCES checkpoints(id),
    archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE runs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
    thread_id TEXT NOT NULL REFERENCES threads(id),
    actor_id TEXT NOT NULL REFERENCES principals(id),
    status TEXT NOT NULL, composition TEXT NOT NULL, checkpoint_id TEXT,
    recovery_of TEXT REFERENCES runs(id), recovery_required INTEGER NOT NULL DEFAULT 0,
    reconciled_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    output TEXT, error TEXT, owner TEXT, created_at TEXT NOT NULL, finished_at TEXT
);
CREATE INDEX runs_thread_order ON runs(thread_id, sequence);
CREATE UNIQUE INDEX one_thread_owner ON runs(thread_id)
    WHERE status IN ('preparing', 'running', 'waiting');
CREATE TABLE inputs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
    thread_id TEXT NOT NULL REFERENCES threads(id), run_id TEXT NOT NULL REFERENCES runs(id),
    actor_id TEXT NOT NULL REFERENCES principals(id), request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL,
    disposition TEXT NOT NULL, created_at TEXT NOT NULL,
    attachment_ids TEXT NOT NULL DEFAULT '[]',
    UNIQUE(actor_id, request_id)
);
CREATE INDEX inputs_run_order ON inputs(run_id, sequence);
CREATE TABLE checkpoints (
    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id),
    run_id TEXT REFERENCES runs(id), state TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE decisions (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
    checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id), requests TEXT NOT NULL,
    response TEXT, actor_id TEXT REFERENCES principals(id), created_at TEXT NOT NULL,
    answered_at TEXT
);
CREATE UNIQUE INDEX one_pending_decision ON decisions(run_id) WHERE response IS NULL;
CREATE TABLE deferred_inputs (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id),
    requests TEXT NOT NULL, response TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES principals(id)
);
CREATE TABLE delegations (
    id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL REFERENCES runs(id),
    child_run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
    request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
    cancel_policy TEXT NOT NULL, notify INTEGER NOT NULL,
    delivery_input_id TEXT REFERENCES inputs(id), delivery_error TEXT,
    UNIQUE(parent_run_id,request_id)
);
CREATE TABLE forks (
    actor_id TEXT NOT NULL REFERENCES principals(id), request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL, thread_id TEXT NOT NULL REFERENCES threads(id),
    PRIMARY KEY(actor_id,request_id)
);
CREATE TABLE recoveries (
    actor_id TEXT NOT NULL REFERENCES principals(id), request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(id),
    PRIMARY KEY(actor_id, request_id)
);
CREATE TABLE audit (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, subject_id TEXT NOT NULL,
    detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE targets (
    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id), generation TEXT NOT NULL,
    definition TEXT NOT NULL, provider_state TEXT, status TEXT NOT NULL, operation_id TEXT,
    error TEXT, updated_at TEXT NOT NULL, UNIQUE(thread_id, generation)
);
CREATE TABLE assets (
    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id),
    run_id TEXT REFERENCES runs(id), actor_id TEXT NOT NULL REFERENCES principals(id),
    request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, kind TEXT NOT NULL,
    name TEXT NOT NULL, media_type TEXT NOT NULL, size INTEGER NOT NULL,
    sha256 TEXT NOT NULL, created_at TEXT NOT NULL, content BLOB NOT NULL,
    UNIQUE(actor_id,request_id)
);
PRAGMA user_version = 1;
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                # A single startup owner applies the initial schema atomically.
                db.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA + "\nCOMMIT;")
            elif version != 1:
                raise ClawError(
                    "schema_incompatible", "This database requires another Claw version"
                )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _audit(db: sqlite3.Connection, kind: str, subject: str, detail: object) -> None:
        db.execute(
            "INSERT INTO audit(kind, subject_id, detail, created_at) VALUES (?, ?, ?, ?)",
            (kind, subject, canonical(detail), now()),
        )

    @staticmethod
    def _principal(db: sqlite3.Connection, actor_id: str) -> Principal:
        row = db.execute("SELECT content FROM principals WHERE id=?", (actor_id,)).fetchone()
        if row is None:
            raise ClawError("unauthorized", "Unknown client", 401)
        actor = Principal.model_validate_json(row[0])
        if not actor.enabled:
            raise ClawError("access_revoked", "This client is disabled", 403)
        return actor

    def bootstrap(self, token_hash: str) -> None:
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM principals LIMIT 1").fetchone() is None:
                principal = Principal(id="operator", admin=True)
                db.execute(
                    "INSERT INTO principals VALUES (?, ?, ?)",
                    (principal.id, token_hash, principal.model_dump_json()),
                )
            elif (
                db.execute(
                    "SELECT 1 FROM principals WHERE id='operator' AND token_hash=?", (token_hash,)
                ).fetchone()
                is None
            ):
                raise ClawError(
                    "operator_token_mismatch",
                    "Operator token does not match the database; "
                    "stop the server and run reset-operator",
                )

    def reset_operator(self, token_hash: str) -> None:
        """Offline local administration, called only while holding the Instance lease."""
        with self._transaction() as db:
            principal = Principal(id="operator", admin=True)
            db.execute(
                "INSERT INTO principals VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "token_hash=excluded.token_hash,content=excluded.content",
                (principal.id, token_hash, principal.model_dump_json()),
            )
            self._audit(db, "operator_token_reset", "operator", {})

    def authenticate(self, token_hash: str) -> Principal:
        with self._connection() as db:
            row = db.execute(
                "SELECT id FROM principals WHERE token_hash=?", (token_hash,)
            ).fetchone()
            if row is None:
                raise ClawError("unauthorized", "Invalid access token", 401)
            return self._principal(db, row[0])

    def principal(self, actor_id: str) -> Principal:
        with self._connection() as db:
            return self._principal(db, actor_id)

    def set_principal(
        self,
        actor_id: str,
        principal: Principal,
        token_hash: str | None,
        *,
        create_only: bool = False,
    ) -> None:
        with self._transaction() as db:
            self._principal(db, actor_id).require("admin")
            if principal.id == "operator":
                raise ClawError(
                    "operator_managed", "Rotate the operator token through local administration"
                )
            existing = db.execute(
                "SELECT token_hash,content FROM principals WHERE id=?", (principal.id,)
            ).fetchone()
            if create_only and existing is not None:
                raise ClawError("client_exists", "Client identity already exists")
            current = Principal.model_validate_json(existing[1]) if existing else None
            if principal.version != (current.version if current else 1):
                raise ClawError(
                    "client_conflict", "Client grants changed; preserve your draft and reload"
                )
            if current is not None:
                principal = principal.model_copy(update={"version": current.version + 1})
            if token_hash is None:
                if existing is None:
                    raise ClawError("client_missing", "Client not found", 404)
                token_hash = existing[0]
            db.execute(
                "INSERT INTO principals VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "token_hash=excluded.token_hash, content=excluded.content",
                (principal.id, token_hash, principal.model_dump_json()),
            )
            self._audit(db, "principal_updated", principal.id, {"actor": actor_id})

    def principals(self, actor_id: str) -> list[Principal]:
        with self._connection() as db:
            self._principal(db, actor_id).require("admin")
            return [
                Principal.model_validate_json(row[0])
                for row in db.execute("SELECT content FROM principals ORDER BY id")
            ]

    def available_profiles(self, actor_id: str) -> list[Resource]:
        with self._connection() as db:
            actor = self._principal(db, actor_id)
            return [
                self._resource(db, "profile", row[0])
                for row in db.execute(
                    "SELECT id FROM resources WHERE kind='profile' AND retired=0 ORDER BY id"
                )
                if actor.admin or row[0] in actor.profile_ids
            ]

    def history(self, actor_id: str, thread_id: str) -> dict[str, JsonValue]:
        with self._transaction() as db:
            self._principal(db, actor_id).require("read", thread_id)
            thread = self._thread(db, thread_id)
            if thread.checkpoint_id is None:
                return {"checkpoint_id": None, "messages": []}
            row = db.execute(
                "SELECT state FROM checkpoints WHERE id=?", (thread.checkpoint_id,)
            ).fetchone()
            if row is None:
                raise ClawError("checkpoint_missing", "Required continuation is missing")
            state = HarnessState.model_validate_json(row[0])
            return {
                "checkpoint_id": thread.checkpoint_id,
                "messages": state.model_dump(mode="json")["message_history"],
            }

    @staticmethod
    def _resource(db: sqlite3.Connection, kind: str, resource_id: str) -> Resource:
        row = db.execute(
            "SELECT * FROM resources WHERE kind=? AND id=?", (kind, resource_id)
        ).fetchone()
        if row is None:
            raise ClawError("resource_missing", f"Missing {kind} resource: {resource_id}", 404)
        return Resource(
            id=row["id"],
            kind=row["kind"],
            version=row["version"],
            content=json.loads(row["content"]),
            retired=bool(row["retired"]),
        )

    def save_resource(
        self,
        actor_id: str,
        kind: str,
        resource_id: str,
        content: dict[str, JsonValue],
        expected_version: int,
        *,
        retired: bool = False,
    ) -> Resource:
        change = ResourceChange(
            id=resource_id,
            kind=kind,
            content=content,
            expected_version=expected_version,
            retired=retired,
        )
        return self.import_resources(actor_id, [change])[0]

    def import_resources(self, actor_id: str, changes: list[ResourceChange]) -> list[Resource]:
        """Publish an entire version-checked configuration import or none of it."""
        if len({(item.kind, item.id) for item in changes}) != len(changes):
            raise ClawError("duplicate_resource", "An import cannot change a resource twice", 422)
        with self._transaction() as db:
            self._principal(db, actor_id).require("admin")
            result: list[Resource] = []
            for item in changes:
                content = validate_resource(item.kind, item.content)
                row = db.execute(
                    "SELECT version FROM resources WHERE kind=? AND id=?", (item.kind, item.id)
                ).fetchone()
                actual = row[0] if row else 0
                if actual != item.expected_version:
                    raise ClawError(
                        "version_conflict", "Resource changed; preserve your draft and refresh"
                    )
                db.execute(
                    "INSERT INTO resources VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(kind,id) DO UPDATE SET "
                    "version=excluded.version, content=excluded.content, retired=excluded.retired",
                    (item.kind, item.id, actual + 1, canonical(content), item.retired),
                )
                self._audit(db, "resource_updated", item.id, {"actor": actor_id, "kind": item.kind})
                result.append(self._resource(db, item.kind, item.id))
            return result

    def resources(self, actor_id: str) -> list[Resource]:
        with self._transaction() as db:
            self._principal(db, actor_id).require("admin")
            rows = db.execute("SELECT kind, id FROM resources ORDER BY kind, id").fetchall()
            return [self._resource(db, row[0], row[1]) for row in rows]

    def _capture(
        self,
        db: sqlite3.Connection,
        actor: Principal,
        profile_id: str,
        workspace: str,
        ancestors: tuple[str, ...] = (),
    ) -> Composition:
        actor.use_profile(profile_id)
        if profile_id in ancestors or len(ancestors) >= 8:
            raise ClawError(
                "delegation_cycle", "Profile delegation must be acyclic and at most 8 levels"
            )
        profile = self._resource(db, "profile", profile_id)
        definition = ProfileDefinition.model_validate(profile.content)
        model = self._resource(db, "model", definition.model_id)
        environment = self._resource(db, "environment", definition.environment_id)
        skills = tuple(self._resource(db, "skill", item) for item in definition.skills)
        servers = tuple(self._resource(db, "mcp", item) for item in definition.mcp_servers)
        names = [SkillDefinition.model_validate(skill.content).name for skill in skills]
        if len(set(names)) != len(names):
            raise ClawError("skill_name_conflict", "Selected Skills must have distinct names", 422)
        if any(value.retired for value in (profile, model, environment, *skills, *servers)):
            raise ClawError("resource_retired", "A selected resource is retired")
        return Composition(
            profile=profile,
            model=model,
            environment=environment,
            skills=skills,
            mcp_servers=servers,
            children=tuple(
                self._capture(db, actor, child, workspace, (*ancestors, profile_id))
                for child in definition.child_profiles
            ),
            workspace=workspace,
        )

    @staticmethod
    def _thread(db: sqlite3.Connection, thread_id: str) -> ThreadRecord:
        row = db.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
        if row is None:
            raise ClawError("thread_missing", "Thread not found", 404)
        return ThreadRecord.model_validate(dict(row))

    def create_thread(
        self,
        actor_id: str,
        title: str,
        profile_id: str | None = None,
        *,
        parent_thread_id: str | None = None,
        parent_run_id: str | None = None,
    ) -> ThreadRecord:
        with self._transaction() as db:
            actor = self._principal(db, actor_id)
            actor.require("create")
            if profile_id is None:
                defaults = self._resource(db, "defaults", "instance")
                profile_id = InstanceDefaults.model_validate(defaults.content).profile_id
                if profile_id is None or defaults.retired:
                    raise ClawError(
                        "profile_required", "Select a Profile before creating a Thread", 422
                    )
            actor.use_profile(profile_id)
            resource = self._resource(db, "profile", profile_id)
            if resource.retired:
                raise ClawError("resource_retired", "Profile is retired")
            if parent_thread_id is not None:
                actor.require("read", parent_thread_id)
                self._thread(db, parent_thread_id)
            if parent_run_id is not None:
                run = self._run(db, parent_run_id)
                if run.thread_id != parent_thread_id:
                    raise ClawError(
                        "parent_mismatch", "Parent Run does not belong to the parent Thread"
                    )
            thread_id = new_id("thread")
            db.execute(
                "INSERT INTO threads(id,title,profile_id,parent_thread_id,"
                "parent_run_id,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (thread_id, title, profile_id, parent_thread_id, parent_run_id, now()),
            )
            self._grant_created_thread(db, actor, thread_id)
            return self._thread(db, thread_id)

    def fork_thread(
        self,
        actor_id: str,
        checkpoint_id: str,
        request_id: str,
        title: str,
        profile_id: str,
    ) -> ThreadRecord:
        fingerprint = digest({"checkpoint": checkpoint_id, "title": title, "profile": profile_id})
        with self._transaction() as db:
            actor = self._principal(db, actor_id)
            actor.require("create")
            actor.use_profile(profile_id)
            resource = self._resource(db, "profile", profile_id)
            if resource.retired:
                raise ClawError("resource_retired", "Profile is retired")
            row = db.execute("SELECT * FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
            if row is None:
                raise ClawError("checkpoint_missing", "Fork source is missing", 404)
            actor.require("read", row["thread_id"])
            prior = db.execute(
                "SELECT * FROM forks WHERE actor_id=? AND request_id=?",
                (actor_id, request_id),
            ).fetchone()
            if prior is not None:
                if prior["fingerprint"] != fingerprint:
                    raise ClawError("request_conflict", "Fork request identity was reused")
                actor.require("read", prior["thread_id"])
                return self._thread(db, prior["thread_id"])
            thread_id, fork_id = new_id("thread"), new_id("checkpoint")
            state = HarnessState.model_validate_json(row["state"]).fork(thread_id=thread_id)
            db.execute(
                "INSERT INTO threads(id,title,profile_id,parent_thread_id,parent_run_id,"
                "fork_checkpoint_id,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    thread_id,
                    title,
                    profile_id,
                    row["thread_id"],
                    row["run_id"],
                    checkpoint_id,
                    now(),
                ),
            )
            db.execute(
                "INSERT INTO checkpoints VALUES (?,?,NULL,?,?)",
                (fork_id, thread_id, state.model_dump_json(), now()),
            )
            db.execute("UPDATE threads SET checkpoint_id=? WHERE id=?", (fork_id, thread_id))
            db.execute(
                "INSERT INTO forks VALUES (?,?,?,?)", (actor_id, request_id, fingerprint, thread_id)
            )
            self._grant_created_thread(db, actor, thread_id)
            self._audit(
                db, "thread_forked", thread_id, {"checkpoint": checkpoint_id, "actor": actor_id}
            )
            return self._thread(db, thread_id)

    @staticmethod
    def _grant_created_thread(db: sqlite3.Connection, actor: Principal, thread_id: str) -> None:
        if not actor.admin:
            updated = actor.model_copy(
                update={"thread_ids": (*actor.thread_ids, thread_id), "version": actor.version + 1}
            )
            db.execute(
                "UPDATE principals SET content=? WHERE id=?", (updated.model_dump_json(), actor.id)
            )

    def threads(self, actor_id: str) -> list[ThreadRecord]:
        with self._connection() as db:
            actor = self._principal(db, actor_id)
            actor.require("read")
            return [
                ThreadRecord.model_validate(dict(row))
                for row in db.execute("SELECT * FROM threads ORDER BY created_at DESC")
                if actor.admin or row["id"] in actor.thread_ids
            ]

    def thread(self, actor_id: str, thread_id: str) -> ThreadRecord:
        with self._connection() as db:
            self._principal(db, actor_id).require("read", thread_id)
            return self._thread(db, thread_id)

    def update_thread(
        self,
        actor_id: str,
        thread_id: str,
        expected_version: int,
        *,
        title: str,
        profile_id: str,
        archived: bool,
    ) -> ThreadRecord:
        with self._transaction() as db:
            actor = self._principal(db, actor_id)
            actor.require("submit", thread_id)
            actor.use_profile(profile_id)
            profile = self._resource(db, "profile", profile_id)
            if profile.retired:
                raise ClawError("resource_retired", "Profile is retired")
            thread = self._thread(db, thread_id)
            if thread.version != expected_version:
                raise ClawError(
                    "version_conflict", "Thread selections changed; refresh before saving"
                )
            db.execute(
                "UPDATE threads SET title=?,profile_id=?,archived=?,version=version+1 WHERE id=?",
                (title, profile_id, archived, thread_id),
            )
            return self._thread(db, thread_id)

    @staticmethod
    def _run(db: sqlite3.Connection, run_id: str) -> RunRecord:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ClawError("run_missing", "Run not found", 404)
        values = dict(row)
        values.pop("sequence")
        values["composition"] = json.loads(values["composition"])
        return RunRecord.model_validate(values)

    def run(self, actor_id: str, run_id: str) -> RunRecord:
        with self._connection() as db:
            run = self._run(db, run_id)
            self._principal(db, actor_id).require("read", run.thread_id)
            return run

    def runs(self, actor_id: str, thread_id: str) -> list[RunRecord]:
        with self._connection() as db:
            self._principal(db, actor_id).require("read", thread_id)
            return [
                self._run(db, row[0])
                for row in db.execute(
                    "SELECT id FROM runs WHERE thread_id=? ORDER BY sequence", (thread_id,)
                )
            ]

    @staticmethod
    def _receipt(row: sqlite3.Row) -> InputReceipt:
        values = dict(row)
        values.pop("fingerprint")
        values["attachment_ids"] = json.loads(values["attachment_ids"])
        return InputReceipt.model_validate(values)

    def inputs(self, actor_id: str, thread_id: str) -> list[InputReceipt]:
        with self._connection() as db:
            self._principal(db, actor_id).require("read", thread_id)
            return [
                self._receipt(row)
                for row in db.execute(
                    "SELECT * FROM inputs WHERE thread_id=? ORDER BY sequence", (thread_id,)
                )
            ]

    def _new_run(
        self,
        db: sqlite3.Connection,
        actor: Principal,
        thread: ThreadRecord,
        workspace: str,
        *,
        composition: Composition | None = None,
        recovery_of: str | None = None,
    ) -> str:
        captured = composition or self._capture(db, actor, thread.profile_id, workspace)
        run_id = new_id("run")
        db.execute(
            "INSERT INTO runs(id,thread_id,actor_id,status,composition,recovery_of,created_at) "
            "VALUES (?,?,?,'queued',?,?,?)",
            (run_id, thread.id, actor.id, captured.model_dump_json(), recovery_of, now()),
        )
        return run_id

    def admit(
        self,
        actor_id: str,
        thread_id: str,
        request: InputRequest,
        workspace: str,
    ) -> InputReceipt:
        with self._transaction() as db:
            return self._admit(db, actor_id, thread_id, request, workspace)

    def _admit(
        self,
        db: sqlite3.Connection,
        actor_id: str,
        thread_id: str,
        request: InputRequest,
        workspace: str,
    ) -> InputReceipt:
        fingerprint = digest({"thread": thread_id, "request": request.model_dump(mode="json")})
        actor = self._principal(db, actor_id)
        actor.require("submit", thread_id)
        prior = db.execute(
            "SELECT * FROM inputs WHERE actor_id=? AND request_id=?",
            (actor_id, request.request_id),
        ).fetchone()
        if prior is not None:
            if prior["fingerprint"] != fingerprint:
                raise ClawError(
                    "request_conflict", "Request identity was already used for different input"
                )
            return self._receipt(prior)
        thread = self._thread(db, thread_id)
        if thread.archived:
            raise ClawError("thread_archived", "Unarchive this Thread before submitting work")
        if (
            request.expected_thread_version is not None
            and request.expected_thread_version != thread.version
        ):
            raise ClawError("version_conflict", "Thread selections changed before submission")
        active = db.execute(
            "SELECT id,status,composition,recovery_of FROM runs WHERE thread_id=? AND status IN "
            "('queued','preparing','running','waiting') "
            "ORDER BY status='queued', recovery_of IS NULL, sequence LIMIT 1",
            (thread_id,),
        ).fetchone()
        recovery = (
            active["recovery_of"] if active is not None and not request.separate_run else None
        )
        unresolved = db.execute(
            "SELECT 1 FROM inputs WHERE thread_id=? "
            "AND (disposition='uncertain' OR (disposition='blocked' AND ? IS NULL)) LIMIT 1",
            (thread_id, recovery),
        ).fetchone()
        if request.profile_id is not None:
            if request.expected_thread_version is None:
                raise ClawError(
                    "version_required", "Combined selection changes require a Thread version"
                )
            if active is not None and not request.separate_run:
                raise ClawError(
                    "composition_conflict", "Select a separate Run to change active composition"
                )
            actor.use_profile(request.profile_id)
            db.execute(
                "UPDATE threads SET profile_id=?,version=version+1 WHERE id=?",
                (request.profile_id, thread_id),
            )
            thread = self._thread(db, thread_id)
        if active is None or request.separate_run:
            run_id = self._new_run(db, actor, thread, workspace)
            disposition = "pending"
        else:
            run_id = active["id"]
            captured = Composition.model_validate_json(active["composition"])
            actor.use_profile(captured.profile.id)
            disposition = {
                "queued": "pending",
                "preparing": "pending",
                "running": "steering",
                "waiting": "held",
            }[active["status"]]
        if (
            unresolved
            or db.execute(
                "SELECT 1 FROM runs WHERE thread_id=? AND recovery_required=1 LIMIT 1",
                (thread_id,),
            ).fetchone()
        ):
            disposition = "blocked"
        for asset_id in request.attachment_ids:
            asset = self._asset(db, asset_id)
            if asset.thread_id != thread_id:
                raise ClawError("asset_scope", "Attachments must belong to this Thread", 403)
        input_id = new_id("input")
        db.execute(
            "INSERT INTO inputs(id,thread_id,run_id,actor_id,request_id,fingerprint,"
            "text,source,disposition,created_at,attachment_ids) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                input_id,
                thread_id,
                run_id,
                actor_id,
                request.request_id,
                fingerprint,
                request.text,
                request.source,
                disposition,
                now(),
                canonical(request.attachment_ids),
            ),
        )
        self._audit(db, "input_accepted", input_id, {"run_id": run_id, "disposition": disposition})
        return self._receipt(db.execute("SELECT * FROM inputs WHERE id=?", (input_id,)).fetchone())

    def claim(self, run_id: str, owner: str, workspace: str) -> RunRecord:
        with self._transaction() as db:
            run = self._run(db, run_id)
            if run.status != "queued":
                raise ClawError("run_not_queued", "Run is not available for dispatch")
            self._authorize_run(db, run)
            if run.composition.workspace != workspace:
                raise ClawError(
                    "workspace_changed", "Restore the captured workspace before dispatch"
                )
            head = db.execute(
                "SELECT id FROM runs WHERE thread_id=? "
                "AND status IN ('queued','preparing','running','waiting') "
                "ORDER BY status='queued', recovery_of IS NULL, sequence LIMIT 1",
                (run.thread_id,),
            ).fetchone()
            blocked = db.execute(
                "SELECT 1 FROM inputs WHERE thread_id=? "
                "AND (disposition='uncertain' OR (disposition='blocked' AND ? IS NULL)) LIMIT 1",
                (run.thread_id, run.recovery_of),
            ).fetchone()
            if (
                (head is not None and head[0] != run.id)
                or blocked
                or db.execute(
                    "SELECT 1 FROM runs WHERE thread_id=? AND recovery_required=1 LIMIT 1",
                    (run.thread_id,),
                ).fetchone()
            ):
                raise ClawError(
                    "thread_blocked", "Earlier work or unresolved inputs block this Run"
                )
            thread = self._thread(db, run.thread_id)
            db.execute(
                "UPDATE runs SET status='preparing',owner=?,checkpoint_id=?,error=NULL WHERE id=?",
                (owner, thread.checkpoint_id, run_id),
            )
            return self._run(db, run_id)

    @staticmethod
    def _owned(db: sqlite3.Connection, run_id: str, owner: str) -> RunRecord:
        run = Store._run(db, run_id)
        if run.owner != owner or run.status not in {"preparing", "running"}:
            raise ClawError("stale_owner", "Execution no longer owns this Run")
        return run

    def start(self, run_id: str, owner: str) -> list[InputReceipt]:
        with self._transaction() as db:
            run = self._owned(db, run_id, owner)
            if run.status != "preparing" or run.cancel_requested:
                raise ClawError("run_not_preparing", "Run cannot start")
            self._authorize_run(db, run)
            rows = db.execute(
                "SELECT * FROM inputs WHERE run_id=? AND disposition='pending' ORDER BY sequence",
                (run_id,),
            ).fetchall()
            for row in rows:
                sender = self._principal(db, row["actor_id"])
                sender.require("submit", run.thread_id)
                sender.use_profile(run.composition.profile.id)
            db.execute(
                "UPDATE inputs SET disposition='delivering' "
                "WHERE run_id=? AND disposition='pending'",
                (run_id,),
            )
            db.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
            return [self._receipt(row) for row in rows]

    def queued(self) -> list[str]:
        with self._connection() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT id FROM runs WHERE status='queued' "
                    "ORDER BY recovery_of IS NULL, sequence"
                )
            ]

    def take_steering(self, run_id: str, owner: str) -> list[InputReceipt]:
        with self._transaction() as db:
            run = self._owned(db, run_id, owner)
            if run.status != "running":
                return []
            rows = db.execute(
                "SELECT * FROM inputs WHERE run_id=? AND disposition='steering' ORDER BY sequence",
                (run_id,),
            ).fetchall()
            for row in rows:
                actor = self._principal(db, row["actor_id"])
                actor.require("submit", run.thread_id)
                actor.use_profile(run.composition.profile.id)
            db.execute(
                "UPDATE inputs SET disposition='delivering' "
                "WHERE run_id=? AND disposition='steering'",
                (run_id,),
            )
            return [self._receipt(row) for row in rows]

    def steering_delivered(self, run_id: str, owner: str, input_id: str, *, accepted: bool) -> None:
        with self._transaction() as db:
            self._owned(db, run_id, owner)
            result = db.execute(
                "UPDATE inputs SET disposition=? "
                "WHERE id=? AND run_id=? AND disposition='delivering'",
                ("delivered" if accepted else "steering", input_id, run_id),
            )
            if result.rowcount != 1:
                row = db.execute(
                    "SELECT disposition FROM inputs WHERE id=? AND run_id=?", (input_id, run_id)
                ).fetchone()
                if accepted and row is not None and row[0] == "incorporated":
                    return
                raise ClawError("input_transition", "Input is not awaiting delivery confirmation")

    def checkpoint(self, checkpoint_id: str) -> HarnessState:
        with self._connection() as db:
            row = db.execute(
                "SELECT state FROM checkpoints WHERE id=?", (checkpoint_id,)
            ).fetchone()
            if row is None:
                raise ClawError("checkpoint_missing", "Required continuation is missing")
            return HarnessState.model_validate_json(row[0])

    def publish(
        self,
        run_id: str,
        owner: str,
        state: HarnessState,
        *,
        incorporated: tuple[str, ...] = (),
        status: str | None = None,
        output: str | None = None,
        error: str | None = None,
        requests: str | None = None,
        pending_deferred: DeferredToolResume | None = None,
    ) -> str:
        if status not in {None, "waiting", "completed", "failed", "cancelled"}:
            raise ValueError("Invalid publication status")
        if (status == "waiting") != (requests is not None):
            raise ValueError("A waiting boundary must include its complete decision batch")
        if requests is not None:
            validate_wait(state, requests)
        serialized = state.model_dump_json()
        with self._transaction() as db:
            run = self._owned(db, run_id, owner)
            if state.thread_id != run.thread_id:
                raise ClawError("checkpoint_thread", "Continuation belongs to another Thread")
            if self._thread(db, run.thread_id).checkpoint_id != run.checkpoint_id:
                raise ClawError(
                    "checkpoint_conflict", "Thread continuation advanced outside this owner"
                )
            checkpoint_id = new_id("checkpoint")
            db.execute(
                "INSERT INTO checkpoints VALUES (?,?,?,?,?)",
                (checkpoint_id, run.thread_id, run_id, serialized, now()),
            )
            db.execute(
                "UPDATE threads SET checkpoint_id=? WHERE id=?", (checkpoint_id, run.thread_id)
            )
            db.execute("UPDATE runs SET checkpoint_id=? WHERE id=?", (checkpoint_id, run_id))
            if pending_deferred is None:
                db.execute("DELETE FROM deferred_inputs WHERE run_id=?", (run_id,))
            else:
                remaining = pending_deferred.remaining(state.message_history)
                if remaining is None:
                    db.execute("DELETE FROM deferred_inputs WHERE run_id=?", (run_id,))
                else:
                    changed = db.execute(
                        "UPDATE deferred_inputs SET checkpoint_id=?,requests=?,response=? "
                        "WHERE run_id=?",
                        (
                            checkpoint_id,
                            REQUESTS.dump_json(remaining.requests).decode(),
                            RESULTS.dump_json(remaining.results).decode(),
                            run_id,
                        ),
                    )
                    if changed.rowcount != 1:
                        raise ClawError("deferred_missing", "Accepted decision facts are missing")
            for input_id in incorporated:
                changed = db.execute(
                    "UPDATE inputs SET disposition='incorporated' WHERE id=? AND run_id=? "
                    "AND disposition IN ('delivering','delivered','incorporated')",
                    (input_id, run_id),
                )
                if changed.rowcount != 1:
                    raise ClawError(
                        "input_evidence", "Checkpoint evidence names input outside this execution"
                    )
            if status == "waiting":
                # Enqueue acceptance is not proof of consumption. Do not silently
                # lose or replay these inputs when restoring a waiting boundary.
                db.execute(
                    "UPDATE inputs SET disposition='uncertain' WHERE run_id=? "
                    "AND disposition IN ('delivering','delivered')",
                    (run_id,),
                )
                db.execute(
                    "INSERT INTO decisions(id,run_id,checkpoint_id,requests,created_at) "
                    "VALUES (?,?,?,?,?)",
                    (new_id("decision"), run_id, checkpoint_id, requests, now()),
                )
                db.execute(
                    "UPDATE inputs SET disposition='held' "
                    "WHERE run_id=? AND disposition='steering'",
                    (run_id,),
                )
            if status is not None:
                self._finish(db, run, status, output, error)
            self._audit(
                db, "checkpoint_selected", checkpoint_id, {"run_id": run_id, "status": status}
            )
            return checkpoint_id

    def _finish(
        self,
        db: sqlite3.Connection,
        run: RunRecord,
        status: str,
        output: str | None,
        error: str | None,
    ) -> None:
        db.execute(
            "UPDATE runs SET status=?,output=?,error=?,owner=NULL,finished_at=? WHERE id=?",
            (status, output, error, now() if status in TERMINAL else None, run.id),
        )
        if status == "waiting":
            return
        if status == "interrupted" or (
            status in {"failed", "cancelled"} and run.status == "running"
        ):
            db.execute("UPDATE runs SET recovery_required=1 WHERE id=?", (run.id,))
        db.execute(
            "UPDATE inputs SET disposition='uncertain' "
            "WHERE run_id=? AND disposition IN ('delivering','delivered')",
            (run.id,),
        )
        pending = db.execute(
            "SELECT * FROM inputs WHERE run_id=? "
            "AND disposition IN ('pending','steering','held') ORDER BY sequence",
            (run.id,),
        ).fetchall()
        if status != "completed":
            db.execute(
                "UPDATE inputs SET disposition='unapplied' "
                "WHERE run_id=? AND disposition IN ('pending','steering','held','blocked')",
                (run.id,),
            )
            return
        # Only confirmed undelivered ordinary inputs cross the completion boundary.
        for row in pending:
            try:
                actor = self._principal(db, row["actor_id"])
                actor.require("submit", run.thread_id)
                target = db.execute(
                    "SELECT id,composition FROM runs WHERE thread_id=? "
                    "AND status='queued' ORDER BY sequence LIMIT 1",
                    (run.thread_id,),
                ).fetchone()
                if target:
                    captured = Composition.model_validate_json(target["composition"])
                    actor.use_profile(captured.profile.id)
                    successor = target["id"]
                else:
                    successor = self._new_run(
                        db, actor, self._thread(db, run.thread_id), run.composition.workspace
                    )
            except ClawError as exc:
                # A later admission failure cannot roll back an already produced result.
                db.execute("UPDATE inputs SET disposition='blocked' WHERE id=?", (row["id"],))
                self._audit(db, "input_transfer_blocked", row["id"], {"reason": exc.code})
                continue
            db.execute(
                "UPDATE inputs SET run_id=?,disposition='pending' WHERE id=?",
                (successor, row["id"]),
            )
            self._audit(db, "input_transferred", row["id"], {"from": run.id, "to": successor})

    def finish_without_checkpoint(
        self, run_id: str, owner: str, *, status: str, error: str
    ) -> None:
        if status not in {"failed", "cancelled"}:
            raise ValueError("Only failed or cancelled work can finish without a new checkpoint")
        with self._transaction() as db:
            run = self._owned(db, run_id, owner)
            self._finish(db, run, status, None, error)

    def cancel(self, actor_id: str, run_id: str) -> RunRecord:
        with self._transaction() as db:
            run = self._run(db, run_id)
            self._principal(db, actor_id).require("cancel", run.thread_id)
            if run.status in TERMINAL:
                return run
            pending = [run_id]
            while pending:
                target_id = pending.pop()
                target = self._run(db, target_id)
                if target.status in TERMINAL:
                    continue
                db.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (target_id,))
                if target.status in {"queued", "waiting"}:
                    self._finish(db, target, "cancelled", None, "Cancelled by client")
                self._audit(
                    db, "cancel_requested", target_id, {"actor": actor_id, "source": run_id}
                )
                pending.extend(
                    row[0]
                    for row in db.execute(
                        "SELECT child_run_id FROM delegations "
                        "WHERE parent_run_id=? AND cancel_policy='cancel'",
                        (target_id,),
                    )
                )
            return self._run(db, run_id)

    def retire_owner(self, run_id: str, owner: str) -> None:
        """Reconcile only after the coordinator has observed this executor stop."""
        with self._transaction() as db:
            run = self._run(db, run_id)
            if run.owner != owner or run.status not in {"preparing", "running"}:
                return
            self._finish(
                db,
                run,
                "cancelled" if run.status == "preparing" or run.cancel_requested else "interrupted",
                None,
                "Executor stopped without publishing an outcome",
            )
            self._audit(db, "executor_retired", run_id, {"owner": owner})

    def reconcile_startup(self) -> list[str]:
        """Call only after taking the exclusive Instance lease, never on a live peer."""
        with self._transaction() as db:
            lost = [
                row[0]
                for row in db.execute("SELECT id FROM runs WHERE status IN ('preparing','running')")
            ]
            for run_id in lost:
                run = self._run(db, run_id)
                self._finish(
                    db,
                    run,
                    "interrupted",
                    None,
                    "Server execution ownership was lost; reconcile effects before recovery",
                )
                self._audit(db, "execution_interrupted", run_id, {})
            return lost

    def backup(self, destination: Path) -> None:
        """Online SQLite backup includes heads and immutable values in one snapshot."""
        if destination.exists():
            raise ClawError("backup_exists", "Backup destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create prevents overwriting a raced operator file.
        with destination.open("xb"):
            pass
        with self._connection() as source, sqlite3.connect(destination) as target:
            source.backup(target)

    def decisions(self, actor_id: str, run_id: str) -> list[dict[str, JsonValue]]:
        with self._connection() as db:
            run = self._run(db, run_id)
            self._principal(db, actor_id).require("read", run.thread_id)
            return [
                {
                    **dict(row),
                    "requests": json.loads(row["requests"]),
                    "response": json.loads(row["response"]) if row["response"] else None,
                }
                for row in db.execute(
                    "SELECT * FROM decisions WHERE run_id=? ORDER BY created_at", (run_id,)
                )
            ]

    def answer(self, actor_id: str, decision_id: str, response_json: str) -> RunRecord:
        response = RESULTS.validate_json(response_json)
        serialized = RESULTS.dump_json(response).decode()
        with self._transaction() as db:
            decision = db.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
            if decision is None:
                raise ClawError("decision_missing", "Decision not found", 404)
            run = self._run(db, decision["run_id"])
            actor = self._principal(db, actor_id)
            actor.require("decide", run.thread_id)
            actor.use_profile(run.composition.profile.id)
            if decision["response"] is not None:
                if json.loads(decision["response"]) == json.loads(serialized):
                    return run
                raise ClawError("decision_conflict", "Another response was already accepted")
            thread = self._thread(db, run.thread_id)
            if run.status != "waiting" or thread.checkpoint_id != decision["checkpoint_id"]:
                raise ClawError(
                    "decision_stale", "Decision is no longer the current waiting boundary"
                )
            state_row = db.execute(
                "SELECT state FROM checkpoints WHERE id=?", (thread.checkpoint_id,)
            ).fetchone()
            validate_answer(
                HarnessState.model_validate_json(state_row[0]), decision["requests"], serialized
            )
            if db.execute(
                "SELECT 1 FROM inputs WHERE run_id=? AND disposition='uncertain' LIMIT 1",
                (run.id,),
            ).fetchone():
                raise ClawError(
                    "reconciliation_required", "Review uncertain input before answering"
                )
            db.execute(
                "INSERT INTO deferred_inputs VALUES (?,?,?,?,?)",
                (run.id, thread.checkpoint_id, decision["requests"], serialized, actor_id),
            )
            db.execute(
                "UPDATE decisions SET response=?,actor_id=?,answered_at=? WHERE id=?",
                (serialized, actor_id, now(), decision_id),
            )
            db.execute("UPDATE runs SET status='queued' WHERE id=?", (run.id,))
            # Held input stays distinct from the answer. It becomes steerable only on resume.
            db.execute(
                "UPDATE inputs SET disposition='steering' WHERE run_id=? AND disposition='held'",
                (run.id,),
            )
            self._audit(db, "decision_answered", decision_id, {"actor": actor_id})
            return self._run(db, run.id)

    def resume_decision(self, run_id: str, owner: str) -> DeferredToolResume | None:
        with self._connection() as db:
            run = self._owned(db, run_id, owner)
            row = db.execute(
                "SELECT * FROM deferred_inputs WHERE run_id=? AND checkpoint_id=?",
                (run_id, run.checkpoint_id),
            ).fetchone()
            if row is None:
                return None
            # Revalidate the responder, as well as the original submitting principal.
            actor = self._principal(db, row["actor_id"])
            actor.require("decide", run.thread_id)
            actor.use_profile(run.composition.profile.id)
            state = db.execute(
                "SELECT state FROM checkpoints WHERE id=?", (run.checkpoint_id,)
            ).fetchone()
            saved = HarnessState.model_validate_json(state[0])
            if run.recovery_of is not None:
                return DeferredToolResume(
                    REQUESTS.validate_json(row["requests"]),
                    RESULTS.validate_json(row["response"]),
                    recovery=True,
                ).remaining(saved.message_history)
            return validate_answer(saved, row["requests"], row["response"])

    def acknowledge_uncertainty(self, actor_id: str, input_id: str, note: str) -> InputReceipt:
        """Resolve uncertainty without inventing checkpoint evidence or replaying effects."""
        if not note.strip():
            raise ClawError(
                "reconciliation_note", "Describe the reviewed effects before acknowledging", 422
            )
        with self._transaction() as db:
            self._principal(db, actor_id).require("admin")
            row = db.execute("SELECT * FROM inputs WHERE id=?", (input_id,)).fetchone()
            if row is None:
                raise ClawError("input_missing", "Input not found", 404)
            run = self._run(db, row["run_id"])
            if run.status not in TERMINAL | {"waiting"} or row["disposition"] != "uncertain":
                raise ClawError(
                    "reconciliation_state",
                    "Only stopped or waiting uncertain input can be acknowledged",
                )
            db.execute("UPDATE inputs SET disposition='unapplied' WHERE id=?", (input_id,))
            self._audit(db, "uncertainty_acknowledged", input_id, {"actor": actor_id, "note": note})
            return self._receipt(
                db.execute("SELECT * FROM inputs WHERE id=?", (input_id,)).fetchone()
            )

    def release_blocked(self, actor_id: str, thread_id: str, workspace: str) -> None:
        with self._transaction() as db:
            actor = self._principal(db, actor_id)
            actor.require("admin")
            unresolved = db.execute(
                "SELECT 1 FROM inputs WHERE thread_id=? AND disposition='uncertain' LIMIT 1",
                (thread_id,),
            ).fetchone()
            if (
                unresolved
                or db.execute(
                    "SELECT 1 FROM runs WHERE thread_id=? AND recovery_required=1 LIMIT 1",
                    (thread_id,),
                ).fetchone()
            ):
                raise ClawError(
                    "reconciliation_required", "Review uncertain effects before releasing input"
                )
            rows = db.execute(
                "SELECT * FROM inputs WHERE thread_id=? "
                "AND disposition='blocked' ORDER BY sequence",
                (thread_id,),
            ).fetchall()
            for row in rows:
                source_actor = self._principal(db, row["actor_id"])
                source_actor.require("submit", thread_id)
                run = self._run(db, row["run_id"])
                if run.status == "queued":
                    source_actor.use_profile(run.composition.profile.id)
                    destination = run.id
                elif run.status in TERMINAL:
                    destination = self._new_run(
                        db, source_actor, self._thread(db, thread_id), workspace, recovery_of=run.id
                    )
                else:
                    raise ClawError(
                        "thread_busy", "Wait for active work before releasing blocked input"
                    )
                db.execute(
                    "UPDATE inputs SET run_id=?,disposition='pending' WHERE id=?",
                    (destination, row["id"]),
                )
                self._audit(
                    db, "input_released", row["id"], {"actor": actor_id, "run_id": destination}
                )

    @staticmethod
    def _delegation(db: sqlite3.Connection, delegation_id: str) -> DelegationRecord:
        row = db.execute(
            "SELECT d.*,r.thread_id AS child_thread_id,r.status,r.output,r.error "
            "FROM delegations d JOIN runs r ON r.id=d.child_run_id WHERE d.id=?",
            (delegation_id,),
        ).fetchone()
        if row is None:
            raise ClawError("delegation_missing", "Delegated work not found", 404)
        values = dict(row)
        values.pop("fingerprint")
        return DelegationRecord.model_validate(values)

    def delegate_work(
        self,
        parent_run_id: str,
        owner: str,
        request_id: str,
        profile_id: str,
        text: str,
        *,
        cancel_policy: str = "keep",
        notify: bool = False,
    ) -> DelegationRecord:
        request = InputRequest(request_id=request_id, text=text, source="delegation")
        if cancel_policy not in {"keep", "cancel"}:
            raise ClawError("cancel_policy", "Choose keep or cancel for child propagation", 422)
        fingerprint = digest(
            {"profile": profile_id, "text": text, "cancel": cancel_policy, "notify": notify}
        )
        with self._transaction() as db:
            parent = self._owned(db, parent_run_id, owner)
            self._authorize_run(db, parent)
            if parent.cancel_requested:
                raise ClawError("parent_stopping", "Cancellation prevents new delegated work")
            actor = self._principal(db, parent.actor_id)
            actor.require("create")
            actor.use_profile(profile_id)
            captured = next(
                (child for child in parent.composition.children if child.profile.id == profile_id),
                None,
            )
            if captured is None:
                raise ClawError(
                    "delegation_forbidden", "Profile is not in the captured child selection", 403
                )
            prior = db.execute(
                "SELECT id,fingerprint FROM delegations WHERE parent_run_id=? AND request_id=?",
                (parent_run_id, request_id),
            ).fetchone()
            if prior is not None:
                if prior["fingerprint"] != fingerprint:
                    raise ClawError("request_conflict", "Delegation request identity was reused")
                return self._delegation(db, prior["id"])
            child_id, delegation_id = new_id("thread"), new_id("delegation")
            db.execute(
                "INSERT INTO threads(id,title,profile_id,parent_thread_id,"
                "parent_run_id,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    child_id,
                    f"Delegated: {profile_id}",
                    profile_id,
                    parent.thread_id,
                    parent.id,
                    now(),
                ),
            )
            self._grant_created_thread(db, actor, child_id)
            child_run = self._new_run(
                db, actor, self._thread(db, child_id), captured.workspace, composition=captured
            )
            db.execute(
                "INSERT INTO inputs(id,thread_id,run_id,actor_id,request_id,"
                "fingerprint,text,source,disposition,created_at) "
                "VALUES (?,?,?,?,?,?,?,'delegation','pending',?)",
                (
                    new_id("input"),
                    child_id,
                    child_run,
                    actor.id,
                    new_id("delegate-input"),
                    fingerprint,
                    request.text,
                    now(),
                ),
            )
            db.execute(
                "INSERT INTO delegations(id,parent_run_id,child_run_id,request_id,"
                "fingerprint,cancel_policy,notify) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    delegation_id,
                    parent.id,
                    child_run,
                    request_id,
                    fingerprint,
                    cancel_policy,
                    notify,
                ),
            )
            self._audit(
                db, "work_delegated", delegation_id, {"parent": parent.id, "child": child_run}
            )
            return self._delegation(db, delegation_id)

    def delegations(self, actor_id: str, parent_run_id: str) -> list[DelegationRecord]:
        with self._connection() as db:
            parent = self._run(db, parent_run_id)
            actor = self._principal(db, actor_id)
            actor.require("read", parent.thread_id)
            result = []
            for row in db.execute(
                "SELECT id FROM delegations WHERE parent_run_id=? ORDER BY rowid", (parent_run_id,)
            ):
                record = self._delegation(db, row[0])
                # Parent access alone is not access to a child's saved result.
                if actor.admin or record.child_thread_id in actor.thread_ids:
                    result.append(record)
            return result

    def child_progress(
        self, parent_run_id: str, owner: str, delegation_id: str
    ) -> DelegationRecord:
        with self._connection() as db:
            parent = self._owned(db, parent_run_id, owner)
            self._authorize_run(db, parent)
            child = self._delegation(db, delegation_id)
            if child.parent_run_id != parent_run_id:
                raise ClawError(
                    "delegation_forbidden", "This work belongs to another parent Run", 403
                )
            self._principal(db, parent.actor_id).require("read", child.child_thread_id)
            return child

    def deliver_child_results(self) -> None:
        """Admission and delivery marker commit together; saved outcome remains separate."""
        with self._transaction() as db:
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT d.id FROM delegations d JOIN runs r ON r.id=d.child_run_id "
                    "WHERE d.notify=1 AND d.delivery_input_id IS NULL "
                    "AND r.status IN ('completed','failed','cancelled','interrupted')"
                )
            ]
            for delegation_id in ids:
                child = self._delegation(db, delegation_id)
                parent = self._run(db, child.parent_run_id)
                db.execute("SAVEPOINT child_delivery")
                try:
                    if (
                        parent.status in {"failed", "cancelled", "interrupted"}
                        or parent.cancel_requested
                    ):
                        raise ClawError(
                            "parent_stopped", "Stopped parent requires explicit result collection"
                        )
                    actor = self._principal(db, parent.actor_id)
                    actor.require("read", child.child_thread_id)
                    payload = {
                        "delegation_id": child.id,
                        "child_run_id": child.child_run_id,
                        "status": child.status,
                        "output": child.output[:64000] if child.output else None,
                        "output_truncated": bool(child.output and len(child.output) > 64000),
                        "error": child.error,
                    }
                    receipt = self._admit(
                        db,
                        actor.id,
                        parent.thread_id,
                        InputRequest(
                            request_id=f"child-result:{child.id}",
                            text=canonical(payload),
                            source="child-result",
                        ),
                        parent.composition.workspace,
                    )
                    db.execute(
                        "UPDATE delegations SET delivery_input_id=?,delivery_error=NULL WHERE id=?",
                        (receipt.id, child.id),
                    )
                    self._audit(db, "child_result_delivered", child.id, {"input": receipt.id})
                except ClawError as exc:
                    db.execute("ROLLBACK TO child_delivery")
                    db.execute(
                        "UPDATE delegations SET delivery_error=? WHERE id=?", (exc.code, child.id)
                    )
                finally:
                    db.execute("RELEASE child_delivery")

    def block_dispatch(self, run_id: str, reason: str) -> None:
        with self._transaction() as db:
            db.execute("UPDATE runs SET error=? WHERE id=? AND status='queued'", (reason, run_id))

    def execution_view(self, run_id: str, owner: str) -> RunRecord:
        with self._connection() as db:
            return self._owned(db, run_id, owner)

    def _authorize_run(self, db: sqlite3.Connection, run: RunRecord) -> None:
        current = run
        while True:
            actor = self._principal(db, current.actor_id)
            actor.require("submit", current.thread_id)
            actor.use_profile(current.composition.profile.id)
            thread = self._thread(db, current.thread_id)
            if thread.parent_run_id is None or thread.fork_checkpoint_id is not None:
                break
            current = self._run(db, thread.parent_run_id)

    def authorize_execution(self, run_id: str, owner: str) -> None:
        with self._connection() as db:
            run = self._owned(db, run_id, owner)
            self._authorize_run(db, run)

    def reconcile_run(self, actor_id: str, run_id: str, note: str) -> RunRecord:
        """Record operator review; this does not execute, rewrite history, or replay input."""
        if not note.strip():
            raise ClawError("reconciliation_note", "Describe the reviewed external effects", 422)
        with self._transaction() as db:
            self._principal(db, actor_id).require("admin")
            run = self._run(db, run_id)
            if run.status not in TERMINAL:
                raise ClawError("run_active", "Active work cannot be reconciled as stopped")
            db.execute(
                "UPDATE runs SET reconciled_at=? WHERE id=?",
                (
                    now(),
                    run_id,
                ),
            )
            self._audit(db, "run_reconciled", run_id, {"actor": actor_id, "note": note})
            return self._run(db, run_id)

    def recover(
        self,
        actor_id: str,
        source_run_id: str,
        request_id: str,
        text: str,
        workspace: str,
    ) -> RunRecord:
        """Admit new, explicitly linked work after review, never replay the old Run."""
        request = InputRequest(request_id=request_id, text=text, source="recovery")
        fingerprint = digest({"source": source_run_id, "text": text, "workspace": workspace})
        with self._transaction() as db:
            actor = self._principal(db, actor_id)
            actor.require("admin")
            prior = db.execute(
                "SELECT fingerprint,run_id FROM recoveries WHERE actor_id=? AND request_id=?",
                (actor_id, request_id),
            ).fetchone()
            if prior is not None:
                if prior[0] != fingerprint:
                    raise ClawError("request_conflict", "Recovery request identity was reused")
                return self._run(db, prior[1])
            source = self._run(db, source_run_id)
            thread = self._thread(db, source.thread_id)
            if source.status not in {"failed", "cancelled", "interrupted"}:
                raise ClawError("recovery_state", "Only stopped unsuccessful work can be recovered")
            if source.reconciled_at is None:
                raise ClawError(
                    "reconciliation_required", "Review external effects before recovery"
                )
            if thread.archived:
                raise ClawError("thread_archived", "Unarchive the Thread before recovery")
            if thread.checkpoint_id != source.checkpoint_id:
                raise ClawError("recovery_stale", "Thread has advanced beyond this recovery source")
            if db.execute(
                "SELECT 1 FROM runs WHERE recovery_of=? LIMIT 1", (source_run_id,)
            ).fetchone():
                raise ClawError("recovery_exists", "This Run already has recovery work")
            if db.execute(
                "SELECT 1 FROM runs WHERE thread_id=? AND "
                "(status IN ('preparing','running','waiting') OR "
                "(recovery_required=1 AND id!=?)) LIMIT 1",
                (thread.id, source.id),
            ).fetchone():
                raise ClawError("thread_blocked", "Other active or unresolved work blocks recovery")
            if db.execute(
                "SELECT 1 FROM inputs WHERE thread_id=? AND disposition='uncertain' LIMIT 1",
                (thread.id,),
            ).fetchone():
                raise ClawError(
                    "reconciliation_required", "Review uncertain inputs before recovery"
                )
            run_id = self._new_run(db, actor, thread, workspace, recovery_of=source.id)
            db.execute(
                "INSERT INTO recoveries VALUES (?,?,?,?)",
                (actor_id, request_id, fingerprint, run_id),
            )
            db.execute(
                "INSERT INTO inputs(id,thread_id,run_id,actor_id,request_id,fingerprint,"
                "text,source,disposition,created_at) VALUES (?,?,?,?,?,?,?,'recovery','pending',?)",
                (
                    new_id("input"),
                    thread.id,
                    run_id,
                    actor_id,
                    new_id("recovery-input"),
                    fingerprint,
                    request.text,
                    now(),
                ),
            )
            db.execute(
                "INSERT INTO deferred_inputs SELECT ?,checkpoint_id,requests,response,actor_id "
                "FROM deferred_inputs WHERE run_id=?",
                (run_id, source.id),
            )
            db.execute("UPDATE runs SET recovery_required=0 WHERE id=?", (source.id,))
            self._audit(db, "recovery_accepted", run_id, {"source": source.id, "actor": actor_id})
            return self._run(db, run_id)

    @staticmethod
    def _target(db: sqlite3.Connection, target_id: str) -> TargetRecord:
        row = db.execute("SELECT * FROM targets WHERE id=?", (target_id,)).fetchone()
        if row is None:
            raise ClawError("target_missing", "Managed target not found", 404)
        values = dict(row)
        values["definition"] = json.loads(values["definition"])
        values["provider_state"] = (
            json.loads(values["provider_state"]) if values["provider_state"] else None
        )
        return TargetRecord.model_validate(values)

    def target_for(self, run_id: str, owner: str, definition: dict[str, JsonValue]) -> TargetRecord:
        """Register a stable Thread/generation association before provider I/O."""
        generation = digest(definition)
        with self._transaction() as db:
            run = self._owned(db, run_id, owner)
            self._authorize_run(db, run)
            row = db.execute(
                "SELECT id FROM targets WHERE thread_id=? AND generation=?",
                (run.thread_id, generation),
            ).fetchone()
            if row is not None:
                return self._target(db, row[0])
            target_id = new_id("target")
            db.execute(
                "INSERT INTO targets(id,thread_id,generation,definition,status,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (target_id, run.thread_id, generation, canonical(definition), "unprepared", now()),
            )
            return self._target(db, target_id)

    def target(self, actor_id: str, target_id: str, *, manage: bool = False) -> TargetRecord:
        with self._connection() as db:
            target = self._target(db, target_id)
            self._principal(db, actor_id).require("admin" if manage else "read", target.thread_id)
            return target

    def targets(self, actor_id: str, thread_id: str) -> list[TargetRecord]:
        with self._connection() as db:
            self._principal(db, actor_id).require("read", thread_id)
            return [
                self._target(db, row[0])
                for row in db.execute(
                    "SELECT id FROM targets WHERE thread_id=? ORDER BY updated_at", (thread_id,)
                )
            ]

    def target_operation(self, target_id: str, operation: str) -> TargetRecord:
        """Internal manager boundary; caller holds the Instance target lock."""
        with self._transaction() as db:
            self._target(db, target_id)
            operation_id = new_id("operation")
            db.execute(
                "UPDATE targets SET status='preparing',operation_id=?,error=NULL,updated_at=? "
                "WHERE id=?",
                (operation_id, now(), target_id),
            )
            self._audit(
                db, "target_operation", target_id, {"operation": operation, "id": operation_id}
            )
            return self._target(db, target_id)

    def target_observed(
        self,
        target_id: str,
        operation_id: str | None,
        *,
        status: str,
        provider_state: dict[str, JsonValue] | None,
        error: str | None = None,
    ) -> TargetRecord:
        with self._transaction() as db:
            current = self._target(db, target_id)
            if current.operation_id != operation_id:
                raise ClawError(
                    "stale_operation", "Target observation belongs to an older operation"
                )
            db.execute(
                "UPDATE targets SET status=?,provider_state=?,error=?,updated_at=? WHERE id=?",
                (
                    status,
                    canonical(provider_state) if provider_state is not None else None,
                    error,
                    now(),
                    target_id,
                ),
            )
            return self._target(db, target_id)

    def save_credential(self, actor_id: str, name: str, value: str | None) -> None:
        """Protected backend values; never returned by configuration export or audit."""
        TypeAdapter(EnvironmentVariable).validate_python(name)
        if value is not None and (not value or len(value) > 65536 or "\x00" in value):
            raise ClawError(
                "credential_invalid", "Credential value must be nonempty and bounded", 422
            )
        with self._transaction() as db:
            self._principal(db, actor_id).require("admin")
            if value is None:
                db.execute("DELETE FROM credentials WHERE name=?", (name,))
            else:
                db.execute(
                    "INSERT INTO credentials VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET "
                    "value=excluded.value,updated_at=excluded.updated_at",
                    (name, value, now()),
                )
            self._audit(
                db, "credential_updated", name, {"actor": actor_id, "configured": value is not None}
            )

    def credential_status(self, actor_id: str) -> list[dict[str, str]]:
        with self._connection() as db:
            self._principal(db, actor_id).require("admin")
            return [
                dict(row)
                for row in db.execute("SELECT name,updated_at FROM credentials ORDER BY name")
            ]

    def resolve_credential(self, name: str) -> str:
        """Internal current-use resolver. Managed values override process environment references."""
        TypeAdapter(EnvironmentVariable).validate_python(name)
        with self._connection() as db:
            row = db.execute("SELECT value FROM credentials WHERE name=?", (name,)).fetchone()
        value = row[0] if row else os.environ.get(name)
        if not value:
            raise ClawError("credential_missing", f"Configure credential reference: {name}")
        return value

    @staticmethod
    def _asset(db: sqlite3.Connection, asset_id: str) -> AssetRecord:
        row = db.execute(
            "SELECT id,thread_id,run_id,kind,name,media_type,size,sha256,created_at "
            "FROM assets WHERE id=?",
            (asset_id,),
        ).fetchone()
        if row is None:
            raise ClawError("asset_missing", "Retained file not found", 404)
        return AssetRecord.model_validate(dict(row))

    def retain_asset(
        self,
        actor_id: str,
        thread_id: str,
        request_id: str,
        name: str,
        media_type: str,
        content: bytes,
        *,
        run_id: str | None = None,
        owner: str | None = None,
    ) -> AssetRecord:
        if not request_id or len(request_id) > 200:
            raise ClawError("request_identity", "A bounded request identity is required", 422)
        if (
            not name
            or len(name) > 255
            or name in {".", ".."}
            or any(char in name for char in "/\\:")
            or any(ord(char) < 32 or ord(char) == 127 for char in name)
        ):
            raise ClawError("asset_name", "Use a filename without paths or control characters", 422)
        if not re.fullmatch(r"[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+", media_type):
            raise ClawError("asset_type", "Use a media type without parameters", 422)
        if len(content) > MAX_ASSET_BYTES:
            raise ClawError("asset_size", "Retained files are limited to 8 MiB each", 413)
        checksum = hashlib.sha256(content).hexdigest()
        fingerprint = digest([thread_id, run_id, name, media_type, checksum])
        with self._transaction() as db:
            actor = self._principal(db, actor_id)
            actor.require("submit", thread_id)
            thread = self._thread(db, thread_id)
            if run_id is not None:
                if owner is None:
                    raise ClawError("stale_owner", "Artifact publication requires an owner")
                run = self._owned(db, run_id, owner)
                self._authorize_run(db, run)
                if run.thread_id != thread_id or run.actor_id != actor_id:
                    raise ClawError("asset_scope", "Artifact owner does not match this Run", 403)
            prior = db.execute(
                "SELECT id,fingerprint FROM assets WHERE actor_id=? AND request_id=?",
                (actor_id, request_id),
            ).fetchone()
            if prior is not None:
                if prior[1] != fingerprint:
                    raise ClawError("request_conflict", "File request identity was already used")
                return self._asset(db, prior[0])
            if thread.archived:
                raise ClawError("thread_archived", "Unarchive the Thread before adding files")
            asset_id = new_id("file")
            db.execute(
                "INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    asset_id,
                    thread_id,
                    run_id,
                    actor_id,
                    request_id,
                    fingerprint,
                    "artifact" if run_id else "attachment",
                    name,
                    media_type,
                    len(content),
                    checksum,
                    now(),
                    content,
                ),
            )
            self._audit(db, "file_retained", asset_id, {"actor": actor_id, "run": run_id})
            return self._asset(db, asset_id)

    def assets(self, actor_id: str, thread_id: str) -> list[AssetRecord]:
        with self._connection() as db:
            self._principal(db, actor_id).require("read", thread_id)
            self._thread(db, thread_id)
            return [
                self._asset(db, row[0])
                for row in db.execute(
                    "SELECT id FROM assets WHERE thread_id=? ORDER BY created_at", (thread_id,)
                )
            ]

    def asset_content(self, actor_id: str, asset_id: str) -> tuple[AssetRecord, bytes]:
        with self._connection() as db:
            asset = self._asset(db, asset_id)
            self._principal(db, actor_id).require("read", asset.thread_id)
            return asset, db.execute(
                "SELECT content FROM assets WHERE id=?", (asset_id,)
            ).fetchone()[0]

    def run_asset(self, run_id: str, owner: str, asset_id: str) -> tuple[AssetRecord, bytes]:
        with self._connection() as db:
            run = self._owned(db, run_id, owner)
            self._authorize_run(db, run)
            asset = self._asset(db, asset_id)
            if asset.thread_id != run.thread_id:
                raise ClawError("asset_scope", "Retained file belongs to a different Thread", 403)
            return asset, db.execute(
                "SELECT content FROM assets WHERE id=?", (asset_id,)
            ).fetchone()[0]

    def files_target(
        self, actor_id: str, run_id: str, definition: dict[str, JsonValue]
    ) -> TargetRecord:
        """Resolve only an already associated target, using captured file permissions."""
        from a13n_claw.domain import EnvironmentDefinition

        with self._connection() as db:
            run = self._run(db, run_id)
            actor = self._principal(db, actor_id)
            actor.require("read", run.thread_id)
            actor.use_profile(run.composition.profile.id)
            selected = EnvironmentDefinition.model_validate(run.composition.environment.content)
            if selected.files == "none":
                raise ClawError(
                    "files_forbidden", "This captured environment disables file access", 403
                )
            row = db.execute(
                "SELECT id FROM targets WHERE thread_id=? AND generation=?",
                (run.thread_id, digest(definition)),
            ).fetchone()
            if row is None:
                raise ClawError(
                    "target_unprepared", "Run has not prepared its selected environment"
                )
            return self._target(db, row[0])
