"""Export the API contract without opening an Instance or runtime docs endpoint."""

import json
from pathlib import Path

from a13n_claw.app import create_app


def main() -> None:
    destination = Path("build/console-openapi.json")
    destination.parent.mkdir(exist_ok=True)
    schema = create_app(root=destination.parent / "schema-only", console=False).openapi()
    destination.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
