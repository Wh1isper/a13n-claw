"""File-owned product configuration and explicit adapter generation replacement."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from a13n_claw.async_utils import database, settle_on_cancel
from a13n_claw.github import GitHubAdapter
from a13n_claw.lark import LarkAdapter
from a13n_claw.settings import ClawSettings, LarkConnection, connection_identity
from a13n_claw.transport import MessagingAdapter

if TYPE_CHECKING:
    from a13n_claw.service import ClawService


def source(path: Path) -> dict[str, str]:
    data = path.read_bytes()
    return {"content": data.decode("utf-8"), "digest": hashlib.sha256(data).hexdigest()}


def parse(content: str) -> ClawSettings:
    try:
        return ClawSettings.model_validate(yaml.safe_load(content))
    except yaml.YAMLError as exc:
        raise ValueError("Invalid product YAML") from exc


def save_source(path: Path, content: str, expected_digest: str) -> str:
    """Serialized by the service; recheck external editors before atomic replacement."""
    if source(path)["digest"] != expected_digest:
        raise ValueError("Product source changed; refetch and preserve your draft")
    data = content.encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".claw-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if source(path)["digest"] != expected_digest:
            raise ValueError("Product source changed; refetch and preserve your draft")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return hashlib.sha256(data).hexdigest()


class ProductConfiguration:
    def __init__(self, service: ClawService):
        self.service = service
        self.path = service.store.path.parent / "claw.yaml"
        self.accepted_digest: str | None = None
        self.error: str | None = None
        self.background = False
        self.tasks: list[asyncio.Task[None]] = []

    async def view(self) -> dict[str, Any]:
        saved = await database(source, self.path)
        return {
            **saved,
            "accepted_digest": self.accepted_digest,
            "pending_reload": saved["digest"] != self.accepted_digest,
            "accepted": self.service.settings.model_dump(mode="json"),
            "error": self.error,
            "host_capture": "Host captures execution configuration at admission",
        }

    async def validate(self, candidate: ClawSettings) -> None:
        current = self.service.settings
        for field in ("mode", "main_project_id", "host_configuration", "host_data", "console"):
            if getattr(candidate, field) != getattr(current, field):
                raise ValueError(f"{field} cannot change through live product publication")
        for key, config in candidate.connections.items():
            previous = current.connections.get(key)
            if previous is not None and connection_identity(config) != connection_identity(
                previous
            ):
                raise ValueError("Connection identity changed; use a new connection ID")
        projects = {project.project_id for project in await self.service.host.projects()}
        required = {candidate.main_project_id} if candidate.mode == "one_thread" else set()
        required.update(
            binding.project_id
            for binding in candidate.channels.values()
            if binding.project_id is not None
        )
        missing = required - projects
        if missing:
            raise ValueError(
                "Product references unavailable Host Projects: " + ", ".join(sorted(missing))
            )
        # A retained per-Channel Thread must not silently acquire another Project.
        for key, binding in candidate.channels.items():
            target = await database(self.service.store.target, f"channel:{key}")
            if target is not None and target["project_id"] != binding.project_id:
                raise ValueError(f"Channel {key} has a retained target in another Project")

    async def save(self, content: str, expected_digest: str) -> dict[str, Any]:
        async with self.service.lock:
            await self.validate(parse(content))
            await database(save_source, self.path, content, expected_digest)
            return await self.view()

    async def reload(self, expected_digest: str) -> dict[str, Any]:
        async with self.service.lock:
            if self.service.stopping:
                raise ValueError("Claw is shutting down")
            try:
                saved = await database(source, self.path)
                if saved["digest"] != expected_digest:
                    raise ValueError("Product source changed; refetch before reload")
                candidate = parse(saved["content"])
                await self.validate(candidate)
                # Cancellation cannot leave stopped old listeners with an unaccepted generation.
                await settle_on_cancel(self.replace(candidate, saved["digest"]))
            except Exception as exc:
                self.error = type(exc).__name__
                raise
            return await self.view()

    async def build(self, settings: ClawSettings) -> dict[str, MessagingAdapter]:
        adapters: dict[str, MessagingAdapter] = {}
        try:
            for key, config in settings.connections.items():
                if config.enabled:
                    adapters[key] = (
                        LarkAdapter(key, config)
                        if isinstance(config, LarkConnection)
                        else GitHubAdapter(key, config, self.service.store, settings.channels)
                    )
        except BaseException:
            await self.close(adapters)
            raise
        return adapters

    def start(self) -> None:
        if self.background:
            self.tasks = [
                asyncio.create_task(self.listen(key, adapter), name=f"claw-adapter-{key}")
                for key, adapter in self.service.adapters.items()
            ]

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    async def close(self, adapters: dict[str, MessagingAdapter]) -> None:
        results = await asyncio.gather(
            *(adapter.close() for adapter in adapters.values()), return_exceptions=True
        )
        for key, result in zip(adapters, results, strict=True):
            if isinstance(result, BaseException):
                self.service.connection_errors[key] = (
                    "Adapter cleanup failed: " + type(result).__name__
                )

    async def replace(self, candidate: ClawSettings, digest: str) -> None:
        adapters = await self.build(candidate)
        # Listeners can be inside receive(); join them before taking the intake lock.
        await self.stop()
        await self.service.sends_idle.wait()
        async with self.service.intake_lock:
            previous = self.service.adapters
            self.service.settings = candidate
            self.service.adapters = adapters
            self.accepted_digest = digest
            self.error = None
            self.service.connection_errors.clear()
            await self.close(previous)
            self.start()
            self.service.wake.set()

    async def listen(self, key: str, adapter: MessagingAdapter) -> None:
        try:
            await adapter.run(self.service.receive)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.service.connection_errors[key] = type(exc).__name__
        else:
            if not self.service.stopping:
                self.service.connection_errors[key] = (
                    "Connection ended; inspect configuration and reload"
                )
