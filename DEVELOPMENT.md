# Development Standards

## Code quality and design

Prefer direct flows, cohesive modules, and explicit ownership. Fix causes at their owner instead of adding overlapping wrappers. Add abstractions, persistence, infrastructure, and dependencies only when an implemented feature requires them. Read affected tests and specifications before editing.

The backend embeds one published Harness UI App, an authenticated operator API, Lark connections, and durable SQLite business state. The runtime frontend is a Hello World placeholder. Keep readiness specific: constructing a client or starting the dispatcher does not prove provider or MCP connectivity. Test fixtures must stay outside production entry points.

## Python

Use Python 3.13, typed public interfaces, Ruff, Pyright, and deptry. Use `isinstance` for type-based branching. Tests are function-style pytest tests, offline by default. The installed distribution metadata owns the version; do not duplicate release versions in source modules.

## Host integration

The [replacement specification](spec/README.md) adopts one package-embedded Harness UI App. New embedding work uses its supported lifecycle, configuration, Thread, execution, environment, collaboration, and memory boundaries. Claw owns product state and Capabilities, not a second runtime. Follow the [required library contract](spec/11-host-integration.md), starting with existing public operations and native Capabilities. Do not import private coordinators, edit Host persistence, or add a parallel HTTP backend. A concrete unsupported required interaction can justify a focused upstream change; optional unavailable features do not block the basic IM path. Keep native collaboration and execution controls under Host ownership.

## Product persistence and execution

An Instance has one process owner. Product services retain intent before Host dispatch or external sends and keep uncertain outcomes blocked. Host receipts are process-local; saved exact evidence, business settlement, and external delivery are independent. Native notifications remain best effort. Do not replay unknown effects or infer failure from missing receipt evidence.

SQLite transactions run in short worker-thread operations; never retain one across execution, external I/O, or waits. Product tools bind actual execution identity and current source/destination grants. Workspace and protected Instance data are separate. Native Global/Project memory and shared working files retain their Host boundaries. Treat Local shell as host-account authority, not a sandbox.

## Database schema

Host state and migrations belong exclusively to Harness UI. Claw owns only business state. Existing standalone data is not a Host database and cannot be silently reinterpreted or deleted.

SQLAlchemy Core metadata in `a13n_claw/business_schema.py` owns the current schema; package-owned Alembic revisions own its evolution. Generate revisions through `make db-migrate`, review them, and keep one linear history. Published revisions retain their historical definitions. Runtime startup uses migrations, not inline DDL, `create_all`, or version-specific branches in BusinessStore. Application reads and writes continue to use short `sqlite3` transactions.

Migrate under the Instance lease before application state is loaded. Schema changes, data transformations, and the revision marker commit atomically; SQLite table rebuilds must preserve references and pass foreign-key validation before commit. Test metadata parity, populated upgrades, rollback, and repeated startup. Unknown revisions fail explicitly; downgrading requires a pre-upgrade backup.

## Documentation and frontend

Keep user content in portable Markdown under `docs/content/`, with front matter and `meta.json` navigation. The private Fumadocs application uses Next.js static export. Its build checks internal links and anchors. Avoid duplicated documentation sources and external font requests at build time. Use Mermaid for diagrams when needed; ensure the site renderer supports any introduced syntax.

The runtime frontend is a separate private React/TypeScript/Vite application under `console/`, reduced to Hello World for the replacement scope. IM and CLI/API provide the functional interaction surfaces. Any later Console uses authoritative saved API views, explicit pending states, exact decision correlation, and version-preconditioned forms. Keep tokens and unsaved drafts in tab memory; render retained output as untrusted text. Reconcile ambiguous submissions by their original identity, never silently submit a new command. Use accessible semantic controls, responsive layouts, and local system fonts. Verify the rendered result and affected interactions in a browser.

## Automation and distribution

The Makefile is the stable local interface used by CI. GitHub-owned actions use readable major-version tags. Workflows use least-required permissions, explicit timeouts, and immutable release tags. Credentials belong in GitHub Environment secrets, not YAML or committed environment files.

Ship one non-root container from the same wheel published in the release. The container prints help by default and starts the runtime only when explicitly invoked with `serve --host 0.0.0.0`. Its writable data and workspace directories are separate and owned by its non-root user. Build the console before Python artifacts; both wheel and sdist include its generated assets so installed serving and sdist-to-wheel builds need no Node.js. RCs publish exact version tags only; stable releases also update `latest`. Keep release checks in CI rather than duplicating full test suites during publication.
