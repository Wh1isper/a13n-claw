---
title: Development
description: Work on the standalone project with reproducible tools.
---

## Toolchain

Use Python 3.13, uv, Node.js 24, pnpm 10.30.3, Make, and Git. Docker validates the image and GitHub Actions workflows.

```bash
make install
make check
make test
make build
make artifact-check
make docs-build
```

`make install` installs dependencies from the Python, console, and documentation lockfiles and installs pre-commit hooks. Read the repository's [contributor guide](https://github.com/Wh1isper/a13n-claw/blob/main/CONTRIBUTING.md) for focused checks and review conventions.

## Console and packaging

`console/` owns a standalone private React/TypeScript/Vite application. `a13n_claw/` owns the Python application and packaged static assets. `make console-dev` serves the frontend with hot reload; `make console-check` validates its types and formatting. Run `make serve` alongside Vite for the backend; Vite proxies `/api` to loopback port 8080 without rewriting the browser origin.

`make console-build` installs locked dependencies and writes the generated bundle to `a13n_claw/static/console/`. `make serve` serves that bundle through FastAPI/Uvicorn on loopback port 8080. There is no SPA catch-all: unknown APIs and missing assets return 404, while protected APIs reject unauthenticated requests with 401.

`make build` builds the console first, then the wheel and sdist. Direct `uv build` requires a prior console build and fails if assets are absent. Editable installs do not require assets until you serve the console. Generated assets are not committed, but both distributions contain them; the sdist also contains frontend source. Installation and sdist-to-wheel rebuilds do not require Node.js. `make artifact-check` installs both wheels outside the checkout and tests real HTTP responses and their referenced JavaScript/CSS. `make image-check` also verifies non-root serving and that saved configuration survives container replacement and loads at the next startup.

## Documentation

Portable Markdown lives under `docs/content/`. The Fumadocs application under `docs/` uses Next.js static export. Run `make docs-serve` to preview it and `make docs-build` to build the site and check internal links and anchors.

The static export is uploaded as a GitHub Actions artifact. A configured deployment publishes that same artifact through Cloudflare Workers Static Assets. The site requires no Next.js server or Cloudflare runtime code.

## Runtime and integration checks

`storage.py` owns short SQLite transactions; `coordinator.py` owns execution and checkpoint publication; `runtime.py` builds fresh public Harness objects from captured definitions. `environments.py` serializes target use and lifecycle intent/observations; `targets.py` owns Local validation and Docker SDK lifecycle operations. `api.py` applies current caller authority. `configuration_files.py` owns source revisions and file replacement; `configuration.py` validates the catalog and publishes its loaded projection in one transaction. CLI, Console, and startup share these owners. Changes crossing owners need invariant-based tests, not only endpoint snapshots.

The supported dependency line is Harness 0.10; environment APIs come from `a13n_harness.environment` and its provider modules, not a separate environment package. Claw prepares a target before handing Harness an `EnvironmentMount(source=...)`. Its fixed `EnvironmentSource` returns an inert, execution-only `EnvironmentConnector`; Harness opens fresh `EnvironmentExecution` instances lazily and closes them before Claw releases the target. Connectors never create, restart, or replace containers. Console file browsing uses the same fixed provider selection with read-only permissions, not lifecycle recovery or a host-path shortcut. Keep management code out of source activation and leave file/shell execution to native Harness providers.

Target mutations run under the manager's per-target lock and settle before cancellation releases it. Docker name reconciliation belongs only to idle lifecycle management; overlapping users retain the exact saved physical identity. Preserve both the Claw generation recipe and native provider-state fingerprint when changing dependencies: `tests/harness_08_docker.json` captures actual published 0.8 state for the 0.10 compatibility regression.

`make test` needs no external credentials. Native FunctionModel fixtures drive decisions, steering, delegation, cancellation, recovery, and retained files through the real runtime. `tests/test_network_runtime.py` additionally uses a loopback HTTP server, the production OpenAI client, a real MCP server/transport, and a real local shell. This proves adapter integration, not compatibility with every external provider deployment. Set `CLAW_TEST_DOCKER_IMAGE` to opt into actual disposable-container lifecycle tests. CI also checks Python on Windows; local Linux results do not substitute for that check.

For repeatable browser acceptance, build the Console and run `uv run python tests/console_server.py --root build/console-acceptance --port 0`. Use the printed listener and the generated local operator token. Configure a model with any nonempty managed test credential, a Local read environment, and a Profile with `claw.files.retain: ask`. A fresh Thread message `approval` requests an artifact decision; a message sent while waiting remains held. Approve it, verify the saved response and artifact, fork the checkpoint, send `slow`, cancel, record review, and start linked recovery. Also exercise attachment upload and selected-Run file browsing. This explicit test-only model never runs in the installed CLI and is not a provider probe.
