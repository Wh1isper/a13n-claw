"""Atomic completion of rejected native continuations and their IM replies."""

import sqlite3

import pytest

from a13n_claw.database.store import BusinessStore
from tests.helpers import settings


@pytest.mark.parametrize("disposition", ["pending", "handled"])
def test_rejected_continuation_completion_rolls_back_as_one_unit(tmp_path, disposition):
    store = BusinessStore(tmp_path / "business.sqlite3", settings())

    def accept(event):
        return store.accept(
            connection="lark",
            event_id=event,
            fingerprint=event,
            channel_id="room",
            mode="one_thread",
            project_id="project-claw-main",
            body={"message": {"chat_id": "chat", "message_id": event}},
        )

    target, control = accept("target"), accept("control")
    if disposition == "handled":
        target = store.settle(target["id"], target["version"], "handled", "Already settled")
    attempt, followup = store.begin_continuation("thread-main", control["id"], target["id"])
    before = {table: store.rows(f"SELECT * FROM {table}") for table in ("inbox", "attempts")}
    with store.transaction() as db:
        db.execute(
            "CREATE TRIGGER fail_restore BEFORE UPDATE ON inbox "
            "WHEN OLD.blocked IS NOT NULL AND NEW.blocked IS NULL "
            "AND OLD.event_id='target' BEGIN SELECT RAISE(ABORT, 'restore failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="restore failed"):
        store.complete_control(
            control["id"],
            "Invalid decision",
            attempt,
            rejected_followup=(followup, disposition),
        )
    assert store.rows("SELECT * FROM deliveries") == []
    for table, rows in before.items():
        assert store.rows(f"SELECT * FROM {table}") == rows
    with store.transaction() as db:
        db.execute("DROP TRIGGER fail_restore")
    store.complete_control(
        control["id"],
        "Invalid decision",
        attempt,
        rejected_followup=(followup, disposition),
    )
    restored = store.item(target["id"])
    assert restored["disposition"] == disposition
    assert restored["blocked"] is None
    assert restored["version"] > target["version"]
    assert store.item(control["id"])["disposition"] == "handled"
    assert {row["id"]: row["status"] for row in store.rows("SELECT * FROM attempts")} == {
        attempt: "observed",
        followup: "rejected",
    }
    assert len(store.rows("SELECT * FROM deliveries")) == 1
