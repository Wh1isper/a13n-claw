---
title: GitHub notification polling
description: Capture issue and pull-request notifications with delayed context and durable read-state evidence.
---

## Configure a connection

Add a connection and explicit repository binding to `claw.yaml`, retaining existing Lark entries:

```yaml
connections:
  github:
    platform: github
    account_login: your-bot-login
    token_env: CLAW_GITHUB_TOKEN
    poll_seconds: 60
    delay_seconds: 600
    initial_lookback_seconds: 0
    mark_read: true
channels:
  repository:
    connection: github
    chat_id: owner/repository
    allowed_senders: [trusted-login]
    reactions: true
    shared_context_acknowledged: true
```

Supply a token able to read the account's notifications and the selected repository's issue/PR context. Comment publication and `eyes` acknowledgements also require the corresponding issue/PR write permissions. The adapter checks `/user` against `account_login`; storing the token alone does not prove permissions. Only `https://api.github.com` is supported. Per-Channel/per-trigger modes additionally require a distinct native `project_id` on each repository binding.

Save and [explicitly reload product configuration](./configuration.md#product-configuration-edits). `a13n-claw github` exposes polling/read-state records; `status` reports connection errors. Polling respects server rate-limit/backoff and poll-interval signals.

## Capture and routing

A notification version is qualified by connection, account, notification ID, and update timestamp. The cursor and delayed mirror are durable. Pagination must finish before the cursor advances. A one-second overlap handles timestamp boundaries, including zero initial lookback; it is deduplicated by retained identities.

After the configured delay, Claw refreshes the notification, then paginates ordinary comments and PR review comments over the retained discovery window. Each request must contain an explicit `@your-bot-login` mention and come from an allowed author. Notification reason alone does not trigger work. Own output, Claw markers, and unmentioned content are excluded. Only the admitted request body enters context, rather than importing unrelated root or comment bodies.

Requests retain source, author, and version identities independently of notification updates. Several mentions during the delay become separate Inbox items. Repeated scans reuse their immutable snapshots; a comment edit is a new version. Root requests use body identity so unrelated label/state updates do not retrigger the same request. Ignored versions remain ignored after a grant change. Notification discovery windows may coalesce while preserving their earliest start. Deleted comments and edits replaced before polling cannot be recovered from this sampled feed.

One Thread routes to Main. Per-Channel retains a separate Thread for each issue/PR number in the repository's configured Project. Per-trigger creates a new task for each admitted request. GitHub input is content, not an IM control command. Explicit Claw replies publish ordinary issue/PR text comments to the retained number, including requests originating in review comments. File uploads remain unavailable; publish authorized file links explicitly.

Set `reactions: true` on a repository binding to acknowledge retained requests with `eyes` on their original issue or comment. This uses the durable delivery queue; an uncertain response stays unknown and is not automatically resent. Acknowledgement is independent of model execution, and its failure does not block work.

## Mark-read and unknown effects

Mark-read occurs only after all linked requests through that notification version settle, including earlier captures, with no unresolved dispatch or unsent business delivery. Service acknowledgements are separate. It is independent of capture and model completion. Refreshing before read-state mutation detects newer versions; subsequent polling includes read notifications so a racing update can still be captured.

A failed mark-read request remains pending with retry/backoff. Before retrying this idempotent operation, the adapter rechecks the notification version and business settlement; it does not resubmit the Agent or repost a comment. Ambiguous comment sends instead use the ordinary [delivery reconciliation](./operations.md#deliveries) path and are never automatically retried. Restore cannot undo GitHub comments or notification read state; review those effects before resuming a restored Instance.
