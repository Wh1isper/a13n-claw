# File Memory and Background Organization

## Design Position

Memory is intentionally retained knowledge for later work, distinct from message history, Harness checkpoints, diagnostics, and ordinary workspace files. Claw stores memory as application-owned plain files using Harness's public file-memory Capability and directory-backed store. Harness owns scoped file tools, bounded untrusted projection, content versions, and conflict-aware mutations. Claw owns scope binding, retention, cursor persistence, and background organization.

## Scopes and Authority

There are exactly two kinds of foreground memory scope:

| Scope          | Owner and visibility                                                                                                    |
| -------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Global         | One Instance-wide scope deliberately shared by all memory-enabled Threads                                               |
| Thread-private | One scope keyed by its owning Thread identity, available only to that Thread's foreground work and its scoped organizer |

A memory-enabled ordinary Run binds Global and its own Thread-private scope with writable memory tools. It cannot select another Thread's private scope. Channel, participant, Profile, workspace path, and parent relationship do not change scope identity. There is no Project scope or arbitrary scope picker. New Threads, including children and forks, start with empty private memory and use the current Global files; copied history is not permission to copy or mount the source Thread's private files.

Global sharing is deliberate, not an operator-only private store. Granting participation in a memory-enabled Thread permits that work to use Global knowledge, which can appear in replies. All participants sharing a Thread also share its private memory context. Binding another Channel or allowing another participant must account for that disclosure. A foreground agent can explicitly retain reusable facts in Global memory when appropriate; private content is not automatically promoted there. Secrets do not belong in either memory scope.

Memory storage is separate from the shared working directory and execution containers. Ordinary workspace mounts do not expose its backing files; memory requires no shell or Docker access. Changing the workspace directory or recreating a container neither moves nor deletes memory. Scope binding is an application boundary, not hostile-tenant isolation against trusted extensions, the operator, or separately granted broad host authority.

Memory content is input, not execution policy. It cannot enable tools, replace a Profile, authorize a sender, or grant filesystem access. Current user instructions take precedence over remembered preferences. Guidance preserves conditions, chronology, ownership, and uncertainty rather than turning one-off requests or agent proposals into standing decisions.

## Foreground Context and Updates

Memory and automatic organization have separate Instance switches, both enabled by default. Disabling organization leaves foreground memory usable; disabling memory omits its Capability from later Runs without deleting files. [Captured composition](02-configuration-and-composition.md) fixes whether foreground memory is enabled and the bound scope identities. Steering cannot add another scope. Content remains mutable, so configuration capture is not a snapshot of knowledge.

Each reconstructed execution receives fresh file-memory collaborators bound only to its captured scopes. Each scope always projects `MEMORY.md` when present, with the index and other context subject to Harness limits. Projection is bounded historical context, not proof of current truth or an exhaustive dump. Authorized tools can read omitted content; limits and unavailable content remain explicit.

Claw persists projection cursors separately from Harness continuation, keyed by scope identity. Resuming the same Thread can restore matching cursors; a fork does not reuse the source private scope's positions. Cursors suppress repeated projection, not access, version checks, or organization dirty detection. They do not prove the model read every entry.

A direct edit or agent update identifies its source and target scope. Mutations use Harness's current-content or version preconditions; conflicts cannot silently discard another writer's changes. Writes persist immediately and independently of checkpoint success. Cancellation does not roll them back, and several edits do not imply a whole-scope transaction. An uncertain write is reconciled against current files before retrying.

## Background Organization

Organization consolidates existing scope files; it is not transcript extraction or another knowledge scope. Foreground work remains responsible for deciding what to remember. The organizer preserves supported facts, corrections, conditions, and uncertainty, respects deletions, checks index references, and makes the smallest useful change. No-op completion is valid. Custom guidance cannot expand its evidence or authority.

An input-bearing new ordinary Run admission offers a nonblocking organization opportunity for its captured Global and Thread-private scopes. Console, API, bridge, and automation admissions use the same boundary. A message joining or steering an existing Run, a decision response, recovery, page viewing, or elapsed idle time is not an opportunity. Bridge filtering precedes this boundary; rejected messages cannot become memory evidence. Maintenance does not sweep other Threads or delay or reject foreground admission.

At most one organizer runs per scope, and pending opportunities are bounded. Disabled, unavailable, empty, unchanged, busy, or cooling-down scopes make no model request. Success cooldown is one hour; failure, cancellation, or crash imposes a fifteen-minute retry backoff. Another eligible admission is required for retry. The retry gate is durable before inference.

Each attempt captures the organization Model override, or otherwise the Instance's default Profile's Model, plus optional organization guidance. It does not inherit the foreground Model override or the default Profile's other composition. An unavailable Model makes maintenance unavailable without blocking foreground memory. The attempt uses a fresh model context, only the target scope's file-memory tools, at most twelve model requests, and a five-minute execution deadline. It receives no conversation transcripts, other memory scopes, workspace tools, MCP servers, skills, delegation, or bridge access. In particular, a Global organizer cannot read private memory, and a private organizer cannot publish into Global.

Each scope has one internal maintenance Thread for observable attempts, history, outcomes, and usage. Maintenance uses the shared Run execution and persistence infrastructure, not a separate agent loop. Its target scope remains owned by Global or the original ordinary Thread, never by the maintenance Thread. Maintenance Threads have no Channel binding or their own private memory and create no recursive organization opportunities. They are excluded from ordinary conversation lists and controls. Saved maintenance history is for observation only: each attempt starts fresh rather than continuing the prior model conversation.

Foreground inputs do not cancel an active organizer. Disabling memory or organization cancels active maintenance; changing its Model or guidance affects later attempts. Shutdown cancels maintenance and settles its execution ownership before releasing scope exclusion. Interrupted maintenance is not automatically resumed or replayed after restart.

## Completion and Observation

The durable organization state records the last successfully accounted file-version manifest and the next eligible attempt time, separately from observation history. The organizer edits current files with the same conflict-aware operations as foreground work; there is no staged directory replacement or rollback. It verifies a consolidation destination before deleting a source.

Success requires completed execution and a final file listing matching the initial versions plus the organizer's accounted mutations. A concurrent change remains dirty, even if the organizer later read it. Failure, cancellation, deadline, or unconfirmed effects retain partial changes and the previous successful manifest. A later eligible attempt inspects current files in fresh context, without replaying stale tool calls. Changes after successful confirmation remain detectable against the manifest.

The console's memory view observes authorized Global or Thread-private files and their maintenance history, with current content distinguished from historical observations. Viewing it never triggers inference. It is not an ordinary conversation composer or a manual control path into the maintenance Thread. Foreground file-memory tools remain usable independently of that observation-only surface. Maintenance failure is visible but does not retroactively fail foreground work.

## Lifecycle

Archiving a Thread, deleting its history, deleting its private memory, and deleting the shared workspace are distinct operations. Global memory survives ordinary Thread removal; removing it is an Instance-wide action. Deletion coordinates with active foreground writers and organizers and cannot leave an admitted attempt writing into a removed scope.

Rebinding a Channel selects the destination Thread's memory for new work; it does not copy the previous Thread's private files. Forked history and already delivered replies can contain previously read knowledge: private-file separation does not retract those copies. Memory deletion likewise cannot retract retained history or external messages.

Unavailable memory at preparation is reported explicitly. Claw does not substitute another Thread's scope or silently create an empty replacement under a retained identity. Memory files and organization state participate in application backup independently of workspace or container retention.

## Invariants

1. Every memory-enabled ordinary Run binds only Global and its own Thread-private memory.
2. Reusable knowledge cannot replace complete continuation or grant execution authority.
3. Memory files are not part of the shared workspace or a container's writable layer.
4. Writes and maintenance outcomes are independent of conversation checkpoint success.
5. Organization reads and writes only its target scope, never extracts transcripts or promotes private knowledge to Global.
6. Failed or concurrent maintenance cannot falsely mark changed knowledge successfully organized.
