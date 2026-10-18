# Claw API, Frontend, and Access

## Design Position

Claw exposes Harness UI interaction through IM adapters, a headless CLI, and its own API. The backend composes supported Harness UI App operations with Claw product operations in-process. It does not proxy or mount the complete stock WebUI API, start another Host per request, or depend on browser-specific drafts and page presence to execute work.

The runtime frontend is a Hello World placeholder in the replacement scope. A full Console is not a prerequisite for setup, conversation, controls, approvals, worker inspection, or recovery. Those operations use the implemented IM capabilities and authorized CLI/API. Any later frontend presents the same owners and access checks; it adds no execution authority.

## Application Surfaces

| Surface                                                       | Authority behind the Claw API                                      |
| ------------------------------------------------------------- | ------------------------------------------------------------------ |
| Models, Agents, Projects, and shared resources                | Supported Host configuration operations                            |
| Threads, history, execution, decisions, workers, and children | Supported Host commands, queries, and controls                     |
| Environments, files, and memory                               | Supported Host resource operations, restricted by product exposure |
| Connections, Channels, Inbox, and delivery                    | Claw product services                                              |
| Schedules, occurrences, and workflows                         | Claw automation services                                           |
| Readiness, cancellation, blockage, and reconciliation         | Clearly distinguished Host observations and Claw business facts    |

The API need not reproduce every Host feature. Missing capabilities are reported explicitly rather than reconstructed through private modules. Payloads may reuse detached Host values where suitable; Claw does not publish native execution objects, credentials, storage sessions, or raw checkpoint internals.

The CLI remains a headless product surface over the same owners. Offline administration uses exclusive ownership and supported library operations. A failed network call never authorizes offline mutation of a running Host store.

## Access Boundary

Claw is an operator-controlled application, not a multi-tenant service. Its administrative CLI/API belongs to trusted operators. External participants and delegated product clients can receive narrower Channel or action grants, but those grants do not create isolation inside the Host or undo knowledge already shared with an Agent.

Every exposed operation checks the caller, target, action, and current product policy before invoking the relevant owner. A Channel sender cannot inspect arbitrary Host Threads, edit Agent resources, answer another conversation's decision, or invoke environment management merely because Claw can call those methods in-process.

Claw filters both queries and realtime subscriptions before exposing them to a restricted caller. Model-facing Host collaboration scope and human/API grants are different boundaries. The Host's unrestricted trusted-human surface is not automatically suitable for an external participant; unsupported restricted operations remain unavailable rather than bypassing checks.

Host-bound execution identity governs Claw product tools. Main-only egress is enforced when accepting send intent, and destination authority is checked again when sending. Host composition and approvals remain in force; a product grant cannot add a missing tool or bypass a decision. A model-supplied Thread ID or role claim is never caller identity.

Shared Main history, Host Global/Project memory, and shared working roots have disclosure consequences. Binding several Channels to that context requires explicit operator acknowledgement. Separate Thread IDs are not a promise of private memory or filesystem isolation. Broad host-account execution and trusted extensions remain outside model-tool isolation guarantees.

Secrets remain with their owner and are excluded from ordinary client state, product projections, outgoing content, and diagnostics. Returned messages and files are untrusted content, not executable frontend code. Serving static assets conveys no API authority.

## Conversation and Observation

Direct conversation submission exposes Host admission semantics, not the former durable Claw prompt queue. The client distinguishes its unsent draft, Host acceptance, available saved output, and unknown outcome. A lost response is reconciled using available original references; a client never silently sends a new prompt to reproduce a missing reply.

Claw relays Host observations through its own realtime surface. It retains bounded presentation state only as needed by the transport. Disconnects, sequence gaps, and process restarts cause supported snapshot refetch, not inference that execution finished. A saved execution summary is not current control authority; an unavailable receipt is not evidence that work was never accepted.

Inbox acceptance and automation occurrence views show their stronger business-retention guarantee separately. Dispatch uncertainty, pending decisions, cancellation, and delivery failure are distinct visible conditions. A successful Host operation cannot turn all related Inbox items green automatically. [Interactive IM behavior](07-bridges-and-clients.md#interactive-im-behavior) owns action targeting and platform feedback.

## Client Behavior

Clients show the selected Thread and action target clearly, including Main versus a worker and active versus saved work. Agent, Project, roots, and memory sharing are disclosed where they affect setup or access. Ordinary task submission does not require navigating an Inbox dashboard or recreating Host configuration in Claw.

Configuration editing preserves the owner's source revision and any conflicting client draft. Source files, accepted generations, next-operation selections, current captured configuration, and connectivity remain distinguishable. Cross-owner edits report each outcome separately.

Authorized users can inspect and converse with Host workers directly. Main attention references already-submitted work; it is not a duplicate task. Exact Host decisions use their native control contract. Failed or unsupported actions identify the target and reason without presenting a generic success acknowledgement.

Disconnecting a client or dismissing a notification does not cancel Host work or settle obligations. Viewing status, memory, or history does not itself request inference. Destructive actions describe scope and retained dependencies.

## Invariants

1. Claw's API composes two explicit owners without creating a second execution authority.
2. Product access checks cover queries, commands, subscriptions, and file exposure.
3. Client transport state never replaces Host saved facts or Claw business state.
4. Human clients see the actual acceptance and recovery limits of the operation they requested.
