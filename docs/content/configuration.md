---
title: Configuration and API
description: Configure Claw through Console or the authenticated Service API.
---

## Project and Inbox

Create a Claw with a native Agent and workspace service account. You can select its resources immediately or finish setup under **Settings → Project**. Select an existing Environment or create one from a template. For a Browser, create or attach one in native Console and select that instance in Claw. The Inbox accepts messages before an Environment is ready; Main starts when it can execute. Browser is optional. Set a heartbeat interval in seconds (`0` disables it) and independent limits for unread items and retained read items. See the [field reference](./configuration-reference.md).

The Project has its own Memory Space and records the Environment and Browser instance IDs. Start, stop, and recovery are native Service operations. A stopped resource can keep its binding; preserve files, CLI configuration and browser profiles according to the provider's retention guarantees. Rebinding applies to later Runs and does not transfer existing state. Archive active or paused fresh-Thread schedules before changing their Project Environment or adding a Browser.

## Headless operation

Claw endpoints use the Service origin, `X-Workspace-ID`, and a Service credential, under `/api/v1/apps/a13n.claw/`. Use `GET /claws` for discovery, `PATCH /claws/{claw_id}/project` to change resource bindings, and `POST /claws/{claw_id}/input` to submit `{ "content": "…" }`. Send an `Idempotency-Key` when submitting input; reuse it only for the same body. Use `GET /claws/{claw_id}/deliveries` and its detail endpoint for retained delivery history. The Service OpenAPI document describes the installed endpoints and their request schemas.

## Reply accounts

The CLI binaries belong to the Project Environment. Configure accounts there using the platform's supported sign-in and secret mechanisms. Binary installation, valid connector configuration, and a usable account are separate facts. Credentials never belong in message content.
