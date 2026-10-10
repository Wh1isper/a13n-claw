"""Deterministic browser acceptance server; never used by the installed CLI.

Run: uv run python tests/console_server.py --root build/console-acceptance
Then exercise the built Console against real admission, Harness, SQLite and files.
The model is an explicit FunctionModel fixture, not an external-provider probe.
"""

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from a13n_claw.app import create_app


@asynccontextmanager
async def fixture_models(definition, credential):
    async def model(messages, info):
        prompts = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
            and isinstance(part.content, str)
            and part.content.startswith('[{"attachment_ids"')
        ]
        submitted = json.loads(prompts[-1]) if prompts else []
        text = submitted[-1]["text"] if submitted else "continuation"
        returned = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if text == "slow":
            await asyncio.sleep(60)
        if text == "approval" and not returned:
            yield {
                0: DeltaToolCall(
                    name="retain_artifact",
                    tool_call_id="browser-artifact",
                    json_args=json.dumps(
                        {
                            "path": "/workspace/result.txt",
                            "name": "result.txt",
                            "media_type": "text/plain",
                        }
                    ),
                )
            }
        else:
            yield "Saved response: " + text

    yield FunctionModel(stream_function=model)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    workspace = args.root.resolve() / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "result.txt").write_text("Browser acceptance artifact\n", encoding="utf-8")
    app = create_app(
        data_root=args.root.resolve() / "data", workspace=workspace, models=fixture_models
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
