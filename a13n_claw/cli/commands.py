"""Headless setup and explicit operator requests to the single running Claw service."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import quote

from a13n_claw.cli.client import body_file, request
from a13n_claw.configuration.models import read_settings, selected_path


def invoke(args: argparse.Namespace, root: Path):
    if args.command in {"backup", "restore"}:
        from a13n_claw.operations.backup import backup, restore

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
        if args.action == "fork":
            return request(args, root, "POST", path + "/fork")
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
    if args.command == "tools":
        path = "/api/threads/" + quote(args.thread_id, safe="") + "/tool-executions"
        if args.action == "list":
            return request(args, root, "GET", f"{path}?offset={args.offset}&limit={args.limit}")
        if args.id is None:
            raise ValueError("Tool cancellation requires an execution ID")
        return request(args, root, "POST", path + "/" + quote(args.id, safe="") + "/cancel")
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
