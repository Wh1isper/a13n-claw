---
title: a13n Claw
description: A fresh start for a Harness-based local-first agent runtime.
---

## Current status

**Console placeholder, not an agent runtime.** Source builds provide a static console preview with overview, threads, environments, and settings screens. The Python CLI serves the packaged frontend; the screens describe planned capabilities without simulating runtime state. Published `0.0.2` artifacts remain the earlier informational CLI placeholder.

The future runtime targets [a13n Harness](https://github.com/converge-ai-labs/agent-foundation), with [YA Claw](https://github.com/Wh1isper/ya-mono/tree/main/packages/ya-claw) as product reference. This is a new project with no YA configuration compatibility.

## Start here

- [Getting started](./getting-started.md): build and serve the console preview.
- [Development](./development.md): repository layout and validation.
- [Release setup](./releasing.md): configure PyPI, GHCR, and Cloudflare.

## What is not available

There is no execution backend, application API, authentication, durable session store, schedule dispatcher, heartbeat, subagent orchestration, or bridge integration yet. The documentation site remains separate from the runtime console.
