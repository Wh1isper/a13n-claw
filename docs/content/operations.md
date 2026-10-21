---
title: Operations
description: Operate the embedded Host and reconcile product uncertainty without replaying effects.
---

## Access and secrets

The service is for a trusted operator and explicitly allowed messaging participants. Keep the default loopback listener, or use a trusted TLS reverse proxy. `/api` requires an Authorization Bearer token and rejects foreign browser Origins. Static content grants no authority. Keep tokens out of URLs and logs.

The operator token is in `operator.token`. The API grants that operator full product/Host management access. Messaging grants are separate: `allowed_senders` permits intake, and `operators` permits associated IM controls. Shared context and broad native execution authority mean this is not hostile multi-tenancy.

To rotate a token, stop the server and run:

```bash
a13n-claw reset-operator --root /absolute/private/claw-root
```

The command takes the same Instance lease and atomically replaces the private token file. Restart to use it. It rejects standalone/uninitialized roots before changing credentials. Restrict filesystem permissions; Windows operators must enforce equivalent ACLs. Provider and Lark secrets can be supplied through environment references. Native Host authentication/storage retains its own security contract.

## Environment authority

Native Host Projects, environments, files, and memory own working context. The starter workspace must exist and be separate from the Instance directory. Main and workers select the shared Project; Channel Projects can still share working files and all use Global memory. Thread history does not isolate shared working files.

The starter selects native files, shell, and Skills capabilities. Actual operations remain subject to native permission and environment policy. Local shell executes as the server OS account and is **not a sandbox**. Host-account code can reach outside logical file roots. Review native environment selections before granting participants access. The service container is a distribution boundary, not automatically a separate sandbox for each worker.

## Decisions, cancellation, and recovery

Use `threads active`, `threads decisions`, `operations show`, and `threads history`. Submit the complete native decision batch with its exact continuation ID. Ordinary chat is not an approval. Cancellation requests target one receipt; they do not roll back tools or establish that an external effect failed.

Direct operator submission retains native Host admission semantics and is never automatically retried. Receipts are process-local. After restart, inspect saved Thread history and exact available execution evidence. Missing correlation means unknown, not failed. The product stores its own Inbox/dispatch/delivery evidence without writing Host tables.

### Dispatch attempts

Inspect `a13n-claw attempts`. Interrupted calls and receipts lacking exact evidence remain blocked. Inspect actual effects before explicitly resolving an attempt:

```json
{"action": "settle", "evidence": "Reviewed saved history and external effects; no further processing required"}
```

Save this as a file and call `a13n-claw api POST /api/attempts/DISPATCH_ID/resolve --file resolution.json`. `settle` ends the associated business obligations. `retry` explicitly permits another attention/input attempt after review; it can repeat effects if the operator's assessment is wrong. Neither option manufactures Host completion evidence.

### Deliveries

Inspect `deliveries`. A send has `pending`, `sending`, `sent`, `not_sent`, or `unknown` status. Current source/destination authority is checked before each attempt. Unknown sends block automatic resend. Reconcile against platform evidence:

```json
{"outcome": "not_sent", "evidence": "Platform inspection confirms no message", "retry": true}
```

POST this body to `/api/deliveries/DELIVERY_ID/resolve`. `retry` defaults to false and is allowed only for confirmed non-send. Saved content and identity are retained. Never rerun the Agent to replace an uncertain delivery.

### Target creation and Inbox disposition

`targets` lists retained creation intents. If the proposed Host Thread exists with the expected Project and role, POST `{"key":"main","action":"adopt","evidence":"Inspected matching Thread"}` to `/api/targets/resolve`. Otherwise, an operator-confirmed `retry` retains the same proposed Thread ID. Claw never silently replaces canonical Main.

For a versioned business disposition, PATCH `/api/inbox/INBOX_ID` with `{"expected_version":1,"disposition":"handled","reason":"Reviewed result"}`. `pending` reopens a settled/deferred item. Unknown dispatches must be reconciled separately first; settlement is not an uncertainty bypass.

A pending native decision blocks dispatch to that Thread. Revoked source authority, missing capabilities, and no-progress backoff have visible reasons. There is no universal Claw pause gate over native collaboration. Stop the service to stop intake; native stop controls one operation.

## Diagnostics and retention

`a13n-claw diagnostics` reports content-free counts, unresolved effects, missing Projects, configuration state, and adapter readiness. It does not expose prompts or tokens and does not probe model providers.

Preview before pruning:

```bash
a13n-claw retention --older-than-days 30
a13n-claw retention --older-than-days 30 --apply
```

Each application processes a bounded batch. Retention removes eligible settled Inbox content and its attachment bytes, preserving deduplication tombstones and all dispatch, delivery, resolution, and unresolved-work evidence. Host history, memory, schedule results, and working directories have separate owners.

## Backup and restart

Stop the service and quiesce working-data writers before backup. Preserve the whole Instance directory, external paths selected by `host_configuration`/`host_data`, and Project working directories. The default layout contains:

- `claw.yaml`: product configuration.
- `business.sqlite3` and journal files: Inbox, dispatch, target creation, delivery, and resolution evidence.
- `operator.token` and lease file.
- `host/`: native configuration, state, Thread files, and memory under their native owners.

For a self-contained Instance root, use the offline checksummed commands:

```bash
a13n-claw backup --root /private/claw --destination /backups/claw-snapshot \
  --acknowledge-external-state
a13n-claw restore --root /private/restored-claw --backup /backups/claw-snapshot \
  --acknowledge-external-state
```

The destination snapshot must be new; restore requires an empty root and verifies the complete inventory. Directory enumeration failures abort the snapshot. An incomplete restore leaves a marker that blocks startup. External Host paths and symlinks require an operator-managed coordinated snapshot instead. Workspaces, external memory/Skills, executor home/cache, browser profile/downloads, and environment credentials need separate backup. Stop executor writers and use consistent volume snapshots; the Claw lease cannot quiesce them.

Configuration export alone is not a backup. Restore with matching versions and restrictive permissions, review absolute paths (they are intentionally preserved), reconcile external effects, then start exactly one owner. The product migrates only its business database; the Host owns its own migrations. Business DDL/data/revision changes commit atomically and unknown revisions fail without resetting data. Downgrades require a pre-upgrade backup.

The business migration history starts from a single initial baseline. Databases from the retired development revisions and old standalone `claw.sqlite3` databases are explicitly rejected. Preserve their files and select a fresh root; automatic conversion or import is unavailable. Mode/Main Project changes also require a fresh Instance in this delivery.

`status` reports Host openness and adapter state, not provider connectivity. Use an intentional model request and inspect its saved outcome to verify that specific provider configuration. Native receipts, platform ACKs, saved continuation, and confirmed delivery remain distinct.
