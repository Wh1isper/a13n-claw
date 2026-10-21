# Schedules and Periodic Tasks

## Design Position

Claw owns scheduled business intent and its durable occurrences. The embedded Host owns Agent execution, decisions, continuation, environments, and memory. A heartbeat is an ordinary interval schedule with a bounded prompt and explicit notification policy. Workflow orchestration is outside this product scope.

Each occurrence runs in a fresh independent Host Thread under an explicitly selected Project. It is neither Main nor a managed worker, and cannot use timer provenance to acquire Main's messaging authority. This isolates scheduled conversation history while retaining the Project's native shared memory and working context. Timing is best effort within one live Instance, not distributed scheduling or exactly-once external execution.

## Definitions and Occurrences

The file-owned schedule catalog has stable schedule IDs and digest-preconditioned mutations. Definitions select a prompt, Host Project and optional Agent/Model, enabled state, cron/interval/one-shot timing, timezone, overlap policy, missed-occurrence policy, and optional result destination. Host resources are resolved at admission; a schedule definition does not freeze execution composition.

Cron uses five fields and an explicit timezone. Intervals are anchored by retained due times; one-shot instants require an offset. On restart, `latest` admits at most the latest missed occurrence, while `skip` records missed work outside its grace window as skipped. `queue` serializes occurrences of a schedule behind unresolved earlier work; `skip` settles an overlapping opportunity without execution. Waiting decisions and unknown attempts count as unresolved.

Every occurrence retains its definition revision, purpose, author, intended Project and qualified destination before dispatch. Scheduled discovery and manual triggers use stable firing/request keys. Rediscovery returns existing work. Definition edits affect future occurrences; retained work keeps its original request. Pausing/removing a schedule prevents pending dispatch and subsequent result delivery, while already accepted Host work requires separate cancellation.

The operator can manage the catalog through CLI/API. Agent tools require an actual admitted Lark Inbox request from a currently authorized Channel operator. Those tools can manage only that author's schedules in the same qualified source Channel, using its Project defaults and destination. A normal allowed sender, another Channel, or a forged execution identity cannot grant scheduling authority. Source and destination identity and current grants are rechecked at use time.

## Dispatch and Restart

1. Discover and retain an occurrence according to timing and catch-up rules.
2. Check enabled state, author/destination authority, overlap, and target availability.
3. Create or recover the occurrence's exact independent Thread reference. Ambiguous creation remains blocked.
4. Persist the attempt before calling the public Host submission boundary.
5. Retain explicit rejection, acceptance, or uncertainty and observe that exact operation.

Occurrence dispositions distinguish pending, calling, accepted, waiting, unknown, completed, failed, cancelled, and skipped. These describe a business request and observed Host evidence, not another execution engine. A crash around submission never resets an attempted occurrence to a new firing. A missing process-local receipt requires exact saved evidence or operator reconciliation, following [dispatch evidence](04-persistence-and-recovery.md#dispatch-evidence).

Suspension retains waiting work. Native decision responses and authorized continuation correlate a new receipt to the same occurrence Thread; ambiguous continuation is blocked. An operator can attach an observed continuation receipt or record a reconciliation with evidence. Cancellation before admission skips work; cancellation after acceptance requests native Host cancellation. Cancelling waiting business work does not erase its native decisions or reverse effects.

## Result Notification

An occurrence's final result and its external notification are separate durable facts. Notification policy is `always`, `error`, or `never`. The service retains completed result content and creates at most one correlated delivery obligation for the configured destination. Missing or truncated retained output is identified explicitly rather than reconstructed by rerunning the Agent.

The scheduler's result notification is a bounded service action authorized by the schedule. It is not a general send capability for the scheduled Thread. In One Thread mode all Agent-originated Channel publication remains Main-only. Native worker collaboration and Main's private final output keep their existing rules.

Delivery uses the common retained-content, current-destination authorization, and unknown-outcome rules. Retrying a confirmed-unsent notification never reruns the scheduled task. A lost send response stays unknown until explicitly reconciled.

## Follow-Up and Product Drain

[Inbox drain](10-one-thread-mode.md#wake-and-drain) independently rediscovers pending messaging obligations. Native coordinator follow-up retains Host ownership. Schedules do not install a competing continuation loop, run a second memory organizer, or obtain access to other Threads' private files.

## Invariants

1. Each automated request has a retained author, purpose, revision, and occurrence identity.
2. Rediscovery cannot repeat an uncertain Host submission or external notification.
3. Every occurrence uses an independent Host Thread and captures Host configuration at admission.
4. Decisions, overlap, authorization, and explicit reconciliation constrain business progress.
5. Result notification and Agent-originated publication have distinct, bounded authority.
