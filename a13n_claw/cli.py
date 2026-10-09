"""Report package identity without implying a working agent runtime."""

import argparse
from collections.abc import Sequence

from a13n_claw import __version__


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="a13n-claw",
        description="a13n Claw placeholder. Agent execution is not implemented yet.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="Serve the console placeholder (no agent execution)")
    serve.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    args = parser.parse_args(argv)
    if args.command == "serve":
        import uvicorn

        from a13n_claw.app import create_app

        try:
            app = create_app()
        except RuntimeError as error:
            parser.error(str(error))
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        parser.print_help()
