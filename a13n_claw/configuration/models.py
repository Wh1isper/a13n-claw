"""Claw-owned settings; native Agent configuration stays in the selected Host tree."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProductModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LarkConnection(ProductModel):
    platform: Literal["lark"] = "lark"
    enabled: bool = True
    app_id: str
    app_secret_env: str = "CLAW_LARK_APP_SECRET"
    domain: Literal["https://open.feishu.cn", "https://open.larksuite.com"] = (
        "https://open.feishu.cn"
    )
    bot_open_id: str
    mentions_only: bool = True


class GitHubConnection(ProductModel):
    platform: Literal["github"] = "github"
    enabled: bool = True
    token_env: str = "CLAW_GITHUB_TOKEN"
    account_login: str = Field(min_length=1)
    api_url: Literal["https://api.github.com"] = "https://api.github.com"
    poll_seconds: int = Field(default=60, ge=10, le=3600)
    delay_seconds: int = Field(default=600, ge=0, le=86400)
    initial_lookback_seconds: int = Field(default=0, ge=0, le=604800)
    mark_read: bool = True
    mentions_only: Literal[False] = False
    reasons: tuple[str, ...] = ()


class ChannelBinding(ProductModel):
    connection: str
    chat_id: str
    topic_id: str | None = None
    enabled: bool = True
    allowed_senders: tuple[str, ...] = ()
    operators: tuple[str, ...] = ()
    mentions_only: bool | None = None
    project_id: str | None = None
    shared_context_acknowledged: bool = False
    progress_updates: bool = False
    reactions: bool = False
    final_reply: bool = False
    decision_prompts: bool = False

    @model_validator(mode="after")
    def acknowledge_context(self) -> ChannelBinding:
        if not self.shared_context_acknowledged:
            raise ValueError("Bindings require acknowledgement of shared Agent context and memory")
        if not self.allowed_senders:
            raise ValueError("Bindings require explicit allowed_senders")
        if not set(self.operators).issubset(self.allowed_senders):
            raise ValueError("Channel operators must be allowed senders")
        return self


class ClawSettings(ProductModel):
    schema_version: Literal[1] = 1
    mode: Literal["one_thread", "per_channel", "per_trigger"] = "one_thread"
    host_configuration: str = "host/a13n-harness-ui.yaml"
    host_data: str = "host/data"
    main_project_id: str = "project-claw-main"
    connections: dict[str, LarkConnection | GitHubConnection] = Field(default_factory=dict)
    channels: dict[str, ChannelBinding] = Field(default_factory=dict)
    reconcile_seconds: float = Field(default=2, ge=0.1, le=60)
    retry_seconds: float = Field(default=30, ge=1, le=3600)
    console: bool = True

    @model_validator(mode="after")
    def binding_references(self) -> ClawSettings:
        keys: set[tuple[str, str, str | None]] = set()
        projects: set[str] = set()
        for binding in self.channels.values():
            if binding.final_reply and self.mode == "one_thread":
                raise ValueError("One Thread final output is private; use explicit Main replies")
            if binding.connection not in self.connections:
                raise ValueError("Channel references an unknown connection")
            if isinstance(self.connections[binding.connection], GitHubConnection):
                import re

                if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", binding.chat_id):
                    raise ValueError("GitHub Channel chat_id must be owner/repository")
                if binding.topic_id is not None:
                    raise ValueError("GitHub repository bindings use no topic_id")
            key = (binding.connection, binding.chat_id, binding.topic_id)
            if key in keys:
                raise ValueError("A qualified Channel has more than one binding")
            keys.add(key)
            if self.mode != "one_thread":
                if not binding.project_id:
                    raise ValueError("Per-Channel and per-trigger bindings require a Host Project")
                if binding.project_id in projects:
                    raise ValueError("Different Channels require distinct Host Projects")
                projects.add(binding.project_id)
        return self


def connection_identity(connection: LarkConnection | GitHubConnection) -> dict[str, str]:
    if isinstance(connection, LarkConnection):
        return {"platform": "lark", "app_id": connection.app_id, "domain": connection.domain}
    return {
        "platform": "github",
        "account": connection.account_login.casefold(),
        "api_url": connection.api_url,
    }


def instance_root(value: Path | None = None) -> Path:
    return (value or Path(os.environ.get("CLAW_ROOT", "~/.a13n-claw"))).expanduser().resolve()


def selected_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def reject_standalone_root(root: Path) -> None:
    if (root / "restore.incomplete").exists():
        raise ValueError("Incomplete restore blocks startup; inspect or restore into a fresh root")
    if (root / "claw.sqlite3").exists():
        raise ValueError(
            "This root contains standalone Claw data. Preserve it and select a fresh --root; "
            "automatic conversion is not supported."
        )


def read_settings(root: Path) -> ClawSettings:
    reject_standalone_root(root)
    path = root / "claw.yaml"
    if not path.is_file():
        raise ValueError("Claw is not initialized; run a13n-claw init --root <directory>")
    return ClawSettings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
