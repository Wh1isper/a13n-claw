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
    connections: dict[str, LarkConnection] = Field(default_factory=dict)
    channels: dict[str, ChannelBinding] = Field(default_factory=dict)
    reconcile_seconds: float = Field(default=2, ge=0.1, le=60)
    retry_seconds: float = Field(default=30, ge=1, le=3600)
    console: bool = True

    @model_validator(mode="after")
    def binding_references(self) -> ClawSettings:
        keys: set[tuple[str, str, str | None]] = set()
        projects: set[str] = set()
        for binding in self.channels.values():
            if binding.connection not in self.connections:
                raise ValueError("Channel references an unknown connection")
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


def instance_root(value: Path | None = None) -> Path:
    return (value or Path(os.environ.get("CLAW_ROOT", "~/.a13n-claw"))).expanduser().resolve()


def selected_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def reject_standalone_root(root: Path) -> None:
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
