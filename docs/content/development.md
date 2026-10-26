---
title: Development
description: Build and validate the embedded product against published dependencies.
---

## Toolchain

Use Python 3.13, uv, Node.js 24, pnpm 10.30.3, Make, and Git. Docker validates the distribution image and workflows. `make install` installs locked dependencies and Git hooks. The contributor guide owns focused validation.

```bash
make check
make test
make build
make artifact-check
make docs-build
```

## Ownership

The root `a13n_claw/` package is organized by responsibility:

| Package          | Responsibility                                                                                                                |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `runtime/`       | Open the embedded App, own process lifetimes, compose services, dispatch retained work, and reconcile exact Host observations |
| `messaging/`     | Authorize and retain ingress; expose scoped Inbox tools; handle controls, feedback, and delivery                              |
| `adapters/`      | Normalize Lark/GitHub protocols behind `MessagingAdapter`; retain GitHub polling evidence                                     |
| `schedules/`     | Validate schedules and own durable occurrences, dispatch, and result selection                                                |
| `configuration/` | Define product settings, initialize native resources, and validate/save/reload product sources                                |
| `database/`      | Own short business transactions, canonical metadata, and generated migrations                                                 |
| `operations/`    | Enforce Instance ownership and provide backup, restore, diagnostics, and retention                                            |
| `api/`           | Authenticate trusted operators and group HTTP routes by owning service                                                        |
| `cli/`           | Parse headless commands and invoke explicit online or offline operations                                                      |

`app.py` remains the public ASGI factory and static-asset boundary. Package initializers avoid hidden initialization and compatibility forwarding layers. `tests/` mirrors the backend responsibility groups; shared fixtures live in `tests/helpers.py` rather than importing other test modules.

Claw uses published bounded Harness UI/Harness dependencies. It does not import neighboring checkouts, private Host coordinators, or Host database tables. Native worker collaboration, environment tools, and Global/Project memory remain native capabilities. Cross-owner changes need invariant-based tests, especially around admission, uncertainty, and publication.

## SQLite migrations

`a13n_claw/database/schema.py` owns product metadata. `a13n_claw/database/migrations/versions/` starts from one generated initial baseline containing the complete business schema. The earlier development revisions are retired; databases using them require a fresh Instance root and are rejected without modification. Host state has a separate owner and migration history.

```bash
make db-migrate msg="describe the schema change"
uv run --locked ruff check --select I --fix a13n_claw/database/migrations/versions
uv run --locked ruff format a13n_claw/database/migrations/versions
uv run --locked pytest tests/database/test_business_migrations.py tests/database/test_migrations.py
```

Generation upgrades a disposable SQLite database, never an operator Instance. Review generated operations and add data backfills where needed. Historical revisions keep their own definitions. SQLite table rebuilds and revision changes commit together after foreign-key validation; failed migrations roll back. Tests cover metadata parity, populated upgrades, repeated startup, unknown/prebaseline rejection, rollback, and single-head history. Downgrades use backups.

## Console and packaging

`console/` is a private React/TypeScript/Vite operator application with four lazy-loaded pages. `make console-build` places assets in `a13n_claw/static/console/`; `make build` includes them in the wheel and sdist. Installed serving and sdist rebuilds need no Node.js. Direct `uv build` requires prebuilt assets. The API can run without assets using `serve --no-console` after explicit initialization.

`make console-schema` exports FastAPI's schema without opening an Instance and regenerates `console/src/api/schema.d.ts`. The generator has its own locked TypeScript 5 toolchain under `console/codegen/`; the application uses TypeScript 7. `make console-schema-check` rejects drift. `make console-test` runs component and protocol regressions. `make console-e2e` builds assets, installs Chromium, and exercises a disposable real Host/server through Playwright. The fixture model produces deterministic replies without provider calls; screenshots and traces stay under ignored `build/`.

`make artifact-check` installs the original wheel and a wheel rebuilt from the sdist outside the checkout, initializes isolated Instances, starts real HTTP listeners, and checks authentication, migration assets, and static-file boundaries. `make image-check` validates non-root initialization, serving, and native configuration persistence across replacement of disposable containers.

## Runtime checks

`make test` uses no external service credentials. Native FunctionModel tests exercise real App execution, tool context, files, decisions, steering, cancellation, restart evidence, routing, and operator attention. Lark tests use local WebSocket and HTTP protocol fixtures; they verify ACK ordering, serialization, and uncertain sends rather than a real tenant. Loopback provider checks use the production model client, not a production account. Linux checks do not replace Windows CI.

## Documentation

Portable Markdown lives in `docs/content/`; the separate Fumadocs/Next.js application exports a static site. `make docs-build` also checks links and anchors. Cloudflare Workers Static Assets hosts documentation only, not the runtime. Keep operational examples aligned with implemented CLI/API surfaces.
