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
- Retained attachments/artifacts, selected-context file browsing, and managed target lifecycle.
- Operator and scoped client access, write-only managed credentials, and offline operator-token recovery.

Published `0.0.2` artifacts are still the earlier informational CLI placeholder. The runtime described here is available from source, not retroactively added to those artifacts. This is an independent project with no legacy configuration or persistence compatibility.

## Start here

- [Getting started](./getting-started.md): start the server and configure a working Profile.
- [Operations](./operations.md): access, shared workspace, recovery, and data handling.
- [Development](./development.md): architecture, test boundaries, and packaging.
- [Release setup](./releasing.md): configure PyPI, GHCR, and the documentation site.

## Later deliveries

Global and Thread-private file memory with background organization; embedded bridges with reliable ingress/delivery; schedules, heartbeat, workflows, and bounded autonomous follow-up are not implemented in this delivery. Their accepted specifications describe intended contracts, not currently enabled controls. The static documentation site is separate from the runtime Console.
