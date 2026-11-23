"""Provider request shape and durable delivery decisions without live Lark calls."""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
from a13n_service.infra.crypto import KeyRing
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.tenancy.tables import WorkspaceRow
from pydantic import SecretStr

from a13n_claw.service_app import connectors, lark_retry
from a13n_claw.service_app.feishu import LarkAdapter, LarkSettings
from a13n_claw.service_app.models import Connector, LarkRetryCard


def test_retry_card_reply_and_patch_use_lark_http_contract(monkeypatch):
    asyncio.run(exercise_delivery(monkeypatch))


async def exercise_delivery(monkeypatch):
    keys = KeyRing(
        active_key_id="fixture",
        keys={"fixture": SecretStr(base64.b64encode(b"x" * 32).decode())},
    )
    settings = LarkSettings(app_id="app", bot_open_id="bot", senders=("user",), groups=("chat",))
    token = "synthetic-card-token"
    card = SimpleNamespace(
        id="lrc_" + "a" * 32,
        claw_id="claw",
        connector_id="connector",
        run_id="failed-run",
        chat_id="chat",
        source_message_id="source",
        is_group=True,
        reply_in_thread=True,
        token=keys.protect(
            token.encode(), lark_retry.location("org", "lrc_" + "a" * 32)
        ).model_dump(),
        state="pending",
        send_started_at=None,
        card_message_id=None,
        patch_pending=False,
    )
    claw = SimpleNamespace(id="claw", main_thread_id="thread", main_session_id="session")
    connector = SimpleNamespace(
        id="connector", claw_id="claw", kind="lark", enabled=True, config=settings.model_dump()
    )
    thread = SimpleNamespace(last_run_id="failed-run", current_run_id=None)

    class Session:
        async def get(self, model, _id, **_kwargs):
            return {
                LarkRetryCard: card,
                Connector: connector,
                ThreadRow: thread,
                RunRow: SimpleNamespace(
                    failure={"code": "environment_unavailable", "message": "secret"}
                ),
            }[model]

        async def get_one(self, model, _id):
            assert model is WorkspaceRow
            return SimpleNamespace(organization_id="org")

    @asynccontextmanager
    async def transaction(_storage):
        yield Session()

    async def enabled(*_args, **_kwargs):
        return True

    async def lock_claw(*_args):
        return claw

    async def now(_session):
        return datetime.now(UTC)

    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "access", "expire": 3600}
            )
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "card-message"}})
        return httpx.Response(200, json={"code": 0})

    transport = httpx.MockTransport(handle)

    class Adapter(LarkAdapter):
        def __init__(self, connection_id, config, secret):
            super().__init__(
                connection_id, config, secret, client=httpx.AsyncClient(transport=transport)
            )
            self._owns_client = True

    monkeypatch.setattr(lark_retry, "transaction", transaction)
    monkeypatch.setattr(lark_retry.inbox, "lock_claw", lock_claw)
    monkeypatch.setattr(lark_retry, "now", now)
    monkeypatch.setattr(lark_retry, "LarkAdapter", Adapter)
    monkeypatch.setattr(connectors, "intake_enabled", enabled)
    monkeypatch.setattr(connectors, "credential", lambda *_args: "fixture-secret")
    runtime = SimpleNamespace(
        storage=None,
        keys=keys,
        settings=SimpleNamespace(server=SimpleNamespace(public_url="https://claw.test")),
    )
    await lark_retry.deliver(runtime, "workspace", "claw", card.id)
    assert card.state == "sent" and card.card_message_id == "card-message"
    reply = next(req for req in requests if req.url.path.endswith("/reply"))
    body = json.loads(reply.content)
    assert reply.url.path == "/open-apis/im/v1/messages/source/reply"
    assert body["uuid"] == "claw-retry-" + "a" * 32
    assert body["reply_in_thread"] is True and body["msg_type"] == "interactive"
    assert token in body["content"]
    assert "execution environment was unavailable" in body["content"]
    assert "secret" not in body["content"]
    card.state, card.patch_pending = "admitted", True
    await lark_retry.deliver(runtime, "workspace", "claw", card.id)
    patch = next(req for req in requests if req.method == "PATCH")
    assert patch.url.path == "/open-apis/im/v1/messages/card-message"
    assert token not in json.loads(patch.content)["content"]
    assert not card.patch_pending

    # Unknown delivery must not be resent after the bounded provider deduplication window.
    card.state = "pending"
    card.send_started_at = datetime.now(UTC) - timedelta(minutes=56)
    count = len(requests)
    await lark_retry.deliver(runtime, "workspace", "claw", card.id)
    assert card.state == "unknown" and len(requests) == count
