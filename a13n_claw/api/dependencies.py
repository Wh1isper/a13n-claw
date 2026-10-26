"""Trusted operator API composing public Host operations and Claw business services."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from a13n_claw.runtime.service import ClawService


def service(request: Request) -> ClawService:
    value = request.app.state.service
    assert isinstance(value, ClawService)
    return value


async def authenticate(request: Request) -> None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, request.app.state.token):
        raise HTTPException(401, "An operator Bearer token is required")
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Cross-origin API access is disabled")


Application = Annotated[ClawService, Depends(service)]
