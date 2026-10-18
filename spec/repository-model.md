# Repository and Product Boundary

## Product and Implementation Boundary

[The specification index](README.md) defines Claw as an IM-first product over an embedded Harness UI Host. The target is one process, one package-embedded App, IM adapters and headless CLI/API, and Claw-owned messaging and automation state. The replacement runtime frontend is a Hello World placeholder; a full Console is separate follow-up scope. The Host owns configuration, Threads, execution, continuation, environments, collaboration, and memory.

### Source Implementation and Release Availability

The source embeds one published Harness UI App with native configuration, Threads, decisions, collaboration, environments, and memory. Claw implements a separate business database, Lark/Feishu adapter, three routing modes, Inbox/drain, exact text controls, explicit text/file delivery, and headless CLI/API. The runtime frontend is Hello World. General automation, other adapters, rich cards, and full Console remain future work.

The standalone runtime and schema are retired. Existing standalone data requires a fresh explicit Instance; no conversion or deletion is performed. Source-facing guides describe implemented capabilities; published artifacts reflect their tagged source, not later source changes.

The replacement implementation validates public Host APIs against a published `a13n-harness-ui` version using the [integration acceptance scenarios](11-host-integration.md#integration-acceptance). Source inspection informs that work; runtime tests establish the supported package boundary. Only a demonstrated required behavior missing from public operations justifies a focused upstream request.

## Compatibility Boundary

The target deliberately replaces Claw-owned execution and resource semantics with Host semantics. In particular, the old single-file Profile catalog, durable direct-prompt queue, independent Run recovery, custom target lifecycle, and Thread-private memory contract are not preserved as wrappers around the Host. Claw retains business acceptance and explicit uncertainty handling.

Existing standalone data is not silently opened as Host state. A fresh embedded Instance or any future import requires an explicit operator path and validation. No automatic schema conversion, history migration, or deletion is authorized by this specification. Changing the implementation requires updating source-facing guides and reporting the precise migration limitations.

Host versions own their resource and saved-state compatibility; Claw owns its product API, configuration, business schema, and binding compatibility. Requirements are bounded and locked against published distributions. A neighboring checkout is reference material, never a build or runtime dependency.

## Content Ownership

| Surface           | Owner                                                           |
| ----------------- | --------------------------------------------------------------- |
| `a13n_claw`       | Python product package, embedded Host integration, CLI, and API |
| `console`         | Private Claw frontend source and build tooling                  |
| `tests`           | Product behavior, Host integration, and release tests           |
| `scripts`         | Release preparation and artifact validation                     |
| `docs/content`    | Available-behavior documentation and navigation                 |
| `docs`            | Private Fumadocs documentation site and Worker configuration    |
| `spec`            | Accepted target product and architecture contracts              |
| GitHub Issues     | Proposals and unresolved decisions                              |
| `deploy/docker`   | Non-root distribution image                                     |
| `CONTRIBUTING.md` | Setup, validation, and release procedure                        |
| `DEVELOPMENT.md`  | Engineering standards                                           |
| `AGENTS.md`       | Concise contributor guidance                                    |
| `.agents/skills`  | Repository-local contributor workflows                          |

This remains an independent single-package repository with `a13n_claw/` at its root. Claw does not vendor Harness UI internals or extract another generic Host package as part of this design. Required Host changes belong to their owner; optional unavailable features do not block independent IM capabilities.

## Distribution

The source version remains `0.0.0`; release tags define artifact versions. Claw's wheel and sdist contain its own built Console assets, and its image installs the same wheel. Installing a wheel, rebuilding from an sdist, and serving require no Node.js. Source frontend builds require their toolchain. Neither frontend is an independent npm release.

Embedding the Python Host does not require serving its bundled WebUI assets or modifying its distribution merely to remove unused assets. Claw starts no separate Harness UI server and exposes only its own selected API and frontend. The stock Host CLI, page-presence, and browser-draft contracts are not Claw public API dependencies.

The documentation site remains independent of the runtime frontend. Cloudflare hosts documentation only. The distribution image runs as a non-root user and starts the application only through an explicit serve command. Static assets convey no API authority. Operator-selected Host state, Claw business state, configuration, memory, and working data retain the ownership and backup rules in [persistence](04-persistence-and-recovery.md).
