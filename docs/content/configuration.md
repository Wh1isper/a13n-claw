---
title: Configuration and CLI
description: Use one YAML source through your editor, the Console, or the CLI; save and load changes deliberately.
---

Claw uses one YAML file as the complete configuration source for an Instance. Edit it in your preferred editor or in **Settings → Configuration**. The Console's forms and YAML view edit the same draft and write the same file.

The runtime loads that file at startup. While the server is running, **Save file** writes reviewed source and **Load saved file** activates it for new work. SQLite stores the loaded projection, resource versions, and captured Run definitions. Credentials, client grants, and conversation history remain separate protected state.

These capabilities are available in source builds. Published `0.0.2` is the earlier informational placeholder. Source commands below use `uv run a13n-claw`; an installed runtime wheel provides `a13n-claw` directly.

## Start without the Console

```bash
uv sync --locked
uv run a13n-claw config init
uv run a13n-claw config path
```

Edit `~/.a13n-claw/config.yaml`. Replace the starter's model name with one supported by your account and choose its provider. The starter selects a read-only Local environment with shell disabled.

```bash
uv run a13n-claw config validate
uv run a13n-claw config reload --offline
uv run a13n-claw credentials set OPENAI_API_KEY --offline
uv run a13n-claw main init --profile default --offline
mkdir -p "$HOME/claw-workspace"
uv run a13n-claw serve --workspace "$HOME/claw-workspace" --no-console
```

The credential command prompts without echoing the value. For automation, pipe a secret manager's output to `credentials set NAME --stdin`; keep secrets out of command arguments and YAML. A managed value overrides the same reference in the server environment.

`config reload --offline` initializes an empty Instance and loads its catalog without dispatching agents. Alternatively, start `serve` immediately after editing: startup loads the file before dispatch becomes ready. Node.js and Console assets are unnecessary for `--no-console`.

In a second terminal:

```bash
uv run a13n-claw status
uv run a13n-claw main show
uv run a13n-claw threads send THREAD_ID --request-id first-message --text "Hello"
uv run a13n-claw runs show RUN_ID
```

Use the Main ID from `main show` and the Run ID from the receipt. Acceptance is not completion. After an uncertain submission, inspect saved state and reuse the same request ID with identical content.

## Configure through the Console

Open **Settings → Configuration**. The source card identifies the server's selected path, file revision, and loaded revision.

1. For an empty Instance, select **Add starter definitions**. Review the model name and provider.
2. Select a group and named definition. Forms cover models, Local/Docker environments, Profile behavior and permissions, skills, MCP servers, Channels, defaults, and server settings.
3. Switch to **YAML** for the complete source, comments, or additional native agent fields. Switching views and Settings tabs preserves the draft.
4. Select **Validate & preview** to check schemas, references, and current Instance policy and inspect the runtime changes.
5. Select **Save file**. The status becomes **Saved · not loaded**; accepted runtime configuration is unchanged.
6. Select **Load saved file** to activate the saved revision. Accepted Runs keep their captured definitions.

Store secret values under **Credentials** and manage scoped clients under **Access**. Those operations take effect independently of configuration saves. Drafts and uncertain save requests remain in this browser tab across navigation, but disappear on page reload or disconnect.

When another editor changes the file, your draft keeps its original revision. Refresh status, expand **Compare with current file**, and choose **Use current file** or explicitly **Keep draft on this revision**. The latter makes your draft a replacement for the reviewed revision; it is not an automatic merge. An uncertain save can be reconciled by refreshing status or retrying its exact original content.

## Save, load, and recover

| Action                          | Changes the source file                                                   | Changes loaded runtime definitions |
| ------------------------------- | ------------------------------------------------------------------------- | ---------------------------------- |
| Edit a Console draft            | No                                                                        | No                                 |
| Save through the Console or CLI | Yes, after validation and a revision check                                | No                                 |
| Save with an external editor    | Yes                                                                       | No                                 |
| Validate or preview             | No                                                                        | No                                 |
| Reload                          | No                                                                        | Yes, in one transaction            |
| Start the server                | Creates an empty file for a new unconfigured Instance, otherwise reads it | Loads before dispatch              |

The file is the **complete desired catalog**. Removing a resource retires it when loaded; removing a Channel disables its binding. Remove or update active references in the same edit. Historical Runs and Threads are preserved. To retain a definition in the file but exclude it from future selection, use `retired: [model/old]`, with `model`, `environment`, `profile`, `skill`, `mcp`, or `defaults/instance` identities. Retirement entries must refer to definitions still present in the file.

An invalid startup file stops startup. A failed reload leaves the previous projection active; a previously saved file remains saved. Correct the source, preview again, and reload. A failed save preserves the previous file. If a response is lost, inspect `config status` before retrying. Identical already-saved bytes reconcile successfully.

There is no watcher. `server` changes require a restart; reloading does not move the running data root or workspace, change its listener, concurrency, mode, or Console setting. The Console lists authored server fields changed since launch; explicit flags and environment overrides still take precedence at restart.

### Edit the server's file locally

```bash
uv run a13n-claw config show
# Edit the selected file with your text editor.
uv run a13n-claw config validate
uv run a13n-claw config reload --dry-run
uv run a13n-claw config reload
```

`config validate` checks the selected local document's syntax and schemas. `reload --dry-run` also checks dependencies and Channel policy against the selected Instance, without publishing the projection. These checks do not contact model/MCP providers or prove credential connectivity.

### Review and save a candidate through the API

```bash
uv run a13n-claw config status --json
uv run a13n-claw config export --output candidate.yaml
# Edit candidate.yaml; retain the original status revision for the save.
uv run a13n-claw config validate --config candidate.yaml
uv run a13n-claw config save --file candidate.yaml --revision FILE_REVISION
uv run a13n-claw config reload --revision SAVED_REVISION --dry-run
uv run a13n-claw config reload --revision SAVED_REVISION
```

Copy `FILE_REVISION` from the original status and `SAVED_REVISION` from the save result. Revisions are SHA-256 hashes of the whole file, including comments. A stale save or reload is rejected. Claw serializes its own writes, rechecks source bytes before atomic replacement, and preserves untouched YAML nodes during form edits. External editors do not participate in Claw's lock; coordinate edits rather than assuming an OS-level compare-and-swap with arbitrary programs.

Live `reload` reads the **server's** selected file; it never uploads a local document implicitly. `--config candidate.yaml` selects local CLI configuration, not a replacement server file. Use `save --file` for uploads. Live `export` returns the server's exact source, including comments and launch settings, rather than synthesizing a database catalog. `--offline` selects a stopped local Instance. `init` and `export --output` refuse to overwrite files. YAML contains credential references only; do not put secret values into authored text.

### Migrate an existing pre-file Instance

This command exports configuration from a database supported by the current migration history; it does not convert the retired `user_version` schemas. Those require a fresh development data root as described in [backup and restart](./operations.md#backup-and-restart).

Stop the server and back up its data and workspace. If a supported database has resource or Channel configuration but its selected file is missing, startup asks for explicit migration:

```bash
uv run a13n-claw config migrate --offline \
  --data-root /absolute/private/claw-data \
  --config /absolute/private/claw-config.yaml
```

Migration exports the existing catalog and active Channel bindings to a new file and loads it. It refuses an existing destination. Review launch settings, then consistently start with that same `--config`. Existing pre-release files containing `versions` must be updated to the file-first format; versions now belong to the loaded projection, not authored YAML. Keep a backup of the earlier file for comparison.

If a malformed file prevents the CLI from resolving connection settings, use an explicit `--url` and `--token-file` to reach the running server. For stopped-source repair, pass `--offline --data-root ...` to `config status`, `export`, or `save`; this avoids relying on the broken file to locate protected state.

## Selection and ownership

Configuration selection is `--config`, then `CLAW_CONFIG`, then `~/.a13n-claw/config.yaml`. A new Instance without a file starts with an empty catalog; `config init` is the explicit starter-generation command. Relative `server.data_root` and `server.workspace` paths resolve from the configuration file's directory. Explicit path flags and environment paths resolve from the invocation directory.

Data-root and workspace flags override `CLAW_DATA_ROOT` / `CLAW_WORKSPACE`, which override file settings. Listener and concurrency flags override the file. With no workspace selection, startup cwd is used; the workspace must exist and must be separate from, and non-nested with, protected data. `--no-console` disables the Console regardless of the file.

New Instances default to `one_thread`. Existing Instances retain their saved mode without an override. Explicit selection is `--mode`, then `CLAW_CONVERSATION_MODE`, then `server.mode`. Switching modes is startup-only, requires settled work, and preserves rather than migrates histories. Omit `server.mode` to retain established mode.

Offline source operations, credential management, and Main initialization require the same exclusive data-root lease as the server. They fail while the Instance is running and never dispatch interrupted work. Network errors do not trigger an offline fallback.

Live commands use authenticated HTTP. By default, they resolve the local listener and operator token from the selected configuration. An explicit `--url` / `CLAW_URL` requires `--token-file` / `CLAW_TOKEN_FILE` or `CLAW_TOKEN`; local operator tokens are not silently sent to arbitrary endpoints. Token files take precedence over `CLAW_TOKEN`. Redirects are not followed. Use HTTPS or a trusted tunnel for remote access.

## Command reference

Every group supports `--help`; `--json` produces scriptable output and rejected commands exit nonzero. See the [configuration field reference](./configuration-reference.md) for complete groups and examples.

| Command group | Operations                                                                                                                                                                          |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `config`      | `path`, `init`, `show`, `validate`, `status`, `save --file --revision`, `reload [--revision] [--dry-run]`, `export [--output]`, `migrate --offline`; `apply` is an alias for reload |
| `status`      | Dispatcher health and saved mode                                                                                                                                                    |
| `credentials` | `list`, `set`, `remove`; values are write-only                                                                                                                                      |
| `main`        | `init --profile`, `show`, `pause --version`, `resume --version`; `--retry` clears eligible processing backoff                                                                       |
| `workers`     | List Main's persistent workers                                                                                                                                                      |
| `threads`     | `list`, `show`, `history`, `inputs`, `runs`, `targets`, `send`, `release-inputs`                                                                                                    |
| `runs`        | `show`, `decisions`, `cancel`, `reconcile --note`, `recover --request-id --text`                                                                                                    |
| `decisions`   | `answer ID --file response.yaml` with the exact saved batch response                                                                                                                |
| `inputs`      | `acknowledge --note` uncertain input or `discard --note` blocked input                                                                                                              |
| `inbox`       | `list --after --limit`, `show ID`, `update ID --version --disposition --note`                                                                                                       |
| `channels`    | `list`; edit policy in the configuration file and reload                                                                                                                            |
| `deliveries`  | `list`, `reconcile ID --version --outcome --note`; retry only confirmed `not_sent`                                                                                                  |
| `targets`     | `inspect`, `prepare`, `stop`, `remove` a saved target ID                                                                                                                            |

Inbox deferral requires exactly one of `--dependency-run`, `--due-at` (timezone required), or `--human-key`. Reading an item does not settle it. Pause controls automatic processing; it does not cancel an existing Run or delivery.

Inspect saved inputs, decisions, and actual effects before `runs reconcile`, then submit a uniquely identified recovery only when safe. Unknown inputs and deliveries require their own evidence-based reconciliation. See [Operations](./operations.md) and [One Thread coordination](./one-thread.md).
