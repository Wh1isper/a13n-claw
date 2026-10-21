"""Durable GitHub polling cursor and delayed, immutable ingress snapshots."""

from __future__ import annotations

from typing import Any

from a13n_claw.business import BusinessStore, encode


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
            # A fresh update supersedes only delayed work that has never reached Inbox.
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
            "AND (state='pending' OR (state='captured' AND read_status='pending')) "
            "ORDER BY updated,event_key LIMIT 50",
            (self.connection, now),
        )

    def prepared(self, key: str, message: dict) -> None:
        with self.store.transaction() as db:
            db.execute(
                "UPDATE github_events SET message=? WHERE connection=? AND event_key=?",
                (encode(message), self.connection, key),
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
