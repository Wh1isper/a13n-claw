"""Claw's listener; execution and configuration live in the embedded Python App."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from a13n_harness_ui.errors import HarnessUiError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from a13n_claw.api import router
from a13n_claw.lifecycle import open_service
from a13n_claw.settings import instance_root, read_settings

CONSOLE_DIRECTORY = Path(__file__).parent / "static" / "console"
logger = logging.getLogger("a13n_claw.app")


def create_app(*, root: Path | None = None, console: bool = True) -> FastAPI:
    selected = instance_root(root)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with open_service(selected) as service:
            app.state.service = service
            app.state.token = (selected / "operator.token").read_text().strip()
            yield

    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.exception_handler(HarnessUiError)
    async def host_error(request: Request, error: HarnessUiError):
        return JSONResponse(
            status_code=409, content={"error": {"code": error.code, "message": str(error)}}
        )

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request: Request, error: Exception):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Invalid fields; preserve your draft and check the request",
                }
            },
        )

    @app.exception_handler(ValueError)
    async def business_error(request: Request, error: ValueError):
        return JSONResponse(
            status_code=409, content={"error": {"code": "business_conflict", "message": str(error)}}
        )

    @app.middleware("http")
    async def response_policy(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            logger.error("Claw request failed; response details withheld")
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "request_unknown",
                        "message": "Reconcile the original operation before retrying",
                    }
                },
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "frame-ancestors 'none'; base-uri 'none'"
            )
        return response

    app.include_router(router)
    if console and read_settings(selected).console and (CONSOLE_DIRECTORY / "index.html").is_file():
        app.mount("/", StaticFiles(directory=CONSOLE_DIRECTORY, html=True), name="console")
    return app
