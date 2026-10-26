"""Disposable real Host/server for Console browser tests; never contacts model providers."""

import argparse
import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import uvicorn
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from pydantic_ai.models.test import TestModel

from a13n_claw.app import create_app
from a13n_claw.configuration.bootstrap import initialize


async def test_model(self, model_id, *, thread_id):
    return TestModel(call_tools=[], custom_output_text="Recorded browser test reply")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="claw-console-") as directory:
        home = Path(directory)
        root = home / "instance"
        workspace = home / "workspace"
        workspace.mkdir()
        os.environ.update(
            HOME=directory, USERPROFILE=directory, A13N_OFFICIAL_MODELS_AUTO_UPDATE="0"
        )
        asyncio.run(initialize(root, workspace, model_route="openai:gpt-4o"))
        # Public, disposable fixture token; this server binds only loopback.
        (root / "operator.token").write_text("console-browser-test-token-not-for-production\n")
        with patch.object(HarnessUiModelResolver, "resolve", test_model):
            uvicorn.run(
                create_app(root=root), host="127.0.0.1", port=args.port, log_level="warning"
            )


if __name__ == "__main__":
    main()
