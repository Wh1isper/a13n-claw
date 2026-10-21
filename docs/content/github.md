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
    shared_context_acknowledged: true
```

Supply a token able to read the account's notifications and the selected repository's issue/PR context. Comment publication also requires write permission. The adapter checks `/user` against `account_login`; storing the token alone does not prove permissions. Only `https://api.github.com` is supported. Per-Channel/per-trigger modes additionally require a distinct native `project_id` on each repository binding.

Save and [explicitly reload product configuration](./configuration.md#product-configuration-edits). `a13n-claw github` exposes polling/read-state records; `status` reports connection errors. Polling respects server rate-limit/backoff and poll-interval signals.

## Capture and routing

A notification version is qualified by connection, account, notification ID, and update timestamp. The cursor and delayed mirror are durable. Pagination must finish before the cursor advances. A one-second overlap handles timestamp boundaries, including zero initial lookback; it is deduplicated by retained identities.

After the configured delay, Claw refreshes the notification and issue/PR context. It accepts configured repositories and allowed author logins, excludes the authenticated account's own output and Claw markers, and omits disallowed root-author text. Content enters the ordinary Inbox/routing path only after policy checks. Unsupported notification subjects are ignored. GitHub notifications are a sampled update feed, not a complete event archive; superseded updates may coalesce before admission.

One Thread routes to Main; other modes use their repository Project. GitHub input is content, not an IM control command. Explicit Claw replies publish issue/PR text comments to the retained number, with a delivery marker and no automatic retry after ambiguous POST outcomes. File uploads and reactions are unavailable for this adapter.

## Mark-read and unknown effects

Mark-read occurs only after the linked business item settles, with no unresolved dispatch or unsent delivery. It is independent of capture and model completion. Refreshing before read-state mutation detects newer versions; subsequent polling includes read notifications so a racing update can still be captured.

A failed mark-read request remains pending with retry/backoff. Before retrying this idempotent operation, the adapter rechecks the notification version and business settlement; it does not resubmit the Agent or repost a comment. Ambiguous comment sends instead use the ordinary [delivery reconciliation](./operations.md#deliveries) path and are never automatically retried. Restore cannot undo GitHub comments or notification read state; review those effects before resuming a restored Instance.
