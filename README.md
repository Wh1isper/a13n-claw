# a13n Claw

[![CI](https://github.com/Wh1isper/a13n-claw/actions/workflows/ci.yml/badge.svg)](https://github.com/Wh1isper/a13n-claw/actions/workflows/ci.yml)

A fresh, local-first agent runtime project targeting [a13n Harness](https://github.com/converge-ai-labs/agent-foundation), inspired by [YA Claw](https://github.com/Wh1isper/ya-mono/tree/main/packages/ya-claw).

**Current scope: console placeholder, not an agent runtime.** Source builds include a responsive React console and a Python static server. Threads, environments, and settings explain planned capabilities; they do not execute agents, save state, or load credentials. Application APIs, authentication, scheduling, bridges, and Harness integration are not implemented.

## Try the placeholder

From a source checkout:

```bash
uv sync --locked
uv run a13n-claw --version
uv run a13n-claw --help
make console-build
make serve
```

Open `http://127.0.0.1:8080`. `a13n-claw serve --host 127.0.0.1 --port 8080` provides explicit bind options; no arguments still print help. This preview has no authentication, so keep it on a trusted interface.

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

`console/` is the private runtime frontend; `a13n_claw/` is the Python package; `docs/` remains the independent documentation site. `make console-dev` provides frontend hot reload on port 5173. `make build` compiles console assets into the wheel and sdist; installed serving and wheel rebuilds from the sdist require no Node.js. Docker installs the same wheel.

- [Contributing](CONTRIBUTING.md): setup, validation, and releases.
- [Development standards](DEVELOPMENT.md): code and test conventions.
- [Documentation](docs/content/index.md): current capabilities and setup.
- [Release setup](docs/content/releasing.md): GitHub environments, secrets, and initial publication.
- [Specifications](spec/README.md): accepted boundaries, not migration proposals.

The documentation is a static Fumadocs site deployed through **Cloudflare Workers Static Assets**, not Pages. The public site is configured for [a13n-claw.wh1isper.top](https://a13n-claw.wh1isper.top). It becomes available after Cloudflare credentials and the custom domain are configured; no account identifier is embedded in the repository.

## Compatibility and license

This is an independent project, not a configuration-compatible fork of YA Claw. No legacy configuration or persistence format is supported. Licensed under [BSD-3-Clause](LICENSE), matching YA Claw / ya-mono. The license correction starts with `0.0.2`; previously published `0.0.1` artifacts remain unchanged.
