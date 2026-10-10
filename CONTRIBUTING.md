# Contributing

## Setup

Use Python 3.13, uv, Node.js 24, pnpm 10.30.3, Git, and Make. Docker is needed for workflow and container checks.

```bash
git clone git@github.com:Wh1isper/a13n-claw.git
cd a13n-claw
make install
```

The independent `uv.lock`, `console/pnpm-lock.yaml`, and `docs/pnpm-lock.yaml` own dependency resolution. No neighboring repository is required. `make install` installs the Git hooks; commits must not bypass them.

## Validation

| Command               | Purpose                                        |
| --------------------- | ---------------------------------------------- |
| `make format`         | Apply pre-commit formatting and file hygiene   |
| `make lint`           | Check Python and Markdown formatting           |
| `make typecheck`      | Run Pyright                                    |
| `make deps-check`     | Check Python dependency declarations           |
| `make test`           | Run tests without external service credentials |
| `make console-check`  | Check console types and formatting             |
| `make console-build`  | Install and build packaged console assets      |
| `make console-dev`    | Preview the console with hot reload            |
| `make serve`          | Serve built console assets on port 8080        |
| `make docs-check`     | Check documentation formatting and types       |
| `make docs-serve`     | Preview the documentation                      |
| `make docs-build`     | Export the static site and check links         |
| `make build`          | Build console, wheel, and source distribution  |
| `make artifact-check` | Install and HTTP-test wheel and rebuilt sdist  |
| `make workflow-check` | Run actionlint in Docker                       |
| `make image-check`    | Build and run the non-root runtime image       |
| `make check`          | Run static checks                              |
| `make check-all`      | Run the full CI-equivalent gates               |

Choose local checks by the changed surface and risk. Do not repeat unaffected checks. CI runs the complete applicable gates for ready pull requests and pushes to `main`. Python checks also run on Windows; package artifacts must be installable without the source checkout. Report unavailable checks honestly.

`make build` owns the source-to-distribution pipeline. A direct `uv build` requires a prior `make console-build`; missing assets fail explicitly. The sdist includes frontend source and prebuilt assets, so rebuilding its wheel requires only Python tooling. Run `make console-install` after console lockfile changes before `make console-dev` or `make console-check`. `make serve` runs the backend with the last built assets, not Vite hot reload. `make console-dev` proxies `/api` to the backend on port 8080. Tests include loopback HTTP provider/MCP and real local shell execution. Opt into Docker lifecycle acceptance with `CLAW_TEST_DOCKER_IMAGE=python:3.13-slim-bookworm uv run pytest tests/test_environments.py`; this creates and removes disposable test-owned containers.

## Changes and reviews

Use GitHub Issues for unresolved product, architecture, security, and scope decisions. Small fixes can go straight to a pull request. Write accepted contracts in `spec/`, not proposals or progress reports.

Use English, scoped Conventional Commit titles, for example `feat(cli): report installed package version`. Explain the user-visible outcome and material limitations in the PR body. Add regression coverage for changed behavior; do not add tests that only mirror incidental wording or layout. Reviewers follow [MAINTAINERS.md](MAINTAINERS.md).

## Repository protection

The GitHub `default` ruleset protects `main`: changes go through a pull request, review conversations must be resolved, and the branch must be up to date before merging. These GitHub Actions checks are required:

- `Python (ubuntu-latest)`
- `Python (windows-latest)`
- `Workflows and container`
- `Build documentation`

Only squash merges are enabled, with the PR title as the commit title. Auto-merge and branch-update controls are available, and merged head branches are deleted automatically. No approval count is required for this single-maintainer repository; this does not waive the PR or CI requirements. No ruleset bypass actors are configured. Default-branch deletion and force pushes are blocked.

Releases use tags, not a long-lived release branch. The `release` ruleset makes `release/a13n-claw-v*` tags immutable: updates, force pushes, and deletion are blocked, with no bypass actors. The `pypi` environment accepts only those tags; the `docs` environment accepts only the `main` branch. Release automation additionally requires the tagged commit to belong to `main` and have successful CI and Site runs.

Workflow tokens default to read-only and cannot approve pull requests. Publication jobs grant their own narrowly scoped write permissions. Secret scanning, push protection, dependency alerts, and Dependabot security updates are enabled. Do not disable protections to work around a failing check; correct the cause or discuss a deliberate policy change.

## Documentation

Edit the Markdown files under `docs/content/` and keep `meta.json` navigation aligned. Use relative Markdown links between source pages; the site converts them to documentation routes. Run `make docs-build` and formatting for content or site changes. Documentation must distinguish available behavior from future direction.

## Releases

Source version is always `0.0.0`. Stable tags use `release/a13n-claw-vX.Y.Z`; RC tags use `release/a13n-claw-vX.Y.Z-rc.N`, with a positive `N`. Python metadata normalizes RC versions to `X.Y.ZrcN`. Never commit a release-only version bump.

Only tag a commit on `main` with successful **CI** and **Site** workflow runs. Release automation verifies those successful checks for the tagged commit, prepares the version in an ephemeral checkout, builds the wheel and sdist, publishes to PyPI, publishes the same wheel as a non-root GHCR image, and creates a GitHub Release with generated notes and both Python artifacts. Stable images also update `latest`; RC images do not. Publication is not a deployment of an agent service.

Configure the prerequisites in [Release setup](docs/content/releasing.md) before creating a tag. Publication requires explicit maintainer authorization for the selected release. Re-run failed jobs after checking which channels already published; never reuse a version for changed bytes.
