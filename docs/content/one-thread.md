---
title: One Thread coordination
description: Coordinate durable Inbox work and persistent workers through one Main Thread.
---

## Choose the conversation mode

One Thread mode gives the Instance one persistent **Main** Thread. External messages enter its durable Inbox; Main selectively reads them and coordinates independent workers. This is available in source builds, not in the published `0.0.2` placeholder.

After [building the Console and configuring a Profile](./getting-started.md), start the server with:

```bash
uv run a13n-claw serve --workspace "$HOME/claw-workspace" --mode one_thread
```

Alternatively set `CLAW_CONVERSATION_MODE=one_thread`. The default is `per_channel`: each external conversation advances its own Thread. Mode selection applies to the whole Instance at startup, not to individual Channels. Stop the server cleanly and settle active or uncertain work before switching. Old-mode histories, bindings, and work are not migrated or silently reactivated; inspect inactive Threads explicitly. Switching modes is not a recovery shortcut.

Open **Coordination**, select Main's starting Profile, and choose **Initialize Main**. Its identity and worker ownership survive Runs and restarts. Repeating initialization returns the existing Main; ordinary Thread creation or forking does not create another coordinator. Open Main to give instructions. Direct Console messages still use ordinary durable admission, independently of external Inbox processing.

## Work with persistent workers

Ask Main to create a worker for a bounded task. Main can inspect its owned workers and send further instructions across Runs. A worker can inspect itself and its owner and send Main a question or report; it cannot adopt unrelated Threads, inspect siblings, or create more persistent workers. These are independent Threads, not Run-scoped delegation results.

Open a worker card to inspect saved outcomes or converse directly. Human input is admitted once to that worker and records Main attention. Main must reconcile the change rather than assign the task again. Worker outcomes retain references to their saved Runs; cancellation, failure, and inactivity are not success. Stopping Main does not stop its workers.

Collaboration receipts prove acceptance, not reading or completion. A worker message to Main is durably retained first, then admitted exactly once as ordinary input when Main's automatic-processing barriers permit. Its saved `input_id` links that admission. Worker history is not merged into Main history.

In this mode, configured MCP integrations and Claw's external messaging tools are available only to Main, not workers or other ordinary Threads. Profile permissions and current application grants still apply. This does not isolate knowledge already shared with Main, and broad Local shell authority remains trusted host-account access, not a sandbox.

## Bind an external conversation

The current transport is generic authenticated HTTP ingress and explicit HTTP egress. Vendor-specific embedded adapters, platform login flows, and platform-native decision cards are not included. An integration must verify the platform event and normalize its sender, addressed status, and stable event identity before calling Claw.

1. Create a dedicated client in **Settings** for the integration. It can have empty ordinary actions, Profile IDs, and Thread IDs; ownership of an ingress binding does not grant Main history access.
2. In **Coordination → Channels → Add Channel**, choose a stable binding ID and edit its policy JSON.
3. Set the exact platform/account/conversation identity, integration client ID, execution principal, and explicit sender allowlist. For groups, keep `require_addressed` enabled unless unaddressed messages are intentionally eligible.
4. Set `shared_context_acknowledged` only when all participating conversations are authorized to share Main's context. This is required in One Thread mode.

For example, an inbound-only binding named `release-room` can use:

```json
{
  "platform": "http",
  "account": "team",
  "channel": "release-room",
  "ingress_actor_id": "bridge",
  "execution_actor_id": "operator",
  "allowed_senders": ["alice"],
  "enabled": true,
  "inbound": true,
  "outbound": false,
  "group": true,
  "require_addressed": true,
  "shared_context_acknowledged": true
}
```

The execution principal needs current submit authority on Main. Existing bindings cannot be retargeted to another qualified conversation; create a new binding instead. Policy edits use the viewed version and reject stale changes.

With the integration client's Bearer token in the Authorization header, submit to `POST /api/channels/release-room/messages`:

```json
{
  "event_id": "platform-event-123",
  "sender": "alice",
  "text": "Review the release readiness.",
  "addressed": true,
  "attachment_ids": [],
  "unavailable_attachments": []
}
```

The successful receipt contains `inbox_id` and `thread_id`, not an agent result. Retry the same event identity with the identical body after a lost response; conflicting reuse returns HTTP 409. Sender/group-filtered messages return HTTP 200 with `event_id` and `disposition: "ignored"`; Claw retains only the fingerprint and minimal receipt, not their body or an Inbox obligation. Changing filters later does not turn a duplicate ignored event into new work. Disabled bindings or unauthorized callers are rejected instead.

Attachment IDs must already be retained on the destination Thread through an authorized file upload; they are not remote URLs. An ingress-only client is not implicitly granted upload access. External references retain their origin and are checked against current Channel policy when read. Put inaccessible platform references in `unavailable_attachments`; this records missing content without claiming that the agent saw it. Revoking a binding or sender can withhold new reads but cannot erase content already incorporated into Main's knowledge.

## Process the Inbox explicitly

The Inbox includes external messages and internal worker/human attention. Reading, notifying, saving a checkpoint, or completing a Main Run does **not** settle an item.

| Disposition | Meaning                                                                           |
| ----------- | --------------------------------------------------------------------------------- |
| `pending`   | Requires attention and remains eligible for automatic processing.                 |
| `deferred`  | Waits for one recorded worker Run, time, or explicit human-resolution key.        |
| `handled`   | Processing is settled with a result or reference; delivery can remain unresolved. |
| `ignored`   | Explicitly dismissed with a reason.                                               |

Use **Review item** to record a result, ignore with a reason, defer, or reopen as pending. A deferral requires exactly one reactivation condition. Worker completion or failure reopens its dependent item, including when completion raced with deferral. A timed deferral is rediscovered after restart. Resolve a human dependency by reviewing and reopening or settling the item; writing its key in a chat is not an acknowledgement.

The editor keeps the version originally read. Concurrent changes return a conflict and preserve your draft note; close and reopen to review fresh state rather than overwriting it. Main's tools follow the same version checks.

Claw checks saved pending obligations on startup, Main Run completion, new arrivals, and barrier release. It coalesces attention into eligible work rather than creating a Run for every event. Unchanged pending work after completion causes a successor Run even with no new message. Repeated non-progress triggers visible, durable backoff; it never silently marks work handled or abandons it after an attempt limit.

**Pause automatic processing** is persistent. It retains the Inbox and blocks automatic Main advancement, but does not stop a running Main, worker, or already accepted delivery. Direct human work remains explicit. **Stop Run** is not a durable pause. Waiting decisions, revoked authority, recovery-required work, uncertain effects, and unknown deliveries also block automatic advancement. Inspect the displayed reason; do not bypass it by submitting replacement work. **Retry pending attention now** clears backoff, not pause or recovery barriers.

## Publish and reconcile separately

Only Main can use `send_message` to save an explicit delivery intent to a permitted Channel. Final responses and worker output stay in the Console unless Main explicitly sends them. There is no automatic broadcast.

To enable a destination, set `outbound: true` and a reviewed HTTP(S) `delivery_url` in its Channel policy. Optionally set `credential_env` to a managed or process-environment credential reference for Bearer authentication; never embed secrets in the URL or policy JSON. Endpoint configuration is privileged and does not prove connectivity.

Claw posts this JSON to that receiver:

```json
{
  "delivery_id": "delivery_example",
  "platform": "http",
  "account": "team",
  "channel": "release-room",
  "text": "Release review completed."
}
```

The `Idempotency-Key` header is the same delivery ID. The receiver must deduplicate that identity and return 2xx only after durably accepting responsibility. Claw records 2xx as sent; redirects are not followed. A timeout, transport error, or non-2xx response leaves an unknown outcome with no automatic resend. A pending delivery whose Channel policy version changed is blocked for review under current policy.

In **Deliveries**, inspect the original delivery ID and receiver evidence. **Reconcile delivery** records confirmed sent or confirmed not sent with a required note. Retrying is a separate, unchecked-by-default choice for confirmed-not-sent work, using the same delivery identity and current authority. Never infer failure from a lost response or rerun the agent to replace an unknown send.

Inbox disposition, Run completion, and delivery completion are independent. Marking an Inbox item handled does not resolve a send; delivery reconciliation does not repeat the Run. After reconnect, use saved Inbox, worker, and delivery state as the source of truth. See [Operations](./operations.md) for decisions, recovery, access boundaries, and backup.
