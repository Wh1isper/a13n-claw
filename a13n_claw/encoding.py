"""Canonical business JSON and kind-prefixed request identities."""

import json
from typing import Any
from uuid import uuid4


def identifier(kind: str) -> str:
    return f"{kind}_{uuid4().hex}"


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
