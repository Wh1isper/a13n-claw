"""Application values; executable objects and credentials never enter these records."""

from __future__ import annotations

import hashlib
import json
import ntpath
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from a13n_harness import AgentSpec
from a13n_harness.tools.permissions import ToolPermissions
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class ClawError(Exception):
    """An actionable application rejection, safe to expose to authorized callers."""

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


ResourceId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
EnvironmentVariable = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]


def endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Use an HTTP(S) endpoint without embedded credentials, query or fragment")
    return value


class ModelDefinition(Value):
    provider: Literal["openai", "anthropic", "google"]
    model_name: str = Field(min_length=1)
    credential_env: EnvironmentVariable
    base_url: str | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        return endpoint(value) if value is not None else None


class EnvironmentDefinition(Value):
    kind: Literal["local", "docker"] = "local"
    image: str | None = None
    user: str | None = None
    retention: Literal["keep", "stop"] = "keep"
    files: Literal["none", "read", "write"] = "read"
    shell: bool = False

    @model_validator(mode="after")
    def docker_image(self) -> EnvironmentDefinition:
        if self.kind == "docker" and not self.image:
            raise ValueError("Docker environments require an explicit image")
        if self.kind == "local" and (self.image or self.user):
            raise ValueError("Container settings are not local environment settings")
        return self


class ProfileAgentSpec(AgentSpec):
    """Harness behavior with infrastructure selected only by Claw resources."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def installed_behavior(self) -> ProfileAgentSpec:
        if self.model is not None or self.capabilities:
            raise ValueError("Select models and installed capabilities through Profile resources")
        if self.deps_schema is not None or self.output_schema is not None:
            raise ValueError("Claw owns the execution dependency and output contracts")
        if self.model_settings is not None:
            supported = {
                "max_tokens",
                "temperature",
                "top_p",
                "timeout",
                "parallel_tool_calls",
                "seed",
                "presence_penalty",
                "frequency_penalty",
                "stop_sequences",
                "openai_reasoning_effort",
                "anthropic_thinking",
                "google_thinking_config",
            }
            if set(self.model_settings) - supported:
                raise ValueError(
                    "Unsupported model settings; credentials and headers belong to resources"
                )
        return self


class ProfileDefinition(Value):
    model_id: ResourceId
    environment_id: ResourceId
    agent: ProfileAgentSpec = Field(default_factory=ProfileAgentSpec)
    permissions: ToolPermissions = Field(default_factory=ToolPermissions)
    skills: tuple[ResourceId, ...] = ()
    mcp_servers: tuple[ResourceId, ...] = ()
    child_profiles: tuple[ResourceId, ...] = ()
    max_requests: int = Field(default=50, ge=1, le=1000)

    @field_validator("skills", "mcp_servers", "child_profiles")
    @classmethod
    def unique_references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Resource selections must be unique")
        return value


class SkillDefinition(Value):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    description: str = Field(min_length=1, max_length=16384)
    instructions: str = Field(min_length=1, max_length=1_000_000)
    files: dict[str, str] = Field(default_factory=dict, max_length=256)

    @field_validator("files")
    @classmethod
    def portable_files(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            path = PurePosixPath(name)
            if (
                not name
                or not path.parts
                or any(ntpath.isreserved(part) for part in path.parts)
                or path.is_absolute()
                or ".." in path.parts
                or str(path) != name
                or "\\" in name
                or ":" in name
                or "\x00" in name
                or path.parts[0].casefold() == "skill.md"
            ):
                raise ValueError("Skill files must be relative portable paths other than SKILL.md")
        folded = {name.casefold() for name in value}
        if len(folded) != len(value) or any(
            str(parent).casefold() in folded
            for name in value
            for parent in PurePosixPath(name).parents
            if str(parent) != "."
        ):
            raise ValueError("Skill paths must not collide on supported filesystems")
        if sum(len(text.encode()) for text in value.values()) > 8_000_000:
            raise ValueError("Skill supporting files exceed 8 MB")
        return value


class MCPDefinition(Value):
    url: str
    header_env: dict[str, EnvironmentVariable] = Field(default_factory=dict)
    allowed_tools: tuple[str, ...] | None = None
    description: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return endpoint(value)

    @field_validator("header_env")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        import re

        if any(not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) for name in value):
            raise ValueError("Invalid HTTP header name")
        if len({name.casefold() for name in value}) != len(value):
            raise ValueError("HTTP header names must be unique")
        return value


class InstanceDefaults(Value):
    profile_id: ResourceId | None = None


RESOURCE_TYPES: dict[str, type[Value]] = {
    "model": ModelDefinition,
    "profile": ProfileDefinition,
    "environment": EnvironmentDefinition,
    "skill": SkillDefinition,
    "mcp": MCPDefinition,
    "defaults": InstanceDefaults,
}


def validate_resource(kind: str, content: dict[str, JsonValue]) -> dict[str, JsonValue]:
    definition = RESOURCE_TYPES.get(kind)
    if definition is None:
        raise ClawError("resource_kind", "Unsupported resource kind", 422)
    return definition.model_validate(content).model_dump(mode="json")


class Resource(Value):
    id: str
    kind: str
    version: int
    content: dict[str, JsonValue]
    retired: bool = False


class ResourceChange(Value):
    id: ResourceId
    kind: str
    content: dict[str, JsonValue]
    expected_version: int = Field(ge=0)
    retired: bool = False

    @model_validator(mode="after")
    def valid_definition(self) -> ResourceChange:
        validate_resource(self.kind, self.content)
        if self.kind == "defaults" and self.id != "instance":
            raise ValueError("Instance defaults use the 'instance' identity")
        return self


class TargetRecord(Value):
    id: str
    thread_id: str
    generation: str
    definition: dict[str, JsonValue]
    provider_state: dict[str, JsonValue] | None
    status: Literal["unprepared", "preparing", "ready", "stopped", "unavailable", "removed"]
    operation_id: str | None
    error: str | None
    updated_at: str


class Composition(Value):
    profile: Resource
    model: Resource
    environment: Resource
    skills: tuple[Resource, ...] = ()
    mcp_servers: tuple[Resource, ...] = ()
    children: tuple[Composition, ...] = ()
    workspace: str


class Principal(Value):
    id: ResourceId
    version: int = Field(default=1, ge=1)
    admin: bool = False
    enabled: bool = True
    thread_ids: tuple[str, ...] = ()
    profile_ids: tuple[str, ...] = ()
    actions: tuple[Literal["read", "submit", "cancel", "decide", "create"], ...] = ()

    def require(self, action: str, thread_id: str | None = None) -> None:
        if not self.enabled:
            raise ClawError("access_revoked", "This client is disabled", 403)
        if self.admin:
            return
        if action not in self.actions or (
            thread_id is not None and thread_id not in self.thread_ids
        ):
            raise ClawError("forbidden", "The client is not permitted to perform this action", 403)

    def use_profile(self, profile_id: str) -> None:
        if not self.enabled or (not self.admin and profile_id not in self.profile_ids):
            raise ClawError("profile_forbidden", "The client cannot use this profile", 403)


RunStatus = Literal[
    "queued", "preparing", "running", "waiting", "completed", "failed", "cancelled", "interrupted"
]
InputStatus = Literal[
    "pending",
    "steering",
    "delivering",
    "delivered",
    "incorporated",
    "held",
    "blocked",
    "unapplied",
    "uncertain",
]
TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})


MAX_ASSET_BYTES = 8 * 1024 * 1024


class AssetRecord(Value):
    id: str
    thread_id: str
    run_id: str | None
    kind: Literal["attachment", "artifact"]
    name: str
    media_type: str
    size: int
    sha256: str
    created_at: str


class InputRequest(Value):
    request_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=1_000_000)
    source: str = Field(default="console", min_length=1, max_length=200)
    attachment_ids: tuple[str, ...] = Field(default=(), max_length=20)
    separate_run: bool = False
    expected_thread_version: int | None = None
    profile_id: str | None = None


class InputReceipt(Value):
    id: str
    sequence: int
    thread_id: str
    run_id: str
    actor_id: str
    request_id: str
    text: str
    source: str
    disposition: InputStatus
    created_at: str
    attachment_ids: tuple[str, ...] = ()


class ThreadRecord(Value):
    id: str
    title: str
    profile_id: str
    version: int
    checkpoint_id: str | None
    parent_thread_id: str | None
    parent_run_id: str | None
    fork_checkpoint_id: str | None = None
    archived: bool
    created_at: str


class DelegationRecord(Value):
    id: str
    parent_run_id: str
    child_run_id: str
    child_thread_id: str
    request_id: str
    cancel_policy: Literal["keep", "cancel"]
    notify: bool
    delivery_input_id: str | None
    delivery_error: str | None
    status: RunStatus
    output: str | None
    error: str | None


class RunRecord(Value):
    id: str
    thread_id: str
    actor_id: str
    status: RunStatus
    composition: Composition
    checkpoint_id: str | None
    recovery_of: str | None
    recovery_required: bool
    reconciled_at: str | None = None
    cancel_requested: bool
    output: str | None
    error: str | None
    owner: str | None
    created_at: str
    finished_at: str | None


def new_id(kind: str) -> str:
    return f"{kind}_{uuid4().hex}"


def now() -> str:
    return datetime.now(UTC).isoformat()


def canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()
