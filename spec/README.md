# A13n Claw Specification

A13n Claw is a local-first, self-hosted agent application built on a13n Harness. It combines persistent conversations and background work with an API-driven console, managed execution environments, and external messaging clients.

These specifications define the accepted target product and its high-level contracts, not a claim that every feature is available. The source implements the authenticated API, durable Thread and Run execution, managed Local/Docker environments, an API-driven Console, and optional One Thread coordination with persistent workers, durable Inbox processing, and generic HTTP ingress/egress. File memory, vendor-specific embedded bridges, and general automation remain target contracts. [Repository boundaries](repository-model.md) distinguish source implementation from release availability.

## Design Boundary

Claw owns application state, admission, execution ownership, recovery, environment management, and client delivery. Harness owns agent execution and portable continuation. The console and bridges are clients of the same application authority, not alternative agent runtimes.

The design is single-node and operator-controlled, with one server hosting the API, console, embedded bridges, automation, and environment manager. Direct conversation messages use always-steer admission; One Thread external ingress first retains a durable Inbox item and requests Main attention. Group-message filtering precedes either route. It does not introduce a hosted multi-tenant platform or a distributed worker scheduler. Configuration, APIs, and saved state follow this project's own contracts.

The Instance selects one conversation mode at startup: per-Channel routing directly to Threads, or One Thread routing external messages through one canonical Main Thread's Inbox with persistent owned workers. Neither mode introduces Session or Project entities. The Instance shares one startup or configured workspace directory across all Threads. Memory uses Global and Thread-private plain-file scopes with optional background organization; neither workspace nor memory requires a Project catalog.

These documents specify responsibilities, relationships, observable lifecycles, and key flows. Database products, tables, physical file layouts, class hierarchies, endpoint schemas, transport choices, and deployment recipes are outside this design level.

## Reading Order and Ownership

| Document                                                                        | Owns                                                                                                      |
| ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| [00 — Overview](00-overview.md)                                                 | Product scope, architectural boundaries, and the end-to-end work path                                     |
| [01 — Domain model](01-domain-model.md)                                         | Shared vocabulary, identities, relationships, and ownership                                               |
| [02 — Configuration and composition](02-configuration-and-composition.md)       | Reusable definitions, defaults, and the configuration captured for work                                   |
| [03 — Execution lifecycle](03-execution-lifecycle.md)                           | Admission, serialized continuation, decisions, cancellation, and delegation                               |
| [04 — Persistence and recovery](04-persistence-and-recovery.md)                 | Durable truth, checkpoint publication, restart, and retention                                             |
| [05 — Workspaces and environment management](05-workspaces-and-environments.md) | Working context, managed targets, Docker lifecycle, and data boundaries                                   |
| [06 — API, console, and access](06-api-console-and-access.md)                   | Application command/query boundary, console behavior, and caller authority                                |
| [07 — Bridges and clients](07-bridges-and-clients.md)                           | Platform-neutral ingress, conversation bindings, interaction, and delivery                                |
| [08 — Automation](08-automation.md)                                             | Schedules, heartbeat, workflows, and bounded autonomous follow-up                                         |
| [09 — Memory](09-memory.md)                                                     | Global and Thread-private file memory, projection, organization, and retention                            |
| [10 — One Thread mode](10-one-thread-mode.md)                                   | Exclusive Instance mode, persistent worker collaboration, durable Inbox, drain, and Main-only publication |
| [Repository boundaries](repository-model.md)                                    | Source surfaces, distribution, compatibility, and current implementation boundary                         |

Start with 00 and 01. For the main user path, continue through 02–04, then 06–07. For unattended work, read 05 and 08–09 with the execution and recovery contracts. For centralized conversation handling, read 10 with 03–04 and 07. A concept's owning document takes precedence over summaries elsewhere.
