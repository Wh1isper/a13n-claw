"""One Instance lease, one embedded App, and instance-local adapter lifetimes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from a13n_harness_ui.errors import HarnessUiError

from a13n_claw.business import BusinessStore
from a13n_claw.capability import messaging_integrations
from a13n_claw.host import open_host
from a13n_claw.instance import InstanceLease, operator_token
from a13n_claw.lark import LarkAdapter
from a13n_claw.service import ClawService, database
from a13n_claw.settings import read_settings
from a13n_claw.transport import MessagingAdapter


@asynccontextmanager
async def open_service(
    root: Path,
    *,
    adapters: dict[str, MessagingAdapter] | None = None,
    background: bool = True,
) -> AsyncIterator[ClawService]:
    settings = read_settings(root)
    lease = InstanceLease(root)
    await database(lease.acquire)
    connections: dict[str, MessagingAdapter] = {}
    service: ClawService | None = None
    tasks: list[asyncio.Task[None]] = []
    try:
        await database(operator_token, root)
        store = await database(BusinessStore, root / "business.sqlite3", settings)
        await database(store.restart)
        connections = (
            adapters
            if adapters is not None
            else {
                key: LarkAdapter(key, config)
                for key, config in settings.connections.items()
                if config.enabled
            }
        )
        service = ClawService(settings, store, connections)
        async with open_host(root, settings, integrations=messaging_integrations(service)) as host:
            service.bind(host)
            if settings.mode == "one_thread":
                async with service.lock:
                    try:
                        await service.ensure_target("main", settings.main_project_id)
                    except (ValueError, HarnessUiError):
                        # Keep operator recovery available when a retained target is blocked.
                        service.last_error = "Main target unavailable; inspect /api/targets"
            if background:
                tasks.append(asyncio.create_task(service.run(), name="claw-reconcile"))
                for key, connection in connections.items():
                    tasks.append(
                        asyncio.create_task(
                            listen(service, key, connection), name=f"claw-adapter-{key}"
                        )
                    )
            try:
                yield service
            finally:
                service.stopping = True
                service.wake.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                # Keep the service and sending clients alive while the Host closes its work.
        await database(store.restart)
    finally:
        try:
            await asyncio.gather(*(connection.close() for connection in connections.values()))
        finally:
            lease.close()


async def listen(service: ClawService, key: str, adapter: MessagingAdapter) -> None:
    try:
        await adapter.run(service.receive)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        service.connection_errors[key] = type(exc).__name__
    else:
        if not service.stopping:
            service.connection_errors[key] = "Connection ended; inspect configuration and restart"
