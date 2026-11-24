# Repository Guide

A13n Claw is a Service App that owns durable Inbox, delivery history, managed worker associations, and scheduled work. Service owns execution, resources and Console. Read [the accepted specification](spec/README.md) before changing those boundaries.

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

Use Python 3.13 and uv. Build against the published Service pin; do not depend on an adjacent checkout. `frontend/` owns the Console contribution and `docs/` owns the separate documentation site. Package the generated App assets in the wheel and sdist. Preserve standalone data. The non-root release image inherits the exact Service image and version-tag publication flow.

## Validation

Select checks proportionately from [CONTRIBUTING.md](CONTRIBUTING.md#validation). Add function-style regression tests for changed behavior. Reuse valid passing results. Report exact checks and material limitations without claiming external deployments were tested locally. Keep all canonical code, documentation, and commit messages in English.
