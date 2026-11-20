---
title: Configuration and API
description: Configure Claw through Console or the authenticated Service API.
---

## Project and Inbox

Claw settings select a native Agent, workspace service account, Environment and optional Browser. The Environment and Browser remain Service resources. Set a heartbeat interval in seconds (`0` disables it) and independent limits for unread items and retained read items. See the [field reference](./configuration-reference.md).

The Project has its own Memory Space. A template-created Environment uses the selected template's Service lifecycle, so use a persistent configuration without idle deletion if you need to keep files indefinitely.

## Headless operation

Claw endpoints use the Service origin, `X-Workspace-ID`, and a Service credential, under `/api/v1/apps/a13n.claw/`. Use `GET /claws` for discovery and `POST /claws/{claw_id}/input` to submit `{ "content": "…" }`. Send an `Idempotency-Key` when submitting input; reuse it only for the same body. Use `GET /claws/{claw_id}/deliveries` and its detail endpoint for retained delivery history. The Service OpenAPI document describes the installed endpoints and their request schemas.

## Reply accounts

The CLI binaries belong to the Project Environment. Configure accounts there using the platform's supported sign-in and secret mechanisms. Binary installation, valid connector configuration, and a usable account are separate facts. Credentials never belong in message content.
