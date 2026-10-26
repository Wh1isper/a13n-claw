"""Durable business intent and evidence. Host state is accessed only by the service."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from a13n_claw.configuration.models import ClawSettings
from a13n_claw.database.migration import DatabaseMigrator
from a13n_claw.encoding import encode, identifier


class BusinessStore:
    def __init__(self, path: Path, settings: ClawSettings):
        """Load business state using the configured startup routing mode."""
        self.path = path
        DatabaseMigrator(path).upgrade()
        with self.transaction() as db:
            current = db.execute("SELECT * FROM instance WHERE id=1").fetchone()
            if current is None:
                db.execute(
                    "INSERT INTO instance VALUES(1, ?, NULL, ?)",
                    (settings.mode, settings.main_project_id),
                )
            else:
                if current["main_project_id"] != settings.main_project_id:
                    raise ValueError("Main Project changed; use a fresh Instance root")
                db.execute("UPDATE instance SET mode=? WHERE id=1", (settings.mode,))

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self.transaction() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def instance(self) -> dict[str, Any]:
        return self.rows("SELECT * FROM instance WHERE id=1")[0]

    def target(self, key: str) -> dict[str, Any] | None:
        rows = self.rows("SELECT * FROM targets WHERE key=?", (key,))
        return rows[0] if rows else None

    def begin_target(self, key: str, project_id: str) -> dict[str, Any]:
        with self.transaction() as db:
            existing = db.execute("SELECT * FROM targets WHERE key=?", (key,)).fetchone()
            if existing is not None:
                if existing["status"] == "retry":
                    db.execute("UPDATE targets SET status='calling' WHERE key=?", (key,))
                return dict(existing)
            thread_id = identifier("thread")
            db.execute(
                "INSERT INTO targets VALUES(?,?,?,'calling','Creation intent recorded')",
                (key, thread_id, project_id),
            )
            return dict(db.execute("SELECT * FROM targets WHERE key=?", (key,)).fetchone())

    def finish_target(self, key: str) -> None:
        with self.transaction() as db:
            db.execute("UPDATE targets SET status='created',detail='' WHERE key=?", (key,))
            if key == "main":
                db.execute(
                    "UPDATE instance SET main_thread_id="
                    "(SELECT thread_id FROM targets WHERE key=?) "
                    "WHERE id=1",
                    (key,),
                )

    def accept(
        self,
        *,
        connection: str,
        event_id: str,
        fingerprint: str,
        channel_id: str | None,
        mode: str,
        project_id: str | None,
        body: dict | None,
        reason: str = "",
    ) -> dict[str, Any]:
        with self.transaction() as db:
            old = db.execute(
                "SELECT * FROM inbox WHERE connection=? AND event_id=?", (connection, event_id)
            ).fetchone()
            if old is not None:
                if old["fingerprint"] != fingerprint:
                    raise ValueError("Conflicting reuse of platform event identity")
                return dict(old)
            item_id = identifier("inbox")
            db.execute(
                "INSERT INTO inbox VALUES(?,?,?,?,?,?,?,NULL,?,?,1,?,NULL,0,NULL,?)",
                (
                    item_id,
                    connection,
                    event_id,
                    fingerprint,
                    channel_id,
                    mode,
                    project_id,
                    encode(body) if body is not None else None,
                    "pending" if body is not None else "ignored",
                    reason,
                    time.time(),
                ),
            )
            return dict(db.execute("SELECT * FROM inbox WHERE id=?", (item_id,)).fetchone())

    def item(self, item_id: str) -> dict[str, Any]:
        rows = self.rows("SELECT * FROM inbox WHERE id=?", (item_id,))
        if not rows:
            raise ValueError("Unknown Inbox item")
        return rows[0]

    def actionable(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.rows(
            "SELECT * FROM inbox WHERE mode=(SELECT mode FROM instance WHERE id=1) "
            "AND disposition='pending' AND blocked IS NULL "
            "AND next_check<=? AND NOT EXISTS (SELECT 1 FROM deliveries d "
            "WHERE d.item_id=inbox.id AND d.status IN ('sending','unknown') "
            "AND NOT EXISTS (SELECT 1 FROM feedback_deliveries f WHERE f.delivery_id=d.id)) "
            "ORDER BY created,id LIMIT ?",
            (time.time(), limit),
        )

    def update_body(self, item_id: str, body: dict) -> None:
        with self.transaction() as db:
            db.execute("UPDATE inbox SET body=? WHERE id=?", (encode(body), item_id))

    def assign_thread(self, item_id: str, thread_id: str) -> None:
        with self.transaction() as db:
            db.execute("UPDATE inbox SET thread_id=? WHERE id=?", (thread_id, item_id))

    def begin_attempt(self, thread_id: str, kind: str, item_ids: list[str]) -> str:
        attempt_id = identifier("dispatch")
        with self.transaction() as db:
            for item_id in item_ids:
                row = db.execute("SELECT * FROM inbox WHERE id=?", (item_id,)).fetchone()
                if row is None or row["blocked"] is not None or row["disposition"] != "pending":
                    raise ValueError("Inbox item is no longer actionable")
            db.execute(
                "INSERT INTO attempts VALUES(?,?,?,'calling',NULL,'',?)",
                (attempt_id, thread_id, kind, time.time()),
            )
            for item_id in item_ids:
                db.execute("INSERT INTO attempt_items VALUES(?,?)", (attempt_id, item_id))
                db.execute("UPDATE inbox SET blocked=? WHERE id=?", (attempt_id, item_id))
        return attempt_id

    def begin_continuation(
        self, thread_id: str, control_id: str, target_id: str
    ) -> tuple[str, str]:
        """Retain control and original-task intent in one transaction before Host admission."""
        control_attempt, followup = identifier("dispatch"), identifier("dispatch")
        with self.transaction() as db:
            for item_id, allowed in (
                (control_id, {"pending"}),
                (target_id, {"pending", "handled"}),
            ):
                row = db.execute("SELECT * FROM inbox WHERE id=?", (item_id,)).fetchone()
                if (
                    row is None
                    or row["body"] is None
                    or row["blocked"] is not None
                    or row["disposition"] not in allowed
                ):
                    raise ValueError("Inbox item requires reconciliation before continuation")
            for attempt_id, kind, item_id in (
                (control_attempt, "control", control_id),
                (followup, "continue", target_id),
            ):
                db.execute(
                    "INSERT INTO attempts VALUES(?,?,?,'calling',NULL,'',?)",
                    (attempt_id, thread_id, kind, time.time()),
                )
                db.execute("INSERT INTO attempt_items VALUES(?,?)", (attempt_id, item_id))
                db.execute(
                    "UPDATE inbox SET blocked=?,disposition='pending',version=version+1 WHERE id=?",
                    (attempt_id, item_id),
                )
        return control_attempt, followup

    def finish_attempt(
        self,
        attempt_id: str,
        status: Literal["accepted", "rejected", "unknown", "observed"],
        *,
        receipt_id: str | None = None,
        evidence: str = "",
        retry_seconds: float = 0,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE attempts SET status=?,receipt_id=COALESCE(?,receipt_id),"
                "evidence=? WHERE id=?",
                (status, receipt_id, evidence, attempt_id),
            )
            if status in {"rejected", "observed"}:
                db.execute(
                    "UPDATE inbox SET blocked=NULL,next_check=? WHERE blocked=?",
                    (time.time() + retry_seconds, attempt_id),
                )
                if status == "observed":
                    db.execute(
                        "UPDATE inbox SET reason='Host operation ended without business "
                        "settlement; retry delayed' "
                        "WHERE disposition='pending' AND id IN "
                        "(SELECT item_id FROM attempt_items WHERE attempt_id=?)",
                        (attempt_id,),
                    )

    @staticmethod
    def _readable(db: sqlite3.Connection, item_id: str, receipt_id: str | None) -> dict[str, Any]:
        row = db.execute("SELECT * FROM inbox WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown Inbox item")
        if row["blocked"] is not None:
            attempt = db.execute("SELECT * FROM attempts WHERE id=?", (row["blocked"],)).fetchone()
            # Only the operation that received admitted attention can process these items.
            if (
                attempt is None
                or attempt["status"] != "accepted"
                or (receipt_id is None or attempt["receipt_id"] != receipt_id)
            ):
                raise ValueError("Inbox item requires dispatch reconciliation")
        return dict(row)

    def readable(self, item_id: str, receipt_id: str | None) -> dict[str, Any]:
        with self.transaction() as db:
            return self._readable(db, item_id, receipt_id)

    def settle(
        self,
        item_id: str,
        expected_version: int,
        disposition: Literal["handled", "ignored", "deferred", "pending"],
        reason: str,
        *,
        receipt_id: str | None = None,
        next_check: float = 0,
        dependency: dict | None = None,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("Disposition requires a reason")
        with self.transaction() as db:
            row = self._readable(db, item_id, receipt_id)
            if disposition in {"pending", "deferred"} and row["body"] is None:
                raise ValueError("Inbox content was filtered or pruned; submit a new message")
            cursor = db.execute(
                "UPDATE inbox SET disposition=?,reason=?,version=version+1,"
                "next_check=?,dependency=? "
                "WHERE id=? AND version=?",
                (
                    disposition,
                    reason,
                    next_check,
                    encode(dependency) if dependency else None,
                    item_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Inbox version changed; refetch before settling")
        return self.item(item_id)

    def restart(self) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE attempts SET status='unknown',"
                "evidence='Process ended before exact outcome' "
                "WHERE status IN ('calling','accepted')"
            )
            db.execute(
                "UPDATE deliveries SET status='unknown',evidence='Process ended during send' "
                "WHERE status='sending'"
            )
            db.execute(
                "UPDATE targets SET status='unknown',detail='Process ended during creation' "
                "WHERE status='calling'"
            )

    def resolve_attempt(
        self, attempt_id: str, action: Literal["retry", "settle"], evidence: str
    ) -> None:
        if not evidence.strip():
            raise ValueError("Resolution requires evidence or acknowledgement of repetition risk")
        with self.transaction() as db:
            row = db.execute("SELECT status FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None or row[0] not in {"unknown", "calling"}:
                raise ValueError("Only unresolved attempts can be reconciled")
            db.execute(
                "INSERT INTO resolutions VALUES(?,?,?,?,?)",
                (identifier("resolution"), attempt_id, action, evidence, time.time()),
            )
            db.execute("UPDATE attempts SET status='resolved' WHERE id=?", (attempt_id,))
            if action == "settle":
                db.execute(
                    "UPDATE inbox SET disposition='handled',reason=?,version=version+1 "
                    "WHERE blocked=? AND disposition IN ('pending','deferred')",
                    (evidence, attempt_id),
                )
            db.execute("UPDATE inbox SET blocked=NULL,next_check=0 WHERE blocked=?", (attempt_id,))

    def request_delivery(
        self,
        *,
        request_key: str,
        channel_id: str,
        destination: dict,
        thread_id: str | None,
        item_id: str | None,
        body: dict,
        feedback_kind: str | None = None,
        continuation_id: str | None = None,
    ) -> dict[str, Any]:
        with self.transaction() as db:
            old = db.execute(
                "SELECT * FROM deliveries WHERE request_key=?", (request_key,)
            ).fetchone()
            if old is not None:
                if (
                    old["channel_id"],
                    old["destination"],
                    old["thread_id"],
                    old["item_id"],
                    old["body"],
                ) != (
                    channel_id,
                    encode(destination),
                    thread_id,
                    item_id,
                    encode(body),
                ):
                    raise ValueError("Conflicting delivery request identity")
                return dict(old)
            delivery_id = identifier("delivery")
            db.execute(
                "INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,'pending','',?)",
                (
                    delivery_id,
                    request_key,
                    channel_id,
                    encode(destination),
                    thread_id,
                    item_id,
                    encode(body),
                    time.time(),
                ),
            )
            if feedback_kind is not None:
                db.execute(
                    "INSERT INTO feedback_deliveries VALUES(?,?,?)",
                    (delivery_id, feedback_kind, continuation_id),
                )
            return dict(
                db.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            )

    def settle_routed_reply(self, receipt_id: str) -> None:
        """A retained policy-selected final settles its routed inputs, independently of sending."""
        with self.transaction() as db:
            if not db.execute(
                "SELECT 1 FROM deliveries WHERE request_key=?", (f"claw:final:{receipt_id}",)
            ).fetchone():
                return
            db.execute(
                "UPDATE inbox SET disposition='handled',version=version+1,"
                "reason='Final result retained for delivery' WHERE disposition='pending' "
                "AND mode!='one_thread' AND id IN (SELECT ai.item_id FROM attempt_items ai "
                "JOIN attempts a ON a.id=ai.attempt_id WHERE a.receipt_id=? "
                "AND a.kind IN ('submit','steer','continue'))",
                (receipt_id,),
            )

    def complete_control(
        self,
        item_id: str,
        text: str,
        attempt_id: str | None = None,
        *,
        rejected_followup: tuple[str, str] | None = None,
    ) -> None:
        """Save reply and disposition together; a lost response never repeats the control."""
        if len(text) > 20_000:
            text = (
                "Response exceeds the IM limit. Use the authorized operator CLI/API to inspect it."
            )
        with self.transaction() as db:
            row = db.execute("SELECT * FROM inbox WHERE id=?", (item_id,)).fetchone()
            if row is None or row["disposition"] != "pending":
                raise ValueError("Control is no longer pending")
            message = json.loads(row["body"])["message"]
            db.execute(
                "INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,'pending','',?)",
                (
                    identifier("delivery"),
                    f"control:{item_id}",
                    row["channel_id"],
                    encode({"chat_id": message["chat_id"], "reply_to": message["message_id"]}),
                    row["thread_id"],
                    item_id,
                    encode({"text": text or "No pending results", "file": None}),
                    time.time(),
                ),
            )
            db.execute(
                "UPDATE inbox SET disposition='handled',reason='Control result retained',"
                "version=version+1,blocked=NULL WHERE id=?",
                (item_id,),
            )
            if attempt_id is not None:
                db.execute(
                    "UPDATE attempts SET status='observed',evidence=? WHERE id=?",
                    (text, attempt_id),
                )
            if rejected_followup is not None:
                followup, disposition = rejected_followup
                db.execute(
                    "UPDATE attempts SET status='rejected',evidence=? WHERE id=?",
                    (text, followup),
                )
                # Undo only the rejected continuation's reopening, not a newer settlement.
                db.execute(
                    "UPDATE inbox SET blocked=NULL,disposition=?,version=version+1 WHERE blocked=?",
                    (disposition, followup),
                )

    def claim_delivery(self, delivery_id: str) -> bool:
        with self.transaction() as db:
            return (
                db.execute(
                    "UPDATE deliveries SET status='sending' WHERE id=? AND status='pending'",
                    (delivery_id,),
                ).rowcount
                == 1
            )

    def finish_delivery(
        self,
        delivery_id: str,
        status: Literal["sent", "not_sent", "unknown"],
        evidence: str,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE deliveries SET status=?,evidence=? WHERE id=? AND status='sending'",
                (status, evidence, delivery_id),
            )

    def delivery(self, delivery_id: str) -> dict[str, Any]:
        rows = self.rows("SELECT * FROM deliveries WHERE id=?", (delivery_id,))
        if not rows:
            raise ValueError("Unknown delivery")
        return rows[0]

    def delay(self, item_id: str, reason: str, seconds: float) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE inbox SET reason=?,next_check=? WHERE id=? AND disposition='pending'",
                (reason, time.time() + seconds, item_id),
            )

    def reactivate(self, item_id: str, expected_version: int, reason: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE inbox SET disposition='pending',version=version+1,reason=?,"
                "dependency=NULL,next_check=0 WHERE id=? AND version=? AND disposition='deferred'",
                (reason, item_id, expected_version),
            )

    def dependency_unavailable(self, item_id: str, expected_version: int) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE inbox SET reason='Exact dependency outcome unavailable; "
                "operator review required' "
                "WHERE id=? AND version=? AND disposition='deferred'",
                (item_id, expected_version),
            )

    def resolve_target(self, key: str, action: str, evidence: str) -> None:
        if action not in {"adopt", "retry"} or not evidence.strip():
            raise ValueError("Target resolution requires an action and evidence")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM targets WHERE key=?", (key,)).fetchone()
            if row is None or row["status"] != "unknown":
                raise ValueError("Target is not awaiting reconciliation")
            db.execute(
                "INSERT INTO resolutions VALUES(?,?,?,?,?)",
                (identifier("resolution"), key, action, evidence, time.time()),
            )
            db.execute(
                "UPDATE targets SET status=?,detail=? WHERE key=?",
                ("created" if action == "adopt" else "retry", evidence, key),
            )
            if action == "adopt" and key == "main":
                db.execute("UPDATE instance SET main_thread_id=? WHERE id=1", (row["thread_id"],))

    def target_failure(self, key: str, detail: str) -> None:
        with self.transaction() as db:
            db.execute("UPDATE targets SET status='unknown',detail=? WHERE key=?", (detail, key))

    def resolve_delivery(
        self,
        delivery_id: str,
        outcome: Literal["sent", "not_sent"],
        evidence: str,
        *,
        retry: bool = False,
    ) -> dict[str, Any]:
        if not evidence.strip() or (retry and outcome != "not_sent"):
            raise ValueError("Resolution requires evidence and only non-sends can be retried")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            if row is None or row["status"] not in {"unknown", "not_sent"}:
                raise ValueError("Delivery is not awaiting resolution")
            db.execute(
                "INSERT INTO resolutions VALUES(?,?,?,?,?)",
                (
                    identifier("resolution"),
                    delivery_id,
                    "retry" if retry else outcome,
                    evidence,
                    time.time(),
                ),
            )
            db.execute(
                "UPDATE deliveries SET status=?,evidence=? WHERE id=?",
                ("pending" if retry else outcome, evidence, delivery_id),
            )
        return self.delivery(delivery_id)
