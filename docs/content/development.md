---
title: Development
description: Build and validate the Service App and its Console bundle.
---

Use Python 3.13, uv, Node.js 24, and pnpm 10.30.3. `make install` prepares the local toolchain. `make check` checks backend and Console code; `make build artifact-check` verifies installed distributions and sdist rebuilding without Node.js.

## App database

Change AppDatabase metadata in `a13n_claw/service_app/models.py` and generate an Alembic revision with `a13n-service apps db revision --app a13n.claw --scratch-url …`. Use disposable PostgreSQL. `make db-check` applies migrations and checks schema parity when `A13N_DATABASE__URL` and `CLAW_TEST_DATABASE_URL` are set to that disposable database. `make test` runs the offline tests and PostgreSQL tests when the test variable is present.

## Console

`make console-build` exports the exact installed Service's Console bridge declaration and builds the private `frontend/` package. The wheel and sdist include its ESM bundle and styles. Console supplies routing, authentication, query cache and UI primitives.
