"""Export Lark's version-matched embedded skill entrypoints at image build time."""

import json
import subprocess
from pathlib import Path

root = Path("/usr/local/share/a13n/skills")
catalog = json.loads(subprocess.check_output(["lark-cli", "skills", "list", "--json"]))
for skill in catalog["skills"]:
    name = skill["name"]
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    text = subprocess.check_output(["lark-cli", "skills", "read", name], text=True)
    # The CLI owns reference content, including references that change per release.
    text += "\nRead referenced files with `lark-cli skills read " + name + " <relative-path>`.\n"
    (directory / "SKILL.md").write_text(text)
