# a13n Claw

[![CI](https://github.com/Wh1isper/a13n-claw/actions/workflows/ci.yml/badge.svg)](https://github.com/Wh1isper/a13n-claw/actions/workflows/ci.yml)

A fresh, local-first agent runtime project targeting [a13n Harness](https://github.com/converge-ai-labs/agent-foundation), inspired by [YA Claw](https://github.com/Wh1isper/ya-mono/tree/main/packages/ya-claw).

**Current scope: placeholder package.** The initial `0.0.1` release establishes the package identity, repository conventions, documentation, and release automation. It does **not** execute agents or provide a server, Web UI, persistent sessions, scheduling, or bridges. Harness integration will be implemented separately; the placeholder intentionally has no runtime dependencies.

## Try the placeholder

From a source checkout:

```bash
uv sync --locked
uv run a13n-claw --version
uv run a13n-claw --help
```

Source builds report `0.0.0`. Release builds derive their version from `release/a13n-claw-vX.Y.Z` tags. Once `0.0.1` has been published, install the package with `uv tool install a13n-claw==0.0.1`.

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

- [Contributing](CONTRIBUTING.md): setup, validation, and releases.
- [Development standards](DEVELOPMENT.md): code and test conventions.
- [Documentation](docs/content/index.md): current capabilities and setup.
- [Release setup](docs/content/releasing.md): GitHub environments, secrets, and initial publication.
- [Specifications](spec/README.md): accepted boundaries, not migration proposals.

The documentation is a static Fumadocs site deployed through **Cloudflare Workers Static Assets**, not Pages. The public site is configured for [a13n-claw.wh1isper.top](https://a13n-claw.wh1isper.top). It becomes available after Cloudflare credentials and the custom domain are configured; no account identifier is embedded in the repository.

## Compatibility and license

This is an independent project, not a configuration-compatible fork of YA Claw. No legacy configuration or persistence format is supported. Licensed under [Apache-2.0](LICENSE).
