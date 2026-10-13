"""Single-owner runtime service and packaged same-origin Console."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from a13n_harness.environment import EnvironmentError
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.errors import HarnessError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from a13n_claw.api import Services, router
from a13n_claw.attention import ConversationMode
from a13n_claw.configuration import Configuration, load_configuration
from a13n_claw.configuration_files import ConfigurationFile
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
    concurrency: int | None = None,
    models: ModelFactory = provider_model,
    mode: str | None = None,
    console: bool | None = None,
    config_path: Path | None = None,
) -> FastAPI:
    source = (
        (config_path or Path(os.environ.get("CLAW_CONFIG", "~/.a13n-claw/config.yaml")))
        .expanduser()
        .resolve()
    )
    document = load_configuration(source) if source.exists() else Configuration()
    settings = document.server

    def relative(value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else source.parent / path

    root = (
        (
            data_root
            or (
                Path(os.environ["CLAW_DATA_ROOT"])
                if "CLAW_DATA_ROOT" in os.environ
                else relative(settings.data_root or "~/.a13n-claw")
            )
        )
        .expanduser()
        .resolve()
    )
    configured = workspace or (
        Path(os.environ["CLAW_WORKSPACE"])
        if "CLAW_WORKSPACE" in os.environ
        else relative(settings.workspace)
        if settings.workspace
        else None
    )
    concurrency = concurrency if concurrency is not None else settings.concurrency
    console = console if console is not None else settings.console
    selected = resolve_workspace(Path.cwd(), configured, root)
    if not 1 <= concurrency <= 64:
        raise ClawError("concurrency_range", "Concurrency must be between 1 and 64", 422)

    selected_mode = (
        mode if mode is not None else os.environ.get("CLAW_CONVERSATION_MODE", settings.mode)
    )
    if selected_mode is not None and selected_mode not in {"per_channel", "one_thread"}:
        raise ClawError("conversation_mode", "Choose per_channel or one_thread", 422)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        lease = InstanceLease(root)
        await asyncio.to_thread(lease.acquire)
        coordinator: Coordinator | None = None
        deliveries: DeliveryDispatcher | None = None
        try:
            token = await asyncio.to_thread(operator_token, root)
            store = await asyncio.to_thread(Store, root / "claw.sqlite3", initial_mode="one_thread")
            await asyncio.to_thread(store.bootstrap, token_hash(token))
            del token
            await asyncio.to_thread(store.reconcile_startup)
            await asyncio.to_thread(store.coordination.reconcile_startup)
            if selected_mode is not None:
                conversation_mode: ConversationMode = (
                    "one_thread" if selected_mode == "one_thread" else "per_channel"
                )
                await asyncio.to_thread(store.coordination.configure_mode, conversation_mode)
            configuration = ConfigurationFile(store, source, launch=settings)
            await asyncio.to_thread(configuration.initialize)
            runtime = Runtime(store, root, models=models)
            coordinator = Coordinator(store, str(selected), runtime, concurrency=concurrency)
            deliveries = DeliveryDispatcher(Messaging(store))
            app.state.services = Services(
                store, runtime, coordinator, selected, configuration, deliveries
            )
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
    @app.exception_handler(EnvironmentProviderError)
    async def harness_error(
        request: Request, error: HarnessError | EnvironmentError | EnvironmentProviderError
    ):
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
    if console and (CONSOLE_DIRECTORY / "index.html").is_file():
        app.mount("/", StaticFiles(directory=CONSOLE_DIRECTORY, html=True), name="console")
    elif console:
        logger.warning("Console assets unavailable; API remains available. Run make console-build.")
    return app
