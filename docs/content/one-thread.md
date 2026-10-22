---
title: Routing and One Thread coordination
description: Route messages, control exact work, and retain business obligations independently of execution.
---

## Choose the conversation mode

Select `mode` in `claw.yaml` before the Instance's first serve:

| Mode                   | Eligible new message                                     | Memory                            |
| ---------------------- | -------------------------------------------------------- | --------------------------------- |
| `one_thread` (default) | Retain Inbox content; notify canonical Main              | Shared Global and Main Project    |
| `per_channel`          | Reuse the Channel's conversation Thread; steer if active | Global and stable Channel Project |
| `per_trigger`          | Create a fresh task Thread for each distinct event       | Global and stable Channel Project |

Duplicate events reuse their retained disposition. A quoted reply is still an ordinary message: in per-trigger mode it starts a new task. Explicit commands below select existing work. Mentions-only group filtering is independent of mode; ignored events create no Thread, download, or model call.

One Thread creates canonical Main as a native Host Coordinator on startup. Main's ID is in `status`. If target creation is uncertain or Main is unavailable, Claw preserves the reference and leaves the recovery API available. Ordinary Thread creation never replaces it.

Changing saved mode or Main Project is currently rejected. Initialize another root for a different mode and preserve the original data. There is no automatic history or memory transfer.

## Bind an external conversation

Follow [Lark setup](./getting-started.md#connect-lark-or-feishu). A binding authorizes exact connection/chat/topic and sender identities. One Thread shares context across Channels; `shared_context_acknowledged: true` is mandatory. Per-Channel and per-trigger bindings require distinct existing native Projects, as shown in the [reference](./configuration-reference.md#native-resources).

Authorized input is committed before transport acknowledgement. Attachments are collected afterward, before dispatch: at most eight files, 10 MiB each, 20 MiB combined, and a 15-second collection budget per event. Failed downloads and Host-rejected image formats are explicitly unavailable. Retained files remain accessible through scoped Claw tools and native Environment files. Ignored events retain no message body.

## IM commands

`operators` grants the selected allowed senders control of work associated with that Channel. Commands need no bot mention. They are processed without invoking a model. Use `/claw tasks` to obtain retained Inbox IDs:

```text
/claw help
/claw tasks
/claw status <inbox-id>
/claw continue <inbox-id> <text>
/claw steer <inbox-id> <receipt-id> <text>
/claw cancel <inbox-id> <receipt-id>
/claw decisions <inbox-id>
/claw answer <inbox-id> <complete DecisionResponseBatch JSON>
```

`continue` requires an idle Thread without pending decisions. `steer` and `cancel` require the exact currently active receipt. A stale receipt never controls a replacement operation. `decisions` returns the native pending batch and correlation; `answer` preserves the complete native JSON shape. Oversized responses direct the operator to CLI/API rather than truncating decision JSON. Cards and automatic token streaming are not implemented.

A Channel binding is not permission to inspect every Thread. Commands select work originally associated with that same Channel and recheck current sender/operator grants. In One Thread, control of associated work addresses shared Main. Its decisions may reflect shared context; grant `operators` accordingly. Worker history and direct worker conversations use the trusted operator CLI/API.

## Feedback and schedules

Enable `progress_updates` for bounded Lark messages about retained input, admission, and outcomes; `reactions` optionally adds status reactions. Enable `decision_prompts` separately for exact native prompts from an unambiguous operator-originated routed task. All default to false, retain delivery identity, and obey current source grants; a lost send response does not cause an automatic duplicate. Topic-reply fallback stays in the original authorized chat.

Channel operators can ask the Agent to manage isolated [schedules and heartbeat tasks](./schedules.md) through its source-scoped `claw_schedules` tool. Scheduled execution has its own occurrence and result-notification policy; it does not turn worker answers into unrestricted broadcasts.

## Work with persistent workers

Main creates and manages workers through native Host collaboration. Workers can report and ask their owner; ownership and same-Project scope remain Host-owned. Native notifications and auto-follow-up are best effort. They can overlap with product attention and are not a durable message bus.

For a direct operator conversation, use `threads send WORKER_ID --text ...` or exact `operations steer`. Claw records Main attention before calling the Host, including later worker inputs. The notice references already-requested work and its receipt or uncertainty. Main inspects that work instead of creating it again. Failed or interrupted publication does not authorize automatic resubmission to the worker.

Workers cannot use Claw tools to publish externally in One Thread mode. Native tools and MCP permissions remain governed by their selected Agent and environment; this product rule does not sandbox host-account shell execution.

## Process the Inbox explicitly

Main receives content-light attention and uses `claw_inbox_list`, `claw_inbox_read`, and `claw_inbox_file`. Routed Threads receive their original content and accepted attachments. `claw_inbox_settle` records a version-preconditioned disposition:

| Disposition | Meaning                                                |
| ----------- | ------------------------------------------------------ |
| `pending`   | Needs eligible attention                               |
| `deferred`  | Waits on an exact operation, time, or human resolution |
| `handled`   | Settled with an explicit reason/result                 |
| `ignored`   | Explicitly dismissed                                   |

Reading, notification, and Host completion do not settle business work. Reconciliation scans on startup, arrivals, and periodically. Known completion releases the exact attempt; remaining pending work is reconsidered after backoff. No-progress reasons and the next check remain visible. Unknown dispatches are excluded from actionable tools, even if Main starts through native collaboration.

Operation dependencies use exact `thread_id` and `receipt_id`. After restart, saved evidence is accepted only when its execution ID matches. Missing or overwritten evidence stays visibly deferred for operator review. Human deferral is reopened through an explicit versioned Inbox update. See [recovery](./operations.md#decisions-cancellation-and-recovery).

## Publish and reconcile separately

`claw_send_message` and `claw_send_file` retain content and a stable request key before sending to the original event's permitted destination. In One Thread only Main has this authority; elsewhere the routed Thread does. One Thread final answers are never broadcast automatically. In per-Channel/per-trigger modes, `final_reply: true` selects the exact associated completed continuation for a reply to its original destination; an explicit Agent delivery suppresses that automatic result.

Lark upload success is followed by a file reply. Failed upload is a definite non-send; a lost message response is unknown. Delivery reconciliation retries saved content, not inference. Inbox settlement, Host outcome, and external delivery are independent facts.
