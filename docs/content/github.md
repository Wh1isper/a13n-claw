---
title: GitHub intake
description: Route allowlisted GitHub activity from a controlled integration to Claw.
---

Create a GitHub connector with a workspace service account for the integration, an `owner/repository` allowlist, and an allowed sender list. Configure your integration to submit verified events to `POST /api/v1/apps/a13n.claw/claws/{claw_id}/connectors/{connector_id}/input` with its Service credential and `X-Workspace-ID`. Supply a stable `event_id`, repository as `channel`, the `sender`, and `content`. Optional issue and comment identifiers preserve reply targets.

Use a local GitHub integration or GitHub Actions to forward eligible events. In **Claw → Connectors**, save a GitHub token restricted to the configured repositories and grant the platform permissions needed for comments or reviews. For a fine-grained token, grant **Issues: Read and write** for issue comments and **Pull requests: Read and write** for PR replies. This token is separate from the Service credential used by the intake integration. The same GitHub token also supports the best-effort eyes reaction on accepted events.

Select **Allow replies from this Project Environment** for a trusted Environment. Save, reopen, and select **Check credential** to test read access to the first configured repository. To verify write permissions, have the integration forward a test event to Claw and confirm Main's reply appears on GitHub.
