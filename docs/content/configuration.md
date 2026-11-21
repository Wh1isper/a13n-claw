---
title: Configuration and API
description: Configure Claw through Console or the authenticated Service API.
---

## Project and Inbox

Create a Claw with a native Agent and workspace service account. You can select its resources immediately or finish setup under **Settings → Project**. Select an existing Environment or create one from a template. For a Browser, create or attach one in native Console and select that instance in Claw. The Inbox accepts messages before an Environment is ready; Main starts when it can execute. Browser is optional. Set a heartbeat interval in seconds (`0` disables it) and independent limits for unread items and retained read items. See the [field reference](./configuration-reference.md).

The Project has its own Memory Space and records the Environment and Browser instance IDs. Start, stop, and recovery are native Service operations. A stopped resource can keep its binding; preserve files, CLI configuration and browser profiles according to the provider's retention guarantees. Rebinding applies to later Runs and does not transfer existing state. Archive active or paused fresh-Thread schedules before changing either Project resource binding.

## Headless operation

Claw endpoints use the Service origin, `X-Workspace-ID`, and a Service credential, under `/api/v1/apps/a13n.claw/`. Use `GET /claws` for discovery, `PATCH /claws/{claw_id}/project` to change resource bindings, and `POST /claws/{claw_id}/input` to submit `{ "content": "…" }`. Send an `Idempotency-Key` when submitting input; reuse it only for the same body. Use `GET /claws/{claw_id}/deliveries` and its detail endpoint for retained delivery history. The Service OpenAPI document lists installed endpoints. To add or edit a connector, send `name`, `kind`, `enabled`, and the platform-specific `config` to the connector collection or resource, with an optional write-only `credential`. Omit `credential` to keep the stored value. Set `reply_environment_id` to the currently bound Environment to authorize it for replies, or to `null` to revoke it.

## Reply accounts

Configure Service credential encryption on both Control and Worker before saving connector credentials. For a single host, set `A13N_ENCRYPTION__KEY_FILE` to a path on a persistent volume; Service creates the key on first start. On separate hosts, configure the same key ring on both. Back up the key separately from the database.

For an existing file-mounted connector, the saved credential takes precedence over `$CLAW_SECRET_DIR/<connector-id>`. Once the saved credential works, remove the old mounted secret through your deployment configuration.

Enable replies only for an Environment you trust with that platform account. The credential is passed to the reply process rather than configured for every shell call. Code running in the same Environment can still inspect the process or change its tools. Use scoped platform credentials and isolate the Environment from unrelated workloads. Replacing it revokes the reply authorization.

CLI maintenance needs a Linux shell with `timeout`, `curl`, `tar`, and `sha256sum`, and network access to the official GitHub releases. Open **Claw settings → Project → Platform CLIs** to see versions observed during the last Main execution. **Update CLIs** queues the latest official release for the current Environment. A queued update waits for the next Main execution; if heartbeat is disabled and Main is idle, submit work to the Inbox to start a Run. The button does not start an Agent on its own.
