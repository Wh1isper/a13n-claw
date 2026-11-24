"""GitHub notification scans use mocked HTTP and never follow foreign origins."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from a13n_claw.service_app.github_poll import (
    GitHubClient,
    GitHubSettings,
    allowed_sender,
    explicitly_mentions,
    same_origin,
)


def settings(**values):
    return GitHubSettings.model_validate({"mode": "polling", "senders": ("*",), **values})


def test_mention_boundaries_sender_and_https_origin():
    assert explicitly_mentions("hello @claw-bot!", "claw-bot")
    assert not explicitly_mentions("name@claw-bot.example @claw-bot-extra", "claw-bot")
    assert allowed_sender(settings(), "Human", "Claw-Bot")
    assert not allowed_sender(settings(), "CLAW-BOT", "claw-bot")
    assert not allowed_sender(settings(senders=("alice",)), "bob", "claw-bot")
    assert same_origin("https://api.github.com", "/user") == "https://api.github.com/user"
    for url in (
        "http://api.github.com/user",
        "https://evil.example/path",
        "https://api.github.com@evil.example/",
    ):
        with pytest.raises(ValueError):
            same_origin("https://api.github.com", url)
    with pytest.raises(ValueError):
        settings(api_url="http://api.github.com")


def test_scan_pages_comments_mentions_and_conditional_304():
    asyncio.run(exercise_scan())


async def exercise_scan():
    requests = []
    date = "Mon, 23 Nov 2026 00:00:00 GMT"
    notice = {
        "id": "99",
        "reason": "mention",
        "repository": {"id": 123, "full_name": "Acme/Repo"},
        "subject": {"type": "PullRequest", "url": "https://api.github.com/repos/Acme/Repo/pulls/7"},
    }
    subject = {
        "number": 7,
        "title": "A PR",
        "body": "Hi @claw-bot",
        "user": {"login": "Alice"},
        "created_at": "2026-11-22T23:55:00Z",
        "updated_at": "2026-11-22T23:55:00Z",
    }
    comments = [
        {
            "id": 10,
            "user": {"login": "Alice"},
            "body": "Fix @CLAW-BOT!",
            "updated_at": "2026-11-22T23:57:00Z",
        },
        {
            "id": 11,
            "user": {"login": "claw-bot"},
            "body": "@claw-bot",
            "updated_at": "2026-11-22T23:57:00Z",
        },
        {
            "id": 12,
            "user": {"login": "Bob"},
            "body": "@claw-bot-other",
            "updated_at": "2026-11-22T23:57:00Z",
        },
    ]

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer fake-token"
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "claw-bot"})
        if path == "/notifications":
            assert request.url.params["per_page"] == "50"
            assert request.url.params["since"].endswith("Z")
            assert "." not in request.url.params["since"]
            if request.headers.get("if-modified-since"):
                return httpx.Response(304, headers={"Date": date, "X-Poll-Interval": "90"})
            return httpx.Response(
                200,
                json=[notice],
                headers={"Date": date, "Last-Modified": date, "X-Poll-Interval": "90"},
            )
        if path == "/repos/Acme/Repo/pulls/7":
            return httpx.Response(200, json=subject)
        if path == "/repos/Acme/Repo/issues/7/comments":
            if request.url.params.get("page") == "2":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": 13,
                            "user": {"login": "Alice"},
                            "body": "Another @claw-bot",
                            "updated_at": "2026-11-22T23:59:00Z",
                        }
                    ],
                )
            return httpx.Response(
                200,
                json=comments,
                headers={
                    "Link": "<https://api.github.com/repos/Acme/Repo/issues/7/comments?page=2>"
                    '; rel="next"'
                },
            )
        if path == "/repos/Acme/Repo/pulls/7/comments":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 20,
                        "user": {"login": "BOB"},
                        "body": "@claw-bot review",
                        "updated_at": "2026-11-22T23:58:00Z",
                    }
                ],
            )
        raise AssertionError(str(request.url))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GitHubClient(settings(), "fake-token", http)
        scan = await client.scan(datetime(2026, 11, 22, 23, 50, tzinfo=UTC), None)
        assert len(scan.events) == 4
        assert any(request.url.params.get("page") == "2" for request in requests)
        assert {event.key.split(":")[1] for event in scan.events} == {"comment", "99"}
        assert {event.source.get("comment_kind") for event in scan.events} == {
            "issue",
            "review",
            None,
        }
        assert scan.date == datetime(2026, 11, 23, tzinfo=UTC) and scan.poll_interval == 90
        second = await client.scan(scan.date, scan.last_modified)
        assert second.not_modified and second.events == ()
        assert requests[-1].headers["if-modified-since"] == date


def test_foreign_pagination_and_redirect_are_rejected_before_forwarding_token():
    async def run():
        called = []

        def handler(request):
            called.append(str(request.url))
            if request.url.path == "/user":
                return httpx.Response(200, json={"login": "bot"})
            return httpx.Response(
                200,
                json=[],
                headers={
                    "Date": "Mon, 23 Nov 2026 00:00:00 GMT",
                    "Link": '<https://evil.example/page>; rel="next"',
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(ValueError, match="outside"):
                await GitHubClient(settings(), "fake", http).scan(
                    datetime(2026, 11, 22, tzinfo=UTC), None
                )
        assert len(called) == 2 and all("evil.example" not in url for url in called)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(302, headers={"Location": "https://evil.example"})
            )
        ) as http:
            with pytest.raises(ValueError, match="redirect"):
                await GitHubClient(settings(), "fake", http).get("https://api.github.com/user")

    asyncio.run(run())
