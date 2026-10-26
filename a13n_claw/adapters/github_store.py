"""Durable GitHub polling cursor and delayed, immutable ingress snapshots."""

from __future__ import annotations

import json
from typing import Any

from a13n_claw.database.store import BusinessStore
from a13n_claw.encoding import encode

_SETTLED = """
    EXISTS (SELECT 1 FROM github_event_requests er
            WHERE er.connection=github_events.connection AND er.event_key=github_events.event_key)
    AND NOT EXISTS (
        SELECT 1 FROM github_event_requests er
        JOIN github_requests r USING(connection,request_key)
        JOIN github_events e ON e.connection=er.connection AND e.event_key=er.event_key
        WHERE er.connection=github_events.connection
        AND e.notification_id=github_events.notification_id AND e.updated<=github_events.updated
        AND r.message IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM inbox i WHERE i.connection=r.connection AND i.event_id=r.request_key
            AND i.disposition IN ('handled','ignored') AND i.blocked IS NULL
            AND NOT EXISTS (SELECT 1 FROM attempt_items ai JOIN attempts a ON a.id=ai.attempt_id
                            WHERE ai.item_id=i.id AND a.status IN ('calling','accepted','unknown'))
            AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.item_id=i.id AND d.status!='sent'
                            AND NOT EXISTS (SELECT 1 FROM feedback_deliveries f
                                            WHERE f.delivery_id=d.id))))
"""


class GitHubStore:
    def __init__(self, store: BusinessStore, connection: str, account: str):
        self.store, self.connection, self.account = store, connection, account

    def state(self, initial: float) -> dict[str, Any]:
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO github_poll_state VALUES(?,?,?,0,'')",
                (self.connection, self.account, initial),
            )
            row = dict(
                db.execute(
                    "SELECT * FROM github_poll_state WHERE connection=?", (self.connection,)
                ).fetchone()
            )
            if row["account"] != self.account:
                raise ValueError("GitHub connection identity changed; use a new connection ID")
            return row

    def poll_finished(self, cursor: float, next_poll: float, error: str = "") -> None:
        with self.store.transaction() as db:
            db.execute(
                "UPDATE github_poll_state SET cursor=?,next_poll=?,error=? WHERE connection=?",
                (cursor, next_poll, error, self.connection),
            )

    def stage(self, notification: dict, updated: float, delay: float) -> None:
        event_key = f"{notification['id']}:{notification['updated_at']}"
        with self.store.transaction() as db:
            if db.execute(
                "SELECT 1 FROM github_events WHERE connection=? AND notification_id=? "
                "AND updated>? LIMIT 1",
                (self.connection, str(notification["id"]), updated),
            ).fetchone():
                return
            notification = dict(notification)
            since = notification.get("since", updated - 1)
            for older in db.execute(
                "SELECT notification FROM github_events WHERE connection=? AND notification_id=? "
                "AND state='pending' AND message IS NULL",
                (self.connection, str(notification["id"])),
            ):
                since = min(since, json.loads(older[0]).get("since", updated - 1))
            notification["since"] = since
            # Coalesce discovery windows, never individual request identities.
            db.execute(
                "UPDATE github_events SET state='ignored',detail='Superseded before capture' "
                "WHERE connection=? AND notification_id=? AND updated<? "
                "AND state='pending' AND message IS NULL",
                (self.connection, str(notification["id"]), updated),
            )
            db.execute(
                "INSERT OR IGNORE INTO github_events "
                "VALUES(?,?,?,?,?,?,NULL,'pending','pending','')",
                (
                    self.connection,
                    event_key,
                    str(notification["id"]),
                    updated,
                    updated + delay,
                    encode(notification),
                ),
            )

    def ready(self, now: float) -> list[dict[str, Any]]:
        return self.store.rows(
            "SELECT * FROM github_events WHERE connection=? AND next_check<=? "
            f"AND (state='pending' OR (state='captured' AND read_status='pending' AND {_SETTLED})) "
            "ORDER BY updated,event_key LIMIT 50",
            (self.connection, now),
        )

    def read_ready(self, key: str) -> bool:
        return bool(
            self.store.rows(
                f"SELECT 1 FROM github_events WHERE connection=? AND event_key=? AND {_SETTLED}",
                (self.connection, key),
            )
        )

    def prepared(self, key: str, requests: list[tuple[str, dict | None]]) -> None:
        """Freeze all request snapshots and their notification association atomically."""
        with self.store.transaction() as db:
            for request_key, message in requests:
                db.execute(
                    "INSERT OR IGNORE INTO github_requests VALUES(?,?,?)",
                    (self.connection, request_key, encode(message) if message else None),
                )
                db.execute(
                    "INSERT OR IGNORE INTO github_event_requests VALUES(?,?,?)",
                    (self.connection, key, request_key),
                )
            db.execute(
                "UPDATE github_events SET message='{}' WHERE connection=? AND event_key=?",
                (self.connection, key),
            )

    def messages(self, key: str, *, through: bool = False) -> list[dict[str, Any]]:
        scope = (
            "e.notification_id=selected.notification_id AND e.updated<=selected.updated"
            if through
            else "e.event_key=selected.event_key"
        )
        return self.store.rows(
            "SELECT DISTINCT r.rowid,r.message FROM github_event_requests er "
            "JOIN github_requests r "
            "USING(connection,request_key) JOIN github_events e "
            "ON e.connection=er.connection AND e.event_key=er.event_key "
            "JOIN github_events selected ON selected.connection=e.connection "
            f"WHERE selected.connection=? AND selected.event_key=? AND {scope} "
            "AND r.message IS NOT NULL ORDER BY r.rowid",
            (self.connection, key),
        )

    def finish(self, key: str, state: str, read_status: str, detail: str = "") -> None:
        with self.store.transaction() as db:
            db.execute(
                "UPDATE github_events SET state=?,read_status=?,detail=? "
                "WHERE connection=? AND event_key=?",
                (state, read_status, detail, self.connection, key),
            )

    def defer(self, key: str, until: float, detail: str) -> None:
        with self.store.transaction() as db:
            db.execute(
                "UPDATE github_events SET next_check=?,detail=? WHERE connection=? AND event_key=?",
                (until, detail, self.connection, key),
            )
