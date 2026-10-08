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

`make install` synchronizes both lockfiles and installs pre-commit hooks. Read the repository's [contributor guide](https://github.com/Wh1isper/a13n-claw/blob/main/CONTRIBUTING.md) for focused checks and review conventions.

## Documentation

Portable Markdown lives under `docs/content/`. The Fumadocs application under `docs/` uses Next.js static export. Run `make docs-serve` to preview it and `make docs-build` to build the site and check internal links and anchors.

The static export is uploaded as a GitHub Actions artifact. A configured deployment publishes that same artifact through Cloudflare Workers Static Assets. The site requires no Next.js server or Cloudflare runtime code.

## Future runtime work

Harness will own agent execution primitives. Claw will own its application-specific lifecycle. Add dependencies and accepted contracts when implementing those features, not merely to fill a scaffold. Do not add a legacy YA configuration translation layer.
