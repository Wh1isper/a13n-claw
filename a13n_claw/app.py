"""Single-owner runtime service and packaged same-origin Console."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from a13n_harness.environment import EnvironmentError
from a13n_harness.errors import HarnessError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from a13n_claw.api import Services, router
from a13n_claw.attention import ConversationMode
from a13n_claw.coordinator import Coordinator
from a13n_claw.domain import ClawError
from a13n_claw.instance import InstanceLease, operator_token, resolve_workspace, token_hash
from a13n_claw.messaging import DeliveryDispatcher, Messaging
from a13n_claw.runtime import ModelFactory, Runtime, provider_model
from a13n_claw.storage import Store

CONSOLE_DIRECTORY = Path(__file__).parent / "static" / "console"
logger = logging.getLogger("a13n_claw.app")


def create_app(
    *,
    data_root: Path | None = None,
    workspace: Path | None = None,
    concurrency: int = 4,
    models: ModelFactory = provider_model,
    mode: ConversationMode | None = None,
) -> FastAPI:
    if not (CONSOLE_DIRECTORY / "index.html").is_file():
        raise RuntimeError(
            "Console assets are missing. In a source checkout, run `make console-build`; "
            "otherwise reinstall an a13n-claw distribution containing the console."
        )
    root = (
        (data_root or Path(os.environ.get("CLAW_DATA_ROOT", "~/.a13n-claw"))).expanduser().resolve()
    )
    configured = workspace or (
        Path(os.environ["CLAW_WORKSPACE"]) if "CLAW_WORKSPACE" in os.environ else None
    )
    selected = resolve_workspace(Path.cwd(), configured, root)
    if not 1 <= concurrency <= 64:
        raise ClawError("concurrency_range", "Concurrency must be between 1 and 64", 422)

    selected_mode = mode or os.environ.get("CLAW_CONVERSATION_MODE", "per_channel")
    if selected_mode not in {"per_channel", "one_thread"}:
        raise ClawError("conversation_mode", "Choose per_channel or one_thread", 422)

    conversation_mode: ConversationMode = (
        "one_thread" if selected_mode == "one_thread" else "per_channel"
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lease = InstanceLease(root)
        await asyncio.to_thread(lease.acquire)
        coordinator: Coordinator | None = None
        deliveries: DeliveryDispatcher | None = None
        try:
            token = await asyncio.to_thread(operator_token, root)
            store = await asyncio.to_thread(Store, root / "claw.sqlite3")
            await asyncio.to_thread(store.bootstrap, token_hash(token))
            del token
            await asyncio.to_thread(store.reconcile_startup)
            await asyncio.to_thread(store.coordination.reconcile_startup)
            await asyncio.to_thread(store.coordination.configure_mode, conversation_mode)
            runtime = Runtime(store, root, models=models)
            coordinator = Coordinator(store, str(selected), runtime, concurrency=concurrency)
            deliveries = DeliveryDispatcher(Messaging(store))
            app.state.services = Services(store, runtime, coordinator, selected, deliveries)
            await coordinator.start()
            deliveries.start()
            logger.info("Operator access token file: %s", root / "operator.token")
            yield
        finally:
            try:
                if coordinator is not None:
                    await coordinator.close()
                if deliveries is not None:
                    await deliveries.close()
            finally:
                await asyncio.to_thread(lease.close)

    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.exception_handler(ClawError)
    async def application_error(request: Request, error: ClawError):
        return JSONResponse(
            status_code=error.status, content={"error": {"code": error.code, "message": str(error)}}
        )

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request: Request, error: Exception):
        # Pydantic's default errors include the submitted input, potentially a credential.
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Invalid request fields; preserve your draft and check the values",
                }
            },
        )

    @app.exception_handler(HarnessError)
    @app.exception_handler(EnvironmentError)
    async def harness_error(request: Request, error: HarnessError | EnvironmentError):
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": error.code,
                    "message": "Request conflicts with saved state or current environment",
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        logger.error("Application request failed; response details withheld")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "request_failed",
                    "message": "Request could not complete; reconcile saved state before retrying",
                }
            },
        )

    @app.middleware("http")
    async def response_policy(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as error:
            # Do not let the ASGI server log exception reprs containing provider secrets.
            response = await unexpected_error(request, error)
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
    app.mount("/", StaticFiles(directory=CONSOLE_DIRECTORY, html=True), name="console")
    return app
