"""GitHub protocol fixtures exercise durable polling, filtering and delivery boundaries."""

import json

import httpx
import pytest

from a13n_claw.business import BusinessStore
from a13n_claw.github import GitHubAdapter, iso
from a13n_claw.service import ClawService
from a13n_claw.settings import ClawSettings, GitHubConnection
from a13n_claw.transport import Destination, OutboundMessage


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
    context = {"body": "Fresh comment after delay"}

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
                json={
                    "user": {"login": "alice"},
                    "title": "Task",
                    "body": "Issue body",
                    "state": "open",
                },
            )
        if request.url.path == "/repos/owner/repo/issues/comments/7":
            return httpx.Response(200, json={"user": {"login": "alice"}, **context})
        if request.method == "GET" and request.url.path == "/notifications/threads/1":
            return httpx.Response(200, json=notification())
        if request.method == "PATCH":
            assert len(service.store.rows("SELECT * FROM inbox")) == 1
            return httpx.Response(205)
        raise AssertionError(request.url)

    adapter, service, client = setup(tmp_path, monkeypatch, handler)
    async with client:
        await adapter.discover(1100)
        await adapter.capture(service.receive, 1599)
        assert service.store.rows("SELECT * FROM inbox") == []
        assert not any("/repos/" in path for _, path in requests)
        await adapter.capture(service.receive, 1600)
        items = service.store.rows("SELECT * FROM inbox")
        assert len(items) == 1
        assert "Fresh comment after delay" in items[0]["body"]
        events = service.store.rows("SELECT * FROM github_events ORDER BY notification_id")
        assert events[0]["state"] == "captured" and events[0]["read_status"] == "read"
        assert events[1]["state"] == "ignored" and events[1]["message"] is None
        # Reopening the adapter preserves the cursor and deduplication, not its process memory.
        reopened = GitHubAdapter(
            "github", adapter.settings, service.store, service.settings.channels, client=client
        )
        await reopened.discover(1700)
        await reopened.capture(service.receive, 1700)
        assert len(service.store.rows("SELECT * FROM inbox")) == 1
        assert len([r for r in requests if r[0] == "PATCH"]) == 1
        assert reopened.store.state(0)["cursor"] == 1700


@pytest.mark.anyio
async def test_lost_capture_response_reuses_immutable_context(tmp_path, monkeypatch):
    body = {"text": "First snapshot"}

    async def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        if request.url.path == "/notifications":
            return httpx.Response(200, json=[notification()])
        if request.url.path == "/notifications/threads/1":
            return httpx.Response(200, json=notification())
        return httpx.Response(200, json={"user": {"login": "alice"}, "body": body["text"]})

    adapter, service, client = setup(
        tmp_path, monkeypatch, handler, delay_seconds=0, mark_read=False
    )
    async with client:
        await adapter.discover(1100)

        async def lost_response(message):
            await service.receive(message)
            raise OSError("Response lost after durable capture")

        await adapter.capture(lost_response, 1100)
        body["text"] = "Changed external content"
        await adapter.capture(service.receive, 1200)
        items = service.store.rows("SELECT * FROM inbox")
        assert len(items) == 1
        assert "First snapshot" in items[0]["body"]
        assert "Changed external content" not in items[0]["body"]
        assert service.store.rows("SELECT * FROM github_events")[0]["state"] == "captured"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "sender,body",
    [
        ("mallory", "Private ignored content"),
        ("claw-bot", "Own content"),
        ("alice", "Reply <!-- a13n-claw:marker -->"),
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
        return httpx.Response(200, json={"user": {"login": sender}, "body": body})

    adapter, service, client = setup(tmp_path, monkeypatch, handler, delay_seconds=0)
    async with client:
        await adapter.discover(1100)
        await adapter.capture(service.receive, 1100)
        assert service.store.rows("SELECT * FROM inbox") == []
        saved = service.store.rows("SELECT * FROM github_events")[0]
        assert saved["state"] == "ignored" and saved["message"] is None
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
        await adapter.capture(service.receive, 1100)
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
        return httpx.Response(200, json={"user": {"login": "alice"}, "body": "Updated"})

    adapter, service, client = setup(tmp_path, monkeypatch, handler, mark_read=False)
    async with client:
        await adapter.discover(1100)
        await adapter.capture(service.receive, 1600)
        assert service.store.rows("SELECT * FROM inbox") == []
        adapter.store.stage(adapter.metadata(notification(updated=1200)), 1200, 600)
        assert len(service.store.rows("SELECT * FROM github_events")) == 2
        await adapter.capture(service.receive, 2099)
        assert service.store.rows("SELECT * FROM inbox") == []
        await adapter.capture(service.receive, 2100)
        assert len(service.store.rows("SELECT * FROM inbox")) == 1
        assert "/repos/owner/repo/issues/comments/8" in calls
        assert "/repos/owner/repo/issues/comments/7" not in calls


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
        return httpx.Response(200, json={"user": {"login": "alice"}, "body": "Task"})

    adapter, service, client = setup(tmp_path, monkeypatch, handler, delay_seconds=0)
    async with client:
        await adapter.discover(1100)
        await adapter.capture(service.receive, 1100)
        assert len(patches) == 1
        service.settings.channels["repo"] = service.settings.channels["repo"].model_copy(
            update={"allowed_senders": ("bob",)}
        )
        await adapter.capture(service.receive, 1200)
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
    from test_business_service import setup as native_setup
    from test_business_service import streaming_model

    from a13n_claw.capability import messaging_integrations
    from a13n_claw.host import open_host
    from a13n_claw.transport import InboundMessage

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
            await service.receive(
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
            assert (await service.deliver(delivery["id"]))["status"] == "unknown"
            assert len(posted) == 1
