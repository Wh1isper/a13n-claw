"""Durable coordination values shared by application, tools and Console."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from a13n_claw.domain import EnvironmentVariable, ResourceId, Value, endpoint

ConversationMode = Literal["per_channel", "one_thread"]
Disposition = Literal["pending", "deferred", "handled", "ignored"]


class ProcessingChange(Value):
    expected_version: int = Field(ge=1)
    disposition: Disposition
    note: str = Field(min_length=1, max_length=10000)
    dependency_run_id: str | None = None
    due_at: str | None = None
    human_key: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("due_at")
    @classmethod
    def utc_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("A deferred time requires an explicit timezone")
        return parsed.astimezone(UTC).isoformat()

    @model_validator(mode="after")
    def dependency(self) -> ProcessingChange:
        count = sum(x is not None for x in (self.dependency_run_id, self.due_at, self.human_key))
        if count != (1 if self.disposition == "deferred" else 0):
            raise ValueError("Only deferred work requires exactly one reactivation condition")
        return self


class ChannelPolicy(Value):
    platform: ResourceId
    account: ResourceId
    channel: str = Field(min_length=1, max_length=500)
    enabled: bool = True
    inbound: bool = True
    outbound: bool = False
    # The integration client is authenticated independently of the external sender.
    ingress_actor_id: ResourceId
    execution_actor_id: ResourceId = "operator"
    allowed_senders: tuple[str, ...] = Field(min_length=1, max_length=1000)
    group: bool = False
    require_addressed: bool = True
    shared_context_acknowledged: bool = False
    profile_id: ResourceId | None = None
    delivery_url: str | None = None
    credential_env: EnvironmentVariable | None = None

    @field_validator("delivery_url")
    @classmethod
    def delivery_endpoint(cls, value: str | None) -> str | None:
        return endpoint(value) if value is not None else None

    @model_validator(mode="after")
    def destination(self) -> ChannelPolicy:
        if self.outbound and not self.delivery_url:
            raise ValueError("Outbound delivery requires a configured HTTP transport endpoint")
        return self


class InboundMessage(Value):
    event_id: str = Field(min_length=1, max_length=200)
    sender: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=1_000_000)
    addressed: bool = False
    # Retained application asset identities, not arbitrary remote URLs.
    attachment_ids: tuple[str, ...] = Field(default=(), max_length=20)
    unavailable_attachments: tuple[str, ...] = Field(default=(), max_length=20)


class WorkerCreate(Value):
    request_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    profile_id: ResourceId
    text: str = Field(min_length=1, max_length=1_000_000)


class DeliveryRequest(Value):
    request_id: str = Field(min_length=1, max_length=200)
    channel_id: ResourceId
    text: str = Field(min_length=1, max_length=1_000_000)


class ProcessingControl(Value):
    expected_version: int = Field(ge=1)
    paused: bool
    retry: bool = False


class DeliveryResolution(Value):
    expected_version: int = Field(ge=1)
    outcome: Literal["sent", "not_sent"]
    note: str = Field(min_length=1, max_length=10000)
    retry: bool = False

    @model_validator(mode="after")
    def known_retry(self) -> DeliveryResolution:
        if self.retry and self.outcome != "not_sent":
            raise ValueError("Only a confirmed not-sent delivery can be retried")
        return self
