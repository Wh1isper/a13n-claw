"""Headless configuration, operations and the single-owner runtime entry point."""

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from a13n_claw import __version__
from a13n_claw.domain import ClawError
from a13n_claw.management import (
    config_path,
    data_root,
    document,
    local_path,
    run_configuration,
    run_offline,
    run_remote,
)


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument(
        "--config", type=Path, help="YAML file (CLAW_CONFIG or ~/.a13n-claw/config.yaml)"
    )
    common.add_argument("--data-root", type=Path, help="Application data directory")
    common.add_argument("--url", help="Running server URL (requires explicit client credentials)")
    common.add_argument(
        "--token-file", type=Path, help="Bearer token file; never pass tokens as arguments"
    )
    common.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    root = argparse.ArgumentParser(
        prog="a13n-claw",
        parents=[common],
        description="a13n Claw: durable agent execution, headless configuration and operations.",
    )
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = root.add_subparsers(dest="command")

    serve = commands.add_parser("serve", parents=[common], help="Run the API and optional Console")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--workspace", type=Path)
    serve.add_argument("--concurrency", type=int)
    serve.add_argument(
        "--mode",
        choices=["per_channel", "one_thread"],
        help="Explicit mode; otherwise retain saved mode (new Instances: one_thread)",
    )
    serve.add_argument("--no-console", action="store_true")
    commands.add_parser(
        "reset-operator", parents=[common], help="Rotate operator access while stopped"
    )
    commands.add_parser("status", parents=[common], help="Inspect running Instance health")
    commands.add_parser("workers", parents=[common], help="Inspect Main's persistent workers")

    def group(name: str, help_text: str):
        node = commands.add_parser(name, parents=[common], help=help_text)
        return node.add_subparsers(dest="action", required=True)

    configuration = group("config", "Edit, validate, save and reload file-first configuration")
    for action in (
        "path",
        "init",
        "show",
        "validate",
        "status",
        "save",
        "reload",
        "apply",
        "export",
        "migrate",
    ):
        node = configuration.add_parser(action, parents=[common])
        if action in {"reload", "apply", "export", "status", "save", "migrate"}:
            node.add_argument(
                "--offline",
                action="store_true",
                help="Require exclusive ownership of a stopped Instance",
            )
        if action in {"reload", "apply", "save"}:
            node.add_argument(
                "--revision",
                required=action == "save",
                help="Expected file revision from config status",
            )
        if action == "save":
            node.add_argument(
                "--file", type=Path, required=True, help="Candidate YAML to save without loading"
            )
        if action in {"reload", "apply"}:
            node.add_argument(
                "--dry-run",
                action="store_true",
                help="Preview the file against loaded state without loading",
            )
        if action == "export":
            node.add_argument(
                "--output", type=Path, help="Write a new file instead of stdout; never overwrite"
            )
    credentials = group("credentials", "Inspect references or securely set/remove managed secrets")
    for action in ("list", "set", "remove"):
        node = credentials.add_parser(action, parents=[common])
        node.add_argument("--offline", action="store_true")
        if action != "list":
            node.add_argument("name")
        if action == "set":
            node.add_argument(
                "--stdin",
                action="store_true",
                help="Read secret from stdin instead of a hidden prompt",
            )
    main = group("main", "Initialize Main, inspect coordination and pause/resume processing")
    for action in ("init", "show", "pause", "resume"):
        node = main.add_parser(action, parents=[common])
        if action == "init":
            node.add_argument("--profile", required=True)
            node.add_argument("--offline", action="store_true")
        elif action in {"pause", "resume"}:
            node.add_argument("--version", required=True, type=int)
            node.add_argument(
                "--retry", action="store_true", help="Clear a known-safe processing backoff"
            )
    threads = group("threads", "Inspect saved conversations and submit explicit work")
    for action in (
        "list",
        "show",
        "history",
        "inputs",
        "runs",
        "targets",
        "send",
        "release-inputs",
    ):
        node = threads.add_parser(action, parents=[common])
        if action != "list":
            node.add_argument("id")
        if action == "send":
            node.add_argument("--request-id", required=True)
            text = node.add_mutually_exclusive_group(required=True)
            text.add_argument("--text")
            text.add_argument("--text-file", type=Path)
    runs = group("runs", "Inspect, cancel, reconcile and explicitly recover Runs")
    for action in ("show", "decisions", "cancel", "reconcile", "recover"):
        node = runs.add_parser(action, parents=[common])
        node.add_argument("id")
        if action == "reconcile":
            node.add_argument("--note", required=True)
        if action == "recover":
            node.add_argument("--request-id", required=True)
            node.add_argument("--text", required=True)
    decisions = group("decisions", "Answer an exact saved decision batch")
    node = decisions.add_parser("answer", parents=[common])
    node.add_argument("id")
    node.add_argument(
        "--file", type=Path, required=True, help="Exact decision response in YAML or JSON"
    )
    inputs = group("inputs", "Reconcile uncertain or discard blocked input")
    for action in ("acknowledge", "discard"):
        node = inputs.add_parser(action, parents=[common])
        node.add_argument("id")
        node.add_argument("--note", required=True)
    inbox = group("inbox", "Inspect and explicitly settle or defer durable attention")
    for action in ("list", "show", "update"):
        node = inbox.add_parser(action, parents=[common])
        if action != "list":
            node.add_argument("id")
        if action == "list":
            node.add_argument("--after", type=int, default=0)
            node.add_argument("--limit", type=int, default=100)
        if action != "show":
            node.add_argument(
                "--disposition",
                choices=["pending", "deferred", "handled", "ignored"],
                required=action == "update",
            )
        if action == "update":
            node.add_argument("--version", type=int, required=True)
            node.add_argument("--note", required=True)
            dependency = node.add_mutually_exclusive_group()
            dependency.add_argument("--dependency-run")
            dependency.add_argument("--due-at")
            dependency.add_argument("--human-key")
    channels = group(
        "channels", "Inspect Channel bindings (edit the configuration file, then reload)"
    )
    channels.add_parser("list", parents=[common])
    deliveries = group("deliveries", "Inspect sends and reconcile unknown outcomes")
    deliveries.add_parser("list", parents=[common])
    node = deliveries.add_parser("reconcile", parents=[common])
    node.add_argument("id")
    node.add_argument("--version", type=int, required=True)
    node.add_argument("--outcome", choices=["sent", "not_sent"], required=True)
    node.add_argument("--note", required=True)
    node.add_argument("--retry", action="store_true")
    targets = group("targets", "Manage saved environment targets")
    for action in ("inspect", "prepare", "stop", "remove"):
        node = targets.add_parser(action, parents=[common])
        node.add_argument("id")
    return root


def main(argv: Sequence[str] | None = None) -> None:
    command_parser = parser()
    args = command_parser.parse_args(
        argv,
        namespace=argparse.Namespace(
            config=None,
            data_root=None,
            url=None,
            token_file=None,
            json=False,
            action=None,
            offline=False,
        ),
    )
    if args.command is None:
        command_parser.print_help()
        return
    try:
        # Path/init must work even when the selected document is not yet valid.
        if args.command == "config" and (
            args.action in {"path", "init"}
            or (
                args.offline
                and (args.data_root or os.environ.get("CLAW_DATA_ROOT"))
                and args.action in {"status", "export", "save", "migrate"}
            )
            or (
                not args.offline
                and (args.url or os.environ.get("CLAW_URL"))
                and args.action in {"status", "export", "save", "reload", "apply"}
            )
        ):
            from a13n_claw.configuration import Configuration

            config = Configuration()
        else:
            config = document(args)
        if args.command == "serve":
            import uvicorn

            from a13n_claw.app import create_app

            settings = config.server
            workspace = args.workspace or (
                Path(os.environ["CLAW_WORKSPACE"]) if "CLAW_WORKSPACE" in os.environ else None
            )
            if workspace is None and settings.workspace is not None:
                workspace = local_path(settings.workspace, config_path(args).parent)
            mode = (
                args.mode
                if args.mode is not None
                else os.environ.get("CLAW_CONVERSATION_MODE", settings.mode)
            )
            app = create_app(
                data_root=data_root(args, config),
                config_path=config_path(args),
                workspace=workspace,
                concurrency=args.concurrency
                if args.concurrency is not None
                else settings.concurrency,
                mode=mode,
                console=settings.console and not args.no_console,
            )
            uvicorn.run(
                app,
                host=args.host if args.host is not None else settings.host,
                port=args.port if args.port is not None else settings.port,
            )
        elif args.command == "reset-operator":
            from a13n_claw.instance import reset_operator

            path = reset_operator(data_root(args, config))
            print(f"Operator access rotated. Read the token locally from: {path}")
        elif args.command == "config":
            run_configuration(args, config)
        elif args.offline:
            run_offline(args, config)
        else:
            run_remote(args, config)
    except ValidationError as error:
        # Location/type only: Pydantic messages and input can contain secrets.
        fields = "; ".join(
            ".".join(map(str, item["loc"])) + f" ({item['type']})"
            for item in error.errors(include_input=False, include_context=False, include_url=False)
        )
        command_parser.error(f"Invalid configuration or request: {fields}")
    except ClawError as error:
        command_parser.error(f"{error.code}: {error}")
    except OSError:
        command_parser.error(
            "Local file operation failed; check paths, permissions "
            "and whether output already exists"
        )
