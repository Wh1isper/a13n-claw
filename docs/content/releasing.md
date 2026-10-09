---
title: Release setup
description: Configure publication and the documentation Worker before the first release.
---

## GitHub environments and secrets

Create these environments in `Wh1isper/a13n-claw` under **Settings → Environments**. Never paste secret values into issues, chat, or repository files.

| Environment | Secret                  | Purpose                                     |
| ----------- | ----------------------- | ------------------------------------------- |
| `pypi`      | `PYPI_TOKEN`            | Publish the `a13n-claw` Python distribution |
| `docs`      | `CLOUDFLARE_API_TOKEN`  | Deploy the documentation Worker             |
| `docs`      | `CLOUDFLARE_ACCOUNT_ID` | Select the Cloudflare account               |

The `pypi` environment should allow tags matching `release/a13n-claw-v*`. The `docs` environment should allow only `main`. Optional required reviewers can protect publication.

### PyPI

Create a PyPI account with 2FA and an API token. For first publication of a new name, use an account-wide token; after the project exists, replace it with a project-scoped token. Store the value as `pypi / PYPI_TOKEN`. Confirm that `a13n-claw` is still available or owned by this maintainer before release. This workflow uses a token, not Trusted Publishing.

### Cloudflare

Enable Workers and choose a `workers.dev` subdomain for the account. Create an API token using **Edit Cloudflare Workers**, restricted to the target account. Grant Account / Workers Scripts / Edit, Account / Account Settings / Read, and Zone / Workers Routes / Edit plus Zone / Zone / Read for `wh1isper.top`. The zone must be active in the selected Cloudflare account. Remove or resolve any conflicting record at `a13n-claw.wh1isper.top` before deployment; do not overwrite an unrelated service. Put the token and Account ID in the `docs` environment.

The Worker is named `a13n-claw-docs`; `docs/wrangler.jsonc` owns its assets configuration. The workflow deploys a static export, not a Pages project and not the Claw package. The custom domain is `https://a13n-claw.wh1isper.top`, declared as a Worker Custom Domain in the configuration. Wrangler provisions its route and certificate during deployment. The account's `workers.dev` URL remains available as a secondary endpoint.

After credentials are configured, set the repository Actions variable `DOCS_DEPLOY_ENABLED` to `true`, then manually run **Site** on `main`. This explicit switch avoids failed or accidental deployments during bootstrap. Later pushes to `main` build and deploy automatically. PRs only build; they do not receive deployment credentials.

### GHCR

Publication uses the workflow's automatic `GITHUB_TOKEN` with `packages: write`; no PAT or additional secret is required. The first package can default to private even in a public repository. After the first image publication, open the package settings and set visibility to **Public** so anonymous pulls work. Preserve the source repository association and Actions access.

## Release checklist

1. Configure the secrets and environment branch/tag policies above.
2. Verify successful **CI** and **Site** runs on the exact `main` commit to tag. The release workflow checks both statuses before publishing.
3. Confirm the documentation deployment and PyPI namespace are ready.
4. Obtain maintainer confirmation to publish, then create and push the tag:

```bash
git tag release/a13n-claw-v0.0.1
git push origin release/a13n-claw-v0.0.1
```

Do not commit a version bump. The workflow injects `0.0.1`, builds the console and packages its assets in both wheel and sdist through `make build`, publishes PyPI, builds GHCR from that wheel for Linux AMD64 and ARM64, and creates a GitHub Release with generated notes and Python artifacts. A stable image publishes exact version and `latest`; an RC publishes only its exact `X.Y.Z-rc.N` tag.

## Verify and recover

Successful completion of **Release** is the all-channel completion signal. A visible PyPI version alone does not prove image or GitHub Release success. Verify `uvx --from a13n-claw==0.0.1 a13n-claw --version`, anonymous image pull after public visibility is set, and the Worker URL.

Use **Re-run failed jobs** after diagnosing a failure; do not move the tag. `uv publish --check-url` tolerates an already-published version. Existing GitHub Releases are left unchanged. Never re-publish changed source under the same version. If a release must change, use a new version.
