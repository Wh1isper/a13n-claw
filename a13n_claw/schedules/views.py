"""Read-only scheduler projections and previews using the canonical timing rules."""

from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from a13n_claw.configuration.models import ProductModel
from a13n_claw.schedules.models import ScheduleCatalog, Timing


class OccurrenceSummary(ProductModel):
    id: str
    status: str
    due: float
    created: float
    thread_id: str | None
    receipt_id: str | None
    detail: str


class ScheduleStatus(ProductModel):
    state: Literal["paused", "pending_discovery", "scheduled", "finished"]
    next_due: datetime | None
    latest: OccurrenceSummary | None


class ScheduleCatalogView(ProductModel):
    digest: str | None
    catalog: ScheduleCatalog
    error: str | None


class ScheduleView(ScheduleCatalogView):
    status: dict[str, ScheduleStatus]


class Occurrence(OccurrenceSummary):
    schedule_id: str
    request_key: str
    definition: str
    destination: str | None
    result: str | None
    next_check: float


class TimingPreview(ProductModel):
    timing: Timing
    after: datetime = Field(default_factory=lambda: datetime.now(UTC))
    count: int = Field(default=5, ge=1, le=10)


class TimingPreviewResult(ProductModel):
    timezone: str
    instants: tuple[datetime, ...]


def preview(request: TimingPreview) -> TimingPreviewResult:
    if request.after.utcoffset() is None:
        raise ValueError("Preview start requires an explicit UTC offset")
    timing = request.timing
    zone = ZoneInfo(timing.timezone)
    instant = timing.first(request.after.timestamp())
    instants = []
    for _ in range(request.count):
        instants.append(datetime.fromtimestamp(instant, zone))
        if timing.kind == "once":
            break
        instant = timing.after(instant)
    return TimingPreviewResult(timezone=timing.timezone, instants=tuple(instants))
