"""Bounded business-content retention and content-free operational diagnostics."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from a13n_claw.async_utils import database
from a13n_claw.database.store import BusinessStore
from a13n_claw.encoding import encode, identifier

if TYPE_CHECKING:
    from a13n_claw.runtime.service import ClawService


_ELIGIBLE = """
    body IS NOT NULL AND created<? AND disposition IN ('handled','ignored')
    AND blocked IS NULL AND dependency IS NULL
    AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.item_id=inbox.id AND d.status!='sent')
    AND NOT EXISTS (SELECT 1 FROM attempt_items ai JOIN attempts a ON a.id=ai.attempt_id
                    WHERE ai.item_id=inbox.id AND a.status IN ('calling','accepted','unknown'))
    AND NOT EXISTS (SELECT 1 FROM attempt_items ai JOIN attempt_items peer
                    ON peer.attempt_id=ai.attempt_id JOIN inbox other ON other.id=peer.item_id
                    WHERE ai.item_id=inbox.id AND other.disposition IN ('pending','deferred'))
    AND NOT EXISTS (SELECT 1 FROM github_events g WHERE g.connection=inbox.connection
                    AND g.event_key=inbox.event_id AND
                    (g.state='pending' OR g.read_status IN ('pending','unknown')))
"""


def retention_candidates(store: BusinessStore, before: float) -> list[dict[str, Any]]:
    return store.rows(
        f"SELECT id,thread_id FROM inbox WHERE {_ELIGIBLE} ORDER BY created,id LIMIT 100",
        (before,),
    )


def prune(store: BusinessStore, before: float, ids: list[str]) -> int:
    with store.transaction() as db:
        count = 0
        for item_id in ids:
            row = db.execute(
                f"SELECT * FROM inbox WHERE id=? AND {_ELIGIBLE}", (item_id, before)
            ).fetchone()
            if row is None:
                continue
            db.execute("UPDATE inbox SET body=NULL,version=version+1 WHERE id=?", (item_id,))
            # The polling identity/cursor remains a tombstone; completed payloads are redundant.
            db.execute(
                "UPDATE github_events SET message=NULL,notification='{}' "
                "WHERE connection=? AND event_key=?",
                (row["connection"], row["event_id"]),
            )
            count += 1
        if count:
            db.execute(
                "INSERT INTO resolutions VALUES(?,?,?,?,?)",
                (
                    identifier("resolution"),
                    "retention",
                    "prune_content",
                    encode({"before": before, "items": ids, "count": count}),
                    time.time(),
                ),
            )
        return count


async def retain(service: ClawService, *, older_than_days: int, apply: bool) -> dict[str, Any]:
    if older_than_days < 1:
        raise ValueError("Retention age must be at least one day")
    before = time.time() - older_than_days * 86400
    async with service.lock, service.intake_lock:
        candidates = await database(retention_candidates, service.store, before)
        ids = []
        active: dict[str, bool] = {}
        for item in candidates:
            thread_id = item["thread_id"]
            if thread_id:
                if thread_id not in active:
                    active[thread_id] = (
                        await service.host.active_root_operation(thread_id) is not None
                    )
                if active[thread_id]:
                    continue
            ids.append(item["id"])
        count = await database(prune, service.store, before, ids) if apply else 0
        return {
            "candidates": ids,
            "pruned": count,
            "applied": apply,
            "batch_limit": 100,
            "retained": "Deduplication, delivery bodies, dispatch evidence and Host state",
        }


def business_diagnostics(store: BusinessStore) -> dict[str, Any]:
    with store.transaction() as db:
        counts = {}
        for table, field in (
            ("inbox", "disposition"),
            ("attempts", "status"),
            ("deliveries", "status"),
            ("occurrences", "status"),
            ("targets", "status"),
        ):
            counts[table] = dict(
                db.execute(f"SELECT {field},count(*) FROM {table} GROUP BY {field}")
            )
        counts["inbox_pruned_or_filtered"] = db.execute(
            "SELECT count(*) FROM inbox WHERE body IS NULL"
        ).fetchone()[0]
        return counts


async def diagnostics(service: ClawService) -> dict[str, Any]:
    projects = {project.project_id for project in await service.host.projects()}
    required = (
        {service.settings.main_project_id} if service.settings.mode == "one_thread" else set()
    )
    required.update(b.project_id for b in service.settings.channels.values() if b.project_id)
    return {
        "business": await database(business_diagnostics, service.store),
        "product_configuration": {
            "accepted_digest": service.configuration.accepted_digest,
            "reload_error": service.configuration.error,
        },
        "missing_projects": sorted(required - projects),
        "reconciliation_error": service.last_error,
        "scheduler_error": service.scheduler.error,
        "connection_errors": dict(service.connection_errors),
        "connectivity": "not_probed",
        "backup": "Stop the Instance and use the offline backup command",
        "restore": "Restoration does not undo external effects; reconcile before starting intake",
    }
