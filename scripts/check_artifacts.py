"""Verify both distributions as installed Service Apps, including the prebuilt ESM."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from tomllib import loads

SMOKE = """
from importlib.metadata import entry_points, version
from importlib.resources import files
import sys
from a13n_service.settings import load_settings
assert version('a13n-service') == '0.4.1'
app = next(item for item in entry_points(group='a13n_service.app_plugins')
           if item.name == 'a13n.claw').load()()
assert app.app_id == 'a13n.claw' and app.database and app.frontend and app.routers
root = files('a13n_claw.service_app')
assert '@a13n/console' in root.joinpath('static', 'index.js').read_text(encoding='utf-8')
assert root.joinpath('static', 'index.css').is_file()
assert root.joinpath('migrations', 'versions').is_dir()
settings = load_settings()
assert settings.plugins.bootstrap_enabled_apps == ('a13n.claw',)
if sys.platform == 'linux':
    from a13n_service.app import build_app
    service = build_app(role='control', settings=settings)
else:
    from fastapi import FastAPI
    service = FastAPI()
    for router in app.routers:
        service.include_router(router)
assert '/api/v1/apps/a13n.claw/claws' in service.openapi()['paths']
"""


def check(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(args, check=True, cwd=cwd, env=env)


def only(directory: Path, pattern: str) -> Path:
    files = list(directory.glob(pattern))
    if len(files) != 1:
        raise RuntimeError(f"Expected one {pattern} in {directory}")
    return files[0].resolve()


def check_base_image() -> None:
    project = loads(Path("pyproject.toml").read_text())["project"]
    versions = [
        dep.removeprefix("a13n-service==")
        for dep in project["dependencies"]
        if dep.startswith("a13n-service==")
    ]
    if len(versions) != 1:
        raise RuntimeError("Expected exactly one a13n-service version pin")
    dockerfile = Path("deploy/docker/Dockerfile").read_text()
    prefix = f"FROM ghcr.io/converge-ai-labs/a13n-service:{versions[0]}@sha256:"
    if not re.search(rf"^{re.escape(prefix)}[0-9a-f]{{64}}$", dockerfile, re.MULTILINE):
        raise RuntimeError("Pin the exact Service base image tag and sha256 digest")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-image-only", action="store_true")
    options = parser.parse_args()
    check_base_image()
    if options.base_image_only:
        return
    with TemporaryDirectory(prefix="claw-distributions-") as temp:
        root = Path(temp)
        wheel = only(Path("dist"), "*.whl")
        source = only(Path("dist"), "*.tar.gz")
        output = root / "rebuilt"
        check("uv", "build", "--wheel", "--out-dir", str(output), str(source))
        for index, artifact in enumerate((wheel, only(output, "*.whl"))):
            env = root / f"env-{index}"
            check("uv", "venv", "--python", sys.executable, str(env))
            python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            check("uv", "pip", "install", "--python", str(python), str(artifact))
            runtime_env = {
                key: value for key, value in os.environ.items() if not key.startswith("A13N_")
            }
            runtime_env["A13N_PLUGINS__BOOTSTRAP_ENABLED_APPS"] = '["a13n.claw"]'
            check(str(python), "-I", "-c", SMOKE, cwd=root, env=runtime_env)


if __name__ == "__main__":
    main()
