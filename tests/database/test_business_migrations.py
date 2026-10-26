"""Product schema parity and populated restart; Host persistence remains a separate owner."""

import sqlite3

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import URL, create_engine, inspect

from a13n_claw.configuration.models import ClawSettings
from a13n_claw.database.migration import DatabaseMigrator
from a13n_claw.database.schema import metadata
from a13n_claw.database.store import BusinessStore
from a13n_claw.errors import ClawError


def test_generated_business_baseline_matches_metadata(tmp_path):
    path = tmp_path / "business.sqlite3"
    migrator = DatabaseMigrator(path)
    revisions = list(migrator.history().walk_revisions())
    assert len(revisions) == 2
    assert revisions[-1].down_revision is None
    migrator.upgrade()
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


@pytest.mark.parametrize(
    "revision",
    ["future-revision", "ae0ac20d04ba", "749e0a64674a", "df8cecbafec6", "3fa99678a97b"],
)
def test_unknown_or_retired_revision_preserves_database(tmp_path, revision):
    path = tmp_path / "business.sqlite3"
    BusinessStore(path, ClawSettings())
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE alembic_version SET version_num=?", (revision,))
    with sqlite3.connect(path) as connection:
        before = list(connection.iterdump())
    with pytest.raises(ClawError, match="unknown") as error:
        DatabaseMigrator(path).upgrade()
    assert error.value.code == "schema_incompatible"
    with sqlite3.connect(path) as connection:
        assert list(connection.iterdump()) == before


def test_github_request_upgrade_preserves_prepared_snapshots(tmp_path):
    from alembic import command

    from a13n_claw.adapters.github_store import GitHubStore
    from a13n_claw.messaging.transport import InboundMessage

    path = tmp_path / "business.sqlite3"
    migrator = DatabaseMigrator(path)
    with migrator.connection() as connection:
        config = migrator.config()
        config.attributes["connection"] = connection
        command.upgrade(config, "19c83d6d1f67")
        message = InboundMessage(
            platform="github",
            connection="github",
            event_id="legacy",
            message_id="42",
            chat_id="owner/repo",
            sender_id="alice",
            account_id="bot",
            text="Saved request",
        ).model_dump_json()
        connection.exec_driver_sql(
            "INSERT INTO github_events VALUES('github','legacy','1',1000,1000,'{}',?,"
            "'pending','pending','')",
            (message,),
        )
        connection.commit()
    store = BusinessStore(path, ClawSettings())
    requests = GitHubStore(store, "github", "bot").messages("legacy")
    assert [row["message"] for row in requests] == [message]
    assert store.rows("SELECT * FROM github_events")[0]["state"] == "pending"
    DatabaseMigrator(path).upgrade()
    assert GitHubStore(store, "github", "bot").messages("legacy") == requests
