---
title: Schedules and heartbeat
description: Run isolated periodic tasks with durable occurrences and explicit result notifications.
---

## Create a task

Schedules live in `schedules.yaml` beside `claw.yaml`. Every occurrence gets a fresh independent native Host Thread in the selected Project. It shares that Project's memory and working files, not earlier task history. Native decisions, shell policy, and model settings still apply. Workflow orchestration is outside this delivery.

Save a definition as `check.yaml`:

```yaml
name: Daily workspace check
prompt: Inspect the workspace status and report actionable problems. Do not change files.
project_id: project-claw-main
timing:
  kind: cron
  cron: '0 9 * * *'
  timezone: Asia/Shanghai
overlap: queue
missed: latest
notification: error
destination:
  channel_id: team
```

The destination must be a configured enabled Channel. To keep results in the Host only, use `notification: never` and omit `destination`.

```bash
a13n-claw schedules list
a13n-claw schedules put daily-check --file check.yaml --expected-digest new
a13n-claw schedules trigger daily-check --request-key manual-check-1
a13n-claw occurrences list --schedule daily-check
```

Use `new` only when the catalog does not exist. Later writes require the digest returned by `schedules list`. Reuse a manual trigger's request key after a lost response; a new key deliberately creates another occurrence. Direct file edits require `schedules reload`; invalid catalogs leave the previous accepted catalog active with a diagnostic reason.

## Timing and policy

- `cron`: five-field expression plus IANA timezone.
- `interval`: `seconds` from 60 to 31,536,000; optional offset-aware `at` anchors the first firing.
- `once`: offset-aware `at` timestamp.
- `overlap: queue`: wait behind unresolved earlier occurrences of this schedule. `skip` records an overlapping opportunity without running it.
- `missed: latest`: run at most the latest missed firing. `skip` skips work older than `grace_seconds` (default 300).
- `notification`: `always`, `error`, or `never`. A heartbeat is an interval task, commonly `seconds: 86400` with error-only notification.

Pause by saving `enabled: false`; remove with `schedules remove ID --expected-digest DIGEST`. This stops pending dispatch and result notification, not already accepted execution. Retained occurrences preserve their original definition and destination; editing a task cannot redirect old output.

## Decisions and recovery

```bash
a13n-claw occurrences show OCCURRENCE_ID
a13n-claw threads decisions THREAD_ID
a13n-claw threads answer THREAD_ID --file complete-response.json
a13n-claw occurrences cancel OCCURRENCE_ID
```

Waiting decisions remain attached to the occurrence. CLI/API continuation tracks the exact new receipt. Unknown admission or continuation blocks automatic retry; inspect Host history and external effects before `occurrences resolve ID --resolution retry|abandon --evidence '...'`. If continuation was admitted separately, `occurrences attach ID --receipt RECEIPT_ID` correlates that observed operation to the same Thread.

Task results and notifications settle independently. A completed task with an unknown send must use delivery reconciliation, never task rerun. In One Thread mode the independent scheduled Agent cannot publish through Main's messaging tools; the service sends only the explicitly configured result notification.

## Manage from Lark

A Channel operator can ask the Agent to list, create, change, remove, or trigger schedules through `claw_schedules`. The tool requires the originating admitted Inbox item and rechecks the sender's current operator grant. It restricts management to that author's schedules in that source Channel, its native Project defaults, and its destination. Ordinary allowed senders and other Channels cannot manage those definitions.

The same tool's `history` action lists the latest 20 occurrences for that author and source, with bounded result previews. Channel operators can also inspect and control their own occurrences directly:

```text
/claw occurrences [schedule-id]
/claw occurrence OCCURRENCE_ID
/claw occurrence-decisions OCCURRENCE_ID
/claw occurrence-answer OCCURRENCE_ID <DecisionResponseBatch JSON>
/claw occurrence-cancel OCCURRENCE_ID RECEIPT_ID
```

Fetch the decision batch first and include its exact continuation ID in the answer. Include every intended response: omitted requests are declined by the native Host. Cancellation requires the current occurrence receipt from the detail command. Waiting-work cancellation abandons the business occurrence; it does not delete native decisions or undo tools. Pending occurrences without a Thread can be cancelled through the operator CLI.

These commands require the original author, qualified source Channel, current grants, and retained Project association. They cannot access operator-authored schedules or another author's tasks. Use the trusted operator CLI/API for those.

With `always` or `error` notification policy, a suspended occurrence also creates one attention notice per exact operation, pointing to its decision controls. `never` suppresses attention notices as well as final notifications. Unsent waiting notices are discarded when the operation is superseded; attempted sends retain their actual outcomes. Correcting an invalid answer preserves the same waiting occurrence, while an uncertain answer remains blocked for reconciliation.
