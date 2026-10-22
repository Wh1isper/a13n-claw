# a13n Claw

[![CI](https://github.com/Wh1isper/a13n-claw/actions/workflows/ci.yml/badge.svg)](https://github.com/Wh1isper/a13n-claw/actions/workflows/ci.yml)

An IM-first assistant in the [a13n ecosystem](https://github.com/converge-ai-labs/agent-foundation), inspired by [YA Claw](https://github.com/Wh1isper/ya-mono/tree/main/packages/ya-claw).

Claw embeds one Harness UI App from its published Python package. The Host owns configuration, Threads, execution, decisions, environments, collaboration, and Global/Project memory. Claw adds Lark/Feishu messaging, GitHub notification polling, durable Inbox and delivery evidence, isolated scheduled tasks, and headless CLI/API operations.

- **One Thread** (default): canonical Main coordinates native workers and explicitly replies to authorized Channels.

- **Per-Channel**: reuse one conversation Thread per Channel.

- **Per-trigger**: create a fresh task Thread for each eligible distinct event in its Channel Project.

- Mention filtering, event deduplication, text/file replies, exact stop/steer/continue commands, and native JSON decisions.

- Explicit reconciliation of uncertain dispatches, target creation, and delivery without automatic effect replay.

- Configurable Lark progress/reactions/decision prompts, isolated schedules and heartbeat, and explicit product configuration reload.

- Persistent Docker executor/browser, diagnostics, conservative retention, and offline backup/restore.

The runtime frontend is Hello World; operational interaction uses IM and CLI/API. Workflow orchestration, rich interactive cards, token streaming, and full Console are outside this delivery. The `0.0.2` tagged artifacts contain the earlier informational placeholder; build this source for the embedded service.

## Start from source

```bash
uv sync --locked
mkdir -p "$HOME/claw-workspace"
uv run a13n-claw init --workspace "$HOME/claw-workspace" --model openai:gpt-4o
# Supply CLAW_MODEL_API_KEY through your environment/secret manager.
uv run a13n-claw serve --no-console
```

The default Instance is `~/.a13n-claw`; `--root` or `CLAW_ROOT` selects another. Product settings live in `claw.yaml`; native Host resources live under `host/`. The workspace must be separate from protected Instance data. Configure Lark credentials and explicit Channel/sender bindings using [Getting started](docs/content/getting-started.md), then inspect `a13n-claw status`.

For persistent remote execution and human browser takeover, use the [Docker deployment](deploy/docker/README.md). See [Schedules](docs/content/schedules.md) and [GitHub polling](docs/content/github.md) for peripheral setup.

Read [Configuration and CLI](docs/content/configuration.md), [routing and IM commands](docs/content/one-thread.md), and [Operations](docs/content/operations.md). Local shell is host-account execution, not a sandbox. One Thread shares Main context and Project memory across Channels. Review grants before enabling participants.

Existing standalone roots containing `claw.sqlite3` are rejected. Preserve their data and initialize a fresh root; there is no implicit conversion or deletion. Source versions remain `0.0.0`; tags define published versions.

## Develop

Python 3.13, uv, Node.js 24, pnpm 10.30.3, and Make are used for source development. Docker validates distribution images and workflows.

```bash
make install
make check
make test
make build
make artifact-check
make docs-build
```

`a13n_claw/` is the Python package, `console/` the private Hello World frontend, and `docs/` the separate documentation site. `make build` compiles assets into the wheel and sdist. Installed serving and rebuilding the sdist need no Node.js. Docker installs the same wheel and requires explicit initialization and `serve`.

- [Contributing](CONTRIBUTING.md): setup, validation, and releases.
- [Development standards](DEVELOPMENT.md): ownership and engineering conventions.
- [Documentation](docs/content/index.md): available capabilities and setup.
- [Specifications](spec/README.md): accepted architecture and boundaries.
- [Release setup](docs/content/releasing.md): PyPI, GHCR, and documentation publication.

The static Fumadocs site uses Cloudflare Workers Static Assets at [a13n-claw.wh1isper.top](https://a13n-claw.wh1isper.top). It hosts documentation only.

## Compatibility and license

This independent product is not configuration-compatible with YA Claw or the retired standalone Claw runtime. It depends on bounded published Harness UI/Harness packages, never a sibling checkout. Licensed under [BSD-3-Clause](LICENSE), matching YA Claw / ya-mono. The license correction starts with `0.0.2`; earlier `0.0.1` artifacts remain unchanged.
