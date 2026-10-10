# Repository Guide

A13n Claw is an independent, single-node agent runtime built on published a13n Harness primitives. The Python application owns durable work, access, configuration, and environment management; the React Console is its API frontend.

## Sources of truth

- [CONTRIBUTING.md](CONTRIBUTING.md) owns setup, validation, and release procedures.
- [DEVELOPMENT.md](DEVELOPMENT.md) owns engineering standards.
- [spec/README.md](spec/README.md) indexes accepted contracts.
- `docs/content/` owns English user-facing Markdown and `meta.json` navigation. `docs/` owns the Fumadocs site, package tooling, and Cloudflare Worker configuration.
- [MAINTAINERS.md](MAINTAINERS.md) owns reviewer routing.
- [.agents/skills/README.md](.agents/skills/README.md) indexes repository-local agent workflows. Skills follow these guides and do not grant additional authorization.

Read the relevant implementation, tests, and contracts before changing behavior. Keep proposals and open decisions in GitHub Issues, not `spec/`. Keep implementation, tests, documentation, and automation aligned. Do not describe planned features as available.

## Scope and safety

Preserve unrelated work and secrets. An implementation request does not automatically authorize publication, deployment, deletion, or history rewrites. Follow the user's authorization for GitHub operations. Do not introduce YA configuration compatibility. Do not copy YA runtime code merely to create the appearance of functionality.

## Package and release boundaries

Use Python 3.13, uv, `a13n-claw` distribution names, and `a13n_claw` Python imports. This single-package repository keeps `a13n_claw/` directly at its root, without a `src/` wrapper. Keep the source version at `0.0.0`; release preparation injects the canonical tag version into an ephemeral checkout. Harness dependencies use bounded published requirements, not paths into a neighboring agent-foundation checkout.

`console/` owns the private React/Vite console; `docs/` owns the separate documentation site. Neither frontend is an npm release. Build console assets into `a13n_claw/static/console/` and include them in both wheel and sdist. Installed serving and sdist rebuilds require no Node.js. The server owns authenticated application APIs and durable SQLite state as well as static assets. Optional One Thread coordination, persistent workers, durable Inbox/drain, and generic HTTP ingress/egress are available in source. Memory organization, vendor-specific embedded adapters, and general automation remain later deliveries; do not describe them as available. Generated Python artifacts, static exports, caches, and credentials are not committed. Cloudflare hosts documentation only; it does not host an agent runtime. RC releases must never advance a container `latest` tag.

## Validation

Select checks proportionately from [CONTRIBUTING.md](CONTRIBUTING.md#validation). Add function-style regression tests for changed behavior. Reuse valid passing results. Report exact checks and material limitations without claiming external deployments were tested locally. Keep all canonical code, documentation, and commit messages in English.
