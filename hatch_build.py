"""Require a prebuilt console for distributable archives, not editable installs."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class ConsoleBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if version == "editable":
            return
        console = Path(self.root) / "a13n_claw" / "service_app" / "static"
        if not all((console / name).is_file() for name in ("index.js", "index.css")):
            raise RuntimeError(
                "Console assets are missing. Run `make console-build` before building."
            )
        build_data["artifacts"].append("a13n_claw/service_app/static/**")
