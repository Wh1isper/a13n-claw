"""Verify both distributions as installed Service Apps, including the prebuilt ESM."""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from tomllib import loads

from packaging.version import Version

if __package__:
    from .service_version import service_line, validate_defaults
else:
    from service_version import service_line, validate_defaults

SMOKE = """
from importlib.metadata import entry_points, version
from importlib.resources import files
import sys
from a13n_service.settings import load_settings
from packaging.specifiers import SpecifierSet
from packaging.version import Version
assert Version(version('a13n-service')) in SpecifierSet(sys.argv[2])
assert not sys.argv[3] or version('a13n-service') == sys.argv[3]
assert version('a13n-claw') == sys.argv[1]
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


def check_base_image() -> str:
    project = loads(Path("pyproject.toml").read_text())
    lock = loads(Path("uv.lock").read_text())
    return validate_defaults(project, lock, Path("deploy/docker/Dockerfile").read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-image-only", action="store_true")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--service-version")
    selection.add_argument("--locked-service", action="store_true")
    options = parser.parse_args()
    locked = check_base_image()
    if options.locked_service:
        options.service_version = locked
    spec, _ = service_line(loads(Path("pyproject.toml").read_text()))
    if options.service_version and Version(options.service_version) not in spec:
        parser.error("Service version is outside the supported range")
    if options.base_image_only:
        return
    with TemporaryDirectory(prefix="claw-distributions-") as temp:
        root = Path(temp)
        wheel = only(Path("dist"), "*.whl")
        source = only(Path("dist"), "*.tar.gz")
        expected_version = loads(Path("pyproject.toml").read_text())["project"]["version"]
        output = root / "rebuilt"
        check("uv", "build", "--wheel", "--out-dir", str(output), str(source))
        for index, artifact in enumerate((wheel, only(output, "*.whl"))):
            env = root / f"env-{index}"
            check("uv", "venv", "--python", sys.executable, str(env))
            python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            requirements = [str(artifact)]
            if options.service_version:
                requirements.append(f"a13n-service=={options.service_version}")
            check("uv", "pip", "install", "--python", str(python), *requirements)
            runtime_env = {
                key: value for key, value in os.environ.items() if not key.startswith("A13N_")
            }
            runtime_env["A13N_PLUGINS__BOOTSTRAP_ENABLED_APPS"] = '["a13n.claw"]'
            check(
                str(python),
                "-I",
                "-c",
                SMOKE,
                expected_version,
                str(spec),
                options.service_version or "",
                cwd=root,
                env=runtime_env,
            )


if __name__ == "__main__":
    main()
