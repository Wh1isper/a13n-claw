# Development Standards

## Code quality and design

Prefer direct flows, cohesive modules, and explicit ownership. Fix causes at their owner instead of adding overlapping wrappers. Add abstractions, persistence, infrastructure, and dependencies only when an implemented feature requires them. Read affected tests and specifications before editing.

The runtime contains an authenticated API, durable SQLite work state, fresh Harness executions, managed Local/Docker environments, and a real Console. Keep readiness specific: constructing a client or starting the dispatcher does not prove provider or MCP connectivity. Test fixtures must stay outside production entry points.

## Python

Use Python 3.13, typed public interfaces, Ruff, Pyright, and deptry. Use `isinstance` for type-based branching. Tests are function-style pytest tests, offline by default. The installed distribution metadata owns the version; do not duplicate release versions in source modules.

## Runtime ownership

Use Harness public primitives for execution, checkpoint serialization, decisions, tools, and environments. Claw owns admission, current authority, durable outcomes, and target lifecycle rather than forking Harness. SQLite transactions run in short worker-thread operations; never retain a transaction across execution, external I/O, or waits. Publish the full checkpoint, Thread head, Run outcome, and input incorporation proof atomically. Live events cannot replace saved facts.

An Instance has one process owner and one shared workspace, separate from its protected data root. Captured definitions govern admitted work while current grants can revoke authority. Recovery must not replay unknown external effects. Target mutation must settle before its ownership lock is released. Treat Local shell as host-account authority, not a sandbox.

## Documentation and frontend

Keep user content in portable Markdown under `docs/content/`, with front matter and `meta.json` navigation. The private Fumadocs application uses Next.js static export. Its build checks internal links and anchors. Avoid duplicated documentation sources and external font requests at build time. Use Mermaid for diagrams when needed; ensure the site renderer supports any introduced syntax.

The runtime console is a separate private React/TypeScript/Vite application under `console/`. Use authoritative saved API views, explicit pending states, exact decision correlation, and version-preconditioned forms. Keep tokens and unsaved drafts in tab memory; render retained output as untrusted text. Reconcile ambiguous submissions by their original identity, never silently submit a new command. Use accessible semantic controls, responsive layouts, and local system fonts. Verify the rendered result and affected interactions in a browser.

## Automation and distribution

The Makefile is the stable local interface used by CI. GitHub-owned actions use readable major-version tags. Workflows use least-required permissions, explicit timeouts, and immutable release tags. Credentials belong in GitHub Environment secrets, not YAML or committed environment files.

Ship one non-root container from the same wheel published in the release. The container prints help by default and starts the runtime only when explicitly invoked with `serve --host 0.0.0.0`. Its writable data and workspace directories are separate and owned by its non-root user. Build the console before Python artifacts; both wheel and sdist include its generated assets so installed serving and sdist-to-wheel builds need no Node.js. RCs publish exact version tags only; stable releases also update `latest`. Keep release checks in CI rather than duplicating full test suites during publication.
