"""Authenticated operator routes, grouped by their owning product surface."""

from fastapi import APIRouter, Depends

from a13n_claw.api import configuration, messaging, operations, schedules, threads
from a13n_claw.api.dependencies import authenticate

router = APIRouter(prefix="/api", dependencies=[Depends(authenticate)])
for child in (operations, threads, configuration, messaging, schedules):
    router.include_router(child.router)
