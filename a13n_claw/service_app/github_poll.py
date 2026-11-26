"""Outbound GitHub Notifications scanning without forwarding credentials across origins."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Literal
from urllib.parse import quote, urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GitHubSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["external", "polling", "webhook"] = "external"
    bot_login: str | None = Field(default=None, min_length=1, max_length=100)
    principal_id: str | None = Field(default=None, min_length=1, max_length=72)
    channels: tuple[str, ...] = Field(default=(), max_length=500)
    senders: tuple[str, ...] = Field(min_length=1, max_length=500)
    api_url: str = "https://api.github.com"
    poll_interval_seconds: int = Field(default=60, ge=15, le=86400)
    max_source_notification_delay_seconds: int = Field(default=86400, ge=0, le=2592000)
    initial_lookback_seconds: int = Field(default=3600, ge=0, le=2592000)
    mark_read: bool = False

    @field_validator("bot_login", mode="before")
    @classmethod
    def strip_bot_login(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("api_url")
    @classmethod
    def valid_api_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise ValueError("GitHub API URL must be an HTTPS origin or API path")
        try:
            _ = parts.port
        except ValueError as error:
            raise ValueError("Invalid GitHub API port") from error
        return value.rstrip("/")

    @model_validator(mode="after")
    def external_identity(self) -> GitHubSettings:
        if self.mode == "external" and (not self.principal_id or not self.channels):
            raise ValueError("External intake requires a service account and repositories")
        if self.mode == "webhook" and not self.bot_login:
            raise ValueError("Webhook intake requires a bot login")
        return self


def origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("GitHub URL must be HTTPS without user information")
    return parts.scheme, parts.hostname.lower(), parts.port or 443


def same_origin(base: str, target: str) -> str:
    absolute = urljoin(base.rstrip("/") + "/", target)
    parts = urlsplit(absolute)
    if origin(absolute) != origin(base) or parts.fragment:
        raise ValueError("GitHub URL is outside the configured API origin")
    return absolute


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("GitHub timestamp must include a timezone")
    return result.astimezone(UTC)


def since_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def explicitly_mentions(body: str, login: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9_-])@{re.escape(login)}(?![A-Za-z0-9_-])", body, re.I))


def allowed_sender(settings: GitHubSettings, sender: str, bot: str) -> bool:
    sender = sender.casefold()
    return (
        bool(sender)
        and sender != bot.casefold()
        and ("*" in settings.senders or sender in {value.casefold() for value in settings.senders})
    )


@dataclass(frozen=True, slots=True)
class Event:
    key: str
    thread_id: str
    channel: str
    sender: str
    content: str
    source: dict
    reaction_url: str


@dataclass(frozen=True, slots=True)
class Scan:
    events: tuple[Event, ...]
    date: datetime
    last_modified: str | None
    poll_interval: int
    not_modified: bool = False
    server_interval: int | None = None


class GitHubClient:
    def __init__(self, settings: GitHubSettings, token: str, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    async def get(
        self, url: str, *, params: dict | None = None, headers: dict | None = None
    ) -> httpx.Response:
        response = await self.client.get(
            same_origin(self.settings.api_url, url),
            params=params,
            headers={**self.headers, **(headers or {})},
            follow_redirects=False,
        )
        if response.is_redirect and response.status_code != 304:
            raise ValueError("GitHub API redirects are not followed")
        if response.status_code != 304:
            response.raise_for_status()
        return response

    async def pages(self, url: str, *, params: dict | None = None) -> list[dict]:
        values: list[dict] = []
        seen: set[str] = set()
        next_url: str | None = same_origin(self.settings.api_url, url)
        while next_url:
            if next_url in seen or len(seen) >= 100:
                raise ValueError("GitHub pagination cycle or page limit")
            seen.add(next_url)
            response = await self.get(next_url, params=params)
            params = None
            page = response.json()
            if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
                raise ValueError("GitHub returned an invalid page")
            values.extend(page)
            next_link = response.links.get("next", {}).get("url")
            next_url = same_origin(self.settings.api_url, next_link) if next_link else None
        return values

    async def scan(
        self,
        cursor: datetime | None,
        last_modified: str | None,
        on_interval: Callable[[int], Awaitable[None]] | None = None,
    ) -> Scan:
        user = (await self.get(f"{self.settings.api_url}/user")).json()
        bot = user.get("login") if isinstance(user, dict) else None
        if not isinstance(bot, str) or not bot:
            raise ValueError("GitHub user has no login")
        cutoff = cursor or (
            datetime.now(UTC) - timedelta(seconds=self.settings.initial_lookback_seconds)
        )
        params = {
            "all": "true",
            "since": since_timestamp(cutoff - timedelta(seconds=60)),
            "per_page": 50,
        }
        headers = {"If-Modified-Since": last_modified} if last_modified else None
        response = await self.get(
            f"{self.settings.api_url}/notifications", params=params, headers=headers
        )
        server_interval = None
        try:
            if "X-Poll-Interval" in response.headers:
                server_interval = int(response.headers["X-Poll-Interval"])
                if not 0 <= server_interval <= 2_147_483_647:
                    raise ValueError("Invalid X-Poll-Interval")
        except ValueError as error:
            raise ValueError("Invalid GitHub poll interval") from error
        interval = max(self.settings.poll_interval_seconds, server_interval or 0)
        if server_interval is not None and on_interval is not None:
            await on_interval(server_interval)
        try:
            date = parsedate_to_datetime(response.headers["Date"]).astimezone(UTC)
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ValueError("GitHub response lacks a valid Date") from error
        modified = response.headers.get("Last-Modified") or last_modified
        if response.status_code == 304:
            return Scan((), date, modified, interval, True, server_interval)
        page = response.json()
        if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
            raise ValueError("GitHub returned invalid notifications")
        notifications = list(page)
        next_url = response.links.get("next", {}).get("url")
        if next_url:
            notifications.extend(await self.pages(next_url))
        events: list[Event] = []
        for notification in notifications:
            events.extend(await self.notification(notification, bot, cutoff, date))
        return Scan(tuple(events), date, modified, interval, server_interval=server_interval)

    async def notification(
        self, notification: dict, bot: str, cutoff: datetime, date: datetime
    ) -> list[Event]:
        subject = notification.get("subject") or {}
        repository = notification.get("repository") or {}
        kind = subject.get("type")
        if kind not in ("Issue", "PullRequest"):
            return []
        channel, repo_id = repository.get("full_name"), repository.get("id")
        if not isinstance(channel, str) or not isinstance(repo_id, int):
            raise ValueError("GitHub notification lacks repository identity")
        if self.settings.channels and channel.casefold() not in {
            c.casefold() for c in self.settings.channels
        }:
            return []
        owner, separator, repo = channel.partition("/")
        if not separator or not owner or not repo or "/" in repo:
            raise ValueError("Invalid GitHub repository")
        subject_url = same_origin(self.settings.api_url, subject["url"])
        subject_data = (await self.get(subject_url)).json()
        number = subject_data.get("number")
        if not isinstance(number, int) or number <= 0:
            raise ValueError("GitHub subject lacks an issue number")
        prefix = f"{self.settings.api_url}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        thread = str(notification["id"])
        result: list[Event] = []
        for resource in ("issues", "pulls") if kind == "PullRequest" else ("issues",):
            path = f"{prefix}/{resource}/{number}/comments"
            for comment in await self.pages(
                path,
                params={"since": since_timestamp(cutoff - timedelta(seconds=60)), "per_page": 100},
            ):
                sender = (comment.get("user") or {}).get("login")
                body = comment.get("body")
                if not isinstance(sender, str) or not isinstance(body, str):
                    continue
                updated = timestamp(comment["updated_at"])
                if (
                    updated < cutoff - timedelta(seconds=60)
                    or not allowed_sender(self.settings, sender, bot)
                    or not explicitly_mentions(body, bot)
                ):
                    continue
                comment_id = comment.get("id")
                if not isinstance(comment_id, int):
                    raise ValueError("GitHub comment lacks identity")
                result.append(
                    Event(
                        key=f"github:comment:{repo_id}:{resource}:{comment_id}:{updated.isoformat()}",
                        thread_id=thread,
                        channel=channel,
                        sender=sender,
                        content=body,
                        source={
                            "issue_number": number,
                            "comment_id": comment_id,
                            "comment_kind": "review" if resource == "pulls" else "issue",
                            "subject_url": subject_url,
                        },
                        reaction_url=f"{prefix}/{resource}/comments/{comment_id}/reactions",
                    )
                )
        created = timestamp(subject_data["created_at"])
        updated = timestamp(subject_data["updated_at"])
        sender = (subject_data.get("user") or {}).get("login")
        body = subject_data.get("body") or ""
        if (
            notification.get("reason") in ("mention", "team_mention")
            and created == updated
            and created >= cutoff - timedelta(seconds=60)
            and timedelta(0)
            <= date - updated
            <= timedelta(seconds=self.settings.max_source_notification_delay_seconds)
            and isinstance(sender, str)
            and isinstance(body, str)
            and allowed_sender(self.settings, sender, bot)
            and explicitly_mentions(body, bot)
        ):
            title = subject_data.get("title") or ""
            content = f"{title}\n\n{body}".strip()
            result.append(
                Event(
                    key=f"github:{thread}:{updated.isoformat()}",
                    thread_id=thread,
                    channel=channel,
                    sender=sender,
                    content=content,
                    source={"issue_number": number, "subject_url": subject_url},
                    reaction_url=f"{prefix}/issues/{number}/reactions",
                )
            )
        return result
