# Repository and Product Boundary

## Product and Implementation Boundary

[The specification index](README.md) defines the target runtime and the owners of its high-level contracts. The target includes persistent execution, an API-driven console, environment management, bridges, automation, and memory. These contracts guide runtime development; their presence does not make those features available in the current package.

### Source Implementation and Release Availability

A13n Claw is an independent, public project built on published a13n Harness primitives. The source implements an authenticated application API, managed resource configuration, durable Thread and Run state, decisions and recovery, Run-scoped delegation, retained files, Local/Docker environment management, and an API-driven Console. `a13n-claw serve` hosts that application and binds to `127.0.0.1:8080` by default, with explicit host and port options. Unknown CLI arguments fail rather than pretending to perform work.

Optional One Thread mode implements canonical Main ownership, persistent workers and native collaboration, durable Inbox/attention dispositions, level-triggered drain with pause/backoff and recovery barriers, and generic HTTP ingress plus explicit Main-only egress with delivery reconciliation. The Console exposes those saved states and direct worker conversations. Global and Thread-private file memory with organization, vendor-specific embedded platform bridges, and general automation remain target contracts rather than available source capabilities. Release artifacts reflect their tagged source, not every accepted specification or later change on the development branch; source implementation does not imply publication of a new package version.

Configuration, API, and persisted state compatibility require explicit contracts in this repository. Harness integration consumes published public APIs with bounded version requirements, not a neighboring source checkout.

## Content ownership

| Surface           | Owner                                           |
| ----------------- | ----------------------------------------------- |
| `a13n_claw`       | Python package and CLI                          |
| `console`         | Private React console source and build tooling  |
| `tests`           | Offline behavioral and release-tooling tests    |
| `scripts`         | Release preparation and artifact validation     |
| `docs/content`    | User documentation and navigation               |
| `docs`            | Private Fumadocs static site and Worker config  |
| `spec`            | Accepted product and architecture contracts     |
| GitHub Issues     | Proposals and open decisions                    |
| `deploy/docker`   | Non-root distribution image                     |
| `CONTRIBUTING.md` | Setup, validation, and release procedure        |
| `DEVELOPMENT.md`  | Engineering standards                           |
| `AGENTS.md`       | Concise coding-agent guidance                   |
| `.agents/skills`  | Repository-local agent workflows and references |

The console and documentation are independent frontends. `console/` builds into generated `a13n_claw/static/console/` assets; `docs/` never becomes runtime console content. The Console issues authenticated application commands and reconciles saved API state. Unknown paths and missing assets return HTTP 404 rather than falling back to the console HTML. Static file serving is limited to the packaged console directory; authorized application file access has its own API boundary.

This is a single-package repository: `a13n_claw/` lives at the repository root, with no `src/` wrapper. Repository skills are contributor tooling, not runtime package data.

This repository owns its dependency resolution, validation, and release lifecycle. Its build and checks do not require a neighboring source checkout. A local checkout's placement does not make it part of another repository's workspace.

## Distribution

The source version remains `0.0.0`. Release tags define the version of Python artifacts and the container image. The runtime reports installed distribution metadata. Frontend tooling has no independent release identity. Generated assets and dependencies are not committed.

The wheel and sdist both contain the built console. Building from a source checkout requires the frontend toolchain and a console build; a missing bundle fails the distributable build explicitly. Editable installs can omit assets until console development begins. Installing a wheel, rebuilding a wheel from its sdist, and serving either require no Node.js. The container installs the same wheel, not a separately built frontend.

Cloudflare Workers Static Assets serves public documentation only. The image runs as a non-root user; its default command prints help and exits. An explicit `serve --host 0.0.0.0` serves the runtime and Console on container port 8080. Runtime APIs require application authority; serving static Console assets does not grant it. Protected application data and the shared workspace have separate storage boundaries.
