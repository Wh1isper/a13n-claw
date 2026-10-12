"""CLI transport and offline administration; never an alternate execution engine."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
import yaml
from pydantic import JsonValue

from a13n_claw.configuration import (
    Configuration,
    load_configuration,
    read_yaml,
)
from a13n_claw.configuration_files import ConfigurationFile, revision
from a13n_claw.domain import ClawError, endpoint
from a13n_claw.instance import InstanceLease, operator_token, token_hash
from a13n_claw.storage import Store

STARTER = """# File-first configuration. Startup loads this file; use config reload after edits.
# Keep secrets in managed credentials or environment variables.
format: 1
server:
  host: 127.0.0.1
  port: 8080
  # workspace: /absolute/path/to/workspace
models:
  primary:
    provider: openai
    model_name: replace-with-your-model
    credential_env: OPENAI_API_KEY
environments:
  local:
    kind: local
    files: read
    shell: false
profiles:
  default:
    model_id: primary
    environment_id: local
    agent:
      instructions: You are a helpful assistant.
defaults:
  profile_id: default
# This is the complete catalog. Removing a resource retires it on reload.
# Server settings take effect after restart; existing Runs keep captured definitions.
"""


def config_path(args: argparse.Namespace) -> Path:
    selected = args.config or os.environ.get("CLAW_CONFIG")
    return Path(selected or "~/.a13n-claw/config.yaml").expanduser().resolve()


def document(args: argparse.Namespace) -> Configuration:
    path = config_path(args)
    if path.is_file():
        return load_configuration(path)
    if args.command == "config" and args.action in {"show", "validate", "reload", "apply"}:
        raise ClawError("configuration_missing", "Selected configuration file does not exist", 422)
    return Configuration()


def local_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def data_root(args: argparse.Namespace, config: Configuration) -> Path:
    explicit = args.data_root or os.environ.get("CLAW_DATA_ROOT")
    if explicit:
        return local_path(str(explicit), Path.cwd())
    return local_path(config.server.data_root or "~/.a13n-claw", config_path(args).parent)


@contextmanager
def offline(
    root: Path, *, create: bool = False, initial_mode: str = "one_thread"
) -> Iterator[Store]:
    if initial_mode not in {"one_thread", "per_channel"}:
        raise ClawError("conversation_mode", "Choose per_channel or one_thread", 422)
    if not create and not (root / "claw.sqlite3").is_file():
        raise ClawError(
            "instance_missing", "Initialize with serve or config reload --offline first", 404
        )
    with InstanceLease(root):
        token = operator_token(root)
        store = Store(root / "claw.sqlite3", initial_mode=initial_mode)
        store.bootstrap(token_hash(token))
        yield store


class Client:
    def __init__(self, args: argparse.Namespace, config: Configuration):
        explicit_url = args.url or os.environ.get("CLAW_URL")
        host = config.server.host
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        self.url = endpoint(explicit_url or f"http://{host}:{config.server.port}").rstrip("/")
        token_file = args.token_file or os.environ.get("CLAW_TOKEN_FILE")
        token = os.environ.get("CLAW_TOKEN")
        if token_file:
            token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
        elif not token:
            # Do not send a local operator token to an arbitrary configured endpoint.
            parsed = urlsplit(self.url)
            if explicit_url or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ClawError(
                    "client_token", "Use --token-file or CLAW_TOKEN for an explicit endpoint", 422
                )
            token = (data_root(args, config) / "operator.token").read_text(encoding="utf-8").strip()
        if not token or "\n" in token or "\r" in token:
            raise ClawError("client_token", "Invalid client token", 422)
        self.http = httpx.Client(
            base_url=self.url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        self.http.close()

    def request(self, method: str, path: str, body: object = None, **params) -> JsonValue:
        try:
            response = self.http.request(method, f"/api/{path}", json=body, params=params)
        except httpx.HTTPError as error:
            raise ClawError(
                "transport_unknown",
                "Server request failed; inspect saved state and retry with the same request ID",
            ) from error
        if response.status_code == 204:
            return None
        try:
            result = response.json()
        except ValueError as error:
            raise ClawError(
                "server_response", "Server returned an invalid response; reconcile before retry"
            ) from error
        if response.is_error:
            detail = result.get("error") if isinstance(result, dict) else None
            if (
                isinstance(detail, dict)
                and isinstance(detail.get("code"), str)
                and isinstance(detail.get("message"), str)
            ):
                raise ClawError(detail["code"], detail["message"], response.status_code)
            raise ClawError("server_response", f"Server rejected request ({response.status_code})")
        if response.is_redirect:
            raise ClawError("server_redirect", "Server redirects are not followed")
        return result


def output(value: object, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(yaml.safe_dump(value, sort_keys=False, allow_unicode=True).rstrip())


def segment(value: str) -> str:
    return quote(value, safe="")


def secret_input(args: argparse.Namespace) -> str:
    if args.stdin:
        value = sys.stdin.read(65538).rstrip("\r\n")
    else:
        if not sys.stdin.isatty():
            raise ClawError("secret_input", "Use --stdin for non-interactive secret input", 422)
        value = getpass.getpass("Credential value: ")
    if not value or len(value) > 65536:
        raise ClawError(
            "secret_input", "Credential must be nonempty and at most 65536 characters", 422
        )
    return value


def run_configuration(args: argparse.Namespace, config: Configuration) -> None:
    action = "reload" if args.action == "apply" else args.action
    path = config_path(args)
    if action == "path":
        output({"path": str(path)}, as_json=args.json)
        return
    if action == "init":
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(STARTER)
        output({"path": str(path), "loaded": False}, as_json=args.json)
        return
    if action == "show":
        if args.json:
            output(read_yaml(path), as_json=True)
        else:
            print(path.read_text(encoding="utf-8"), end="")
        return
    if action == "validate":
        output(
            {"valid": True, "scope": "document schema", "connectivity": "not_probed"},
            as_json=args.json,
        )
        return
    if action == "migrate" and not args.offline:
        raise ClawError(
            "offline_required", "Migration requires --offline and a stopped Instance", 422
        )
    if args.offline:
        with offline(
            data_root(args, config),
            create=action == "reload",
            initial_mode=os.environ.get(
                "CLAW_CONVERSATION_MODE", config.server.mode or "one_thread"
            ),
        ) as store:
            source = ConfigurationFile(store, path)
            if action == "migrate":
                source.initialize(migrate=True)
                result = source.status("operator")
            elif action == "reload":
                expected = args.revision or revision(path.read_bytes())
                result = source.reload("operator", expected, dry_run=args.dry_run)
            elif action == "save":
                result = source.save(
                    "operator", args.file.read_bytes().decode("utf-8"), args.revision
                )
            else:
                result = source.status("operator")
    else:
        client = Client(args, config)
        try:
            if action == "save":
                result = client.request(
                    "PUT",
                    "configuration",
                    {
                        "text": args.file.read_bytes().decode("utf-8"),
                        "expected_revision": args.revision,
                    },
                )
            elif action == "reload":
                # Inspect the server-owned source, not an unrelated local YAML document.
                expected = args.revision
                if expected is None:
                    status = client.request("GET", "configuration")
                    assert isinstance(status, dict)
                    expected = status["revision"]
                result = client.request(
                    "POST",
                    "configuration/reload",
                    {
                        "expected_revision": expected,
                    },
                    dry_run=args.dry_run,
                )
            else:
                result = client.request("GET", "configuration")
        finally:
            client.close()
    if action == "export":
        assert isinstance(result, dict) and isinstance(result["text"], str)
        text = result["text"]
        if args.output:
            with args.output.open("xb") as handle:
                handle.write(text.encode("utf-8"))
        elif args.json:
            output(result["document"], as_json=True)
        else:
            print(text, end="")
    else:
        output(result, as_json=args.json)


def run_offline(args: argparse.Namespace, config: Configuration) -> None:
    with offline(data_root(args, config)) as store:
        if args.command == "credentials":
            if args.action == "list":
                result = store.credential_status("operator")
            else:
                store.save_credential(
                    "operator", args.name, secret_input(args) if args.action == "set" else None
                )
                result = {"name": args.name, "configured": args.action == "set"}
        elif args.command == "main" and args.action == "init":
            result = store.coordination.initialize_main("operator", args.profile).model_dump(
                mode="json"
            )
        else:
            raise ClawError("offline_operation", "This operation requires the running server", 422)
    output(result, as_json=args.json)


def run_remote(args: argparse.Namespace, config: Configuration) -> None:
    client = Client(args, config)
    try:
        result = remote_operation(client, args)
        output(result, as_json=args.json)
    finally:
        client.close()


def remote_operation(client: Client, args: argparse.Namespace) -> JsonValue:
    command, action = args.command, args.action
    if command == "status":
        return client.request("GET", "instance")
    if command == "credentials":
        if action == "list":
            return client.request("GET", "credentials")
        return client.request(
            "PUT",
            f"credentials/{segment(args.name)}",
            {"value": secret_input(args) if action == "set" else None},
        )
    if command == "main":
        if action == "init":
            return client.request("POST", "coordination/main", {"profile_id": args.profile})
        if action == "show":
            return client.request("GET", "coordination")
        return client.request(
            "PUT",
            "coordination/control",
            {"expected_version": args.version, "paused": action == "pause", "retry": args.retry},
        )
    if command == "workers":
        state = client.request("GET", "coordination")
        assert isinstance(state, dict)
        return state["workers"]
    if command == "threads":
        if action == "list":
            return client.request("GET", "threads")
        path = f"threads/{segment(args.id)}"
        if action in {"show", "history", "inputs", "runs", "targets"}:
            return client.request("GET", path + ("" if action == "show" else f"/{action}"))
        if action == "send":
            text = args.text_file.read_text(encoding="utf-8") if args.text_file else args.text
            return client.request(
                "POST", path + "/inputs", {"request_id": args.request_id, "text": text}
            )
        return client.request("POST", path + "/release-inputs")
    if command == "runs":
        path = f"runs/{segment(args.id)}"
        if action in {"show", "decisions"}:
            return client.request("GET", path + ("" if action == "show" else "/decisions"))
        body = (
            {"note": args.note}
            if action == "reconcile"
            else {"request_id": args.request_id, "text": args.text}
            if action == "recover"
            else None
        )
        return client.request("POST", path + f"/{action}", body)
    if command == "decisions":
        return client.request("POST", f"decisions/{segment(args.id)}/answer", read_yaml(args.file))
    if command == "inputs":
        return client.request("POST", f"inputs/{segment(args.id)}/{action}", {"note": args.note})
    if command == "inbox":
        if action in {"list", "show"}:
            return client.request(
                "GET",
                "inbox",
                **(
                    {"item_id": args.id}
                    if action == "show"
                    else {
                        "after": args.after,
                        "limit": args.limit,
                        **({"disposition": args.disposition} if args.disposition else {}),
                    }
                ),
            )
        return client.request(
            "PUT",
            f"inbox/{segment(args.id)}",
            {
                "expected_version": args.version,
                "disposition": args.disposition,
                "note": args.note,
                "dependency_run_id": args.dependency_run,
                "due_at": args.due_at,
                "human_key": args.human_key,
            },
        )
    if command == "channels":
        return client.request("GET", "channels")
    if command == "deliveries":
        if action == "list":
            return client.request("GET", "deliveries")
        return client.request(
            "POST",
            f"deliveries/{segment(args.id)}/reconcile",
            {
                "expected_version": args.version,
                "outcome": args.outcome,
                "note": args.note,
                "retry": args.retry,
            },
        )
    if command == "targets":
        return client.request("POST", f"targets/{segment(args.id)}/manage", {"operation": action})
    raise ClawError("command", "Unsupported operation", 422)
