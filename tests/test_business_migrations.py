"""Product schema parity and populated restart; Host persistence remains a separate owner."""

import sqlite3

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import URL, create_engine, inspect

from a13n_claw.business import BusinessStore
from a13n_claw.business_schema import metadata
from a13n_claw.database.migration import DatabaseMigrator
from a13n_claw.errors import ClawError
from a13n_claw.settings import ClawSettings


def test_generated_business_baseline_matches_metadata(tmp_path):
    path = tmp_path / "business.sqlite3"
    DatabaseMigrator(path).upgrade()
    engine = create_engine(URL.create("sqlite", database=str(path)))
    try:
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), metadata) == []
            assert set(inspect(connection).get_table_names()) == set(metadata.tables) | {
                "alembic_version"
            }
    finally:
        engine.dispose()


def test_populated_repeat_startup_and_transaction_rollback(tmp_path):
    path = tmp_path / "business.sqlite3"
    store = BusinessStore(path, ClawSettings())
    item = store.accept(
        connection="platform",
        event_id="event",
        fingerprint="hash",
        channel_id="channel",
        mode="one_thread",
        project_id="project-claw-main",
        body={"test": "saved"},
    )
    with pytest.raises(RuntimeError), store.transaction() as connection:
        connection.execute("UPDATE inbox SET body='discarded' WHERE id=?", (item["id"],))
        raise RuntimeError("transaction interrupted")
    reopened = BusinessStore(path, ClawSettings())
    reopened.restart()
    assert reopened.item(item["id"])["body"] == item["body"]
    assert len(reopened.actionable()) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_unknown_revision_preserves_database(tmp_path):
    path = tmp_path / "business.sqlite3"
    DatabaseMigrator(path).upgrade()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE alembic_version SET version_num='future-revision'")
    with pytest.raises(ClawError, match="unknown"):
        DatabaseMigrator(path).upgrade()
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "future-revision"
        )
