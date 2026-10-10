# a13n Claw

[![CI](https://github.com/Wh1isper/a13n-claw/actions/workflows/ci.yml/badge.svg)](https://github.com/Wh1isper/a13n-claw/actions/workflows/ci.yml)

A fresh, local-first agent runtime project targeting [a13n Harness](https://github.com/converge-ai-labs/agent-foundation), inspired by [YA Claw](https://github.com/Wh1isper/ya-mono/tree/main/packages/ya-claw).

**Current source scope: a single-node runtime and real conversation Console.** Run agents through Harness with durable Threads, inputs, checkpoints, decisions, cancellation/recovery, delegated work, and checkpoint forks. Manage versioned Profiles, model credentials, skills, MCP servers, scoped clients, reusable Local/Docker targets, and retained files through authenticated APIs and the Console. All Threads share one explicitly selected workspace.

Optional [One Thread mode](docs/content/one-thread.md) adds a persistent Main coordinator, owned workers, a durable Inbox with automatic drain and pause controls, and explicit Main-only delivery. Generic HTTP ingress and egress are available; vendor-specific embedded adapters, file-memory organization, and scheduling/automation are later deliveries. Published `0.0.2` remains an informational CLI placeholder; these runtime capabilities require a source build until a runtime release is published.

## Start from source

From a source checkout:

```bash
uv sync --locked
uv run a13n-claw --version
uv run a13n-claw --help
make console-build
mkdir -p "$HOME/claw-workspace"
uv run a13n-claw serve --workspace "$HOME/claw-workspace"
```

Open `http://127.0.0.1:8080` and connect with the locally generated `~/.a13n-claw/operator.token`. Add a credential, model, environment, and Profile in Settings before creating work. See [Getting started](docs/content/getting-started.md) for the complete setup.

The default workspace is startup cwd; the default data root is `~/.a13n-claw`. They must be separate, non-nested directories. Local shell is host-account execution, not a sandbox. Managed credentials are plaintext in the protected SQLite database. Read [Operations](docs/content/operations.md) before granting access, exposing the listener, or backing up data.

Source builds report `0.0.0`. Release builds derive their version from `release/a13n-claw-vX.Y.Z` tags. The published `0.0.2` package is the earlier informational CLI placeholder and does not include the console.

## Develop

Python 3.13, uv, Node.js 24, pnpm 10.30.3, and Make are required. Docker is used for container and workflow checks.

```bash
make install
make check
make test
make build
make artifact-check
make docs-build
```

`console/` is the private runtime frontend; `a13n_claw/` is the Python package; `docs/` remains the independent documentation site. `make console-dev` provides frontend hot reload on port 5173 and proxies APIs to the backend on port 8080. `make build` compiles console assets into the wheel and sdist; installed serving and wheel rebuilds from the sdist require no Node.js. Docker installs the same wheel.

- [Contributing](CONTRIBUTING.md): setup, validation, and releases.
- [Development standards](DEVELOPMENT.md): code and test conventions.
- [Documentation](docs/content/index.md): current capabilities and setup.
- [Release setup](docs/content/releasing.md): GitHub environments, secrets, and initial publication.
- [Specifications](spec/README.md): accepted boundaries, not migration proposals.

The documentation is a static Fumadocs site deployed through **Cloudflare Workers Static Assets**, not Pages. The public site is configured for [a13n-claw.wh1isper.top](https://a13n-claw.wh1isper.top). It hosts documentation only, not the agent runtime; no account identifier is embedded in the repository.

## Compatibility and license

This is an independent project, not a configuration-compatible fork of YA Claw. No legacy configuration or persistence format is supported. Licensed under [BSD-3-Clause](LICENSE), matching YA Claw / ya-mono. The license correction starts with `0.0.2`; previously published `0.0.1` artifacts remain unchanged.
