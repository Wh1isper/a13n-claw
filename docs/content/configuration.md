---
title: Headless configuration and CLI
description: Configure and operate Claw through reviewable YAML and authenticated commands, without a browser.
---

## Start without the Console

These commands are available in source builds, not the published `0.0.2` placeholder. From the source checkout, run them through `uv run a13n-claw`; an installed runtime wheel supplies `a13n-claw` directly.

```bash
uv sync --locked
uv run a13n-claw config init
uv run a13n-claw config path
```

Edit the generated `~/.a13n-claw/config.yaml`: replace the example model name with one supported by your account and select its provider. The starter uses a read-only Local environment with shell disabled. Then:

```bash
uv run a13n-claw config validate
uv run a13n-claw config apply --offline
uv run a13n-claw credentials set OPENAI_API_KEY --offline
uv run a13n-claw main init --profile default --offline
mkdir -p "$HOME/claw-workspace"
uv run a13n-claw serve --workspace "$HOME/claw-workspace" --no-console
```

The credential command uses a hidden prompt. For automation, use `--stdin` with a pipe from your secret manager; never put the secret itself in command arguments. A server environment variable is also supported; a managed credential with the same name takes precedence.

No Node.js or Console build is needed for this headless source workflow. Distribution builds still package Console assets. Without `--no-console`, available assets are served; missing assets produce a warning, not an execution failure.

In another terminal:

```bash
uv run a13n-claw status
uv run a13n-claw main show
uv run a13n-claw threads send THREAD_ID --request-id first-message --text "Hello"
uv run a13n-claw runs show RUN_ID
```

Use the Main ID from `main show` and the Run ID from the submission receipt. Acceptance is not completion. Repeat an uncertain submission with the same request ID and content; do not manufacture a new ID to work around a timeout.

## One document, explicit publication

A configuration file groups definitions by their stable resource IDs:

```yaml
format: 1
server:
  workspace: /home/operator/claw-workspace
  data_root: /home/operator/.a13n-claw
  host: 127.0.0.1
  port: 8080
  concurrency: 4
  console: false
models:
  primary:
    provider: openai
    model_name: your-model-name
    credential_env: OPENAI_API_KEY
environments:
  local:
    kind: local
    files: read
    shell: false
profiles:
  default:
    model_id: primary
    environment_id: local
    agent:
      instructions: |
        Help the user complete their work.
        Inspect saved worker results before accepting completion.
      usage_limits:
        request_limit: 50
defaults:
  profile_id: default
```

Additional groups are `skills`, `mcp`, and `channels`. Profile definitions use the same `permissions`, `skills`, `mcp_servers`, and `child_profiles` fields as the API. Skills contain `name`, `description`, `instructions`, and optional supporting `files`. MCP definitions contain `url`, optional `header_env` credential references, and optional `allowed_tools`. No imported definition can register executable providers. Local shell still means host-account execution, not a sandbox.

`server` is local launch configuration. `serve` reads it but does **not** apply resource definitions. `config apply` publishes resources and Channels but does **not** reconfigure a running listener, workspace, concurrency, or conversation mode. Export retains the selected local file's launch settings; the server does not export another machine's filesystem settings.

There is no file watcher, implicit import, deletion-by-omission, or two-way synchronization. Accepted database state remains authoritative. Removing a definition from a file leaves the saved definition unchanged. To retire it explicitly, list its key, such as `model/old`, under `retired`; remove active references in the same publication. Retirement does not erase historical Run compositions.

### Edit existing configuration safely

Export before editing existing resources:

```bash
uv run a13n-claw config export --output reviewed.yaml
# Edit reviewed.yaml without removing its versions.
uv run a13n-claw config validate --config reviewed.yaml
uv run a13n-claw config apply --config reviewed.yaml --dry-run
uv run a13n-claw config apply --config reviewed.yaml
```

Exports carry a `versions` map, for example:

```yaml
versions:
  model/primary: 1
  environment/local: 1
  profile/default: 1
  defaults/instance: 1
```

Missing versions mean **create-only**, not permission to overwrite. Stale versions reject the entire publication and preserve your file. Export fresh state separately and reconcile your edits; do not replace old versions with current values without reviewing intervening changes. Resource and Channel changes, their audit entries, and the publication receipt commit together. Active Profile references and delegation cycles are validated against the resulting configuration. Existing accepted Runs keep their captured definitions.

`config validate` checks document syntax and definition schemas without contacting a server. `config apply --dry-run` also checks current versions, Profile dependencies, and Channel policy using the selected live or offline Instance, then rolls back the publication. Neither check calls model providers or proves credential connectivity. An offline apply, including a first dry run, initializes an empty data root if necessary; it does not run agents.

Apply derives a stable request ID from the normalized publication content; `--request-id` can override it. Retrying the same request returns the original acceptance even across restart. It does not claim that later edits left those versions current. Reusing an ID for different content fails. Export again before preparing a subsequent edit. Server launch settings are not part of publication identity.

`config init` and `config export --output` refuse to overwrite existing files. `config show` displays the selected local document; `config export` displays accepted state. Both default to YAML and support `--json`. Managed credential values never appear in either export.

## Selection and ownership

Configuration selection is `--config`, then `CLAW_CONFIG`, then `~/.a13n-claw/config.yaml`. A missing explicit file is an error. `serve` can run without a default file; configuration apply requires one. Relative `server.workspace` and `server.data_root` resolve against the configuration file's directory, not the invoking terminal. Explicit path flags and environment paths resolve from the invocation directory.

For data root and workspace, explicit flags win over `CLAW_DATA_ROOT` / `CLAW_WORKSPACE`, which win over file settings. Listener and concurrency flags override file settings. With no workspace selection, startup cwd is used. Workspace and protected data must be separate, non-nested directories.

New Instances default to `one_thread`. Existing Instances keep their saved mode when no override is supplied. Explicit mode selection uses `--mode`, then `CLAW_CONVERSATION_MODE`, then `server.mode`. A mode switch is startup-only, requires settled work, and does not migrate old bindings or histories. Leave `server.mode` absent when you want to retain the established mode rather than force a switch.

`--offline` is available only for configuration apply/export, credential management, and Main initialization. It acquires the same exclusive data-root lease as the server and fails if the Instance is running. It does not reconcile or dispatch interrupted work. There is no automatic offline fallback after a network error.

Other commands use authenticated HTTP. By default they connect to the local listener and read the operator token from the selected data root. An explicit `--url` / `CLAW_URL` requires explicit credentials via `--token-file` / `CLAW_TOKEN_FILE` or `CLAW_TOKEN`; local operator tokens are not silently sent to arbitrary endpoints. Token-file selection takes precedence over `CLAW_TOKEN`. Redirects are not followed. Use HTTPS or a trusted tunnel for a non-local server.

## Channels and operational commands

Channel definitions use the [existing Channel policy](./one-thread.md#bind-an-external-conversation). They participate in the same atomic configuration publication:

```yaml
channels:
  personal:
    platform: http
    account: local
    channel: personal
    ingress_actor_id: operator
    allowed_senders: [alice]
    shared_context_acknowledged: true
```

This minimal example uses the local operator for ingress. Do not give an external integration the operator token; configure a dedicated scoped client through the application access-management API or Console. Vendor-specific platform adapters are not included yet. For an existing binding, export its `channel/personal` version before editing. A qualified destination cannot be retargeted in place. Context sharing must be explicitly acknowledged in One Thread mode.

Common commands are listed below. Every group has `--help`; `--json` provides scriptable output and rejected commands exit nonzero.

| Command group | Operations                                                                                                    |
| ------------- | ------------------------------------------------------------------------------------------------------------- |
| `status`      | Instance dispatcher health and saved mode; not a provider connectivity probe                                  |
| `credentials` | `list`, `set`, `remove`; values are write-only                                                                |
| `main`        | `init --profile`, `show`, `pause --version`, `resume --version`; `--retry` clears eligible processing backoff |
| `workers`     | List Main's saved persistent workers                                                                          |
| `threads`     | `list`, `show`, `history`, `inputs`, `runs`, `targets`, `send`, `release-inputs`                              |
| `runs`        | `show`, `decisions`, `cancel`, `reconcile --note`, `recover --request-id --text`                              |
| `decisions`   | `answer ID --file response.yaml` with the exact saved batch response                                          |
| `inputs`      | `acknowledge --note` uncertain input or `discard --note` blocked input                                        |
| `inbox`       | `list --after --limit`, `show ID`, `update ID --version --disposition --note`                                 |
| `channels`    | `list`; edit policy via configuration export/apply                                                            |
| `deliveries`  | `list`, `reconcile ID --version --outcome --note`; retry only confirmed `not_sent`                            |
| `targets`     | `inspect`, `prepare`, `stop`, `remove` a saved target ID                                                      |

Inbox deferral requires exactly one of `--dependency-run`, `--due-at` (timezone required), or `--human-key`. Reading an Inbox item does not settle it. Pause is durable automatic-processing control, not cancellation of an existing Run or delivery.

For a failed Run, inspect saved inputs, effects and decisions before `runs reconcile`. Then use an explicit, uniquely identified `runs recover` if recovery is safe. Unknown inputs require their own evidence-based acknowledgement; unknown deliveries require separate delivery reconciliation. CLI commands preserve these barriers and cannot turn an unknown effect into an automatic retry. See [Operations](./operations.md) and [One Thread coordination](./one-thread.md) for the underlying recovery semantics.
