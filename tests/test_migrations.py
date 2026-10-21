"""Atomic SQLite migration and generated batch-upgrade safety for business state."""

import shutil
import sqlite3
from contextlib import closing

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from a13n_claw import business_schema as schema
from a13n_claw.business import BusinessStore
from a13n_claw.database import migration
from a13n_claw.database.migration import DatabaseMigrator
from a13n_claw.errors import ClawError
from a13n_claw.settings import ClawSettings


def snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        return list(db.iterdump())


@pytest.mark.parametrize("version", [0, 1, 5])
def test_prebaseline_database_is_rejected_without_reset(tmp_path, version):
    path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE retained (content TEXT NOT NULL)")
        db.execute("INSERT INTO retained VALUES ('keep this')")
        db.execute(f"PRAGMA user_version={version}")
        db.commit()
    before = snapshot(path)
    with pytest.raises(ClawError, match="fresh development data root"):
        DatabaseMigrator(path).upgrade()
    assert snapshot(path) == before


@pytest.mark.parametrize("empty", [True, False])
@pytest.mark.parametrize("failure", ["exception", "foreign_key"])
def test_failed_upgrade_rolls_back_schema_data_and_revision(tmp_path, monkeypatch, empty, failure):
    path = tmp_path / "business.sqlite3"
    migrator = DatabaseMigrator(path)
    if not empty:
        BusinessStore(path, ClawSettings())
    before = snapshot(path)
    upgrade = command.upgrade

    def fail(config, target):
        upgrade(config, target)
        connection = config.attributes["connection"]
        connection.exec_driver_sql("CREATE TABLE incomplete (value TEXT)")
        connection.exec_driver_sql("INSERT INTO incomplete VALUES ('not committed')")
        connection.exec_driver_sql("UPDATE alembic_version SET version_num='not_committed'")
        if failure == "exception":
            raise RuntimeError("injected migration failure")
        connection.exec_driver_sql("INSERT INTO attempt_items VALUES ('missing','missing')")

    with monkeypatch.context() as patch:
        patch.setattr(command, "upgrade", fail)
        error = RuntimeError if failure == "exception" else ClawError
        with pytest.raises(error, match="injected migration failure|invalid foreign keys"):
            migrator.upgrade()
    assert snapshot(path) == before
    BusinessStore(path, ClawSettings())


def test_generated_batch_upgrade_preserves_work_and_foreign_keys(tmp_path, monkeypatch):
    store = BusinessStore(tmp_path / "business.sqlite3", ClawSettings())
    item = store.accept(
        connection="lark",
        event_id="event",
        fingerprint="hash",
        channel_id="room",
        mode="one_thread",
        project_id="project-claw-main",
        body={"text": "saved"},
    )
    attempt = store.begin_attempt("thread-id", "submit", [item["id"]])
    before_item = store.item(item["id"])
    scripts = tmp_path / "migrations"
    shutil.copytree(
        migration.MIGRATIONS_PATH, scripts, ignore=shutil.ignore_patterns("__pycache__")
    )
    metadata = sa.MetaData()
    for table in schema.metadata.tables.values():
        table.to_metadata(metadata)
    metadata.tables["inbox"].c.reason.type = sa.String(4096)
    monkeypatch.setattr(schema, "metadata", metadata)
    monkeypatch.setattr(migration, "MIGRATIONS_PATH", scripts)
    previous_count = len(list(DatabaseMigrator(store.path).history().walk_revisions()))
    DatabaseMigrator(tmp_path / "generation.sqlite3").revision("widen test reason")
    migrator = DatabaseMigrator(store.path)
    assert len(list(migrator.history().walk_revisions())) == previous_count + 1
    migrator.upgrade()
    reopened = BusinessStore(store.path, ClawSettings())
    assert reopened.item(item["id"]) == before_item
    assert reopened.rows("SELECT * FROM attempt_items") == [
        {"attempt_id": attempt, "item_id": item["id"]}
    ]
    with migrator.connection() as connection:
        assert compare_metadata(MigrationContext.configure(connection), metadata) == []
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        config = migrator.config()
        config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="forward-only"):
            command.downgrade(config, "base")
    assert reopened.item(item["id"]) == before_item


def test_package_graph_rejects_multiple_heads(tmp_path, monkeypatch):
    scripts = tmp_path / "migrations"
    shutil.copytree(
        migration.MIGRATIONS_PATH, scripts, ignore=shutil.ignore_patterns("__pycache__")
    )
    config = DatabaseMigrator.config()
    config.set_main_option("script_location", str(scripts))
    baseline = DatabaseMigrator(tmp_path / "unused.sqlite3").history().get_current_head()
    command.revision(config, message="first branch", head=baseline)
    command.revision(config, message="second branch", head=baseline, splice=True)
    monkeypatch.setattr(migration, "MIGRATIONS_PATH", scripts)
    path = tmp_path / "business.sqlite3"
    with pytest.raises(ClawError, match="one linear history"):
        DatabaseMigrator(path).upgrade()
    assert not path.exists()
