# A13n Claw Specification

Claw is an IM-first application on A13n Service. One Service deployment can host multiple Claw Instances. Claw owns the external conversation, its business state, and its operator experience. Service owns the agents and their execution. Claw contributes its pages directly to A13n Console.

These documents describe the Service App target. The embedded Harness UI product remains available as [Claw 0.1.0](https://github.com/Wh1isper/a13n-claw/tree/release/a13n-claw-v0.1.0/spec); its released workflows are the behavioral baseline, not the architecture of this line. See [source and release availability](repository-model.md#source-implementation-and-release-availability).

| Document                                                                  | Owns                                                               |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| [00 — Overview](00-overview.md)                                           | Product and architecture                                           |
| [01 — Domain model](01-domain-model.md)                                   | Instance, Channel, business work, and native references            |
| [02 — Configuration and composition](02-configuration-and-composition.md) | Instance configuration, resource selection, and change boundaries  |
| [03 — Execution lifecycle](03-execution-lifecycle.md)                     | Submission, observation, decisions, and exact controls             |
| [04 — Persistence and recovery](04-persistence-and-recovery.md)           | Durable work, uncertain outcomes, retention, backup, and migration |
| [05 — Workspaces and environments](05-workspaces-and-environments.md)     | Authorized working context, files, environments, and browsers      |
| [06 — API, Console, and access](06-api-console-and-access.md)             | Operator pages, headless workflows, and access                     |
| [07 — Bridges and clients](07-bridges-and-clients.md)                     | Lark, GitHub, ingress, and external delivery                       |
| [08 — Automation](08-automation.md)                                       | Native schedules and Claw notification and control policy          |
| [09 — Memory](09-memory.md)                                               | Shared and Channel memory                                          |
| [10 — One Thread mode](10-one-thread-mode.md)                             | Main, workers, and Inbox settlement                                |
| [11 — Service integration](11-host-integration.md)                        | App boundary and division of platform and product authority        |
| [Repository boundaries](repository-model.md)                              | Source status, distribution, and compatibility                     |
