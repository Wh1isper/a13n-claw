"""Headless setup and explicit operator requests to the single running Claw service."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml
from pydantic import ValidationError

from a13n_claw import __version__
from a13n_claw.errors import ClawError
from a13n_claw.instance import reset_operator
from a13n_claw.settings import instance_root, read_settings, selected_path


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
        choices=["list", "create", "show", "history", "send", "decisions", "answer", "active"],
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


def request(args: argparse.Namespace, root: Path, method: str, path: str, body=None):
    if not path.startswith("/api/") or path.startswith("//"):
        raise ValueError("API path must begin with /api/")
    url = args.url or os.environ.get("CLAW_URL", "http://127.0.0.1:8080")
    token = (args.token_file or root / "operator.token").expanduser().read_text().strip()
    with httpx.Client(base_url=url, timeout=30, follow_redirects=False) as client:
        try:
            response = client.request(
                method, path, json=body, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            raise ValueError(
                "Server response unavailable. Reconcile the original request before retrying; "
                "no offline fallback was used"
            ) from exc
    if not response.is_success:
        raise ValueError(f"Server returned {response.status_code}: {response.text}")
    return response.json() if response.content else None


def body_file(path: Path | None):
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path is not None else None


def invoke(args: argparse.Namespace, root: Path):
    if args.command in {"backup", "restore"}:
        from a13n_claw.backup import backup, restore

        operation = backup if args.command == "backup" else restore
        path = args.destination if args.command == "backup" else args.backup
        return operation(root, path, acknowledge_external_state=args.acknowledge_external_state)
    if args.command == "retention":
        return request(
            args,
            root,
            "POST",
            "/api/retention",
            {
                "older_than_days": args.older_than_days,
                "apply": args.apply,
            },
        )
    if args.command == "config":
        if args.action == "path":
            return {
                "product": str(root / "claw.yaml"),
                "native_host": str(selected_path(root, read_settings(root).host_configuration)),
            }
        if args.action == "show":
            return read_settings(root).model_dump(mode="json")
        if args.action == "sources":
            return request(args, root, "GET", "/api/configuration")
        if args.action == "reload":
            return request(args, root, "POST", "/api/configuration/reload")
        if not args.path:
            raise ValueError("config read/write requires --path")
        if args.action == "read":
            return request(
                args, root, "GET", "/api/configuration/source?path=" + quote(args.path, safe="")
            )
        if args.file is None or args.expected_digest is None:
            raise ValueError("config write requires --file and --expected-digest")
        return request(
            args,
            root,
            "PUT",
            "/api/configuration/source",
            {
                "relative_path": args.path,
                "content": args.file.read_text(encoding="utf-8"),
                "expected_digest": None if args.expected_digest == "new" else args.expected_digest,
            },
        )
    if args.command == "product-config":
        path = "/api/product-configuration"
        if args.action == "show":
            return request(args, root, "GET", path)
        if args.expected_digest is None:
            raise ValueError("Product write/reload requires --expected-digest")
        body = {"expected_digest": args.expected_digest}
        if args.action == "write":
            if args.file is None:
                raise ValueError("Product write requires --file")
            body["content"] = args.file.read_bytes().decode("utf-8")
            return request(args, root, "PUT", path, body)
        return request(args, root, "POST", path + "/reload", body)
    if args.command == "threads":
        if args.action == "list":
            return request(args, root, "GET", "/api/threads")
        if args.action == "create":
            return request(
                args,
                root,
                "POST",
                "/api/threads",
                {
                    "title": args.title,
                    "defaults": {"project_id": args.project} if args.project else None,
                },
            )
        if args.id is None:
            raise ValueError("This Thread operation requires an ID")
        path = "/api/threads/" + quote(args.id, safe="")
        if args.action == "send":
            text = (
                args.text
                if args.text is not None
                else args.file.read_text(encoding="utf-8")
                if args.file
                else None
            )
            if not text:
                raise ValueError("send requires --text or --file")
            return request(args, root, "POST", path + "/inputs", {"text": text})
        if args.action == "answer":
            if args.file is None:
                raise ValueError("answer requires --file with the complete native response batch")
            return request(args, root, "POST", path + "/decisions", body_file(args.file))
        return request(
            args, root, "GET", path + ("" if args.action == "show" else "/" + args.action)
        )
    if args.command == "operations":
        path = "/api/operations/" + quote(args.id, safe="")
        if args.action == "show":
            return request(args, root, "GET", path)
        if args.action == "steer" and not args.text:
            raise ValueError("steer requires --text")
        return request(
            args,
            root,
            "POST",
            path + "/" + args.action,
            {"text": args.text} if args.action == "steer" else None,
        )
    if args.command == "schedules":
        if args.action == "list":
            return request(args, root, "GET", "/api/schedules")
        if args.action == "reload":
            return request(args, root, "POST", "/api/schedules/reload")
        if args.id is None:
            raise ValueError("Schedule operation requires an ID")
        path = "/api/schedules/" + quote(args.id, safe="")
        if args.action == "trigger":
            if not args.request_key:
                raise ValueError("trigger requires --request-key; reuse it after a lost response")
            return request(args, root, "POST", path + "/trigger", {"request_key": args.request_key})
        if args.expected_digest is None or (args.action == "put" and args.file is None):
            raise ValueError("put/remove requires --expected-digest; put also requires --file")
        return request(
            args,
            root,
            "PUT",
            path,
            {
                "definition": body_file(args.file) if args.action == "put" else None,
                "expected_digest": None if args.expected_digest == "new" else args.expected_digest,
            },
        )
    if args.command == "occurrences":
        if args.action == "list":
            query = "?schedule_id=" + quote(args.schedule, safe="") if args.schedule else ""
            return request(args, root, "GET", "/api/occurrences" + query)
        if args.id is None:
            raise ValueError("Occurrence operation requires an ID")
        path = "/api/occurrences/" + quote(args.id, safe="")
        if args.action == "show":
            return request(args, root, "GET", path)
        body = None
        if args.action == "resolve":
            if not args.resolution or not args.evidence:
                raise ValueError("resolve requires --resolution and --evidence")
            body = {"action": args.resolution, "evidence": args.evidence}
        elif args.action == "attach":
            if not args.receipt:
                raise ValueError("attach requires --receipt")
            body = {"receipt_id": args.receipt}
        return request(args, root, "POST", path + "/" + args.action, body)
    if args.command == "api":
        return request(args, root, args.method, args.path, body_file(args.file))
    return request(args, root, "GET", "/api/" + args.command)


def main(argv: Sequence[str] | None = None) -> None:
    cli = parser()
    args = cli.parse_args(argv, namespace=argparse.Namespace(root=None, url=None, token_file=None))
    if args.command is None:
        cli.print_help()
        return
    root = instance_root(args.root)
    try:
        if args.command == "init":
            from a13n_claw.bootstrap import initialize

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
