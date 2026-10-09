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

`console/` owns a standalone private React/TypeScript/Vite application. `a13n_claw/` owns the Python package and static server. `make console-dev` serves the frontend with hot reload; `make console-check` validates its types and formatting. Neither starts an execution backend.

`make console-build` installs locked dependencies and writes the generated bundle to `a13n_claw/static/console/`. `make serve` serves that bundle through FastAPI/Uvicorn on loopback port 8080. There is no SPA catch-all: unknown APIs and missing assets return 404.

`make build` builds the console first, then the wheel and sdist. Direct `uv build` requires a prior console build and fails if assets are absent. Editable installs do not require assets until you serve the console. Generated assets are not committed, but both distributions contain them; the sdist also contains frontend source. Installation and sdist-to-wheel rebuilds do not require Node.js. `make artifact-check` installs both wheels outside the checkout and tests real HTTP responses and their referenced JavaScript/CSS. `make image-check` also verifies serving from the non-root image.

## Documentation

Portable Markdown lives under `docs/content/`. The Fumadocs application under `docs/` uses Next.js static export. Run `make docs-serve` to preview it and `make docs-build` to build the site and check internal links and anchors.

The static export is uploaded as a GitHub Actions artifact. A configured deployment publishes that same artifact through Cloudflare Workers Static Assets. The site requires no Next.js server or Cloudflare runtime code.

## Future runtime work

Harness will own agent execution primitives. Claw will own its application-specific lifecycle. Add dependencies and accepted contracts when implementing those features, not merely to fill a scaffold. Do not add a legacy YA configuration translation layer.
