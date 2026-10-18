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

`host.py` opens the native App; `lifecycle.py` holds the Instance lease and owns adapter/application lifetimes. `business.py` owns short SQLite transactions; `service.py` owns product dispatch, scoped tool authority, and delivery. `controls.py` implements exact IM interactions. `lark.py` owns platform normalization and asynchronous wire transport behind the neutral `MessagingAdapter` protocol. `api.py` and `cli.py` provide trusted operator access.

Claw uses published bounded Harness UI/Harness dependencies. It does not import neighboring checkouts, private Host coordinators, or Host database tables. Native worker collaboration, environment tools, and Global/Project memory remain native capabilities. Cross-owner changes need invariant-based tests, especially around admission, uncertainty, and publication.

## SQLite migrations

`a13n_claw/business_schema.py` owns product metadata. `a13n_claw/business_migrations/versions/` contains its linear generated Alembic history. Host state has a separate owner and migration history.

```bash
make db-migrate msg="describe the schema change"
uv run --locked ruff check --select I --fix a13n_claw/business_migrations/versions
uv run --locked ruff format a13n_claw/business_migrations/versions
uv run --locked pytest tests/test_business_migrations.py tests/test_migrations.py
```

Generation upgrades a disposable SQLite database, never an operator Instance. Review generated operations and add data backfills where needed. Historical revisions keep their own definitions. SQLite table rebuilds and revision changes commit together after foreign-key validation; failed migrations roll back. Tests cover metadata parity, populated upgrades, repeated startup, unknown/prebaseline rejection, rollback, and single-head history. Downgrades use backups.

## Console and packaging

`console/` is a private React/Vite Hello World page. `make console-build` places assets in `a13n_claw/static/console/`; `make build` includes them in the wheel and sdist. Installed serving and sdist rebuilds need no Node.js. Direct `uv build` requires prebuilt assets. The API can run without assets using `serve --no-console` after explicit initialization.

`make artifact-check` installs the original wheel and a wheel rebuilt from the sdist outside the checkout, initializes isolated Instances, starts real HTTP listeners, and checks authentication, migration assets, and static-file boundaries. `make image-check` validates non-root initialization, serving, and native configuration persistence across replacement of disposable containers.

## Runtime checks

`make test` uses no external service credentials. Native FunctionModel tests exercise real App execution, tool context, files, decisions, steering, cancellation, restart evidence, routing, and operator attention. Lark tests use local WebSocket and HTTP protocol fixtures; they verify ACK ordering, serialization, and uncertain sends rather than a real tenant. Loopback provider checks use the production model client, not a production account. Linux checks do not replace Windows CI.

## Documentation

Portable Markdown lives in `docs/content/`; the separate Fumadocs/Next.js application exports a static site. `make docs-build` also checks links and anchors. Cloudflare Workers Static Assets hosts documentation only, not the runtime. Keep operational examples aligned with implemented CLI/API surfaces.
