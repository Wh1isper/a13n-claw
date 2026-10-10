"""Host-owned ingress, delivery intent and generic HTTP message transport.

A configured integration client submits qualified events. The HTTP receiver must
persist its result before returning 2xx. No agent result is implicitly broadcast.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import TYPE_CHECKING, Any

import httpx

from a13n_claw.attention import ChannelPolicy, DeliveryRequest, DeliveryResolution, InboundMessage
from a13n_claw.domain import ClawError, InputRequest, canonical, digest, new_id, now

if TYPE_CHECKING:
    from a13n_claw.storage import Store


class Messaging:
    def __init__(self, store: Store):
        self.store = store
        self.coordination = store.coordination

    @staticmethod
    def _channel(db: sqlite3.Connection, channel_id: str) -> tuple[sqlite3.Row, ChannelPolicy]:
        row = db.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
        if row is None:
            raise ClawError("channel_missing", "Channel binding not found", 404)
        return row, ChannelPolicy.model_validate_json(row["policy"])

    def save_channel(
        self, actor_id: str, channel_id: str, policy: ChannelPolicy, expected_version: int
    ) -> dict[str, Any]:
        with self.store._transaction() as db:
            self.store._principal(db, actor_id).require("admin")
            state = self.coordination.state(db)
            if state["mode"] == "one_thread" and not policy.shared_context_acknowledged:
                raise ClawError(
                    "shared_context", "Acknowledge that Main shares context across Channels", 422
                )
            self.store._principal(db, policy.ingress_actor_id)
            executor = self.store._principal(db, policy.execution_actor_id)
            executor.require("submit")
            prior = db.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
            qualified = canonical([policy.platform, policy.account, policy.channel])
            if prior:
                if prior["version"] != expected_version:
                    raise ClawError("version_conflict", "Channel policy changed; refresh first")
                if not prior["active"] or prior["qualified_key"] != qualified:
                    raise ClawError(
                        "channel_identity", "Create a new binding instead of retargeting old state"
                    )
                db.execute(
                    "UPDATE channels SET policy=?,version=version+1 WHERE id=?",
                    (policy.model_dump_json(), channel_id),
                )
            else:
                if expected_version != 0:
                    raise ClawError("version_conflict", "Channel does not exist")
                if db.execute(
                    "SELECT 1 FROM channels WHERE qualified_key=? AND active=1", (qualified,)
                ).fetchone():
                    raise ClawError(
                        "channel_duplicate", "This qualified conversation already has a binding"
                    )
                db.execute(
                    "INSERT INTO channels(id,version,policy,qualified_key) VALUES (?,1,?,?)",
                    (channel_id, policy.model_dump_json(), qualified),
                )
            self.store._audit(db, "channel_policy", channel_id, {"actor": actor_id})
            return {
                "id": channel_id,
                "version": expected_version + 1,
                "policy": policy.model_dump(mode="json"),
            }

    def channels(self, actor_id: str) -> list[dict[str, Any]]:
        with self.store._transaction() as db:
            self.store._principal(db, actor_id).require("admin")
            return [
                dict(row)
                | {
                    "policy": ChannelPolicy.model_validate_json(row["policy"]).model_dump(
                        mode="json"
                    )
                }
                for row in db.execute("SELECT * FROM channels ORDER BY id")
            ]

    def destinations(self, run_id: str, owner: str) -> list[dict[str, Any]]:
        with self.store._transaction() as db:
            self.coordination._main_bound(db, run_id, owner)
            return [
                {
                    "id": row["id"],
                    "platform": policy.platform,
                    "account": policy.account,
                    "channel": policy.channel,
                }
                for row in db.execute("SELECT * FROM channels WHERE active=1")
                if (policy := ChannelPolicy.model_validate_json(row["policy"])).enabled
                and policy.outbound
            ]

    def receive(
        self, actor_id: str, channel_id: str, message: InboundMessage, workspace: str
    ) -> dict[str, Any]:
        with self.store._transaction() as db:
            actor = self.store._principal(db, actor_id)
            row, policy = self._channel(db, channel_id)
            if not actor.admin and actor.id != policy.ingress_actor_id:
                raise ClawError(
                    "ingress_forbidden", "This client does not own the integration", 403
                )
            if not row["active"] or not policy.enabled or not policy.inbound:
                raise ClawError("channel_disabled", "Channel ingress is disabled", 403)
            fingerprint = digest(message.model_dump(mode="json"))
            prior = db.execute(
                "SELECT * FROM ingress WHERE channel_id=? AND event_id=?",
                (channel_id, message.event_id),
            ).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ClawError("request_conflict", "External event identity was reused")
                import json

                return json.loads(prior["receipt"])
            if message.sender not in policy.allowed_senders or (
                policy.group and policy.require_addressed and not message.addressed
            ):
                ignored = {"event_id": message.event_id, "disposition": "ignored"}
                db.execute(
                    "INSERT INTO ingress VALUES (?,?,?,?)",
                    (channel_id, message.event_id, fingerprint, canonical(ignored)),
                )
                return ignored
            state = self.coordination.state(db)
            if state["mode"] == "one_thread":
                if not state["main_id"]:
                    raise ClawError("main_unconfigured", "Configure Main before accepting messages")
                if not policy.shared_context_acknowledged:
                    raise ClawError(
                        "shared_context", "This binding does not authorize shared Main context", 403
                    )
                thread_id = state["main_id"]
            else:
                thread_id = row["thread_id"]
                if thread_id is None:
                    executor = self.store._principal(db, policy.execution_actor_id)
                    executor.require("create")
                    if not policy.profile_id:
                        raise ClawError(
                            "profile_required", "Per-Channel bindings require a Profile"
                        )
                    self.store._capture(db, executor, policy.profile_id, workspace)
                    thread_id = new_id("thread")
                    db.execute(
                        "INSERT INTO threads(id,title,profile_id,created_at) VALUES (?,?,?,?)",
                        (thread_id, policy.channel, policy.profile_id, now()),
                    )
                    self.store._grant_created_thread(db, executor, thread_id)
                    db.execute(
                        "UPDATE channels SET thread_id=? WHERE id=?", (thread_id, channel_id)
                    )
            self.store._principal(db, policy.execution_actor_id).require("submit", thread_id)
            for asset_id in message.attachment_ids:
                asset = self.store._asset(db, asset_id)
                if asset.thread_id != thread_id:
                    raise ClawError(
                        "asset_scope", "Attachments must be retained on the destination Thread", 403
                    )
                db.execute(
                    "INSERT OR IGNORE INTO external_assets VALUES (?,?,?,?,?)",
                    (asset_id, channel_id, message.event_id, message.sender, message.addressed),
                )
            payload = {
                "platform": policy.platform,
                "account": policy.account,
                "channel": policy.channel,
                **message.model_dump(mode="json"),
            }
            if state["mode"] == "one_thread":
                item = self.coordination._record(
                    db,
                    thread_id,
                    f"external:{channel_id}:{message.event_id}",
                    "external",
                    payload,
                    channel_id=channel_id,
                )
                receipt = {"inbox_id": item["id"], "thread_id": thread_id}
            else:
                accepted = self.store._admit(
                    db,
                    policy.execution_actor_id,
                    thread_id,
                    InputRequest(
                        request_id=new_id("external"),
                        text=canonical(payload),
                        source=f"channel:{channel_id}",
                        attachment_ids=message.attachment_ids,
                    ),
                    workspace,
                )
                receipt = {
                    "input_id": accepted.id,
                    "run_id": accepted.run_id,
                    "thread_id": thread_id,
                }
            db.execute(
                "INSERT INTO ingress VALUES (?,?,?,?)",
                (channel_id, message.event_id, fingerprint, canonical(receipt)),
            )
            return receipt

    def send(self, run_id: str, owner: str, request: DeliveryRequest) -> dict[str, Any]:
        with self.store._transaction() as db:
            run = self.coordination._main_bound(db, run_id, owner)
            self.store._principal(db, run.actor_id).require("submit", run.thread_id)
            row, policy = self._channel(db, request.channel_id)
            if not row["active"] or not policy.enabled or not policy.outbound:
                raise ClawError(
                    "destination_forbidden", "Destination does not permit publication", 403
                )
            fingerprint = digest(request.model_dump(mode="json"))
            prior = db.execute(
                "SELECT * FROM deliveries WHERE thread_id=? AND request_id=?",
                (run.thread_id, request.request_id),
            ).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ClawError(
                        "request_conflict", "Send identity was reused for different content"
                    )
                return self._delivery(prior)
            delivery_id = new_id("delivery")
            db.execute(
                "INSERT INTO deliveries(id,thread_id,run_id,actor_id,request_id,fingerprint,"
                "channel_id,channel_version,text,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    delivery_id,
                    run.thread_id,
                    run.id,
                    run.actor_id,
                    request.request_id,
                    fingerprint,
                    request.channel_id,
                    row["version"],
                    request.text,
                    now(),
                    now(),
                ),
            )
            return self._delivery(
                db.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            )

    @staticmethod
    def _delivery(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value.pop("fingerprint")
        return value

    def deliveries(self, actor_id: str) -> list[dict[str, Any]]:
        with self.store._transaction() as db:
            actor = self.store._principal(db, actor_id)
            actor.require("read")
            return [
                self._delivery(row)
                for row in db.execute("SELECT * FROM deliveries ORDER BY created_at DESC LIMIT 200")
                if actor.admin or row["thread_id"] in actor.thread_ids
            ]

    def bound_deliveries(self, run_id: str, owner: str) -> list[dict[str, Any]]:
        with self.store._transaction() as db:
            run = self.coordination._main_bound(db, run_id, owner)
        return [row for row in self.deliveries(run.actor_id) if row["thread_id"] == run.thread_id]

    def claim_delivery(self) -> tuple[dict[str, Any], ChannelPolicy] | None:
        with self.store._transaction() as db:
            rows = db.execute(
                "SELECT * FROM deliveries WHERE status='pending' ORDER BY created_at LIMIT 50"
            ).fetchall()
            for row in rows:
                try:
                    state = self.coordination.state(db)
                    if state["mode"] != "one_thread" or state["main_id"] != row["thread_id"]:
                        raise ClawError("delivery_inactive", "Source mode is inactive")
                    run = self.store._run(db, row["run_id"])
                    self.store._authorize_run(db, run)
                    channel, policy = self._channel(db, row["channel_id"])
                    if not channel["active"] or not policy.enabled or not policy.outbound:
                        raise ClawError("destination_revoked", "Destination was revoked")
                    if channel["version"] != row["channel_version"]:
                        raise ClawError(
                            "destination_changed", "Review delivery against the changed policy"
                        )
                except ClawError as exc:
                    db.execute(
                        "UPDATE deliveries SET status='blocked',error=?,version=version+1,"
                        "updated_at=? WHERE id=?",
                        (exc.code, now(), row["id"]),
                    )
                    continue
                db.execute(
                    "UPDATE deliveries SET status='sending',version=version+1,"
                    "updated_at=? WHERE id=?",
                    (now(), row["id"]),
                )
                return self._delivery(row), policy
            return None

    def finish_delivery(self, delivery_id: str, *, sent: bool, receipt: str | None = None) -> None:
        with self.store._transaction() as db:
            db.execute(
                "UPDATE deliveries SET status=?,transport_receipt=?,error=?,version=version+1,"
                "updated_at=? "
                "WHERE id=? AND status='sending'",
                (
                    "sent" if sent else "unknown",
                    receipt,
                    None if sent else "transport_outcome_unknown",
                    now(),
                    delivery_id,
                ),
            )

    def resolve_delivery(
        self, actor_id: str, delivery_id: str, change: DeliveryResolution
    ) -> dict[str, Any]:
        with self.store._transaction() as db:
            self.store._principal(db, actor_id).require("admin")
            row = db.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            if row is None:
                raise ClawError("delivery_missing", "Delivery not found", 404)
            if row["version"] != change.expected_version:
                raise ClawError("version_conflict", "Delivery changed; refresh first")
            if row["status"] not in {"unknown", "blocked", "not_sent"}:
                raise ClawError(
                    "delivery_not_reconcilable",
                    "Only stopped unresolved deliveries can be reconciled",
                )
            channel, _ = self._channel(db, row["channel_id"])
            db.execute(
                "UPDATE deliveries SET status=?,channel_version=?,error=NULL,version=version+1,"
                "updated_at=? WHERE id=?",
                (
                    "pending" if change.retry else change.outcome,
                    channel["version"],
                    now(),
                    delivery_id,
                ),
            )
            self.store._audit(
                db, "delivery_reconciled", delivery_id, {"actor": actor_id, **change.model_dump()}
            )
            return self._delivery(
                db.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            )


class DeliveryDispatcher:
    def __init__(self, messaging: Messaging):
        self.messaging = messaging
        self.stopping = False
        self.task: asyncio.Task[None] | None = None
        self.failure: str | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self._dispatch(), name="claw-delivery")

    async def close(self) -> None:
        self.stopping = True
        if self.task:
            await self.task

    async def _dispatch(self) -> None:
        try:
            async with httpx.AsyncClient(
                timeout=15, follow_redirects=False, trust_env=False
            ) as client:
                while not self.stopping:
                    claimed = await asyncio.to_thread(self.messaging.claim_delivery)
                    if claimed is None:
                        await asyncio.sleep(0.2)
                        continue
                    delivery, policy = claimed
                    sent, receipt = False, None
                    try:
                        headers = {"Idempotency-Key": delivery["id"]}
                        if policy.credential_env:
                            credential = await asyncio.to_thread(
                                self.messaging.store.resolve_credential, policy.credential_env
                            )
                            headers["Authorization"] = f"Bearer {credential}"
                        assert policy.delivery_url is not None
                        response = await client.post(
                            policy.delivery_url,
                            headers=headers,
                            json={
                                "delivery_id": delivery["id"],
                                "platform": policy.platform,
                                "account": policy.account,
                                "channel": policy.channel,
                                "text": delivery["text"],
                            },
                        )
                        sent = response.is_success
                        # Do not save arbitrary response bodies or credential-bearing headers.
                        receipt = f"HTTP {response.status_code}" if sent else None
                    except (httpx.HTTPError, ClawError):
                        pass
                    finally:
                        await asyncio.to_thread(
                            self.messaging.finish_delivery,
                            delivery["id"],
                            sent=sent,
                            receipt=receipt,
                        )
        except Exception:
            self.failure = "delivery_dispatch_failed"
