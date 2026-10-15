# A13n Claw Specification

A13n Claw is a local-first assistant product built on the embedded Harness UI Host. One Claw process composes one `HarnessUiApp` from the `a13n-harness-ui` Python package, Claw messaging and automation services, and Claw's own API and frontend. Harness UI owns the Agent Host; Claw owns the product built around it.

## Design Boundary

The dependency direction is Claw product services → Harness UI App → Harness. Claw uses an in-process library boundary, not the Harness UI WebUI HTTP protocol, a second server, or a switchable local/remote backend framework. The stock Harness UI browser and terminal adapters are not required to run Claw.

Harness UI owns configuration resources, Projects, Threads, execution composition, root and child execution, decisions, continuation, environments, file memory, and native collaboration. Claw retains Channel routing, durable Inbox and automation intent, external delivery, product access policy, and its frontend. Claw refers to Host identities and observations instead of maintaining a second execution store or state machine.

Business acceptance, Host admission, saved continuation, business settlement, and external delivery are distinct boundaries. Retained Inbox work survives restart; Host receipts and unsaved execution input do not become durable merely because Claw embeds the Host. Uncertain submissions and effects block automatic replay. [Persistence and recovery](04-persistence-and-recovery.md) owns this deliberate reliability boundary.

## Specification and Source Status

These documents define the accepted replacement architecture. The current source still implements the earlier standalone Harness-based runtime. This specification change does not implement embedding, change dependencies, convert existing data, or make an upstream embedding API available. [Repository boundaries](repository-model.md#source-implementation-and-release-availability) owns source and release status. [Host integration](11-host-integration.md) defines the library contract required before implementation can claim conformance.

The specifications retain product requirements for One Thread and per-Channel routing, embedded messaging adapters, and automation. They replace the old Claw-owned execution, configuration, workspace lifecycle, and Thread-private memory contracts with Host ownership. Deployment and wire-format details belong to implementation documentation; required observable behavior and ownership belong here.

## Reading Order and Ownership

| Document                                                                  | Owns                                                                         |
| ------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| [00 — Overview](00-overview.md)                                           | Product architecture, ownership, and end-to-end flow                         |
| [01 — Domain model](01-domain-model.md)                                   | Product concepts, references to Host concepts, and identity boundaries       |
| [02 — Configuration and composition](02-configuration-and-composition.md) | Host resource authority and separate Claw settings                           |
| [03 — Execution lifecycle](03-execution-lifecycle.md)                     | Product-to-Host submission, controls, and business progress                  |
| [04 — Persistence and recovery](04-persistence-and-recovery.md)           | Separate storage ownership, dispatch uncertainty, restart, and retention     |
| [05 — Workspaces and environments](05-workspaces-and-environments.md)     | Host-managed working context and product exposure                            |
| [06 — API, console, and access](06-api-console-and-access.md)             | Claw frontend/API, trust, and observation semantics                          |
| [07 — Bridges and clients](07-bridges-and-clients.md)                     | Qualified ingress, filtering, routing, and external delivery                 |
| [08 — Automation](08-automation.md)                                       | Schedule occurrences and business orchestration through Host execution       |
| [09 — Memory](09-memory.md)                                               | Adoption of Host memory scopes and maintenance                               |
| [10 — One Thread mode](10-one-thread-mode.md)                             | Main selection, worker integration, Inbox dispositions, and drain            |
| [11 — Host integration](11-host-integration.md)                           | Required embeddable library boundary, extension rules, and conformance tests |
| [Repository boundaries](repository-model.md)                              | Source status, packaging, compatibility, and repository ownership            |

Start with 00, 01, and 11 for the architecture. Read 03–04 for the execution and reliability boundary, then 07 and 10 for the main messaging path. Read 02, 05, and 09 together to understand the Host concepts Claw adopts. A concept's owning document takes precedence over summaries elsewhere.
