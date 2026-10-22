"""Bounded operator SSE presentation over the Host's public focused watch."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from a13n_harness_ui.app import HarnessUiApp
from a13n_harness_ui.errors import HarnessUiError


def frame(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


async def thread_events(host: HarnessUiApp, thread_id: str) -> AsyncIterator[str]:
    """Every connection bootstraps anew; EOF never means execution completion."""
    try:
        async with host.watch_thread(root_thread_id=thread_id) as watch:
            yield frame("snapshot", watch.snapshot.model_dump_json())
            if watch.root_stream is not None:
                for batch in watch.root_stream.batches():
                    for event in batch:
                        yield frame("replay", event.model_dump_json())
            while True:
                try:
                    async with asyncio.timeout(15):
                        event = await anext(watch.events)
                except TimeoutError:
                    yield ": keepalive\n\n"
                except StopAsyncIteration:
                    break
                else:
                    yield frame("observation", event.model_dump_json())
        yield frame("refetch", '{"reason":"host_stream_closed"}')
    except HarnessUiError as exc:
        # Gaps, changed snapshots and Host shutdown all require a fresh supported view.
        yield frame("refetch", json.dumps({"reason": exc.code}))
