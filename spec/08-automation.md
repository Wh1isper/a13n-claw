# Automation and Background Work

## Design Position

Claw includes unattended work in the same product scope as interactive conversation. Schedules, heartbeat, workflows, and autonomous follow-up create or control ordinary Claw work; none owns an alternate execution or persistence engine.

The product remains single-node. Durable intent does not promise execution at an exact wall-clock instant, continuous availability, distributed scheduling, or exactly-once external side effects.

## Automation Concepts

| Concept              | Responsibility                                                                                    |
| -------------------- | ------------------------------------------------------------------------------------------------- |
| Schedule             | A managed intent to request work at a time or recurrence, with an explicit target and policy      |
| Occurrence           | One identified firing opportunity, distinct from the Runs it may create                           |
| Heartbeat            | Periodic operational work within a declared scope and bounded purpose                             |
| Workflow             | An orchestration instance whose steps and dependencies progress from recorded work outcomes       |
| Autonomous follow-up | Bounded agent-directed continuation of a declared goal in response to permitted events or results |

An automation definition records who authorized it, what it may initiate, which context it uses, and where results may be delivered. Editing a definition affects later occurrences; already accepted Runs retain their captured configuration. An agent may manage automation only through explicitly granted Claw operations.

## Trigger-to-Work Flow

```mermaid
sequenceDiagram
    participant Trigger as Timer or authorized event
    participant Claw
    participant State as Durable automation state
    participant Execution
    Trigger->>Claw: Firing opportunity
    Claw->>State: Reconcile occurrence and prior disposition
    Claw->>Claw: Check enabled state, authority, and overlap policy
    Claw->>Execution: Admit ordinary work with occurrence origin
    Execution-->>Claw: Accepted Run or explicit disposition
    Claw->>State: Retain occurrence outcome
```

An occurrence is not considered successfully dispatched merely because a local timer fired. Repeated discovery of the same occurrence must resolve to the same accepted work or saved skip/rejection. Failure between accepting a Run and recording a notification cannot cause another Run to be accepted for the same occurrence.

Schedules have explicit time interpretation, missed-occurrence, and overlap policies. These choices are visible before enablement. A restart must not silently replay every elapsed interval or start a burst of overlapping work. Queued or skipped is not equivalent to completed, and disabling future occurrences does not cancel existing work unless that action is explicitly requested.

## Conversation and Working Context

Automation can target an existing Thread or create separate work according to its definition. It must not silently inject operational prompts into a user's conversation, bypass a waiting decision, or inherit unrestricted access from a shared workspace.

An occurrence directed to an existing Thread follows ordinary ordering. Explicit steering remains subject to the [active-input contract](03-execution-lifecycle.md#steering-and-cancellation). Work that requires independent progress uses another Thread instead of a second writer on the same history.

Automation uses the Instance's shared workspace, not a separate Project context. Memory-enabled work receives Global and the target Thread's private memory; creating a new Thread does not inherit the initiating Thread's private store.

Heartbeat uses a declared operational purpose and bounded authority. Its periodic origin does not make workspace instructions trusted policy or grant new tools. Output is retained even when no human is watching; external notification is a separate delivery choice.

## Memory Maintenance

[Background organization](09-memory.md#background-organization) is scoped maintenance over existing memory files, not a heartbeat, transcript extraction, or periodic sweep. It uses ordinary execution and observation infrastructure with a restricted composition and internal maintenance Thread. Its admission opportunities, retry gates, and fresh-context rule are owned by the memory contract; general automation cannot bypass those restrictions.

## Workflow Progress

A workflow owns orchestration progress; a Run owns execution progress. Each step identifies its input, dependencies, accepted work, and saved outcome. Downstream work advances only after the required durable outcome exists, not after submission or a suggestive stream message.

A waiting decision pauses the dependent portion of the workflow. Failure, cancellation, and recovery remain explicit at both levels. Retrying a step creates identifiable new work linked to the prior result; it does not erase the previous Run or automatically repeat a possibly applied external effect.

Cancelling a workflow stops new step admission and requests cancellation of active work according to its policy. It is not reported as fully stopped while required child outcomes remain unknown. Unrelated work and shared resources are not cancelled as a side effect.

## Autonomous Follow-Up

An autonomous goal has a declared scope, allowed triggers, stopping conditions, and resource limits. Input observations and child completion can justify new work only within that envelope. Exhausted limits, withdrawn authority, or an unanswered decision block further admission rather than authorizing escalation.

Feedback delivery does not recursively authorize unlimited work. Work initiated by another automated action retains that provenance and is subject to the same loop and admission policy as direct events. An agent cannot grant itself more authority by creating a schedule, workflow, or bridge binding.

## Durable Inbox Drain Boundary

[One Thread drain](10-one-thread-mode.md#wake-and-drain) is application processing of already accepted input, not an optional autonomous goal or a heartbeat. It does not require a timer definition, a new platform event, or the model's permission to continue. Startup and Main Run settlement rediscover actionable backlog even when a prior Run claimed completion. General autonomous-follow-up limits cannot silently discard this obligation: required backoff or a safety block retains pending state and exposes why progress stopped. Pause, decisions, recovery, and unknown-effect restrictions still apply.

One Thread automation and worker results do not gain an alternate external publication path. Agent-originated publication remains Main-only under the messaging contract; ordinary saved operational observations are not broadcast replies.

## Invariants

1. Every automated Run has an accountable definition or triggering cause.
2. Repeated occurrence discovery and repeated result delivery do not create duplicate execution intent.
3. Definition edits and timer restarts do not rewrite accepted work.
4. Workflow progress follows saved outcomes, not observation timing.
5. Automation never bypasses Thread serialization, pending decisions, current permissions, or environment limits.
