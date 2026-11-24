# Operator Workflows

A13n Console is the operator UI. Claw presents its business state in that Console and uses native Service surfaces for Service-owned resources and execution facts. Headless setup, status, and maintenance remain available without loading the frontend. Both paths use Service identity and workspace authorization, with Claw checking authority for its business operations. Headless access does not require a second Claw operator-token store.

## Setup and Diagnosis

An operator enables the installed App through Service. The Console sidebar groups Overview, Deliveries, Connectors, and Schedules under Claw. Workspace administrators configure Claws in Workspace Settings. Operators manage connectors on the Connectors page in the intended Workspace, and bind the Claw’s [Project](04-project.md) in Workspace Settings. Setup exposes which pieces are ready and which need attention: the Project resources, connector intake, and [reply CLI and account access](03-connectors-and-replies.md#project-setup). The operator can optionally describe available worker models for Main's delegation choices; default [agent composition](04-project.md#agent-composition) requires no per-tool configuration. Console offers a Project setup after Claw creation as well as initial selection. It shows the concrete native resources, provides a path to create or select them, and distinguishes missing setup, preparation, normal stop, and unavailable resources. Lifecycle management stays in Service. Valid configuration alone does not establish platform connectivity or a ready reply path. An operator can identify the affected Claw, resource, or connection without inspecting an agent transcript or exposing credentials.

Archiving a Claw stops new intake and Claw-driven execution. The Claw keeps its business records under the existing retention rules, and archive does not delete native resources. Administrators can sync the pinned Agent revision from the Agent's default using the same summarization requirements as initial setup. Already captured Runs remain unchanged.

## Console Jobs

| Job               | Operator outcome                                                                                                                                                                                                             |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Overview          | Inspect actionable Main and Project conditions, Inbox counts and compact usage by time and model before reviewing recent deliveries and workers. Follow native Thread and Run links.                                         |
| Deliveries        | Inspect retained previews and filter by admission and Inbox state. Opening a delivery does not mark it read.                                                                                                                 |
| Connectors        | Manage intake and write-only credentials, verify connectivity, and distinguish listener state from event receipt and reply authorization.                                                                                    |
| Runs              | Investigate saved history, configuration, usage, and retained tools with exact Thread and Run references. Claw links the business sources it records; Service owns the execution facts. The investigation view is read-only. |
| Models & Accounts | Use Service's model and provider configuration and account support. Distinguish saved configuration, connectivity, and observed provider allowance where Service exposes it.                                                 |
| Schedules         | Find and manage scheduled work and inspect occurrence and processing outcomes separately from the Main Thread's idle heartbeat.                                                                                              |

Current activity and the latest saved Run outcome are distinct. A successful [quiet heartbeat completion](02-main-thread.md#heartbeat-follow-up) remains inspectable with its Run status and `null` output; `null` alone does not establish success. For scheduled work, show its owner and occurrence identity alongside the associated Inbox item or fresh Thread when available. Claw is authoritative for both Main and fresh-Thread schedule and occurrence history. Editing the rule affects future scheduling; deleting a schedule stops future occurrences without removing recorded occurrences or admitted Inbox items. A retained occurrence links to its accepted delivery when available, or to the exact fresh Thread and Run. Distinguish rejected Inbox delivery, accepted but unread or processing work, and the recorded processing outcome; admission and Read are not completion. Lateness and overlap skips remain visible as facts of the owning scheduler. Show captured configuration separately from selections for the next Run. A disconnected observation stream is not evidence that execution ended; refresh the authorized saved view to recover observation. Exact execution actions, including stop and decision responses, are separate from read-only inspection.

Reported usage comes from Service facts. Keep partial or unknown pricing visible. Provider allowance is a separate observation with its own timestamp; stale or unavailable allowance does not become zero usage or zero remaining quota.

## Backup and Restore

Protect Claw's business data in its AppDatabase alongside the corresponding Service state. A recovery plan also accounts for the persistent Environment, any Browser or memory backing data, and the credential configuration needed to restore access. External provider state remains subject to that provider's backup and recovery support. Restoring database rows alone does not restore files, browser profiles, credentials, or platform side effects.

Before resuming external intake or publication after a restore, verify the target Claws, resource references, and connector identities. Do not replay an operation whose external outcome is unknown just because its local record was restored.

## Fresh Installation

The Service App starts from new Service App state. Claw 0.1.0 has no import or upgrade-in-place path, and its Thread, Inbox, and schedule state is not automatically replayed. Preserve any existing standalone data root; installation does not delete it.
