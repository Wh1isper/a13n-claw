"""Instance coordination over the same short transactions as ordinary work.

Inbox dispositions, worker ownership and wake admission are durable application
facts. Dispatch scans levels, never treats a live notice as an acknowledgement.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from a13n_claw.attention import (
    ChannelPolicy,
    ConversationMode,
    ProcessingChange,
    ProcessingControl,
    WorkerCreate,
)
from a13n_claw.domain import (
    TERMINAL,
    ClawError,
    Composition,
    InputReceipt,
    InputRequest,
    Principal,
    RunRecord,
    ThreadRecord,
    canonical,
    digest,
    new_id,
    now,
)

if TYPE_CHECKING:
    from a13n_claw.storage import Store

SCHEMA = """
ALTER TABLE threads ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE threads ADD COLUMN owner_main_id TEXT REFERENCES threads(id);
ALTER TABLE delegations ADD COLUMN delivery_attention_id TEXT;
CREATE TABLE coordination (
    id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL DEFAULT 'per_channel',
    main_id TEXT REFERENCES threads(id), actor_id TEXT REFERENCES principals(id),
    paused INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
    progress INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
    retry_at TEXT, blocked_reason TEXT
);
INSERT INTO coordination(id) VALUES (1);
CREATE TABLE worker_creations (
    main_id TEXT NOT NULL REFERENCES threads(id), request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL, worker_id TEXT NOT NULL REFERENCES threads(id),
    input_id TEXT NOT NULL REFERENCES inputs(id), PRIMARY KEY(main_id,request_id)
);
CREATE TABLE inbox (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
    main_id TEXT NOT NULL REFERENCES threads(id), origin TEXT NOT NULL,
    fingerprint TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
    source_thread_id TEXT REFERENCES threads(id), source_run_id TEXT REFERENCES runs(id),
    channel_id TEXT, input_id TEXT REFERENCES inputs(id),
    disposition TEXT NOT NULL DEFAULT 'pending',
    version INTEGER NOT NULL DEFAULT 1, note TEXT NOT NULL DEFAULT '',
    dependency_run_id TEXT REFERENCES runs(id), due_at TEXT, human_key TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(main_id,origin)
);
CREATE INDEX inbox_actionable ON inbox(main_id,disposition,sequence);
CREATE TABLE attention_notices (
    run_id TEXT PRIMARY KEY REFERENCES runs(id), sequence INTEGER NOT NULL
);
CREATE TABLE automatic_inputs (
    input_id TEXT PRIMARY KEY REFERENCES inputs(id)
);
CREATE TABLE automatic_runs (
    run_id TEXT PRIMARY KEY REFERENCES runs(id), progress INTEGER NOT NULL,
    settled INTEGER NOT NULL DEFAULT 0, automatic_only INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE channels (
    id TEXT PRIMARY KEY, version INTEGER NOT NULL, policy TEXT NOT NULL,
    thread_id TEXT REFERENCES threads(id), active INTEGER NOT NULL DEFAULT 1,
    qualified_key TEXT NOT NULL
);
CREATE UNIQUE INDEX active_channel_key ON channels(qualified_key) WHERE active=1;
CREATE TABLE ingress (
    channel_id TEXT NOT NULL REFERENCES channels(id), event_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL, receipt TEXT NOT NULL,
    PRIMARY KEY(channel_id,event_id)
);
CREATE TABLE external_assets (
    asset_id TEXT NOT NULL REFERENCES assets(id), channel_id TEXT NOT NULL REFERENCES channels(id),
    event_id TEXT NOT NULL, sender TEXT NOT NULL, addressed INTEGER NOT NULL,
    PRIMARY KEY(asset_id,channel_id,event_id)
);
CREATE TABLE deliveries (
    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id),
    run_id TEXT NOT NULL REFERENCES runs(id), actor_id TEXT NOT NULL REFERENCES principals(id),
    request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
    channel_id TEXT NOT NULL REFERENCES channels(id), channel_version INTEGER NOT NULL,
    text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    version INTEGER NOT NULL DEFAULT 1, transport_receipt TEXT, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(thread_id,request_id)
);
CREATE INDEX delivery_pending ON deliveries(status);
"""


class Coordination:
    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def state(db: sqlite3.Connection) -> sqlite3.Row:
        return db.execute("SELECT * FROM coordination WHERE id=1").fetchone()

    def configure_mode(self, mode: ConversationMode) -> None:
        """Called under the Instance lease, after startup execution reconciliation.

        Switching has no migration semantics: every old Thread remains readable,
        but inactive; old bindings must never acquire new execution authority.
        """
        with self.store._transaction() as db:
            old = self.state(db)
            if old["mode"] == mode:
                return
            if (
                db.execute(
                    "SELECT 1 FROM runs WHERE status IN ('queued','preparing','running','waiting') "
                    "OR recovery_required=1 LIMIT 1"
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM deliveries WHERE status IN ('sending','unknown') LIMIT 1"
                ).fetchone()
            ):
                raise ClawError(
                    "mode_unsettled",
                    "Settle outstanding work and unknown effects before changing mode",
                )
            db.execute("UPDATE threads SET active=0")
            db.execute("UPDATE channels SET active=0")
            db.execute(
                "UPDATE coordination SET mode=?,main_id=NULL,actor_id=NULL,paused=0,"
                "version=version+1,progress=0,failures=0,retry_at=NULL,blocked_reason=NULL",
                (mode,),
            )
            self.store._audit(db, "mode_selected", mode, {"previous": old["mode"]})

    def initialize_main(self, actor_id: str, profile_id: str) -> ThreadRecord:
        with self.store._transaction() as db:
            actor = self.store._principal(db, actor_id)
            actor.require("admin")
            state = self.state(db)
            if state["mode"] != "one_thread":
                raise ClawError("mode_required", "Start the Instance in One Thread mode")
            if state["main_id"] is not None:
                return self.store._thread(db, state["main_id"])
            actor.use_profile(profile_id)
            profile = self.store._resource(db, "profile", profile_id)
            if profile.retired:
                raise ClawError("resource_retired", "Profile is retired")
            main_id = new_id("thread")
            db.execute(
                "INSERT INTO threads(id,title,profile_id,created_at) VALUES (?,'Main',?,?)",
                (main_id, profile_id, now()),
            )
            db.execute(
                "UPDATE coordination SET main_id=?,actor_id=?,version=version+1 WHERE id=1",
                (main_id, actor_id),
            )
            return self.store._thread(db, main_id)

    def _access(self, db: sqlite3.Connection, actor_id: str, action: str = "read") -> sqlite3.Row:
        state = self.state(db)
        if state["mode"] != "one_thread" or not state["main_id"]:
            raise ClawError("main_unconfigured", "Configure the canonical Main Thread first")
        self.store._principal(db, actor_id).require(action, state["main_id"])
        return state

    def mode(self) -> str:
        with self.store._connection() as db:
            return self.state(db)["mode"]

    def view(self, actor_id: str) -> dict[str, Any]:
        with self.store._transaction() as db:
            actor = self.store._principal(db, actor_id)
            actor.require("read")
            state = dict(self.state(db))
            if state["main_id"]:
                actor.require("read", state["main_id"])
                state["counts"] = {
                    row[0]: row[1]
                    for row in db.execute(
                        "SELECT disposition,COUNT(*) FROM inbox WHERE main_id=? "
                        "GROUP BY disposition",
                        (state["main_id"],),
                    )
                }
                state["workers"] = [
                    self.store._thread(db, row[0]).model_dump(mode="json")
                    for row in db.execute(
                        "SELECT id FROM threads WHERE owner_main_id=? AND active=1 "
                        "ORDER BY created_at",
                        (state["main_id"],),
                    )
                    if actor.admin or row[0] in actor.thread_ids
                ]
            else:
                state.update(counts={}, workers=[])
            return state

    def control(self, actor_id: str, change: ProcessingControl) -> dict[str, Any]:
        with self.store._transaction() as db:
            state = self._access(db, actor_id, "submit")
            if state["version"] != change.expected_version:
                raise ClawError("version_conflict", "Processing controls changed; refresh first")
            db.execute(
                "UPDATE coordination SET paused=?,version=version+1,"
                "retry_at=CASE WHEN ? THEN NULL ELSE retry_at END,"
                "failures=CASE WHEN ? THEN 0 ELSE failures END WHERE id=1",
                (change.paused, change.retry, change.retry),
            )
            self.store._audit(db, "processing_control", state["main_id"], change.model_dump())
        return self.view(actor_id)

    def validate_composition(
        self, db: sqlite3.Connection, thread: ThreadRecord, composition: Composition
    ) -> None:
        if not thread.active:
            raise ClawError("thread_inactive", "This Thread belongs to an inactive mode")
        state = self.state(db)
        # Arbitrary MCP cannot prove absence of publication tools or credentials.
        # Broad operator-granted shell is not a sandbox.
        if (
            state["mode"] == "one_thread"
            and thread.id != state["main_id"]
            and composition.mcp_servers
        ):
            raise ClawError(
                "worker_mcp_forbidden",
                "Only Main may bind MCP integrations in One Thread mode",
                403,
            )

    def capability_role(self, run_id: str, owner: str) -> str | None:
        with self.store._transaction() as db:
            run = self.store._owned(db, run_id, owner)
            self.store._authorize_run(db, run)
            state = self.state(db)
            if state["mode"] != "one_thread":
                return None
            if run.thread_id == state["main_id"]:
                return "main"
            thread = self.store._thread(db, run.thread_id)
            return "worker" if thread.owner_main_id == state["main_id"] else None

    def _bound(self, db: sqlite3.Connection, run_id: str, owner: str) -> RunRecord:
        run = self.store._owned(db, run_id, owner)
        self.store._authorize_run(db, run)
        if run.cancel_requested:
            raise ClawError("run_stopping", "Stopped work cannot authorize new coordination")
        state = self._access(db, run.actor_id)
        thread = self.store._thread(db, run.thread_id)
        if run.thread_id != state["main_id"] and thread.owner_main_id != state["main_id"]:
            raise ClawError(
                "collaboration_forbidden", "This Thread is not a persistent worker", 403
            )
        return run

    def _main_bound(self, db: sqlite3.Connection, run_id: str, owner: str) -> RunRecord:
        run = self._bound(db, run_id, owner)
        if run.thread_id != self.state(db)["main_id"]:
            raise ClawError(
                "main_only", "Only the actual Main Thread may perform this operation", 403
            )
        return run

    def _record(
        self,
        db: sqlite3.Connection,
        main_id: str,
        origin: str,
        kind: str,
        payload: dict[str, Any],
        *,
        source_thread_id: str | None = None,
        source_run_id: str | None = None,
        channel_id: str | None = None,
    ) -> dict[str, Any]:
        fingerprint = digest(payload)
        prior = db.execute(
            "SELECT * FROM inbox WHERE main_id=? AND origin=?", (main_id, origin)
        ).fetchone()
        if prior:
            if prior["fingerprint"] != fingerprint:
                raise ClawError(
                    "request_conflict", "Attention identity was reused for different content"
                )
            return self._item(prior)
        item_id = new_id("inbox")
        db.execute(
            "INSERT INTO inbox(id,main_id,origin,fingerprint,kind,payload,source_thread_id,"
            "source_run_id,channel_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                item_id,
                main_id,
                origin,
                fingerprint,
                kind,
                canonical(payload),
                source_thread_id,
                source_run_id,
                channel_id,
                now(),
                now(),
            ),
        )
        return self._item(db.execute("SELECT * FROM inbox WHERE id=?", (item_id,)).fetchone())

    @staticmethod
    def _item(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value.pop("fingerprint")
        value["payload"] = json.loads(value["payload"])
        return value

    def human_input(self, db: sqlite3.Connection, receipt: InputReceipt) -> None:
        """Called only by the trusted public admission path, never InputRequest.source."""
        thread = self.store._thread(db, receipt.thread_id)
        db.execute("UPDATE automatic_runs SET automatic_only=0 WHERE run_id=?", (receipt.run_id,))
        if thread.owner_main_id and thread.active:
            self._record(
                db,
                thread.owner_main_id,
                f"human:{receipt.id}",
                "human_input",
                {
                    "input_id": receipt.id,
                    "actor_id": receipt.actor_id,
                    "instruction": (
                        "Already admitted. Inspect and reconcile; do not assign it again."
                    ),
                },
                source_thread_id=thread.id,
                source_run_id=receipt.run_id,
            )

    def create_worker(self, run_id: str, owner: str, request: WorkerCreate) -> dict[str, Any]:
        with self.store._transaction() as db:
            main = self._main_bound(db, run_id, owner)
            actor = self.store._principal(db, main.actor_id)
            actor.require("create")
            fingerprint = digest(request.model_dump(mode="json"))
            prior = db.execute(
                "SELECT * FROM worker_creations WHERE main_id=? AND request_id=?",
                (main.thread_id, request.request_id),
            ).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ClawError("request_conflict", "Worker request identity was reused")
                actor.require("read", prior["worker_id"])
                admitted = db.execute(
                    "SELECT run_id FROM inputs WHERE id=?", (prior["input_id"],)
                ).fetchone()
                return {
                    "thread_id": prior["worker_id"],
                    "input_id": prior["input_id"],
                    "run_id": admitted[0],
                }
            captured = self.store._capture(
                db, actor, request.profile_id, main.composition.workspace
            )
            worker_id = new_id("thread")
            db.execute(
                "INSERT INTO threads(id,title,profile_id,owner_main_id,created_at) "
                "VALUES (?,?,?,?,?)",
                (worker_id, request.title, request.profile_id, main.thread_id, now()),
            )
            self.validate_composition(db, self.store._thread(db, worker_id), captured)
            self.store._grant_created_thread(db, actor, worker_id)
            receipt = self.store._admit(
                db,
                actor.id,
                worker_id,
                InputRequest(request_id=new_id("worker-input"), text=request.text, source="main"),
                main.composition.workspace,
            )
            db.execute(
                "INSERT INTO worker_creations VALUES (?,?,?,?,?)",
                (
                    main.thread_id,
                    request.request_id,
                    fingerprint,
                    worker_id,
                    receipt.id,
                ),
            )
            return {"thread_id": worker_id, "input_id": receipt.id, "run_id": receipt.run_id}

    def inspect_collaboration(
        self, run_id: str, owner: str, thread_id: str | None = None
    ) -> dict[str, Any]:
        with self.store._transaction() as db:
            run = self._bound(db, run_id, owner)
            main_id = self.state(db)["main_id"]
            allowed = {run.thread_id, main_id}
            if run.thread_id == main_id:
                allowed.update(
                    row[0]
                    for row in db.execute(
                        "SELECT id FROM threads WHERE owner_main_id=? AND active=1", (main_id,)
                    )
                )
            actor = self.store._principal(db, run.actor_id)
            allowed = {item for item in allowed if actor.admin or item in actor.thread_ids}
            if thread_id is None:
                return {
                    "threads": [
                        self.store._thread(db, item).model_dump(mode="json")
                        for item in sorted(allowed)
                    ]
                }
            if thread_id not in allowed:
                raise ClawError(
                    "collaboration_forbidden", "Thread is outside the bound ownership scope", 403
                )
            rows = db.execute(
                "SELECT id FROM runs WHERE thread_id=? ORDER BY sequence DESC LIMIT 20",
                (thread_id,),
            ).fetchall()
            return {
                "thread": self.store._thread(db, thread_id).model_dump(mode="json"),
                "inputs": [
                    self.store._receipt(row).model_dump(mode="json")
                    for row in db.execute(
                        "SELECT * FROM inputs WHERE thread_id=? ORDER BY sequence DESC LIMIT 20",
                        (thread_id,),
                    )
                ],
                "runs": [
                    self.store._run(db, row[0]).model_dump(
                        mode="json", exclude={"owner", "composition"}
                    )
                    for row in rows
                ],
            }

    def send_collaboration(
        self, run_id: str, owner: str, thread_id: str, request: InputRequest
    ) -> dict[str, Any]:
        with self.store._transaction() as db:
            run = self._bound(db, run_id, owner)
            main_id = self.state(db)["main_id"]
            target = self.store._thread(db, thread_id)
            if run.thread_id == main_id:
                if target.owner_main_id != main_id or not target.active:
                    raise ClawError(
                        "collaboration_forbidden", "Main may send only to its owned workers", 403
                    )
                receipt = self.store._admit(
                    db,
                    run.actor_id,
                    thread_id,
                    InputRequest(
                        request_id=f"collab:{digest([run.thread_id, request.request_id])}",
                        text=request.text,
                        source=f"thread:{run.thread_id}",
                    ),
                    run.composition.workspace,
                )
                return receipt.model_dump(mode="json")
            if thread_id != main_id:
                raise ClawError(
                    "collaboration_forbidden", "Workers may send only to their owner", 403
                )
            self.store._principal(db, run.actor_id).require("submit", main_id)
            return self._record(
                db,
                main_id,
                f"message:{run.thread_id}:{request.request_id}",
                "worker_message",
                {"text": request.text, "request_id": request.request_id},
                source_thread_id=run.thread_id,
                source_run_id=run.id,
            )

    def track_automatic_input(self, db: sqlite3.Connection, receipt: InputReceipt) -> None:
        db.execute("INSERT OR IGNORE INTO automatic_inputs VALUES (?)", (receipt.id,))
        self.refresh_automatic_run(db, receipt.run_id)

    def refresh_automatic_run(self, db: sqlite3.Connection, run_id: str) -> None:
        """Input provenance survives transfers; caller-controlled source labels do not count."""
        counts = db.execute(
            "SELECT COUNT(*),COUNT(a.input_id) FROM inputs i LEFT JOIN automatic_inputs a "
            "ON a.input_id=i.id WHERE i.run_id=?",
            (run_id,),
        ).fetchone()
        if counts[1]:
            db.execute(
                "INSERT INTO automatic_runs(run_id,progress,automatic_only) VALUES (?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET automatic_only=excluded.automatic_only",
                (run_id, self.state(db)["progress"], counts[0] == counts[1]),
            )

    def _admit_worker_messages(self, db: sqlite3.Connection, main_id: str, workspace: str) -> None:
        rows = db.execute(
            "SELECT * FROM inbox WHERE main_id=? AND kind='worker_message' "
            "AND disposition='pending' AND input_id IS NULL ORDER BY sequence",
            (main_id,),
        ).fetchall()
        for row in rows:
            source = self.store._run(db, row["source_run_id"])
            self.store._authorize_run(db, source)
            payload = json.loads(row["payload"])
            receipt = self.store._admit(
                db,
                source.actor_id,
                main_id,
                InputRequest(
                    request_id=f"collab:{digest([row['source_thread_id'], payload['request_id']])}",
                    text=payload["text"],
                    source=f"thread:{row['source_thread_id']}",
                ),
                workspace,
            )
            self.track_automatic_input(db, receipt)
            db.execute("UPDATE inbox SET input_id=? WHERE id=?", (receipt.id, row["id"]))

    @staticmethod
    def authorize_context(
        db: sqlite3.Connection, channel_id: str, sender: str, addressed: bool
    ) -> None:
        channel = db.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
        policy = ChannelPolicy.model_validate_json(channel["policy"])
        if not channel["active"] or not policy.enabled or not policy.inbound:
            raise ClawError("channel_revoked", "This Channel no longer permits context access", 403)
        if sender not in policy.allowed_senders or (
            policy.group and policy.require_addressed and not addressed
        ):
            raise ClawError("sender_revoked", "This sender no longer permits context access", 403)

    def authorize_asset(self, db: sqlite3.Connection, asset_id: str) -> None:
        for origin in db.execute("SELECT * FROM external_assets WHERE asset_id=?", (asset_id,)):
            self.authorize_context(db, origin["channel_id"], origin["sender"], origin["addressed"])

    def _item_access(self, db: sqlite3.Connection, actor: Principal, row: sqlite3.Row) -> None:
        actor.require("read", row["main_id"])
        if row["source_thread_id"]:
            actor.require("read", row["source_thread_id"])
        if row["channel_id"]:
            payload = json.loads(row["payload"])
            self.authorize_context(db, row["channel_id"], payload["sender"], payload["addressed"])

    def inbox(
        self,
        actor_id: str,
        *,
        kind: str | None = None,
        attention: bool | None = None,
        disposition: str | None = None,
        after: int = 0,
        limit: int = 100,
        item_id: str | None = None,
        binding: tuple[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ClawError("limit_range", "Use a limit between 1 and 100", 422)
        with self.store._transaction() as db:
            if binding:
                actor_id = self._main_bound(db, *binding).actor_id
            state = self._access(db, actor_id)
            actor = self.store._principal(db, actor_id)
            rows = db.execute(
                "SELECT * FROM inbox WHERE main_id=? AND sequence>? "
                "AND (? IS NULL OR kind=?) AND (? IS NULL OR disposition=?) "
                "AND (? IS NULL OR id=?) AND (? IS NULL OR (kind!='external')=?) "
                "ORDER BY sequence LIMIT ?",
                (
                    state["main_id"],
                    after,
                    kind,
                    kind,
                    disposition,
                    disposition,
                    item_id,
                    item_id,
                    attention,
                    attention,
                    limit,
                ),
            ).fetchall()
            result = []
            for row in rows:
                try:
                    self._item_access(db, actor, row)
                except ClawError:
                    if item_id:
                        raise
                    # Keep processing responsibility visible, with revoked content withheld.
                    result.append(
                        {
                            key: row[key]
                            for key in (
                                "id",
                                "sequence",
                                "kind",
                                "disposition",
                                "version",
                                "channel_id",
                                "note",
                                "dependency_run_id",
                                "due_at",
                                "human_key",
                                "updated_at",
                            )
                        }
                        | {"payload": None, "access": "revoked"}
                    )
                else:
                    result.append(self._item(row))
            return result

    def change_item(
        self,
        actor_id: str,
        item_id: str,
        change: ProcessingChange,
        *,
        binding: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        with self.store._transaction() as db:
            if binding:
                actor_id = self._main_bound(db, *binding).actor_id
            state = self._access(db, actor_id, "submit")
            row = db.execute(
                "SELECT * FROM inbox WHERE id=? AND main_id=?", (item_id, state["main_id"])
            ).fetchone()
            if row is None:
                raise ClawError("inbox_missing", "Inbox item not found", 404)
            if row["version"] != change.expected_version:
                raise ClawError("version_conflict", "Inbox item changed; inspect its current state")
            disposition = change.disposition
            if change.dependency_run_id:
                dependency = self.store._run(db, change.dependency_run_id)
                worker = self.store._thread(db, dependency.thread_id)
                self.store._principal(db, actor_id).require("read", worker.id)
                if worker.owner_main_id != state["main_id"] or not worker.active:
                    raise ClawError("dependency_scope", "Defer only to an owned worker Run", 403)
                if dependency.status in TERMINAL:
                    disposition = "pending"
            if change.due_at and change.due_at <= now():
                disposition = "pending"
            db.execute(
                "UPDATE inbox SET disposition=?,note=?,dependency_run_id=?,due_at=?,human_key=?,"
                "version=version+1,updated_at=? WHERE id=?",
                (
                    disposition,
                    change.note,
                    change.dependency_run_id,
                    change.due_at,
                    change.human_key,
                    now(),
                    item_id,
                ),
            )
            # A meaningful settlement/defer is progress. Repeated pending->pending is not.
            if disposition != "pending" and (row["disposition"] != disposition):
                db.execute("UPDATE coordination SET progress=progress+1 WHERE id=1")
            self.store._audit(
                db, "inbox_disposition", item_id, {"actor": actor_id, **change.model_dump()}
            )
            saved = self._item(db.execute("SELECT * FROM inbox WHERE id=?", (item_id,)).fetchone())
            saved.pop("payload")
            return saved

    def bound_inbox(
        self,
        run_id: str,
        owner: str,
        *,
        attention: bool = False,
        disposition: str | None = None,
        after: int = 0,
        item_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.inbox(
            "",
            disposition=disposition,
            after=after,
            item_id=item_id,
            attention=attention,
            binding=(run_id, owner),
        )

    def bound_change(
        self, run_id: str, owner: str, item_id: str, change: ProcessingChange
    ) -> dict[str, Any]:
        return self.change_item("", item_id, change, binding=(run_id, owner))

    def _reactivate(self, db: sqlite3.Connection, main_id: str) -> None:
        db.execute(
            "UPDATE inbox SET disposition='pending',version=version+1,updated_at=? "
            "WHERE main_id=? AND disposition='deferred' AND "
            "((due_at IS NOT NULL AND due_at<=?) OR dependency_run_id IN "
            "(SELECT id FROM runs WHERE status IN "
            "('completed','failed','cancelled','interrupted')))",
            (now(), main_id, now()),
        )

    def reconcile_startup(self) -> None:
        with self.store._transaction() as db:
            # An HTTP call may have succeeded before the process stopped. Never resend it.
            db.execute(
                "UPDATE deliveries SET status='unknown',error='process_interrupted',"
                "version=version+1,updated_at=? WHERE status='sending'",
                (now(),),
            )

    def _collect_outcomes(self, db: sqlite3.Connection, main_id: str) -> None:
        rows = db.execute(
            "SELECT r.id,r.thread_id,r.status FROM runs r JOIN threads t ON t.id=r.thread_id "
            "WHERE t.owner_main_id=? AND t.active=1 "
            "AND r.status IN ('completed','failed','cancelled','interrupted') "
            "AND NOT EXISTS(SELECT 1 FROM inbox i WHERE i.main_id=? AND i.origin='outcome:'||r.id)",
            (main_id, main_id),
        ).fetchall()
        for row in rows:
            self._record(
                db,
                main_id,
                f"outcome:{row['id']}",
                "worker_outcome",
                {
                    "run_id": row["id"],
                    "status": row["status"],
                    "instruction": "Inspect the saved outcome before accepting or retrying work.",
                },
                source_thread_id=row["thread_id"],
                source_run_id=row["id"],
            )

    def _barrier(self, db: sqlite3.Connection, state: sqlite3.Row) -> str | None:
        main_id = state["main_id"]
        if state["paused"]:
            return "paused"
        if db.execute(
            "SELECT 1 FROM runs WHERE thread_id=? AND recovery_required=1", (main_id,)
        ).fetchone():
            return "recovery_required"
        if db.execute(
            "SELECT 1 FROM inputs WHERE thread_id=? AND disposition IN ('blocked','uncertain')",
            (main_id,),
        ).fetchone():
            return "input_reconciliation_required"
        if db.execute(
            "SELECT 1 FROM deliveries WHERE thread_id=? AND status IN ('sending','unknown')",
            (main_id,),
        ).fetchone():
            return "delivery_reconciliation_required"
        if db.execute(
            "SELECT 1 FROM runs WHERE thread_id=? AND status='waiting'", (main_id,)
        ).fetchone():
            return "waiting_decision"
        if state["retry_at"] and state["retry_at"] > now():
            return "no_progress_backoff"
        return None

    def drain(self, workspace: str) -> None:
        """Level-triggered admission; the SQLite writer lock closes arrival/end races."""
        with self.store._transaction() as db:
            state = self.state(db)
            main_id = state["main_id"]
            if state["mode"] != "one_thread" or main_id is None:
                return
            self._collect_outcomes(db, main_id)
            self._reactivate(db, main_id)
            ended = db.execute(
                "SELECT a.*,r.status FROM automatic_runs a JOIN runs r ON r.id=a.run_id "
                "WHERE r.thread_id=? AND a.settled=0 "
                "AND r.status IN ('completed','failed','cancelled','interrupted') "
                "ORDER BY r.sequence",
                (main_id,),
            ).fetchall()
            for attempt in ended:
                progressed = state["progress"] > attempt["progress"]
                failures = 0 if progressed else state["failures"] + 1
                retry_at = (
                    None
                    if progressed
                    else (
                        datetime.now(UTC) + timedelta(seconds=min(300, 2 ** min(failures, 9)))
                    ).isoformat()
                )
                db.execute(
                    "UPDATE coordination SET failures=?,retry_at=? WHERE id=1", (failures, retry_at)
                )
                db.execute(
                    "UPDATE automatic_runs SET settled=1 WHERE run_id=?", (attempt["run_id"],)
                )
                state = self.state(db)
            backlog = db.execute(
                "SELECT COUNT(*),MAX(sequence) FROM inbox "
                "WHERE main_id=? AND disposition='pending'",
                (main_id,),
            ).fetchone()
            barrier = self._barrier(db, state)
            db.execute("UPDATE coordination SET blocked_reason=? WHERE id=1", (barrier,))
            if not backlog[0] or barrier:
                return
            db.execute("SAVEPOINT wake_admission")
            try:
                actor = self.store._principal(db, state["actor_id"])
                actor.require("submit", main_id)
                thread = self.store._thread(db, main_id)
                if thread.archived or not thread.active:
                    raise ClawError("main_inactive", "Main is inactive")
                self._admit_worker_messages(db, main_id, workspace)
                active = db.execute(
                    "SELECT id FROM runs WHERE thread_id=? "
                    "AND status IN ('queued','preparing','running','waiting') "
                    "ORDER BY status='queued',recovery_of IS NULL,sequence LIMIT 1",
                    (main_id,),
                ).fetchone()
                if active:
                    active_run = self.store._run(db, active[0])
                    self.store._authorize_run(db, active_run)
                    notice = db.execute(
                        "SELECT sequence FROM attention_notices WHERE run_id=?", (active[0],)
                    ).fetchone()
                    if notice and notice[0] >= backlog[1]:
                        return
                receipt = self.store._admit(
                    db,
                    actor.id,
                    main_id,
                    InputRequest(
                        request_id=new_id("attention"),
                        source="attention",
                        text="Durable Inbox/coordination work needs attention. Use the bound "
                        "tools to inspect pending items and explicitly handle, ignore or "
                        "defer them. A completed Run does not settle pending work.",
                    ),
                    workspace,
                )
                db.execute(
                    "INSERT INTO attention_notices VALUES (?,?) ON CONFLICT(run_id) "
                    "DO UPDATE SET sequence=excluded.sequence",
                    (receipt.run_id, backlog[1]),
                )
                self.track_automatic_input(db, receipt)
            except ClawError as exc:
                db.execute("ROLLBACK TO wake_admission")
                db.execute("UPDATE coordination SET blocked_reason=? WHERE id=1", (exc.code,))
            finally:
                db.execute("RELEASE wake_admission")

    def authorize_claim(self, db: sqlite3.Connection, run: RunRecord) -> None:
        auto = db.execute(
            "SELECT automatic_only FROM automatic_runs WHERE run_id=?", (run.id,)
        ).fetchone()
        if auto and auto[0]:
            barrier = self._barrier(db, self.state(db))
            if barrier:
                raise ClawError("thread_blocked", barrier)
