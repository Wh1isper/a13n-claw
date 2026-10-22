"""Headless setup and explicit operator requests to the single running Claw service."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import httpx
import yaml


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
