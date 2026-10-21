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
from a13n_claw.product_configuration import parse, source
from a13n_claw.service import ClawService, database
from a13n_claw.settings import reject_standalone_root
from a13n_claw.transport import MessagingAdapter


@asynccontextmanager
async def open_service(
    root: Path,
    *,
    adapters: dict[str, MessagingAdapter] | None = None,
    background: bool = True,
) -> AsyncIterator[ClawService]:
    lease = InstanceLease(root)
    await database(lease.acquire)
    service: ClawService | None = None
    tasks: list[asyncio.Task[None]] = []
    try:
        reject_standalone_root(root)
        saved = await database(source, root / "claw.yaml")
        settings = parse(saved["content"])
        await database(operator_token, root)
        store = await database(BusinessStore, root / "business.sqlite3", settings)
        await database(store.restart)
        service = ClawService(settings, store, adapters if adapters is not None else {})
        if adapters is None:
            service.adapters = await service.configuration.build(settings)
        service.configuration.accepted_digest = saved["digest"]
        service.configuration.background = background
        await database(service.scheduler.store.restart)
        try:
            await service.scheduler.reload()
        except (ValueError, OSError) as exc:
            # Leave diagnostics and config repair usable without silently dispatching stale jobs.
            service.scheduler.error = type(exc).__name__
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
                service.configuration.start()
            try:
                yield service
            finally:
                service.stopping = True
                service.wake.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await service.configuration.stop()
                # Keep sending clients alive while the Host closes its work.
        await database(store.restart)
    finally:
        try:
            if service is not None:
                await service.configuration.close(service.adapters)
        finally:
            lease.close()
