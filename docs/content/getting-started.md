---
title: Getting started
description: Start an Instance, configure execution, and continue work in the Console.
---

## From source

Use Python 3.13 and [uv](https://docs.astral.sh/uv/) for the [headless configuration and CLI workflow](./configuration.md). The optional Console source build additionally uses Node.js 24, pnpm 10.30.3, and Make:

```bash
git clone https://github.com/Wh1isper/a13n-claw.git
cd a13n-claw
uv sync --locked
make console-build
mkdir -p "$HOME/claw-workspace"
uv run a13n-claw serve --workspace "$HOME/claw-workspace"
```

Open `http://127.0.0.1:8080`. For a new Instance, the first startup creates an empty `~/.a13n-claw/config.yaml` and `~/.a13n-claw/operator.token`; read that file locally and paste its value into the Console's **Access token** field. Do not put it in a URL, screenshot, shared terminal log, or configuration bundle. The browser keeps it only in this tab's memory; reloading requires reconnecting.

The default data root is `~/.a13n-claw`. The default workspace is the server's startup directory. Use `--data-root` / `CLAW_DATA_ROOT` and `--workspace` / `CLAW_WORKSPACE` to select explicit paths. The workspace must exist. Data and workspace must be separate, non-nested directories: credentials and saved application state must not sit inside the agent's ordinary file tree.

Use `--host 127.0.0.1 --port 9000` to change the listener and `--concurrency 4` to set the maximum concurrent Runs. One server process owns a data root; do not start multiple Uvicorn workers. Help and version commands do not start the server. Source builds report `0.0.0`.

## Configure the first Profile

In **Settings → Configuration**:

1. Select **Add starter definitions** to create a model, read-only Local environment, default Profile, and Instance defaults in one draft.
2. In **Models**, choose `openai`, `anthropic`, or `google`, supply the provider's exact model name, and set a credential reference such as `OPENAI_API_KEY`. OpenAI uses Chat Completions. An optional compatible base URL does not prove connectivity.
3. In **Environments**, review file authority and shell access. Docker requires an explicit image and a reachable daemon. Read [environment authority](./operations.md#environment-authority) before enabling shell or writes.
4. In **Profiles**, edit instructions, usage limits, tool policy, and optional skill/MCP/child selections. **YAML** exposes the complete shared draft and additional native agent settings.
5. Select **Validate & preview**, then **Save file**, then **Load saved file**. Saving writes the selected YAML file; loading activates it for new work. Accepted Runs retain captured definitions.
6. Open **Credentials** and save the reference's secret value. Alternatively provide it in the server environment before startup. Managed credentials take precedence and values cannot be read back.

Missing references and stale file revisions are rejected while your draft is preserved. See [Configuration and CLI](./configuration.md) for editing an existing file, conflict recovery, and migrating a pre-file Instance, and the [field reference](./configuration-reference.md) for each group.

## Choose centralized coordination

New Instances default to `one_thread`; existing Instances retain their saved mode unless explicitly overridden. Initialize Main in **Coordination** or with `main init --profile PROFILE_ID` after configuring its Profile. Use `--mode per_channel` only when independent per-Channel routing is intended. See [One Thread coordination](./one-thread.md) for Channel bindings, pause controls, and safe delivery reconciliation. Mode changes are startup-only and do not migrate old histories or pending work.

## Work in a Thread

Create a Thread, select a Profile, and send a message. The receipt is durable acceptance, not completion. Further ordinary messages steer active work or stay held at a pending decision. **Queue a separate Run** requests a distinct execution instead.

The Console polls saved inputs and Run state. It shows the saved final response rather than pretending a live stream is the durable result. **History** exposes saved native messages; **Execution details** shows captured definitions, children, pending recovery, and checkpoint forking. Closing the browser does not cancel work.

An approval requires an explicit choice for every call in the exact pending batch. Profile permission rules use native tool identity selectors, not just visible function names. File/delegation IDs are `claw.files.read`, `claw.files.retain`, `claw.work.delegate`, and `claw.work.inspect`. One Thread tools use `claw.collaboration.create`, `claw.collaboration.inspect`, `claw.collaboration.send`, `claw.attention.read`, `claw.attention.update`, `claw.inbox.read`, `claw.inbox.update`, `claw.messaging.destinations`, `claw.messaging.send`, and `claw.messaging.deliveries`; their actual availability also depends on Main/worker ownership. For example:

```json
{
  "default": "inherit",
  "rules": { "claw.files.retain": "ask" }
}
```

Additional native tools follow Harness permission selectors. Environment file/shell ceilings apply independently: an approval cannot grant a disabled operation. Child work is enabled only through the Profile's explicit child Profile selections and current caller grants.

Upload attachments in the composer and select the retained references to include. Each retained file is limited to 8 MiB. `read_retained_file` supports bounded UTF-8 text and provider-supported image, audio, and PDF content; it is not a general document converter. `retain_artifact` snapshots a selected workspace file into immutable storage. Downloads are attachments, never executable Console content. Forks copy portable conversation history, not retained files or pending work; old file references in forked history can therefore be inaccessible.

In **Environments**, select a Thread and captured Run to inspect its workspace through that environment, not through an unrestricted host file browser. A stopped Docker target must be prepared before file browsing.

## Installed artifacts and containers

Published `0.0.2` remains the informational CLI placeholder and does not contain this runtime. Until a runtime release is published, use a source build or install its locally built wheel. Installed serving and rebuilding a wheel from the sdist need no Node.js.

```bash
make build
docker build -f deploy/docker/Dockerfile -t a13n-claw:local .
docker run --name claw -p 127.0.0.1:8080:8080 \
  -v claw-data:/home/claw/data \
  -v claw-workspace:/home/claw/workspace \
  a13n-claw:local serve --host 0.0.0.0
```

The image runs as UID 10001. The configuration file is `/home/claw/data/config.yaml`. Named volumes preserve configuration and data across container replacement; bind mounts must be writable by that UID when writes are required. Read `/home/claw/data/operator.token` locally inside the server container to connect. The image prints help unless `serve` is supplied. It does not mount a Docker socket or create agent execution containers automatically. Read [Operations](./operations.md) before exposing or backing up an Instance.
