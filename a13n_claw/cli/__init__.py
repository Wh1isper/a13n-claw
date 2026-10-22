"""Headless setup and explicit operator requests to the single running Claw service."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from pydantic import ValidationError

from a13n_claw.cli.commands import invoke
from a13n_claw.cli.parser import parser
from a13n_claw.configuration.models import instance_root
from a13n_claw.errors import ClawError
from a13n_claw.operations.instance import reset_operator


def main(argv: Sequence[str] | None = None) -> None:
    cli = parser()
    args = cli.parse_args(argv, namespace=argparse.Namespace(root=None, url=None, token_file=None))
    if args.command is None:
        cli.print_help()
        return
    root = instance_root(args.root)
    try:
        if args.command == "init":
            from a13n_claw.configuration.bootstrap import initialize

            asyncio.run(
                initialize(
                    root,
                    args.workspace,
                    model_route=args.model,
                    model_key_env=args.model_key_env,
                    model_base_url=args.model_base_url,
                    executor_endpoint=args.executor,
                    executor_workspace=args.executor_workspace,
                )
            )
            print(
                json.dumps(
                    {
                        "root": str(root),
                        "configuration": str(root / "claw.yaml"),
                        "operator_token_file": str(root / "operator.token"),
                    },
                    indent=2,
                )
            )
        elif args.command == "reset-operator":
            print(json.dumps({"operator_token_file": str(reset_operator(root))}))
        elif args.command == "serve":
            import uvicorn

            from a13n_claw.app import create_app

            uvicorn.run(
                create_app(root=root, console=not args.no_console), host=args.host, port=args.port
            )
        else:
            print(json.dumps(invoke(args, root), indent=2, ensure_ascii=False))
    except ValidationError:
        cli.error("Invalid configuration or request fields")
    except (ValueError, OSError, ClawError) as exc:
        cli.error(str(exc))
