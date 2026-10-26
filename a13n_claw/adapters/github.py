"""GitHub notification polling and explicit issue/PR comments over durable intake."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from a13n_claw.adapters.github_store import GitHubStore
from a13n_claw.async_utils import database
from a13n_claw.configuration.models import ChannelBinding, GitHubConnection
from a13n_claw.database.store import BusinessStore
from a13n_claw.messaging.transport import (
    AttachmentRef,
    Destination,
    InboundMessage,
    MessageHandler,
    OutboundMessage,
    SendResult,
)


def timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("GitHub timestamp is missing its offset")
    return parsed.timestamp()


def iso(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds")


class GitHubAdapter:
    def __init__(
        self,
        connection_id: str,
        settings: GitHubConnection,
        store: BusinessStore,
        channels: dict[str, ChannelBinding],
        *,
        client: httpx.AsyncClient | None = None,
    ):
        self.connection_id, self.settings = connection_id, settings
        self.business = store
        self.store = GitHubStore(store, connection_id, settings.account_login.casefold())
        self.channels = channels
        self.client = client or httpx.AsyncClient(timeout=20, follow_redirects=False)
        self._owns_client = client is None
        self._closed = asyncio.Event()
        self._authenticated: str | None = None
        self._rate_until = 0.0
        self._poll_seconds = settings.poll_seconds
        self.status = "disconnected"
        self.error: str | None = None

    def headers(self) -> dict[str, str]:
        token = os.environ.get(self.settings.token_env)
        if not token:
            raise ValueError("Configured GitHub token environment variable is missing")
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("GitHub API path must be relative to the configured origin")
        if time.time() < self._rate_until:
            raise ValueError("GitHub rate limit backoff is active")
        response = await self.client.request(
            method, self.settings.api_url + path, headers=self.headers(), **kwargs
        )
        if response.status_code in {403, 429}:
            retry = response.headers.get("retry-after")
            reset = response.headers.get("x-ratelimit-reset")
            self._rate_until = max(
                time.time() + 60,
                time.time() + float(retry) if retry and retry.isdigit() else 0,
                float(reset) if reset and reset.isdigit() else 0,
            )
        interval = response.headers.get("x-poll-interval")
        if interval and interval.isdigit():
            self._poll_seconds = max(self.settings.poll_seconds, int(interval))
        response.raise_for_status()
        return response

    async def authenticate(self) -> None:
        identity = hashlib.sha256(self.headers()["Authorization"].encode()).hexdigest()
        if identity == self._authenticated:
            return
        response = await self.request("GET", "/user")
        if response.json().get("login", "").casefold() != self.settings.account_login.casefold():
            raise ValueError("GitHub token account does not match the configured login")
        self._authenticated = identity

    def binding(self, repository: str) -> ChannelBinding | None:
        return next(
            (
                c
                for c in self.channels.values()
                if c.enabled and c.connection == self.connection_id and c.chat_id == repository
            ),
            None,
        )

    def subject_path(self, url: str, repository: str, *, comment: bool = False) -> str:
        prefix = self.settings.api_url + "/repos/" + repository + "/"
        if not url.startswith(prefix):
            raise ValueError("GitHub subject URL is outside the authorized repository")
        suffix = url[len(prefix) :]
        pattern = r"(?:issues|pulls)/comments/[0-9]+" if comment else r"(?:issues|pulls)/[0-9]+"
        if not re.fullmatch(pattern, suffix):
            raise ValueError("Unsupported GitHub notification subject")
        return url[len(self.settings.api_url) :]

    @staticmethod
    def metadata(item: dict) -> dict:
        return {
            "id": str(item["id"]),
            "updated_at": item["updated_at"],
            "repository": item["repository"]["full_name"],
            "reason": item["reason"],
            "subject": {
                key: item["subject"].get(key) for key in ("url", "type", "latest_comment_url")
            },
        }

    async def discover(self, now: float) -> None:
        state = await database(self.store.state, now - self.settings.initial_lookback_seconds)
        if now < state["next_poll"]:
            return
        try:
            await self.authenticate()
            # Include read notifications: mark-read is independent of durable capture and can
            # race a newer update. One-second overlap plus durable event keys handles boundaries.
            for page in range(1, 101):
                response = await self.request(
                    "GET",
                    "/notifications",
                    params={
                        "all": "true",
                        "since": iso(state["cursor"] - 1),
                        "before": iso(now),
                        "per_page": 50,
                        "page": page,
                    },
                )
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError("Unexpected GitHub notifications response")
                for item in payload:
                    updated = timestamp(item["updated_at"])
                    if updated < state["cursor"] - 1 or updated > now:
                        continue
                    # Do not retain notification titles or repository metadata before filtering.
                    notification = {**self.metadata(item), "since": state["cursor"] - 1}
                    await database(
                        self.store.stage, notification, updated, self.settings.delay_seconds
                    )
                if "next" not in response.links:
                    break
            else:
                raise ValueError("GitHub pagination exceeded 100 pages; cursor was not advanced")
        except Exception as exc:
            self.error = type(exc).__name__
            await database(
                self.store.poll_finished,
                state["cursor"],
                max(now + self._poll_seconds, self._rate_until),
                self.error,
            )
            raise
        await database(self.store.poll_finished, now, now + self._poll_seconds)
        self.error = None
        self.status = "connected"

    async def comments(self, path: str, since: float) -> list[dict]:
        comments = []
        for page in range(1, 101):
            response = await self.request(
                "GET", path, params={"since": iso(since), "per_page": 100, "page": page}
            )
            values = response.json()
            if not isinstance(values, list):
                raise ValueError("Unexpected GitHub comments response")
            comments.extend(values)
            if "next" not in response.links:
                return comments
        raise ValueError("GitHub comments exceeded 100 pages; capture remains pending")

    async def prepare(self, row: dict[str, Any]) -> list[tuple[str, dict | None]]:
        notification = json.loads(row["notification"])
        repository = notification["repository"]
        binding = self.binding(repository)
        subject = notification["subject"]
        if (
            binding is None
            or subject["type"] not in {"Issue", "PullRequest"}
            or (self.settings.reasons and notification["reason"] not in self.settings.reasons)
        ):
            return []
        path = self.subject_path(subject["url"], repository)
        issue = (await self.request("GET", path)).json()
        number = path.rsplit("/", 1)[1]
        since = notification.get("since", row["updated"] - 1)
        candidates = [(f"/repos/{repository}/issues/{number}", issue, True)]
        for kind in ("issues", "pulls") if subject["type"] == "PullRequest" else ("issues",):
            for comment in await self.comments(
                f"/repos/{repository}/{kind}/{number}/comments", since
            ):
                comment_path = self.subject_path(comment["url"], repository, comment=True)
                candidates.append((comment_path, comment, False))
        allowed = {name.casefold() for name in binding.allowed_senders}
        mention = re.compile(
            r"(?<![\w@/])@" + re.escape(self.settings.account_login) + r"(?![\w-])", re.I
        )
        requests = []
        for source_path, content, root in candidates:
            updated = timestamp(content["updated_at"])
            if not since <= updated <= row["updated"]:
                continue
            author = content.get("user", {}).get("login", "")
            body = content.get("body") or ""
            # Root state/label changes are not fresh requests. Comment edits have native versions.
            version = body if root else f"{content['updated_at']}:{body}"
            key = (
                "ghreq-"
                + hashlib.sha256(
                    json.dumps(
                        [self.store.account, source_path, author.casefold(), version]
                    ).encode()
                ).hexdigest()
            )
            if (
                author.casefold() not in allowed
                or author.casefold() == self.store.account
                or "<!-- a13n-claw:" in body
                or not mention.search(body)
            ):
                requests.append((key, None))
                continue
            # Only the explicitly admitted request body enters context. Other comments and the
            # root body may have been ignored earlier, even when their author is allowed now.
            text = (
                f"GitHub {subject['type']} {repository}#{number}\n"
                f"Source: {self.settings.api_url}{source_path}\n"
                f"State: {issue.get('state', 'unknown')}\n"
                f"Request by {author}:\n{body[:90000]}\n"
                "Use an explicit Claw reply to comment on this issue/PR; intake is not completion."
            )
            message = InboundMessage(
                platform="github",
                connection=self.connection_id,
                event_id=key,
                message_id=number,
                chat_id=repository,
                sender_id=author,
                account_id=self.store.account,
                source_ref=source_path,
                text=text,
            )
            requests.append((key, message.model_dump(mode="json")))
        return requests

    async def capture(self, receive: MessageHandler, now: float) -> None:
        for row in await database(self.store.ready, now):
            try:
                if row["state"] == "pending":
                    if row["message"] is None:
                        notification = json.loads(row["notification"])
                        if self.binding(notification["repository"]) is not None:
                            # Refresh the notification itself before fetching context. Its
                            # latest-comment pointer can change during the debounce delay.
                            current = (
                                await self.request(
                                    "GET",
                                    "/notifications/threads/"
                                    + quote(row["notification_id"], safe=""),
                                )
                            ).json()
                            updated = timestamp(current["updated_at"])
                            if str(current["id"]) != row["notification_id"]:
                                raise ValueError("GitHub notification identity changed")
                            if updated > row["updated"]:
                                await database(
                                    self.store.stage,
                                    self.metadata(current),
                                    updated,
                                    self.settings.delay_seconds,
                                )
                                continue
                            if updated != row["updated"]:
                                raise ValueError("GitHub notification version moved backwards")
                            row["notification"] = json.dumps(
                                {
                                    **self.metadata(current),
                                    "since": notification.get("since", updated - 1),
                                }
                            )
                    if row["message"] is None:
                        await database(
                            self.store.prepared, row["event_key"], await self.prepare(row)
                        )
                    messages = await database(self.store.messages, row["event_key"])
                    admitted = False
                    for saved in messages:
                        message = InboundMessage.model_validate_json(saved["message"])
                        await receive(message)
                        items = await database(
                            self.business.rows,
                            "SELECT disposition FROM inbox WHERE connection=? AND event_id=?",
                            (self.connection_id, message.event_id),
                        )
                        if not items:
                            raise ValueError(
                                "GitHub handler returned without durable intake evidence"
                            )
                        admitted |= items[0]["disposition"] != "ignored"
                    state = "captured" if admitted else "ignored"
                    read_status = "pending" if self.settings.mark_read and admitted else "skipped"
                    await database(self.store.finish, row["event_key"], state, read_status)
                    if read_status != "pending":
                        continue
                messages = await database(self.store.messages, row["event_key"], through=True)
                if not all(
                    self.authorized(InboundMessage.model_validate_json(s["message"]))
                    for s in messages
                ):
                    await database(
                        self.store.finish,
                        row["event_key"],
                        "captured",
                        "skipped",
                        "Current source authorization revoked",
                    )
                    continue
                if not self.settings.mark_read:
                    await database(self.store.finish, row["event_key"], "captured", "skipped")
                    continue
                if not await database(self.store.read_ready, row["event_key"]):
                    continue
                # Confirm the version before an idempotent read marker. A newer event is left
                # for its own delayed capture. all=true polling covers the remaining API race.
                path = "/notifications/threads/" + quote(row["notification_id"], safe="")
                current = (await self.request("GET", path)).json()
                if timestamp(current["updated_at"]) != row["updated"]:
                    await database(self.store.finish, row["event_key"], "captured", "superseded")
                    continue
                await self.request("PATCH", path)
                await database(self.store.finish, row["event_key"], "captured", "read")
            except Exception as exc:
                self.error = type(exc).__name__
                await database(
                    self.store.defer,
                    row["event_key"],
                    max(now + self._poll_seconds, self._rate_until),
                    self.error,
                )
                if self._rate_until > now:
                    break

    def authorized(self, message: InboundMessage) -> bool:
        binding = self.binding(message.chat_id)
        return bool(
            binding is not None
            and self.settings.enabled
            and message.sender_id.casefold() in {s.casefold() for s in binding.allowed_senders}
            and message.account_id == self.store.account
        )

    async def run(self, receive: MessageHandler) -> None:
        while not self._closed.is_set():
            now = time.time()
            try:
                await self.discover(now)
                if now >= self._rate_until:
                    await self.authenticate()
                    await self.capture(receive, now)
            except Exception as exc:
                self.error = type(exc).__name__
                self.status = "retrying"
            with suppress(TimeoutError):
                await asyncio.wait_for(self._closed.wait(), min(self._poll_seconds, 10))

    async def send(
        self, destination: Destination, message: OutboundMessage, *, request_id: str
    ) -> SendResult:
        if message.file is not None:
            return SendResult(
                "not_sent", "GitHub comments accept text; publish file links explicitly"
            )
        if self.binding(destination.chat_id) is None or not re.fullmatch(
            r"[0-9]+", destination.reply_to or ""
        ):
            return SendResult(
                "not_sent", "GitHub delivery requires an authorized repository and issue number"
            )
        try:
            await self.authenticate()
        except Exception:
            return SendResult("not_sent", "GitHub authentication unavailable")
        marker = hashlib.sha256(request_id.encode()).hexdigest()
        path = f"/repos/{destination.chat_id}/issues/{destination.reply_to}/comments"
        payload = {"body": f"{message.text}\n\n<!-- a13n-claw:{marker} -->"}
        if message.reaction is not None:
            source = destination.reaction_to or ""
            prefix = f"/repos/{destination.chat_id}/"
            if (
                message.reaction != "eyes"
                or not source.startswith(prefix)
                or not re.fullmatch(
                    r"(?:issues/[0-9]+|(?:issues|pulls)/comments/[0-9]+)", source[len(prefix) :]
                )
            ):
                return SendResult(
                    "not_sent", "GitHub reaction requires the original request source"
                )
            path, payload = source + "/reactions", {"content": "eyes"}
        try:
            if time.time() < self._rate_until:
                return SendResult("not_sent", "GitHub rate limit backoff is active")
            response = await self.request(
                "POST",
                path,
                json=payload,
            )
            comment_id = response.json().get("id")
            return (
                SendResult("sent", str(comment_id))
                if comment_id
                else SendResult("unknown", "GitHub response omitted comment identity")
            )
        except httpx.HTTPStatusError as exc:
            return SendResult(
                "not_sent" if exc.response.status_code < 500 else "unknown",
                f"GitHub comment HTTP {exc.response.status_code}",
            )
        except Exception:
            return SendResult(
                "unknown", "GitHub comment response unavailable; reconcile before retry"
            )

    async def download(self, message_id: str, attachment: AttachmentRef) -> bytes:
        raise ValueError("GitHub notification attachments are links, not retained files")

    async def close(self) -> None:
        self._closed.set()
        if self._owns_client:
            await self.client.aclose()
