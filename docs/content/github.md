---
title: GitHub intake
description: Route GitHub notifications or external events to Claw.
---

## Poll GitHub Notifications

In **Claw → Connectors**, choose GitHub and **Notifications polling**. Save a classic GitHub token with `notifications` and `repo` access. Set the allowed sender logins; `*` permits any sender except the authenticated bot account. Logins and repository names are matched case-insensitively. Leave the repository list empty to include every repository visible to the token, or enter specific `owner/repository` names.

Claw reads issue and pull request notifications and admits comments that explicitly mention the authenticated bot login. It also admits a newly created, attributable issue or pull request when its notification reason is a mention and the configured maximum source delay has not elapsed. Configure the initial lookback and poll interval in seconds. The GitHub `X-Poll-Interval` header can lengthen the interval. The API URL must use HTTPS; pagination and subject links must remain on the same origin, and redirects are not followed.

Claw records each accepted source version and sends an eyes reaction as delivery feedback. It retries pending reactions after failures or restarts; deleted or unavailable targets stop retrying. Enable **Mark accepted notifications read** to mark their notification threads read. Claw does not mark notifications done. If the Inbox is full, the checkpoint stays in place and Claw retries the scan after space becomes available.

## Forward external events

For an external integration or GitHub Actions, choose **External events**, select its workspace service account, and enter repository and sender allowlists. Submit verified events to `POST /api/v1/apps/a13n.claw/claws/{claw_id}/connectors/{connector_id}/input` with the Service credential and `X-Workspace-ID`. Supply a stable `event_id`, repository as `channel`, the `sender`, and `content`. Optional issue and comment identifiers preserve reply targets.

## Credentials and replies

The GitHub token is separate from the Service credential used by an external intake integration. Open the saved connector and select **Check saved connection** to test read access. To let Main use `gh` and GitHub API requests, select **Use for Project egress** on one enabled GitHub connector. Service Controlled HTTPS Egress injects `GH_TOKEN` into approved GitHub host headers without exposing the token in the Environment. Ordinary Git HTTPS Basic authentication is outside this integration because Git encodes the placeholder before header substitution. See [Connector credentials](./configuration.md#connector-credentials) for policy and revocation behavior.
