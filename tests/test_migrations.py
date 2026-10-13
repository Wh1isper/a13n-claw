"""Package history, atomic SQLite upgrades, and schema ownership."""

import shutil
import sqlite3
from contextlib import closing

import pytest
import sqlalchemy as sa
from a13n_harness import HarnessState
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from a13n_claw.database import migration, schema
from a13n_claw.database.migration import DatabaseMigrator
from a13n_claw.domain import ClawError, InputRequest
from a13n_claw.storage import Store


def snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        return list(db.iterdump())


def test_clean_upgrade_has_one_head_and_matches_metadata(tmp_path):
    path = tmp_path / "claw.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator.upgrade()
    expected = migrator.history().get_current_head()
    with migrator.connection() as connection:
        assert MigrationContext.configure(connection).get_current_heads() == (expected,)
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        assert compare_metadata(context, schema.metadata) == []
        assert set(sa.inspect(connection).get_table_names()) == {
            *schema.metadata.tables,
            "alembic_version",
        }
        assert len(schema.metadata.tables) == 25
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
        assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
    before = snapshot(path)
    migrator.upgrade()
    assert snapshot(path) == before


@pytest.mark.parametrize("version", [0, 1, 2, 3, 4, 5])
def test_prebaseline_database_is_rejected_without_reset(tmp_path, version):
    path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE retained (content TEXT NOT NULL)")
        db.execute("INSERT INTO retained VALUES ('keep this')")
        db.execute(f"PRAGMA user_version={version}")
        db.commit()
    before = snapshot(path)
    with pytest.raises(ClawError, match="fresh development data root"):
        Store(path)
    assert snapshot(path) == before


def test_reset_operator_rejects_old_schema_before_replacing_token(tmp_path):
    from a13n_claw.instance import reset_operator

    path = tmp_path / "claw.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE retained (content TEXT)")
        db.commit()
    token_path = tmp_path / "operator.token"
    token_path.write_text("original-token\n")
    before = snapshot(path)
    with pytest.raises(ClawError, match="fresh development data root"):
        reset_operator(tmp_path)
    assert token_path.read_text() == "original-token\n"
    assert snapshot(path) == before


def test_future_revision_is_rejected_without_stamping(tmp_path):
    path = tmp_path / "claw.sqlite3"
    Store(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("UPDATE alembic_version SET version_num='future_revision'")
        db.commit()
    before = snapshot(path)
    with pytest.raises(ClawError, match="unknown to this Claw version"):
        Store(path)
    assert snapshot(path) == before


@pytest.mark.parametrize("empty", [True, False])
@pytest.mark.parametrize("failure", ["exception", "foreign_key"])
def test_failed_upgrade_rolls_back_schema_data_and_revision(tmp_path, monkeypatch, empty, failure):
    path = tmp_path / "claw.sqlite3"
    migrator = DatabaseMigrator(path)
    if not empty:
        Store(path).bootstrap("operator-token-hash")
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
        connection.exec_driver_sql(
            "INSERT INTO threads(id,title,profile_id,created_at,parent_thread_id) "
            "VALUES ('dangling','bad','default','now','missing')"
        )

    with monkeypatch.context() as patch:
        patch.setattr(command, "upgrade", fail)
        error = RuntimeError if failure == "exception" else ClawError
        with pytest.raises(error, match="injected migration failure|invalid foreign keys"):
            migrator.upgrade()
    assert snapshot(path) == before
    Store(path).bootstrap("operator-token-hash")


def test_generated_batch_upgrade_preserves_work_and_foreign_keys(
    runtime_store, tmp_path, monkeypatch
):
    store = runtime_store
    thread = store.create_thread("operator", "Retained", "default", request_id="create")
    receipt = store.admit(
        "operator", thread.id, InputRequest(request_id="input", text="Retained input"), "/shared"
    )
    store.claim(receipt.run_id, "owner", "/shared")
    store.start(receipt.run_id, "owner")
    store.publish(
        receipt.run_id,
        "owner",
        HarnessState.new(thread_id=thread.id),
        incorporated=(receipt.id,),
        status="completed",
    )
    before_thread = store.thread("operator", thread.id)
    before_run = store.run("operator", receipt.run_id)
    before_inputs = store.inputs("operator", thread.id)
    scripts = tmp_path / "migrations"
    shutil.copytree(
        migration.MIGRATIONS_PATH, scripts, ignore=shutil.ignore_patterns("__pycache__")
    )
    metadata = sa.MetaData(naming_convention=schema.metadata.naming_convention)
    for table in schema.metadata.tables.values():
        table.to_metadata(metadata)
    # A type change forces a real SQLite table copy while checkpoint and child
    # references still target threads. Generate, rather than handwrite, the delta.
    metadata.tables["threads"].c.title.type = sa.String(4096)
    monkeypatch.setattr(schema, "metadata", metadata)
    monkeypatch.setattr(migration, "MIGRATIONS_PATH", scripts)
    DatabaseMigrator(tmp_path / "generation.sqlite3").revision("widen test title")
    migrator = DatabaseMigrator(store.path)
    assert len(list(migrator.history().walk_revisions())) == 2
    migrator.upgrade()
    reopened = Store(store.path)
    assert reopened.thread("operator", thread.id) == before_thread
    assert reopened.run("operator", receipt.run_id) == before_run
    assert reopened.inputs("operator", thread.id) == before_inputs
    assert (
        reopened.create_thread("operator", "Retained", "default", request_id="create").id
        == thread.id
    )
    with migrator.connection() as connection:
        assert compare_metadata(MigrationContext.configure(connection), metadata) == []
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        config = migrator.config()
        config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="forward-only"):
            command.downgrade(config, "base")
    assert reopened.thread("operator", thread.id) == before_thread


def test_package_graph_rejects_multiple_heads(tmp_path, monkeypatch):
    scripts = tmp_path / "migrations"
    shutil.copytree(
        migration.MIGRATIONS_PATH, scripts, ignore=shutil.ignore_patterns("__pycache__")
    )
    config = DatabaseMigrator.config()
    config.set_main_option("script_location", str(scripts))
    # Alembic creates both revisions from its packaged template; no test-owned DDL.
    baseline = DatabaseMigrator(tmp_path / "unused.sqlite3").history().get_current_head()
    command.revision(config, message="first branch", head=baseline)
    command.revision(config, message="second branch", head=baseline, splice=True)
    monkeypatch.setattr(migration, "MIGRATIONS_PATH", scripts)
    path = tmp_path / "claw.sqlite3"
    with pytest.raises(ClawError, match="one linear history"):
        Store(path)
    assert not path.exists()


@pytest.mark.parametrize(
    "table,column",
    [
        ("threads", "checkpoint_id"),
        ("threads", "parent_run_id"),
        ("runs", "checkpoint_id"),
        ("delegations", "delivery_attention_id"),
        ("inbox", "channel_id"),
    ],
)
def test_baseline_has_relationship_foreign_keys(tmp_path, table, column):
    store = Store(tmp_path / "claw.sqlite3")
    with store._connection() as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        keys = db.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        assert column in {row[3] for row in keys}


def test_selected_checkpoint_cannot_reference_missing_value(runtime_store):
    store = runtime_store
    thread = store.create_thread("operator", "Retained", "default")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"), store._transaction() as db:
        db.execute("UPDATE threads SET checkpoint_id='missing' WHERE id=?", (thread.id,))
    assert store.thread("operator", thread.id).checkpoint_id is None
