---
title: GitHub intake
description: Route allowlisted GitHub activity from a controlled integration to Claw.
---

Create a GitHub connector with a workspace service account for the integration, an `owner/repository` allowlist, and an allowed sender list. Configure your integration to submit verified events to `POST /api/v1/apps/a13n.claw/claws/{claw_id}/connectors/{connector_id}/input` with its Service credential and `X-Workspace-ID`. Supply a stable `event_id`, repository as `channel`, the `sender`, and `content`. Optional issue and comment identifiers preserve reply targets.

Use a local GitHub integration or GitHub Actions to drive intake; Claw does not poll personal notifications. For replies, configure the official `gh` CLI in the Project Environment. If using the optional acceptance reaction, mount the GitHub token for that operation separately at `$CLAW_SECRET_DIR/<connector-id>`.
