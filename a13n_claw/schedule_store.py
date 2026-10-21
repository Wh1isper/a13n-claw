"""Atomic schedule clocks, occurrences and output intents; no execution checkpoints."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from a13n_claw.business import BusinessStore, encode, identifier
from a13n_claw.schedule_models import ScheduleCatalog, ScheduleDefinition

_COMPLETABLE = ("accepted", "waiting", "unknown")


class ScheduleStore:
    def __init__(self, business: BusinessStore):
        self.business = business

    def get(self, occurrence_id: str) -> dict[str, Any]:
        rows = self.business.rows("SELECT * FROM occurrences WHERE id=?", (occurrence_id,))
        if not rows:
            raise ValueError("Unknown occurrence")
        return rows[0]

    def list(self, schedule_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.business.rows(
            "SELECT * FROM occurrences WHERE (? IS NULL OR schedule_id=?) "
            "ORDER BY created DESC,id DESC LIMIT ?",
            (schedule_id, schedule_id, limit),
        )

    def restart(self) -> None:
        with self.business.transaction() as db:
            db.execute(
                "UPDATE occurrences SET status='unknown',detail='Process ended during submission' "
                "WHERE status='calling'"
            )

    @staticmethod
    def _insert(
        db: sqlite3.Connection,
        key: str,
        schedule_id: str,
        definition: ScheduleDefinition,
        destination: dict | None,
        due: float,
        now: float,
        *,
        skipped: str = "",
    ) -> dict[str, Any]:
        old = db.execute("SELECT * FROM occurrences WHERE request_key=?", (key,)).fetchone()
        if old is not None:
            if old["schedule_id"] != schedule_id or old["definition"] != encode(
                definition.model_dump(mode="json")
            ):
                raise ValueError("Conflicting occurrence request identity")
            return dict(old)
        occurrence_id = identifier("occurrence")
        db.execute(
            "INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,NULL,NULL,?,NULL,0,?)",
            (
                occurrence_id,
                schedule_id,
                key,
                encode(definition.model_dump(mode="json")),
                encode(destination) if destination else None,
                due,
                "skipped" if skipped else "pending",
                skipped,
                now,
            ),
        )
        return dict(db.execute("SELECT * FROM occurrences WHERE id=?", (occurrence_id,)).fetchone())

    def trigger(
        self,
        schedule_id: str,
        definition: ScheduleDefinition,
        destination: dict | None,
        request_key: str,
        now: float,
    ) -> dict[str, Any]:
        with self.business.transaction() as db:
            return self._insert(
                db, f"manual:{request_key}", schedule_id, definition, destination, now, now
            )

    def discover(
        self, catalog: ScheduleCatalog, destinations: dict[str, dict | None], now: float
    ) -> None:
        with self.business.transaction() as db:
            for schedule_id, definition in catalog.schedules.items():
                row = db.execute(
                    "SELECT * FROM schedule_state WHERE id=?", (schedule_id,)
                ).fetchone()
                if row is None or row["revision"] != definition.revision:
                    due = definition.timing.first(now) if definition.enabled else None
                    db.execute(
                        "INSERT INTO schedule_state VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET "
                        "revision=excluded.revision,next_due=excluded.next_due",
                        (schedule_id, definition.revision, due),
                    )
                else:
                    due = row["next_due"]
                if not definition.enabled or due is None or due > now:
                    continue
                # One catch-up per discovery; downtime never launches an unbounded burst.
                latest = definition.timing.latest(due, now)
                next_due = (
                    None if definition.timing.kind == "once" else definition.timing.after(latest)
                )
                skipped = (
                    "Missed occurrence skipped"
                    if definition.missed == "skip" and now - due > definition.grace_seconds
                    else ""
                )
                overlap = db.execute(
                    "SELECT 1 FROM occurrences WHERE schedule_id=? AND status IN "
                    "('pending','calling','accepted','waiting','unknown') LIMIT 1",
                    (schedule_id,),
                ).fetchone()
                if overlap is not None and definition.overlap == "skip":
                    skipped = "Previous occurrence is unresolved"
                self._insert(
                    db,
                    f"timer:{schedule_id}:{definition.revision}:{latest:.6f}",
                    schedule_id,
                    definition,
                    destinations.get(schedule_id),
                    latest,
                    now,
                    skipped=skipped,
                )
                db.execute(
                    "UPDATE schedule_state SET next_due=? WHERE id=?", (next_due, schedule_id)
                )

    def pending(self, now: float) -> list[dict[str, Any]]:
        # Prefer actionable work before the batch limit. Blocked queue rows remain
        # inspectable so removed/paused schedules can still settle their backlog.
        return self.business.rows(
            "SELECT pending.* FROM occurrences AS pending "
            "WHERE pending.status='pending' AND pending.next_check<=? "
            "ORDER BY (json_extract(pending.definition,'$.overlap')='queue' AND EXISTS ("
            "SELECT 1 FROM occurrences AS earlier WHERE earlier.schedule_id=pending.schedule_id "
            "AND (earlier.created<pending.created OR "
            "(earlier.created=pending.created AND earlier.id<pending.id)) "
            "AND earlier.status IN ('pending','calling','accepted','waiting','unknown'))), "
            "pending.created,pending.id LIMIT 50",
            (now,),
        )

    def earlier_open(self, row: dict[str, Any]) -> bool:
        return bool(
            self.business.rows(
                "SELECT id FROM occurrences WHERE schedule_id=? AND id!=? "
                "AND (created<? OR (created=? AND id<?)) "
                "AND status IN ('pending','calling','accepted','waiting','unknown') LIMIT 1",
                (row["schedule_id"], row["id"], row["created"], row["created"], row["id"]),
            )
        )

    def start(self, occurrence_id: str, thread_id: str) -> None:
        with self.business.transaction() as db:
            if (
                db.execute(
                    "UPDATE occurrences SET status='calling',thread_id=?,detail='' "
                    "WHERE id=? AND status='pending'",
                    (thread_id, occurrence_id),
                ).rowcount
                != 1
            ):
                raise ValueError("Occurrence is no longer pending")

    def status(
        self,
        occurrence_id: str,
        status: str,
        detail: str = "",
        *,
        receipt_id: str | None = None,
        delay: float = 0,
    ) -> None:
        with self.business.transaction() as db:
            db.execute(
                "UPDATE occurrences SET status=?,detail=?,receipt_id=COALESCE(?,receipt_id),"
                "next_check=? WHERE id=?",
                (status, detail, receipt_id, time.time() + delay, occurrence_id),
            )

    def complete(self, occurrence_id: str, status: str, result: str) -> None:
        """Publish exact outcome and its configured notification together."""
        with self.business.transaction() as db:
            row = db.execute("SELECT * FROM occurrences WHERE id=?", (occurrence_id,)).fetchone()
            if status not in {"completed", "failed", "cancelled"}:
                raise ValueError("Expected an exact terminal outcome")
            if row is None or row["status"] not in _COMPLETABLE:
                return
            definition = ScheduleDefinition.model_validate_json(row["definition"])
            db.execute(
                "UPDATE occurrences SET status=?,result=?,detail='' WHERE id=?",
                (status, result, occurrence_id),
            )
            notify = definition.notification == "always" or (
                definition.notification == "error" and status != "completed"
            )
            if notify and row["destination"] is not None:
                destination = json.loads(row["destination"])
                delivery_id = identifier("delivery")
                text = f"{definition.name}: {status}\n{result}"[:19_800]
                if len(result) > 19_000:
                    text += f"\nFull result: occurrence {occurrence_id} (operator API)."
                db.execute(
                    "INSERT INTO deliveries VALUES(?,?,?,?,?,NULL,?,'pending','',?)",
                    (
                        delivery_id,
                        f"occurrence:{occurrence_id}",
                        destination["channel_id"],
                        encode(
                            {"chat_id": destination["chat_id"], "reply_to": destination["reply_to"]}
                        ),
                        row["thread_id"],
                        encode({"text": text, "file": None}),
                        time.time(),
                    ),
                )
                db.execute("INSERT INTO job_deliveries VALUES(?,?)", (delivery_id, occurrence_id))

    def resolve(self, occurrence_id: str, action: str, evidence: str) -> dict[str, Any]:
        if not evidence.strip() or action not in {"retry", "abandon"}:
            raise ValueError("Resolution requires retry/abandon and evidence")
        with self.business.transaction() as db:
            row = db.execute("SELECT * FROM occurrences WHERE id=?", (occurrence_id,)).fetchone()
            if row is None or row["status"] != "unknown":
                raise ValueError("Only unknown occurrences require resolution")
            db.execute(
                "INSERT INTO resolutions VALUES(?,?,?,?,?)",
                (identifier("resolution"), occurrence_id, action, evidence, time.time()),
            )
            db.execute(
                "UPDATE occurrences SET status=?,detail=?,receipt_id=NULL WHERE id=?",
                ("pending" if action == "retry" else "skipped", evidence, occurrence_id),
            )
        return self.get(occurrence_id)

    def continuing(self, occurrence_id: str) -> None:
        with self.business.transaction() as db:
            row = db.execute("SELECT * FROM occurrences WHERE id=?", (occurrence_id,)).fetchone()
            if row is None or row["status"] != "waiting":
                raise ValueError("Occurrence is not waiting")
            db.execute(
                "INSERT INTO resolutions VALUES(?,?,?,?,?)",
                (
                    identifier("resolution"),
                    occurrence_id,
                    "continue",
                    f"Continue suspended operation {row['receipt_id']}",
                    time.time(),
                ),
            )
            db.execute(
                "UPDATE occurrences SET status='calling',receipt_id=NULL,detail='' WHERE id=?",
                (occurrence_id,),
            )
