"""GitHub protocol fixtures exercise durable polling, filtering and delivery boundaries."""

import json

import httpx
import pytest

from a13n_claw.adapters.github import GitHubAdapter, iso
from a13n_claw.configuration.models import ClawSettings, GitHubConnection
from a13n_claw.database.store import BusinessStore
from a13n_claw.messaging.transport import Destination, OutboundMessage
from a13n_claw.runtime.service import ClawService


def settings(**overrides):
    return ClawSettings.model_validate(
        {
            "connections": {
                "github": {
                    "platform": "github",
                    "account_login": "claw-bot",
                    "initial_lookback_seconds": 3600,
                    **overrides,
                }
            },
            "channels": {
                "repo": {
                    "connection": "github",
                    "chat_id": "owner/repo",
                    "allowed_senders": ["alice"],
                    "shared_context_acknowledged": True,
                }
            },
        }
    )


def notification(identity="1", updated=1000, repository="owner/repo"):
    return {
        "id": identity,
        "updated_at": iso(updated),
        "reason": "mention",
        "repository": {"full_name": repository},
        "subject": {
            "type": "Issue",
            "url": f"https://api.github.com/repos/{repository}/issues/42",
            "latest_comment_url": f"https://api.github.com/repos/{repository}/issues/comments/7",
        },
    }


def comment(body="@claw-bot Task", author="alice", updated=1000, identity=7, kind="issues"):
    return {
        "id": identity,
        "url": f"https://api.github.com/repos/owner/repo/{kind}/comments/{identity}",
        "updated_at": iso(updated),
        "user": {"login": author},
        "body": body,
    }


def issue():
    return {
        "id": 42,
        "updated_at": iso(1000),
        "user": {"login": "alice"},
        "title": "Task",
        "body": "Issue context without a request",
        "state": "open",
    }


def setup(tmp_path, monkeypatch, handler, **overrides):
    monkeypatch.setenv("CLAW_GITHUB_TOKEN", "fixture-token")
    config = settings(**overrides)
    store = BusinessStore(tmp_path / "business.sqlite3", config)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = GitHubAdapter(
        "github",
        GitHubConnection.model_validate(config.connections["github"]),
        store,
        config.channels,
        client=client,
    )
    service = ClawService(config, store, {"github": adapter})
    return adapter, service, client


@pytest.mark.anyio
async def test_delay_pagination_capture_before_mark_read_and_restart(tmp_path, monkeypatch):
    requests = []
    snapshots = [notification(), notification("2", repository="not/bound")]
    context = {"body": "@claw-bot Fresh comment after delay"}

    async def handler(request):
        requests.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer fixture-token"
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        if request.url.path == "/notifications":
            assert request.url.params["all"] == "true"
            assert request.url.params["per_page"] == "50"
            page = int(request.url.params["page"])
            return httpx.Response(
                200,
                json=[snapshots[page - 1]],
                headers={"link": '<https://api.github.com/notifications?page=2>; rel="next"'}
                if page == 1
                else {},
            )
        if request.url.path == "/repos/owner/repo/issues/42":
            return httpx.Response(
                200,
                json=issue(),
            )
        if request.url.path == "/repos/owner/repo/issues/42/comments":
            return httpx.Response(200, json=[comment(context["body"])])
        if request.method == "GET" and request.url.path == "/notifications/threads/1":
            return httpx.Response(200, json=notification())
        if request.method == "PATCH":
            assert len(service.store.rows("SELECT * FROM inbox")) == 1
            return httpx.Response(205)
        raise AssertionError(request.url)

    adapter, service, client = setup(tmp_path, monkeypatch, handler)
    async with client:
        await adapter.discover(1100)
        await adapter.capture(service.ingress.receive, 1599)
        assert service.store.rows("SELECT * FROM inbox") == []
        assert not any("/repos/" in path for _, path in requests)
        await adapter.capture(service.ingress.receive, 1600)
        items = service.store.rows("SELECT * FROM inbox")
        assert len(items) == 1
        assert "Fresh comment after delay" in items[0]["body"]
        events = service.store.rows("SELECT * FROM github_events ORDER BY notification_id")
        assert events[0]["state"] == "captured" and events[0]["read_status"] == "pending"
        assert not any(method == "PATCH" for method, _ in requests)
        service.store.settle(items[0]["id"], items[0]["version"], "handled", "Work complete")
        assert events[1]["state"] == "ignored"
        assert adapter.store.messages(events[1]["event_key"]) == []
        # Reopening the adapter preserves the cursor and deduplication, not its process memory.
        reopened = GitHubAdapter(
            "github", adapter.settings, service.store, service.settings.channels, client=client
        )
        await reopened.discover(1700)
        await reopened.capture(service.ingress.receive, 1700)
        assert len(service.store.rows("SELECT * FROM inbox")) == 1
        assert len([r for r in requests if r[0] == "PATCH"]) == 1
        assert reopened.store.state(0)["cursor"] == 1700


@pytest.mark.anyio
async def test_lost_capture_response_reuses_immutable_context(tmp_path, monkeypatch):
    body = {"text": "@claw-bot First snapshot"}

    async def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        if request.url.path == "/notifications":
            return httpx.Response(200, json=[notification()])
        if request.url.path == "/notifications/threads/1":
            return httpx.Response(200, json=notification())
        return httpx.Response(
            200, json=[comment(body["text"])] if request.url.path.endswith("/comments") else issue()
        )

    adapter, service, client = setup(
        tmp_path, monkeypatch, handler, delay_seconds=0, mark_read=False
    )
    async with client:
        await adapter.discover(1100)

        async def lost_response(message):
            await service.ingress.receive(message)
            raise OSError("Response lost after durable capture")

        await adapter.capture(lost_response, 1100)
        body["text"] = "Changed external content"
        await adapter.capture(service.ingress.receive, 1200)
        items = service.store.rows("SELECT * FROM inbox")
        assert len(items) == 1
        assert "First snapshot" in items[0]["body"]
        assert "Changed external content" not in items[0]["body"]
        assert service.store.rows("SELECT * FROM github_events")[0]["state"] == "captured"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "sender,body",
    [
        ("mallory", "@claw-bot Private ignored content"),
        ("claw-bot", "@claw-bot Own content"),
        ("alice", "@claw-bot Reply <!-- a13n-claw:marker -->"),
        ("alice", "No direct request"),
        ("alice", "@claw-bot-other is not the account"),
    ],
)
async def test_ignored_sender_and_echo_never_reach_inbox(tmp_path, monkeypatch, sender, body):
    async def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        if request.url.path == "/notifications":
            return httpx.Response(200, json=[notification()])
        if request.url.path == "/notifications/threads/1":
            return httpx.Response(200, json=notification())
        return httpx.Response(
            200, json=[comment(body, sender)] if request.url.path.endswith("/comments") else issue()
        )

    adapter, service, client = setup(tmp_path, monkeypatch, handler, delay_seconds=0)
    async with client:
        await adapter.discover(1100)
        await adapter.capture(service.ingress.receive, 1100)
        assert service.store.rows("SELECT * FROM inbox") == []
        saved = service.store.rows("SELECT * FROM github_events")[0]
        assert saved["state"] == "ignored"
        assert adapter.store.messages(saved["event_key"]) == []
        assert body not in saved["notification"]


@pytest.mark.anyio
async def test_rate_limit_preserves_cursor_and_server_poll_interval(tmp_path, monkeypatch):
    import time

    calls = []

    async def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        return httpx.Response(429, headers={"retry-after": "120", "x-poll-interval": "180"})

    adapter, service, client = setup(tmp_path, monkeypatch, handler, initial_lookback_seconds=0)
    async with client:
        now = time.time()
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.discover(now)
        state = adapter.store.state(0)
        assert state["cursor"] == now
        assert state["next_poll"] >= now + 180
        await adapter.discover(now + 10)
        assert calls == ["/user", "/notifications"]


@pytest.mark.anyio
async def test_egress_unknown_never_becomes_automatic_retry(tmp_path, monkeypatch):
    sent = []

    async def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        sent.append(request)
        raise httpx.ReadTimeout("Lost comment response", request=request)

    adapter, service, client = setup(tmp_path, monkeypatch, handler)
    async with client:
        result = await adapter.send(
            Destination(chat_id="owner/repo", reply_to="42"),
            OutboundMessage(text="Explicit reply"),
            request_id="reply-1",
        )
        assert result.status == "unknown"
        assert len(sent) == 1
        assert sent[0].url.path == "/repos/owner/repo/issues/42/comments"
        assert "<!-- a13n-claw:" in json.loads(sent[0].content)["body"]
        blocked = await adapter.send(
            Destination(chat_id="other/repo", reply_to="42"),
            OutboundMessage(text="Wrong repo"),
            request_id="reply-2",
        )
        assert blocked.status == "not_sent" and len(sent) == 1


@pytest.mark.anyio
async def test_subject_url_cannot_redirect_token(tmp_path, monkeypatch):
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        item = notification()
        item["subject"]["url"] = "https://evil.example/steal-token"
        return httpx.Response(200, json=item if "/threads/" in request.url.path else [item])

    adapter, service, client = setup(tmp_path, monkeypatch, handler, delay_seconds=0)
    async with client:
        await adapter.discover(1100)
        await adapter.capture(service.ingress.receive, 1100)
        assert len(calls) == 3
        assert service.store.rows("SELECT * FROM inbox") == []
        assert service.store.rows("SELECT * FROM github_events")[0]["detail"] == "ValueError"


@pytest.mark.anyio
async def test_newer_notification_resets_delay_and_out_of_order_discovery(tmp_path, monkeypatch):
    newest = notification(updated=1500)
    newest["subject"]["latest_comment_url"] = (
        "https://api.github.com/repos/owner/repo/issues/comments/8"
    )
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        if request.url.path == "/notifications":
            return httpx.Response(200, json=[notification()])
        if request.url.path == "/notifications/threads/1":
            return httpx.Response(200, json=newest)
        return httpx.Response(
            200,
            json=[comment("@claw-bot Updated", updated=1500, identity=8)]
            if request.url.path.endswith("/comments")
            else issue(),
        )

    adapter, service, client = setup(tmp_path, monkeypatch, handler, mark_read=False)
    async with client:
        await adapter.discover(1100)
        await adapter.capture(service.ingress.receive, 1600)
        assert service.store.rows("SELECT * FROM inbox") == []
        adapter.store.stage(adapter.metadata(notification(updated=1200)), 1200, 600)
        assert len(service.store.rows("SELECT * FROM github_events")) == 2
        await adapter.capture(service.ingress.receive, 2099)
        assert service.store.rows("SELECT * FROM inbox") == []
        await adapter.capture(service.ingress.receive, 2100)
        assert len(service.store.rows("SELECT * FROM inbox")) == 1
        assert "/repos/owner/repo/issues/42/comments" in calls
        assert "Updated" in service.store.rows("SELECT * FROM inbox")[0]["body"]


@pytest.mark.anyio
async def test_read_marker_rechecks_revoked_sender_after_capture(tmp_path, monkeypatch):
    patches = []

    async def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        if request.url.path == "/notifications":
            return httpx.Response(200, json=[notification()])
        if request.method == "PATCH":
            patches.append(request)
            raise httpx.ReadTimeout("Lost read response", request=request)
        if request.url.path == "/notifications/threads/1":
            return httpx.Response(200, json=notification())
        return httpx.Response(
            200, json=[comment()] if request.url.path.endswith("/comments") else issue()
        )

    adapter, service, client = setup(tmp_path, monkeypatch, handler, delay_seconds=0)
    async with client:
        await adapter.discover(1100)
        await adapter.capture(service.ingress.receive, 1100)
        assert patches == []
        item = service.store.rows("SELECT * FROM inbox")[0]
        service.store.settle(item["id"], item["version"], "handled", "Work complete")
        await adapter.capture(service.ingress.receive, 1160)
        assert len(patches) == 1
        service.settings.channels["repo"] = service.settings.channels["repo"].model_copy(
            update={"allowed_senders": ("bob",)}
        )
        await adapter.capture(service.ingress.receive, 1260)
        assert len(patches) == 1
        event = service.store.rows("SELECT * FROM github_events")[0]
        assert event["state"] == "captured" and event["read_status"] == "skipped"


@pytest.mark.anyio
async def test_zero_lookback_keeps_same_second_boundary_and_restart_overlap(tmp_path, monkeypatch):
    polls = []

    async def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        polls.append(dict(request.url.params))
        return httpx.Response(200, json=[notification(updated=1000)])

    adapter, service, client = setup(tmp_path, monkeypatch, handler, initial_lookback_seconds=0)
    async with client:
        await adapter.discover(1000.9)
        events = service.store.rows("SELECT * FROM github_events")
        assert len(events) == 1
        assert polls[0]["since"] == iso(999)
        reopened = GitHubAdapter(
            "github", adapter.settings, service.store, service.settings.channels, client=client
        )
        await reopened.discover(1061)
        assert polls[1]["since"] == iso(999)
        assert len(service.store.rows("SELECT * FROM github_events")) == 1


@pytest.mark.anyio
async def test_native_host_github_comment_unknown_is_not_replayed(tmp_path, monkeypatch):
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart

    from a13n_claw.messaging.capability import messaging_integrations
    from a13n_claw.messaging.transport import InboundMessage
    from a13n_claw.runtime.host import open_host
    from tests.helpers import setup as native_setup
    from tests.helpers import streaming_model

    posted = []
    results = []

    async def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        if request.method == "POST":
            posted.append(json.loads(request.content))
            raise httpx.ReadTimeout("Response lost after comment acceptance")
        raise AssertionError(request.url)

    async def model(messages, info):
        returns = [
            part for msg in messages for part in msg.parts if isinstance(part, ToolReturnPart)
        ]
        results[:] = returns
        if not returns:
            return ModelResponse(parts=[ToolCallPart("claw_inbox_list", {})])
        last = returns[-1]
        assert "error" not in last.content, last.content
        if last.tool_name == "claw_inbox_list":
            return ModelResponse(
                parts=[ToolCallPart("claw_inbox_read", {"item_id": last.content["items"][0]["id"]})]
            )
        if last.tool_name == "claw_inbox_read":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "claw_send_message",
                        {
                            "item_id": last.content["id"],
                            "request_key": "native-github-reply",
                            "text": "Native comment",
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Unknown send needs reconciliation")])

    root, service, _ = await native_setup(tmp_path, monkeypatch, model=streaming_model(model))
    service.settings = settings(delay_seconds=0)
    monkeypatch.setenv("CLAW_GITHUB_TOKEN", "fixture-token")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = GitHubAdapter(
            "github",
            service.settings.connections["github"],
            service.store,
            service.settings.channels,
            client=client,
        )
        service.adapters = {"github": adapter}
        async with open_host(
            root, service.settings, integrations=messaging_integrations(service)
        ) as host:
            service.bind(host)
            await service.ingress.receive(
                InboundMessage(
                    platform="github",
                    connection="github",
                    event_id="event",
                    message_id="42",
                    chat_id="owner/repo",
                    sender_id="alice",
                    account_id="claw-bot",
                    text="Reply",
                )
            )
            await service.reconcile()
            attempt = service.store.rows("SELECT * FROM attempts")[0]
            operation = await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=20)
            assert operation.status == "completed"
            returned = next(r.content for r in results if r.tool_name == "claw_send_message")
            assert returned["status"] == "unknown"
            delivery = service.store.rows("SELECT * FROM deliveries")[0]
            assert delivery["status"] == "unknown"
            assert len(posted) == 1
            service.store.restart()
            assert (await service.delivery.send(delivery["id"]))["status"] == "unknown"
            assert len(posted) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "obligation",
    [
        "pending",
        "deferred",
        "calling",
        "accepted",
        "unknown_dispatch",
        "pending_send",
        "sending",
        "unknown_send",
    ],
)
async def test_read_marker_waits_for_all_business_obligations(tmp_path, monkeypatch, obligation):
    patches = []

    async def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        if request.url.path == "/notifications":
            return httpx.Response(200, json=[notification()])
        if request.method == "PATCH":
            patches.append(request)
            return httpx.Response(205)
        if request.url.path == "/notifications/threads/1":
            return httpx.Response(200, json=notification())
        return httpx.Response(
            200, json=[comment()] if request.url.path.endswith("/comments") else issue()
        )

    adapter, service, client = setup(tmp_path, monkeypatch, handler, delay_seconds=0)
    async with client:
        await adapter.discover(1100)
        await adapter.capture(service.ingress.receive, 1100)
        assert patches == []
        item = service.store.rows("SELECT * FROM inbox")[0]
        if obligation in {"pending_send", "sending", "unknown_send"}:
            service.store.settle(item["id"], item["version"], "handled", "Work complete")
        attempt = None
        delivery = None
        if obligation == "deferred":
            current = service.store.item(item["id"])
            service.store.settle(item["id"], current["version"], "deferred", "Await human")
        elif obligation in {"calling", "accepted", "unknown_dispatch"}:
            attempt = service.store.begin_attempt("thread-test", "submit", [item["id"]])
            service.store.finish_attempt(attempt, "accepted", receipt_id="receipt-test")
            service.store.settle(
                item["id"],
                item["version"],
                "handled",
                "Settled during execution",
                receipt_id="receipt-test",
            )
            if obligation == "unknown_dispatch":
                service.store.finish_attempt(attempt, "unknown")
            elif obligation == "calling":
                # A retained business settlement cannot stand in for missing dispatch evidence.
                with service.store.transaction() as db:
                    db.execute("UPDATE attempts SET status='calling' WHERE id=?", (attempt,))
        elif obligation in {"pending_send", "sending", "unknown_send"}:
            delivery = service.store.request_delivery(
                request_key="reply",
                channel_id="repo",
                thread_id=None,
                item_id=item["id"],
                destination={"chat_id": "owner/repo", "reply_to": "42"},
                body={"text": "Reply"},
            )
            if obligation != "pending_send":
                service.store.claim_delivery(delivery["id"])
            if obligation == "unknown_send":
                service.store.finish_delivery(delivery["id"], "unknown", "Lost response")
        await adapter.capture(service.ingress.receive, 1200)
        assert patches == []
        if attempt:
            service.store.finish_attempt(attempt, "observed", evidence="Exact terminal receipt")
        if delivery:
            if obligation == "unknown_send":
                service.store.resolve_delivery(delivery["id"], "sent", "Confirmed by operator")
            else:
                if obligation == "pending_send":
                    service.store.claim_delivery(delivery["id"])
                service.store.finish_delivery(delivery["id"], "sent", "Confirmed response")
        current = service.store.item(item["id"])
        service.store.settle(item["id"], current["version"], "handled", "All work settled")
        await adapter.capture(service.ingress.receive, 1300)
        await adapter.capture(service.ingress.receive, 1400)
        assert len(patches) == 1


@pytest.mark.anyio
async def test_coalesced_pr_captures_each_request_and_retains_ignored_versions(
    tmp_path, monkeypatch
):
    current = notification(updated=1500)
    current["subject"].update(
        type="PullRequest", url="https://api.github.com/repos/owner/repo/pulls/42"
    )
    comments = [
        comment("@claw-bot first", updated=1000),
        comment("@claw-bot second", updated=1400, identity=8),
    ]
    ignored = comment("@claw-bot unauthorized", author="bob", updated=1450, identity=9)
    review = comment("@claw-bot review this line", updated=1490, identity=10, kind="pulls")
    calls = []

    async def handler(request):
        calls.append(request)
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        if path == "/notifications":
            return httpx.Response(200, json=[current])
        if path == "/notifications/threads/1":
            return httpx.Response(200, json=current)
        if path == "/repos/owner/repo/pulls/42":
            return httpx.Response(200, json=issue())
        if path == "/repos/owner/repo/pulls/42/comments":
            return httpx.Response(200, json=[review])
        if path == "/repos/owner/repo/issues/42/comments":
            page = int(request.url.params["page"])
            assert request.url.params["since"] == iso(899)
            return httpx.Response(
                200,
                json=comments if page == 1 else [ignored],
                headers={"link": '<https://api.github.com/comments?page=2>; rel="next"'}
                if page == 1
                else {},
            )
        raise AssertionError(request.url)

    adapter, service, client = setup(
        tmp_path, monkeypatch, handler, delay_seconds=0, mark_read=False
    )
    adapter.store.stage({**adapter.metadata(notification()), "since": 899}, 1000, 0)
    async with client:
        # Refresh coalesces the notification, retaining its earliest discovery window.
        await adapter.capture(service.ingress.receive, 1600)
        await adapter.capture(service.ingress.receive, 1600)
        items = service.store.rows("SELECT * FROM inbox ORDER BY created")
        assert len(items) == 3
        assert len({i["event_id"] for i in items}) == 3
        assert all(json.loads(i["body"])["message"]["message_id"] == "42" for i in items)
        assert "unauthorized" not in str(service.store.rows("SELECT * FROM github_requests"))
        assert "Issue context" not in str(items)
        # Rescanning overlapping windows with relaxed grants cannot resurrect an ignored version.
        service.settings.channels["repo"] = service.settings.channels["repo"].model_copy(
            update={"allowed_senders": ("alice", "bob")}
        )
        current["updated_at"] = iso(1600)
        adapter.store.stage({**adapter.metadata(current), "since": 899}, 1600, 0)
        await adapter.capture(service.ingress.receive, 1700)
        assert len(service.store.rows("SELECT * FROM inbox")) == 3
        # Editing an admitted comment produces a distinct request; unchanged comments stay deduped.
        comments[0] = comment("@claw-bot edited", updated=1650)
        current["updated_at"] = iso(1700)
        adapter.store.stage({**adapter.metadata(current), "since": 899}, 1700, 0)
        await adapter.capture(service.ingress.receive, 1800)
        assert len(service.store.rows("SELECT * FROM inbox")) == 4


@pytest.mark.anyio
async def test_partial_request_capture_restarts_without_changed_context(tmp_path, monkeypatch):
    comments = [comment("@claw-bot first"), comment("@claw-bot second", identity=8)]

    async def handler(request):
        if request.url.path == "/notifications/threads/1":
            return httpx.Response(200, json=notification())
        return httpx.Response(
            200, json=comments if request.url.path.endswith("/comments") else issue()
        )

    adapter, service, client = setup(
        tmp_path, monkeypatch, handler, delay_seconds=0, mark_read=False
    )
    adapter.store.stage(adapter.metadata(notification()), 1000, 0)
    async with client:

        async def interrupted(message):
            await service.ingress.receive(message)
            raise OSError("Lost first intake response")

        await adapter.capture(interrupted, 1100)
        assert len(service.store.rows("SELECT * FROM inbox")) == 1
        comments[:] = [comment("@claw-bot replacement", updated=1100)]
        reopened = GitHubAdapter(
            "github", adapter.settings, service.store, service.settings.channels, client=client
        )
        await reopened.capture(service.ingress.receive, 1200)
        items = service.store.rows("SELECT * FROM inbox")
        assert len(items) == 2
        assert "second" in str(items) and "replacement" not in str(items)


@pytest.mark.anyio
@pytest.mark.parametrize("lost", [False, True])
async def test_github_eyes_uses_retained_delivery_and_never_replays_unknown(
    tmp_path, monkeypatch, lost
):
    sent = []

    async def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        if request.method == "POST":
            sent.append(request)
            assert request.url.path == "/repos/owner/repo/issues/comments/7/reactions"
            assert json.loads(request.content) == {"content": "eyes"}
            if lost:
                raise httpx.ReadTimeout("Lost reaction response", request=request)
            return httpx.Response(201, json={"id": 91})
        if request.url.path == "/notifications/threads/1":
            return httpx.Response(200, json=notification())
        return httpx.Response(
            200, json=[comment()] if request.url.path.endswith("/comments") else issue()
        )

    adapter, service, client = setup(
        tmp_path, monkeypatch, handler, delay_seconds=0, mark_read=False
    )
    service.settings.channels["repo"] = service.settings.channels["repo"].model_copy(
        update={"reactions": True}
    )
    adapter.store.stage(adapter.metadata(notification()), 1000, 0)
    async with client:
        await adapter.capture(service.ingress.receive, 1100)
        assert sent == []
        row = service.store.rows("SELECT * FROM deliveries")[0]
        assert row["status"] == "pending"
        await service.delivery.send(row["id"])
        service.store.restart()
        await service.feedback.status(service.store.rows("SELECT * FROM inbox")[0], "retained")
        await service.delivery.reconcile()
        assert len(sent) == 1
        assert service.store.delivery(row["id"])["status"] == ("unknown" if lost else "sent")


@pytest.mark.anyio
async def test_comments_pagination_failure_keeps_capture_atomic(tmp_path, monkeypatch):
    async def handler(request):
        if request.url.path == "/notifications/threads/1":
            return httpx.Response(200, json=notification())
        if request.url.path.endswith("/comments"):
            if request.url.params["page"] == "2":
                return httpx.Response(503)
            return httpx.Response(
                200,
                json=[comment()],
                headers={"link": '<https://api.github.com/comments?page=2>; rel="next"'},
            )
        return httpx.Response(200, json=issue())

    adapter, service, client = setup(tmp_path, monkeypatch, handler, delay_seconds=0)
    adapter.store.stage(adapter.metadata(notification()), 1000, 0)
    async with client:
        await adapter.capture(service.ingress.receive, 1100)
        assert service.store.rows("SELECT * FROM inbox") == []
        assert service.store.rows("SELECT * FROM github_requests") == []
        event = service.store.rows("SELECT * FROM github_events")[0]
        assert event["message"] is None and event["state"] == "pending"


@pytest.mark.anyio
async def test_new_notification_read_waits_for_earlier_request(tmp_path, monkeypatch):
    current = notification()
    comments = [comment()]
    patches = []

    async def handler(request):
        if request.method == "PATCH":
            patches.append(request)
            return httpx.Response(205)
        if request.url.path == "/notifications/threads/1":
            return httpx.Response(200, json=current)
        return httpx.Response(
            200, json=comments if request.url.path.endswith("/comments") else issue()
        )

    adapter, service, client = setup(tmp_path, monkeypatch, handler, delay_seconds=0)
    adapter.store.stage(adapter.metadata(current), 1000, 0)
    async with client:
        await adapter.capture(service.ingress.receive, 1100)
        first = service.store.rows("SELECT * FROM inbox")[0]
        current["updated_at"] = iso(1200)
        comments[:] = [comment("@claw-bot next", updated=1200, identity=8)]
        adapter.store.stage({**adapter.metadata(current), "since": 1100}, 1200, 0)
        await adapter.capture(service.ingress.receive, 1300)
        second = service.store.rows("SELECT * FROM inbox ORDER BY created DESC")[0]
        service.store.settle(second["id"], second["version"], "handled", "Second settled")
        await adapter.capture(service.ingress.receive, 1400)
        assert patches == []
        service.store.settle(first["id"], first["version"], "handled", "First settled")
        await adapter.capture(service.ingress.receive, 1500)
        assert len(patches) == 1
        assert [
            r["read_status"]
            for r in service.store.rows("SELECT * FROM github_events ORDER BY updated")
        ] == ["superseded", "read"]


@pytest.mark.anyio
async def test_github_per_channel_reuses_issue_thread_not_repository_thread(tmp_path, monkeypatch):
    from a13n_claw.messaging.capability import messaging_integrations
    from a13n_claw.messaging.transport import InboundMessage
    from a13n_claw.runtime.host import open_host
    from tests.helpers import setup as native_setup

    root, service, _ = await native_setup(tmp_path, monkeypatch, mode="per_channel")
    project_id = service.settings.channels["room"].project_id
    github = settings().connections["github"]
    binding = settings().channels["repo"].model_copy(update={"project_id": project_id})
    service.settings = service.settings.model_copy(
        update={"connections": {"github": github}, "channels": {"repo": binding}}
    )
    service.adapters = {}
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        from a13n_harness_ui.configuration.mutation import ResourceMutationRequest

        service.bind(host)
        await host.mutate_configuration(
            relative_path="projects/channel.yaml",
            request=ResourceMutationRequest(
                content=json.dumps(
                    {
                        "schema_version": "1",
                        "kind": "project",
                        "id": project_id,
                        "name": "Channel",
                        "roots": [{"path": str(tmp_path / "work")}],
                    }
                )
            ),
        )
        for event, number in (("one", "42"), ("two", "43"), ("three", "42")):
            await service.ingress.receive(
                InboundMessage(
                    platform="github",
                    connection="github",
                    event_id=event,
                    message_id=number,
                    chat_id="owner/repo",
                    sender_id="alice",
                    account_id="claw-bot",
                    text="Request",
                )
            )
            await service.reconcile()
            for attempt in service.store.rows("SELECT * FROM attempts WHERE status='accepted'"):
                await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=20)
            await service.reconcile()
        items = service.store.rows("SELECT * FROM inbox ORDER BY created")
        assert items[0]["thread_id"] == items[2]["thread_id"]
        assert items[0]["thread_id"] != items[1]["thread_id"]
        assert {i["project_id"] for i in items} == {project_id}


@pytest.mark.anyio
async def test_github_retained_repository_route_preserves_pending_decision(tmp_path, monkeypatch):
    from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    from a13n_claw.messaging.capability import messaging_integrations
    from a13n_claw.messaging.transport import InboundMessage
    from a13n_claw.runtime.host import open_host
    from tests.helpers import setup as native_setup
    from tests.helpers import streaming_model

    async def question(messages, info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "ask_user_question",
                    {
                        "questions": [
                            {
                                "header": "Choice",
                                "question": "Which option?",
                                "options": [
                                    {"label": "One", "description": "First option"},
                                    {"label": "Two", "description": "Second option"},
                                ],
                            }
                        ]
                    },
                )
            ]
        )

    root, service, _ = await native_setup(
        tmp_path, monkeypatch, mode="per_channel", model=streaming_model(question)
    )
    project_id = service.settings.channels["room"].project_id
    service.settings = service.settings.model_copy(
        update={
            "connections": settings().connections,
            "channels": {
                "repo": settings().channels["repo"].model_copy(update={"project_id": project_id})
            },
        }
    )
    service.adapters = {}
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await host.mutate_configuration(
            relative_path="projects/channel.yaml",
            request=ResourceMutationRequest(
                content=json.dumps(
                    {
                        "schema_version": "1",
                        "kind": "project",
                        "id": project_id,
                        "name": "Channel",
                        "roots": [{"path": str(tmp_path / "work")}],
                    }
                )
            ),
        )
        await service.ingress.receive(
            InboundMessage(
                platform="github",
                connection="github",
                event_id="legacy-request",
                message_id="42",
                chat_id="owner/repo",
                sender_id="alice",
                account_id="claw-bot",
                text="Request",
            )
        )
        item = service.store.rows("SELECT * FROM inbox")[0]
        thread_id = await service.ensure_target("channel:repo", project_id)
        service.store.assign_thread(item["id"], thread_id)
        item = service.store.item(item["id"])
        await service._dispatch(thread_id, [item])
        attempt = service.store.rows("SELECT * FROM attempts")[0]
        result = await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=20)
        assert result.status == "suspended"
        pending = await host.thread_decisions(thread_id=thread_id)
        assert pending is not None
        await service.reconcile()
        with service.store.transaction() as db:
            db.execute("UPDATE inbox SET next_check=0 WHERE id=?", (item["id"],))
        await service.reconcile()
        assert service.store.item(item["id"])["thread_id"] == thread_id
        assert len(service.store.rows("SELECT * FROM targets")) == 1
        assert len(service.store.rows("SELECT * FROM attempts")) == 1
        current = await host.thread_decisions(thread_id=thread_id)
        assert current is not None
        assert current.continuation_id == pending.continuation_id
        assert current.requests == pending.requests
