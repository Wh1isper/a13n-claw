---
title: a13n Claw
description: An IM-first assistant and headless service embedding Harness UI.
---

## Current source capabilities

One Python process embeds one published Harness UI App. Claw owns messaging and durable business obligations; the Host owns Agents, Models, Projects, Threads, execution, decisions, environments, collaboration, and memory.

- Lark/Feishu long-connection intake, identity-qualified sender/mention filtering, event deduplication, text/posts, files, and explicit replies.
- One Thread (default), per-Channel, and per-trigger routing with native Global/Project memory.
- Native Main/workers, retained Inbox dispositions, exact dependencies, backoff, and uncertainty reconciliation.
- IM commands for status, idle continuation, exact steering/cancellation, and native JSON decisions.
- Native scoped Claw tools for Inbox reads, retained files, settlement, and explicit text/file delivery.
- CLI/API initialization, native/product configuration edits, Thread operations, attachments, decisions, recovery, and token rotation.
- Lark progress/reactions/decision feedback, GitHub notification polling, and isolated schedules/heartbeat.
- Persistent Docker executor/browser, content-free diagnostics, conservative retention, and offline backup/restore.

The runtime frontend is Hello World. Operational interaction uses IM and CLI/API. `0.0.2` tagged artifacts contain the earlier informational placeholder; these capabilities require a build of this source. Old standalone databases need a fresh Instance rather than implicit conversion.

## Start here

- [Getting started](./getting-started.md): initialize and connect Lark.
- [Configuration and CLI](./configuration.md): native resource edits and headless operations.
- [Configuration field reference](./configuration-reference.md): product/connection/Channel fields.
- [Routing and One Thread coordination](./one-thread.md): modes, commands, workers, and Inbox.
- [GitHub polling](./github.md): repository bindings, delayed context, replies, and mark-read.
- [Schedules](./schedules.md): isolated tasks, heartbeat, notifications, and recovery.
- [Operations](./operations.md): authority, exact evidence, reconciliation, and backup.
- [Development](./development.md): implementation ownership, checks, and packaging.
- [Release setup](./releasing.md): PyPI, GHCR, and documentation publication.

## Later deliveries

Workflow orchestration is outside this delivery. Rich decision cards, token streaming, a full runtime Console, and further platforms remain separate work. Native memory is reused; automatic organization is disabled in the starter. Local protocol and Host tests establish the tested integration boundary; a particular live Lark tenant and model account still require an operator connectivity check.
