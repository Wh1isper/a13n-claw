# Automation and Business Orchestration

## Design Position

Claw owns when and why business work should be requested. The embedded Host owns how Agent execution advances. Schedules, heartbeat, workflows, and product follow-up use Host operations and never instantiate another Agent loop, child operator, or durable Run scheduler.

Automation is an additional entry into the same interaction path. Basic IM conversation, controls, and worker collaboration work without schedules or a workflow engine. Retained automation intent does not promise execution at an exact instant, continuous availability, distributed failover, or exactly-once external effects.

## Definitions and Occurrences

An automation definition identifies its authorizing operator, purpose, target Host context, permitted operations, timing, overlap policy, and output destinations. Automation enters Main or an independent Host root; Main delegates managed-worker tasks through native collaboration. It does not submit automated first input through a path that falsely notifies an owner of direct human work. A schedule has explicit timezone and missed-occurrence policy. A heartbeat is a bounded periodic purpose, not permission to inspect every Thread or expand tools.

Each firing opportunity has a stable occurrence identity. Claw records whether it is pending, skipped, blocked, or resolved, plus any [dispatch evidence](04-persistence-and-recovery.md#dispatch-evidence). These are business dispositions, not replacement Host Run states. Duplicate discovery returns the existing occurrence instead of dispatching again merely because a timer fired.

Editing a definition affects future occurrences. Retained occurrences preserve their authorized business request and target; Host resource content is resolved and captured at actual Host admission. Disabling a definition stops future dispatch according to policy and does not claim to cancel already accepted Host work.

## Dispatch and Restart

1. Discover an occurrence under its explicit timing and catch-up rules.
2. Retain the occurrence before attempting execution.
3. Check enabled state, current authorization, overlap, target availability, and unresolved previous attempts.
4. Record an attempt before calling the supported Host input boundary with automation provenance.
5. Record the actual acceptance, explicit rejection, or uncertainty; inspect available Host results without resubmission.

A restart cannot turn an attempted occurrence back into a new firing. A crash between the Host call and recording its receipt blocks automatic replay. Missing evidence requires the same operator reconciliation as Inbox work. Catch-up cannot silently create a burst of overlapping executions or bypass an unanswered decision.

An occurrence awaiting a busy Host may remain pending under its policy. It does not reserve or freeze a future Host Run. Work needing independent progress explicitly selects or creates a different Host Thread; Claw never creates a second writer for the same Thread.

## Workflow Progress

A workflow retains business step dependencies and their correlated Host references or explicit product results. It is not a checkpoint store. A downstream step advances only from the required exact evidence, never from an admission receipt, quiet stream, unrelated later outcome, or process-local notification alone.

Waiting decisions block dependent progress. Failed, cancelled, and uncertain attempts retain their evidence. A retry is an explicit new attempt under the recovery policy, not erasure of the prior occurrence. External side effects can remain even when the Host failed to publish its outcome.

Cancelling a workflow prevents new step dispatch and requests supported Host cancellation for identified active work. It reports unresolved cancellation separately and does not stop unrelated Threads or destroy shared environments.

## Follow-Up and Product Drain

Agent-directed follow-up has an explicit purpose, allowed triggers, resource limits, and stopping conditions. Where the Host already owns a goal or coordinator continuation loop, Claw uses that capability or leaves it disabled; it does not install a competing loop over the same task. Product limits can stop new requests but do not grant control over unrelated Host work.

[Inbox drain](10-one-thread-mode.md#wake-and-drain) reconciles retained business obligations independently of optional schedules or Agent goals. It rediscovers pending items without new platform events and coexists with native coordinator follow-up. Decisions, unresolved dispatch, revoked authority, and backoff constrain the affected product requests. Limits expose a reason and preserve backlog rather than relabeling it handled.

In One Thread mode automation uses Main for Agent-originated external publication. A worker or operational Thread cannot obtain a separate send path by being timer-triggered. Platform notification remains a separately authorized delivery action.

## Memory and Working Context

Automation uses its target's Host-selected working context and memory scopes. It does not mount the initiating Thread's private files, create a Claw memory namespace, or run a second memory organizer. Host-native maintenance retains its own trigger and lifecycle rules; a timer is not an alternative way to bypass them.

## Invariants

1. Each automated request has a retained accountable business cause.
2. Repeated occurrence discovery does not repeat an uncertain Host submission.
3. Host execution configuration is captured at Host admission, not at timer registration or business acceptance.
4. Workflow progress uses correlated evidence and respects Host decisions and ownership.
5. Automation and Inbox drain cannot create a second execution or recovery engine.
