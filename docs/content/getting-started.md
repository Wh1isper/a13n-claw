---
title: Getting started
description: Build and serve the console preview, or inspect the published CLI placeholder.
---

## From source

Install Python 3.13 or later, [uv](https://docs.astral.sh/uv/), Node.js 24, pnpm 10.30.3, and Make, then:

```bash
git clone https://github.com/Wh1isper/a13n-claw.git
cd a13n-claw
uv sync --locked
uv run a13n-claw --version
uv run a13n-claw --help
make console-build
make serve
```

Open `http://127.0.0.1:8080`. The server serves only packaged console assets; no provider credentials are needed. Threads, environments, and settings are informational previews, not working runtime controls. There is no authentication; keep the preview on a trusted interface.

Use `uv run a13n-claw serve --host 127.0.0.1 --port 9000` to change the binding. Source installs report `0.0.0`. Running the command without arguments prints help. Help and version commands do not start a server or require built assets.

For frontend development, `make console-dev` runs Vite on loopback port 5173 with hot reload. `make serve` uses built assets; rebuild after editing the frontend. A missing bundle produces an actionable error rather than an empty server.

## From a release

The published BSD-3-Clause licensed `0.0.2` placeholder supports version and help only, not `serve`:

```bash
uv tool install a13n-claw==0.0.2
a13n-claw --version
```

The module entry point is also available with `python -m a13n_claw --version` in an environment containing the package.

## Container

Run the matching release image:

```bash
docker run --rm ghcr.io/wh1isper/a13n-claw:0.0.2 --version
```

That published image runs the informational CLI and exits. To try the console in a **source-built** non-root image instead:

```bash
make build
docker build -f deploy/docker/Dockerfile -t a13n-claw:local .
docker run --rm -p 127.0.0.1:8080:8080 a13n-claw:local serve --host 0.0.0.0
```

The explicit host option listens on the container interface; the port mapping limits host access to loopback. The image uses the same console-containing wheel, needs no Node.js at runtime, and prints help by default. No volumes, API tokens, or agent health endpoint are available.
