"""GitHub listeners coordinate through an actual disposable Redis lease."""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from a13n_claw.service_app import github_intake
from a13n_claw.service_app.github_poll import GitHubSettings


def test_only_one_replica_polls_and_cancellation_releases_lease(monkeypatch):
    url = os.environ.get("CLAW_TEST_REDIS_URL") or os.environ.get("A13N_REDIS__URL")
    if not url:
        pytest.skip("Set CLAW_TEST_REDIS_URL to a disposable Redis instance")
    asyncio.run(exercise_lease(monkeypatch, url))


async def exercise_lease(monkeypatch, url):
    redis = Redis.from_url(url, decode_responses=True)
    runtime = SimpleNamespace(redis=redis)
    intake = github_intake.Intake(
        "org",
        "workspace",
        "claw",
        uuid4().hex,
        GitHubSettings(mode="polling", senders=("*",)),
        "fake",
    )
    starts = asyncio.Queue()
    never = asyncio.Event()

    async def poll(*args):
        await starts.put(True)
        await never.wait()

    monkeypatch.setattr(github_intake, "poll", poll)
    first = asyncio.create_task(github_intake.listen(runtime, intake))
    third = None
    try:
        await asyncio.wait_for(starts.get(), timeout=5)
        await asyncio.wait_for(github_intake.listen(runtime, intake), timeout=5)
        assert starts.empty()
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        third = asyncio.create_task(github_intake.listen(runtime, intake))
        await asyncio.wait_for(starts.get(), timeout=5)
    finally:
        first.cancel()
        if third is not None:
            third.cancel()
        await asyncio.gather(first, *([third] if third is not None else []), return_exceptions=True)
        await redis.aclose()
