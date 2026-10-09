"""Serve the packaged console; application APIs are not implemented yet."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

CONSOLE_DIRECTORY = Path(__file__).parent / "static" / "console"


def create_app() -> FastAPI:
    if not (CONSOLE_DIRECTORY / "index.html").is_file():
        raise RuntimeError(
            "Console assets are missing. In a source checkout, run `make console-build`; "
            "otherwise reinstall an a13n-claw distribution containing the console."
        )
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.mount("/", StaticFiles(directory=CONSOLE_DIRECTORY, html=True), name="console")
    return app
