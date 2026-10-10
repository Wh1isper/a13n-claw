"""Cancellation boundaries for work that must settle before releasing ownership."""

import asyncio
from collections.abc import Awaitable


async def settle_on_cancel[T](work: Awaitable[T]) -> T:
    """Join in-flight work before propagating native Task cancellation."""
    task = asyncio.ensure_future(work)
    cancelled: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = exc
    result = task.result()
    if cancelled is not None:
        raise cancelled
    return result
