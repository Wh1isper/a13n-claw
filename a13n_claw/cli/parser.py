"""Headless setup and explicit operator requests to the single running Claw service."""

from __future__ import annotations

import argparse
from pathlib import Path

from a13n_claw import __version__


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--root", type=Path, help="Instance directory (CLAW_ROOT or ~/.a13n-claw)")
    common.add_argument("--url", help="Running Claw URL (CLAW_URL or http://127.0.0.1:8080)")
    common.add_argument(
        "--token-file", type=Path, help="Operator token file; never pass the token as an argument"
    )
    cli = argparse.ArgumentParser(
        prog="a13n-claw",
        parents=[common],
        description="Claw: messaging and headless operations over an embedded Harness UI App.",
    )
    cli.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = cli.add_subparsers(dest="command")
    init = commands.add_parser(
        "init", parents=[common], help="Initialize a fresh isolated Instance"
    )
    init.add_argument("--workspace", required=True, type=Path)
    init.add_argument("--model", help="Native model route, for example openai:gpt-4o")
    init.add_argument("--model-key-env", default="CLAW_MODEL_API_KEY")
    init.add_argument("--model-base-url")
    init.add_argument(
        "--executor", help="Persistent Envd HTTP endpoint; token: CLAW_EXECUTOR_TOKEN"
    )
    init.add_argument(
        "--executor-workspace", default="/workspace", help="Executor working directory"
    )
    serve = commands.add_parser(
        "serve",
        parents=[common],
        help="Run the Host, messaging connections, API and Hello World page",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--no-console", action="store_true")
    commands.add_parser(
        "reset-operator", parents=[common], help="Rotate the token while the Instance is stopped"
    )
    commands.add_parser("status", parents=[common])
    commands.add_parser("diagnostics", parents=[common])
    retention = commands.add_parser("retention", parents=[common])
    retention.add_argument("--older-than-days", type=int, required=True)
    retention.add_argument(
        "--apply", action="store_true", help="Prune eligible content; default is preview"
    )
    snapshot = commands.add_parser("backup", parents=[common], help="Snapshot a stopped Instance")
    snapshot.add_argument("--destination", type=Path, required=True)
    snapshot.add_argument("--acknowledge-external-state", action="store_true")
    restore = commands.add_parser("restore", parents=[common], help="Restore into an empty root")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--acknowledge-external-state", action="store_true")
    config = commands.add_parser("config", parents=[common])
    config.add_argument("action", choices=["path", "show", "sources", "read", "write", "reload"])
    config.add_argument("--path", help="Native resource relative path for read/write")
    config.add_argument("--file", type=Path, help="Native YAML candidate for write")
    config.add_argument(
        "--expected-digest", help="Source digest from read; use 'new' for a new source"
    )
    product = commands.add_parser("product-config", parents=[common])
    product.add_argument("action", choices=["show", "write", "reload"])
    product.add_argument(
        "--file", type=Path, help="Product YAML candidate, preserved byte-for-byte"
    )
    product.add_argument("--expected-digest", help="Product source digest from show")
    threads = commands.add_parser("threads", parents=[common])
    threads.add_argument(
        "action",
        choices=[
            "list",
            "create",
            "show",
            "history",
            "send",
            "decisions",
            "answer",
            "active",
            "fork",
            "configuration",
        ],
    )
    threads.add_argument("id", nargs="?")
    threads.add_argument("--text")
    threads.add_argument(
        "--file", type=Path, help="Text input or complete native decision batch (YAML/JSON)"
    )
    threads.add_argument("--project")
    threads.add_argument("--title")
    operations = commands.add_parser("operations", parents=[common])
    operations.add_argument("action", choices=["show", "steer", "cancel"])
    operations.add_argument("id")
    operations.add_argument("--text")
    tools = commands.add_parser(
        "tools", parents=[common], help="Inspect or cancel retained tool executions"
    )
    tools.add_argument("action", choices=["list", "cancel"])
    tools.add_argument("thread_id")
    tools.add_argument("id", nargs="?")
    tools.add_argument("--offset", type=int, default=0)
    tools.add_argument("--limit", type=int, default=20)
    schedules = commands.add_parser("schedules", parents=[common])
    schedules.add_argument("action", choices=["list", "put", "remove", "reload", "trigger"])
    schedules.add_argument("id", nargs="?")
    schedules.add_argument("--file", type=Path, help="Schedule definition YAML/JSON")
    schedules.add_argument("--expected-digest", help="Catalog digest; 'new' for first creation")
    schedules.add_argument("--request-key", help="Stable identity for manual firing")
    occurrences = commands.add_parser("occurrences", parents=[common])
    occurrences.add_argument("action", choices=["list", "show", "cancel", "resolve", "attach"])
    occurrences.add_argument("id", nargs="?")
    occurrences.add_argument("--schedule")
    occurrences.add_argument("--resolution", choices=["retry", "abandon"])
    occurrences.add_argument("--evidence")
    occurrences.add_argument("--receipt")
    for name in ("inbox", "attempts", "deliveries", "channels", "targets", "github"):
        commands.add_parser(
            name, parents=[common], help=f"Inspect {name}; use api for exact mutations"
        )
    api = commands.add_parser(
        "api", parents=[common], help="Call an explicit /api operation; no automatic retries"
    )
    api.add_argument("method", choices=["GET", "POST", "PUT", "PATCH"])
    api.add_argument("path", help="Path beginning with /api/")
    api.add_argument("--file", type=Path, help="JSON or YAML request body")
    return cli
