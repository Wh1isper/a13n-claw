---
title: a13n Claw
description: A local-first agent runtime with durable conversations and a self-hosted Console.
---

## Current source capabilities

Claw runs agents through published [a13n Harness](https://github.com/converge-ai-labs/agent-foundation) primitives. One Python server owns authenticated APIs, SQLite state, configuration, and reusable Local/Docker working environments. Its React Console manages real conversations and settings over those APIs.

- Durable Threads, accepted inputs, full checkpoints, and saved Run outcomes.
- Always-steer messages, held inputs at decision boundaries, explicit queued Runs, approvals, cancellation, and reviewed recovery.
- Captured model, Profile, environment, skill, MCP, and child-work definitions with current authorization checks.
- Independent delegated Threads and checkpoint forks sharing the Instance workspace.
- Optional One Thread coordination with persistent Main/workers, explicit Inbox dispositions, durable drain/backoff/pause, and direct worker conversations.
- Generic HTTP Channel ingress and Main-only explicit egress with independent delivery reconciliation.
- Retained attachments/artifacts, selected-context file browsing, and managed target lifecycle.
- Operator and scoped client access, write-only managed credentials, and offline operator-token recovery.

Published `0.0.2` artifacts are still the earlier informational CLI placeholder. The runtime described here is available from source, not retroactively added to those artifacts. This is an independent project with no legacy configuration or persistence compatibility.

## Start here

- [Getting started](./getting-started.md): start the server and configure a working Profile.
- [One Thread coordination](./one-thread.md): Main/workers, Channel bindings, Inbox processing, and delivery.
- [Operations](./operations.md): access, shared workspace, recovery, and data handling.
- [Development](./development.md): architecture, test boundaries, and packaging.
- [Release setup](./releasing.md): configure PyPI, GHCR, and the documentation site.

## Later deliveries

Global and Thread-private file memory with background organization; vendor-specific embedded messaging adapters; schedules, heartbeat, workflows, and general autonomous goals are not implemented in this delivery. Durable One Thread attention processing and generic HTTP ingress/delivery are available, not proof of a live vendor integration. Their accepted specifications describe intended contracts, not currently enabled controls. The static documentation site is separate from the runtime Console.
