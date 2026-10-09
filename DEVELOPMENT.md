# Development Standards

## Code quality and design

Prefer direct flows, cohesive modules, and explicit ownership. Fix causes at their owner instead of adding overlapping wrappers. Add abstractions, persistence, infrastructure, and dependencies only when an implemented feature requires them. Read affected tests and specifications before editing.

The placeholder contains package metadata, an informational CLI, and a static console server. It deliberately does not import or install Harness until an actual execution feature requires it. Do not add simulated services, fake health probes, or no-op runtime commands.

## Python

Use Python 3.13, typed public interfaces, Ruff, Pyright, and deptry. Use `isinstance` for type-based branching. Tests are function-style pytest tests, offline by default. The installed distribution metadata owns the version; do not duplicate release versions in source modules.

## Future runtime work

Use Harness public primitives for agent execution and continuation. Claw will own its application lifecycles rather than forking Harness. Any future persistence or authorization contract requires corresponding specification and tests. Async service code must not retain database sessions across model calls, streams, external I/O, or waits. These rules do not imply that a database or service exists today.

## Documentation and frontend

Keep user content in portable Markdown under `docs/content/`, with front matter and `meta.json` navigation. The private Fumadocs application uses Next.js static export. Its build checks internal links and anchors. Avoid duplicated documentation sources and external font requests at build time. Use Mermaid for diagrams when needed; ensure the site renderer supports any introduced syntax.

The runtime console is a separate private React/TypeScript/Vite application under `console/`. Keep navigation and preview explanations honest: no simulated runtime state or working-looking submission controls. Use accessible semantic controls, responsive layouts, and local system fonts. Verify the rendered result and affected interactions in a browser.

## Automation and distribution

The Makefile is the stable local interface used by CI. GitHub-owned actions use readable major-version tags. Workflows use least-required permissions, explicit timeouts, and immutable release tags. Credentials belong in GitHub Environment secrets, not YAML or committed environment files.

Ship one non-root container from the same wheel published in the release. The placeholder container prints help by default and serves static console assets only when explicitly invoked with `serve --host 0.0.0.0`. Build the console before Python artifacts; both wheel and sdist include its generated assets so installed serving and sdist-to-wheel builds need no Node.js. RCs publish exact version tags only; stable releases also update `latest`. Keep release checks in CI rather than duplicating full test suites during publication.
