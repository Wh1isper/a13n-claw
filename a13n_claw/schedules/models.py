"""File-owned schedule definitions and timezone-aware firing calculations."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from croniter import CroniterBadDateError, croniter
from pydantic import Field, model_validator

from a13n_claw.configuration.models import ProductModel
from a13n_claw.encoding import encode


class Timing(ProductModel):
    kind: Literal["cron", "interval", "once"]
    timezone: str = "UTC"
    cron: str | None = None
    seconds: int | None = Field(default=None, ge=60, le=31_536_000)
    at: datetime | None = None

    @model_validator(mode="after")
    def valid_timing(self) -> Timing:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown schedule timezone") from exc
        if self.kind == "cron":
            if self.cron is None or len(self.cron.split()) != 5 or not croniter.is_valid(self.cron):
                raise ValueError("Cron requires a valid five-field expression")
            if self.seconds is not None or self.at is not None:
                raise ValueError("Cron cannot include interval or once fields")
            try:
                croniter(
                    self.cron, datetime(2024, 1, 1, tzinfo=UTC), max_years_between_matches=8
                ).get_next(datetime)
            except CroniterBadDateError as exc:
                raise ValueError("Cron has no occurrence within eight years") from exc
        elif self.kind == "interval":
            if self.seconds is None or self.cron is not None:
                raise ValueError("Interval requires seconds and no cron expression")
        elif self.at is None or self.seconds is not None or self.cron is not None:
            raise ValueError("Once requires at and no cron or interval")
        if self.at is not None and self.at.utcoffset() is None:
            raise ValueError("at requires an explicit UTC offset")
        return self

    def first(self, now: float) -> float:
        if self.at is not None:
            return self.at.timestamp()
        return self.after(now)

    def after(self, instant: float) -> float:
        if self.kind == "once":
            raise ValueError("One-shot schedules have no next occurrence")
        if self.kind == "interval":
            assert self.seconds is not None
            return instant + self.seconds
        assert self.cron is not None
        base = datetime.fromtimestamp(instant, ZoneInfo(self.timezone))
        return croniter(self.cron, base, max_years_between_matches=8).get_next(datetime).timestamp()

    def latest(self, due: float, now: float) -> float:
        if self.kind == "once":
            return due
        if self.kind == "interval":
            assert self.seconds is not None
            return due + math.floor((now - due) / self.seconds) * self.seconds
        assert self.cron is not None
        base = datetime.fromtimestamp(now + 0.001, ZoneInfo(self.timezone))
        return max(
            due,
            croniter(self.cron, base, max_years_between_matches=8).get_prev(datetime).timestamp(),
        )


class JobDestination(ProductModel):
    channel_id: str = Field(min_length=1)
    reply_to: str | None = None


class ScheduleTask(ProductModel):
    name: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=100_000)
    enabled: bool = True
    timing: Timing
    overlap: Literal["queue", "skip"] = "queue"
    missed: Literal["skip", "latest"] = "latest"
    grace_seconds: int = Field(default=300, ge=1, le=86400)
    notification: Literal["always", "error", "never"] = "always"
    destination: JobDestination | None = None


class ScheduleSource(ProductModel):
    connection: str
    app_id: str
    domain: str
    chat_id: str
    topic_id: str | None = None


class ScheduleDefinition(ScheduleTask):
    project_id: str = Field(pattern=r"^project-")
    agent_id: str | None = None
    model_id: str | None = None
    # API writes use operator. IM writes capture the qualified source and actor.
    author: str = Field(default="operator", min_length=1, max_length=256)
    source_channel: str | None = None
    source: ScheduleSource | None = None

    @model_validator(mode="after")
    def valid_output(self) -> ScheduleDefinition:
        if self.notification != "never" and self.destination is None:
            raise ValueError("Notifications require an explicit destination")
        if self.author != "operator" and (self.source_channel is None or self.source is None):
            raise ValueError("IM-created schedules require their qualified source Channel")
        return self

    @property
    def revision(self) -> str:
        return hashlib.sha256(encode(self.model_dump(mode="json")).encode()).hexdigest()


class ScheduleCatalog(ProductModel):
    schema_version: Literal[1] = 1
    schedules: dict[str, ScheduleDefinition] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_ids(self) -> ScheduleCatalog:
        import re

        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", key) for key in self.schedules):
            raise ValueError("Schedule IDs must be 1-80 letters, numbers, hyphens or underscores")
        return self


def read_catalog(path: Path) -> tuple[ScheduleCatalog, str | None]:
    if not path.exists():
        return ScheduleCatalog(), None
    data = path.read_bytes()
    return ScheduleCatalog.model_validate(yaml.safe_load(data)), hashlib.sha256(data).hexdigest()


def write_catalog(path: Path, catalog: ScheduleCatalog, expected_digest: str | None) -> str:
    """Caller serializes service writers; an external edit is checked before atomic replacement."""
    _, current = read_catalog(path)
    if current != expected_digest:
        raise ValueError("Schedule source changed; refetch before editing")
    data = yaml.safe_dump(catalog.model_dump(mode="json"), sort_keys=False).encode()
    descriptor, temporary = tempfile.mkstemp(prefix=".schedules-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if read_catalog(path)[1] != expected_digest:
            raise ValueError("Schedule source changed; refetch before editing")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return hashlib.sha256(data).hexdigest()


def utc_text(instant: float) -> str:
    return datetime.fromtimestamp(instant, UTC).isoformat()
