"""Run the single-owner Claw application and report installed package identity."""

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from a13n_claw import __version__


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="a13n-claw",
        description="a13n Claw: durable agent execution and conversation Console.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="Serve the authenticated runtime and Console")
    serve.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    serve.add_argument(
        "--data-root", type=Path, help="Application data (default: CLAW_DATA_ROOT or ~/.a13n-claw)"
    )
    serve.add_argument(
        "--workspace",
        type=Path,
        help="Shared workspace (default: CLAW_WORKSPACE or startup directory)",
    )
    serve.add_argument(
        "--concurrency", type=int, default=4, help="Maximum concurrent independent Runs"
    )
    serve.add_argument(
        "--mode",
        choices=["per_channel", "one_thread"],
        help="Instance conversation mode (default: CLAW_CONVERSATION_MODE or per_channel)",
    )
    reset = commands.add_parser(
        "reset-operator", help="Rotate operator access while the server is stopped"
    )
    reset.add_argument("--data-root", type=Path, help="Application data directory")
    args = parser.parse_args(argv)
    if args.command == "serve":
        import uvicorn

        from a13n_claw.app import create_app
        from a13n_claw.domain import ClawError

        try:
            app = create_app(
                data_root=args.data_root,
                workspace=args.workspace,
                concurrency=args.concurrency,
                mode=args.mode,
            )
        except (RuntimeError, ClawError) as error:
            parser.error(str(error))
        uvicorn.run(app, host=args.host, port=args.port)
    elif args.command == "reset-operator":
        from a13n_claw.domain import ClawError
        from a13n_claw.instance import reset_operator

        root = (
            (args.data_root or Path(os.environ.get("CLAW_DATA_ROOT", "~/.a13n-claw")))
            .expanduser()
            .resolve()
        )
        try:
            path = reset_operator(root)
        except ClawError as error:
            parser.error(str(error))
        print(f"Operator access rotated. Read the token locally from: {path}")
    else:
        parser.print_help()
