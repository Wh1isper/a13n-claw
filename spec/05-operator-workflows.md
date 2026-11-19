# Operator Workflows

A13n Console is the operator UI. Claw presents its business state in that Console and uses native Service surfaces for Service-owned resources and execution facts. Headless setup, status, and maintenance remain available without loading the frontend. Both paths use Service identity and workspace authorization, with Claw checking authority for its business operations. Headless access does not require a second Claw operator-token store.

## Setup and Diagnosis

An operator enables the installed App through Service, configures a Claw and its connectors in the intended Workspace, and creates or attaches its [Project](04-project.md). Setup exposes which pieces are ready and which need attention: the Project resources, connector intake, and [reply CLI and account access](03-connectors-and-replies.md#project-setup). The operator can optionally describe available worker models for Main's delegation choices; default [agent composition](04-project.md#agent-composition) requires no per-tool configuration. Valid configuration alone does not establish platform connectivity or a ready reply path. An operator can identify the affected Claw, resource, or connection without inspecting an agent transcript or exposing credentials.

## Console Jobs

| Job               | Operator outcome                                                                                                                                                                                                             |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Overview          | Inspect a Claw's status, Inbox and Project, and find its Main and workers. Inspect recorded usage by time and model and follow the relevant Thread for detail.                                                               |
| Runs              | Investigate saved history, configuration, usage, and retained tools with exact Thread and Run references. Claw links the business sources it records; Service owns the execution facts. The investigation view is read-only. |
| Models & Accounts | Use Service's model and provider configuration and account support. Distinguish saved configuration, connectivity, and observed provider allowance where Service exposes it.                                                 |
| Schedules         | Find and manage scheduled work and inspect its outcomes separately from the Main Thread's idle heartbeat.                                                                                                                    |

Current activity and the latest saved Run outcome are distinct. Show captured configuration separately from selections for the next Run. A disconnected observation stream is not evidence that execution ended; refresh the authorized saved view to recover observation. Exact execution actions, including stop and decision responses, are separate from read-only inspection.

Reported usage comes from Service facts. Keep partial or unknown pricing visible. Provider allowance is a separate observation with its own timestamp; stale or unavailable allowance does not become zero usage or zero remaining quota.

## Backup and Restore

Protect Claw's business data in its AppDatabase alongside the corresponding Service state. A recovery plan also accounts for the persistent Environment, any Browser or memory backing data, and the credential configuration needed to restore access. External provider state remains subject to that provider's backup and recovery support. Restoring database rows alone does not restore files, browser profiles, credentials, or platform side effects.

Before resuming external intake or publication after a restore, verify the target Claws, resource references, and connector identities. Do not replay an operation whose external outcome is unknown just because its local record was restored.

## Fresh Installation

The Service App starts from new Service App state. Claw 0.1.0 has no import or upgrade-in-place path, and its Thread, Inbox, and schedule state is not automatically replayed. Preserve any existing standalone data root; installation does not delete it.
