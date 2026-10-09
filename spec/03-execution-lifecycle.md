# Execution and Interaction Lifecycle

## Design Position

Claw owns work acceptance and durable outcomes. Harness owns process-local agent execution and produces continuation candidates. An API response, observed model output, checkpoint publication, and external reply are different facts.

## Admission and Ordering

All work sources use the same admission authority. Before acknowledging an input, Claw retains its content, origin, destination Thread, routing disposition, and identity needed to reconcile retries. When admission creates a Run, it also captures that Run's composition; an input joining existing work uses the existing composition. Required input attachments must be retained or explicitly unavailable; a short-lived external download link alone does not satisfy acceptance.

A repeat of the same identified request returns the same input receipt with its current disposition and Run association. Reusing that identity with different content or scope is a conflict. A timeout does not establish rejection: the caller first checks whether the original work exists.

Only one Run owns advancement of a Thread at a time. Ordinary conversation messages follow the always-steer path below rather than creating a Run for every message. Separately requested work, such as an automation occurrence, can create a queued Run in an observable order. A waiting Run retains its position; later prompts do not bypass an unanswered decision. Independent Threads can execute concurrently, subject to their declared access to shared resources.

At execution start, Claw selects the Thread's current complete checkpoint under exclusive advancement ownership. It uses the composition fixed at admission, verifies current authority, and prepares the required environment. This allows queued work to follow committed conversation progress without changing its accepted behavior.

## Conversation Message Admission

Always-steer is the conversation message behavior for both console and bridge ingress, not a per-platform execution mode. The caller need not inspect liveness or choose between submit and steer. Claw serializes the routing decision with Thread ownership:

| Thread condition                                       | Disposition of a new eligible message                                                  |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| No active or queued Run                                | Create a Run and capture its composition                                               |
| Next Run is queued or preparing, with no running owner | Append the message to that Run's pending input in receipt order                        |
| A Run is running                                       | Retain the message for steering into that Run at a supported Harness input boundary    |
| A Run is waiting on a decision                         | Retain the message as held input for that Run; do not resume it or answer the decision |
| Execution or prior input consumption is unresolved     | Keep the input blocked until reconciliation; do not create a competing execution       |

Joining work preserves each message's identity, sender, attachments, and disposition even when several messages are presented together. A later message cannot overwrite an earlier pending prompt. Accepted messages do not replace the Run's captured model, tools, workspace, memory selection, or permissions. A caller lacking permission to contribute to the selected Run is rejected rather than creating parallel work.

Held input becomes eligible for steering only after the exact decision response allows the waiting Run to continue. If that Run is cancelled, fails, or is interrupted, remaining input stays inspectable as unapplied or uncertain; it is not automatically used to restart work past the decision.

## Work Lifecycle

The names below describe observable phases, not a wire-format enum.

```mermaid
stateDiagram-v2
    [*] --> Queued: Durable acceptance
    Queued --> Running: Ownership and preparation succeed
    Queued --> Failed: Required preparation cannot succeed
    Queued --> Cancelled: Cancellation accepted
    Running --> Waiting: Complete checkpoint and decision saved
    Waiting --> Running: Exact authorized response accepted
    Running --> Completed: Outcome and continuation committed
    Running --> Failed: Failure committed
    Running --> Cancelled: Execution stopped and outcome committed
    Waiting --> Cancelled: Waiting work cancelled
    Running --> Interrupted: Execution ownership lost
    Completed --> [*]
    Failed --> [*]
    Cancelled --> [*]
    Interrupted --> [*]
```

Queued work can expose a preparation or dependency blockage without pretending to be running. Preparation is part of the accepted Run and cannot become an invisible competing execution. A cancellation during preparation follows the same ownership and completion checks as cancellation during execution.

An interrupted Run is retained with its last known progress and selected checkpoint. Recovery creates a new Run on the same Thread with an explicit source reference after [reconciliation](04-persistence-and-recovery.md); it does not resurrect the old Run or silently replay uncertain side effects.

## Normal Execution Flow

```mermaid
sequenceDiagram
    participant Source
    participant Claw
    participant State as Durable state
    participant Harness
    Source->>Claw: Submit message to idle Thread
    Claw->>State: Retain input and new Run composition
    Claw-->>Source: Input receipt and associated Run
    Claw->>State: Select current Thread continuation
    Claw->>Harness: Execute with fresh authority
    Harness-->>Claw: Live observations
    Harness-->>Claw: Outcome and complete state candidate
    Claw->>State: Publish checkpoint and commit outcome
    Claw-->>Source: Saved outcome available
```

A Run is not reported as successfully completed until its required result and continuation boundary are durable. Failure to deliver a notification afterwards cannot change that outcome. A failed or cancelled execution can still leave a valid checkpoint or changed external data; Claw reports those facts rather than implying rollback.

## Human Decisions

A Pending decision names the exact waiting work, requested action, and continuation to which an answer applies. Claw saves the complete waiting boundary before presenting it as resumable. A question and a tool approval are distinct requests; an arbitrary new chat message is not an answer to either.

The first valid response from an authorized client is accepted against the still-current decision. A repeated identical response can be recognized; stale or conflicting answers cannot resume work twice. Policy is checked again before continuation. Rejecting an action is not silently converted into approval through a later client or automatic retry.

Console and bridge clients may answer the same decision only when their permissions allow it. If a platform cannot express a required interaction safely, work remains waiting and the user is directed to an authorized capable surface.

## Steering and Cancellation

Ordinary accepted messages steer the current Run without a separate steering command. Claw distinguishes durable receipt, delivery to the executor, and incorporation into a saved continuation. Always-steer does not mean interrupting a tool call, approving an action, or immediately changing the model's current request.

Routing and completion must not lose or double-apply a message. Before committing successful completion, the owner checks pending input. If an ordinary message is confirmed not delivered and execution has already closed, Claw transfers that same input once to the next pending Run, or creates a successor Run using current composition if none exists. The receipt exposes the resulting association; retrying the message cannot repeat the transfer. Confirmed incorporated input is never submitted again. If delivery or consumption is uncertain, Claw retains that uncertainty for reconciliation rather than guessing that another Run is safe.

An explicit Run-targeted control request remains different from ordinary message submission: a stale steer is rejected or reported unapplied, never retargeted to another Run. Cancellation, decision responses, and separately scheduled work keep their own semantics.

Cancellation records intent before reporting a final outcome. Queued work can be cancelled without execution. Active work is cancelled only after the executor has stopped or its loss has been reconciled. If completion wins the race, the completed outcome remains authoritative. Cancellation does not undo external actions already performed.

## Delegation

Delegated work uses a distinct Thread and Run with a recorded parent relationship and explicitly bounded authority. Inline execution does not create a new independent work owner; background execution does. Child progress and completion are visible without merging their history into the parent Thread.

A parent that waits for a child can continue only from the child's saved outcome or an explicit failure condition, not merely a delegation receipt. Child-result delivery and parent continuation are tracked separately so a retry cannot append the result twice. Stopping a parent does not prove every child stopped; the requested propagation policy and actual child outcomes remain visible.

## Invariants

1. Accepted input survives client disconnect and is never silently discarded on restart.
2. No two Runs publish competing continuation heads for the same Thread.
3. A waiting decision cannot be bypassed by another client, later prompt, or duplicate callback.
4. Saved terminal outcomes are not rewritten by notification failures or stale executors.
5. Unknown effects remain explicit until reconciled; retry is not a promise of exactly-once external execution.
6. Every accepted message remains identifiable across pending input, steering, completion races, and recovery; receipt is not proof of incorporation.
