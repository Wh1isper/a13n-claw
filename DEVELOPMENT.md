# Development Standards

## Code quality and design

Follow the [accepted specifications](spec/README.md). Keep product state and native Service state distinct. Use direct, typed interfaces and add infrastructure only where the contract requires it. Record readiness from observed facts, separately from configuration.

Use Python 3.13, Ruff, Pyright, and deptry. Prefer explicit `isinstance` checks and function-style pytest tests. Tests use synthetic provider inputs and a disposable PostgreSQL database; they must not depend on provider credentials.

## Service integration

Claw is a Service App and a Harness plugin. Service owns identity, authorization, native Agent, Thread and Run execution, Project resources, and fresh-Thread scheduling. Claw owns Inbox data, delivery history, Main-mode schedules, and associations with native resources. Keep references scoped to their workspace. Revalidate execution identity and the attempt lease in the transaction that changes business state.

Use short, async SQLAlchemy transactions on Service storage. Use deterministic identities for operations retried after uncertain admission. Do not hold database locks across model execution, provider I/O, or waits. Notify or perform best-effort platform feedback after business acceptance commits. Persist native IDs for investigation; submission and completion are separate facts.

## Database schema

`a13n_claw/service_app/models.py` owns current AppDatabase metadata. Generate revisions with `a13n-service apps db revision --app a13n.claw` against a disposable database and review the operations before landing them. Service installs the AppDatabase migrations; the App does not issue runtime DDL. Use `make db-check` on PostgreSQL to check the migrated schema against metadata. Preserve published revisions and keep standalone data in its original data root.

## Documentation and frontend

Canonical user documentation is under `docs/content/`, with front matter and `meta.json` navigation. The docs site is separate from the runtime Console contribution in `frontend/`. Use the Service-generated `@a13n/console` bridge for native components, scoped API requests, query caching, authorization, and navigation. Share Console tokens and semantics, keep drafts local, and render retained messages as text. Validate important states in the real Console at desktop and narrow widths.

## Automation and distribution

The Makefile is the stable interface for local and CI checks. The Python package supports one bounded Service minor line. CI and release images use the locked patch and matching digest-pinned base; the `:dev` image uses the latest stable compatible patch and matching base. Build the Console before packaging; both the wheel and source distribution include generated assets, and rebuilding a wheel from the source distribution requires no Node.js. The image installs the same Claw wheel and inherits Service's non-root runtime and role selection. Stable version tags also advance `latest`; release candidates receive only exact version tags.
