# Repository and Product Boundary

## Current product

A13n Claw is an independent, public project targeting a13n Harness. Its initial `0.0.1` distribution is a placeholder: it provides installed version metadata and an informational `a13n-claw` command. No agent execution, server, configuration loader, persistence, or authentication surface exists. Unknown CLI arguments fail rather than silently pretending to perform runtime work.

No YA Claw configuration, API, or persisted state compatibility is promised. The placeholder does not depend on Harness yet; future integration consumes its published public APIs and declares bounded version requirements.

## Content ownership

| Surface           | Owner                                          |
| ----------------- | ---------------------------------------------- |
| `src/a13n_claw`   | Python package and CLI                         |
| `tests`           | Offline behavioral and release-tooling tests   |
| `scripts`         | Release preparation and artifact validation    |
| `docs/content`    | User documentation and navigation              |
| `docs`            | Private Fumadocs static site and Worker config |
| `spec`            | Accepted product and architecture contracts    |
| GitHub Issues     | Proposals and open decisions                   |
| `deploy/docker`   | Non-root distribution image                    |
| `CONTRIBUTING.md` | Setup, validation, and release procedure       |
| `DEVELOPMENT.md`  | Engineering standards                          |
| `AGENTS.md`       | Concise coding-agent guidance                  |

The repository is independent from agent-foundation workspaces and validation. A checkout inside that repository's ignored `local-reference/` remains an ordinary standalone Git repository, not a submodule or package workspace member.

## Distribution

The source version remains `0.0.0`. Release tags define the version of Python artifacts and the container image. The runtime reports installed distribution metadata. Frontend tooling has no independent release identity. Generated assets and dependencies are not committed.

Cloudflare Workers Static Assets serves public documentation only. The image runs the placeholder CLI as a non-root user and exits. Neither artifact is an agent runtime deployment.
