---
title: Getting started
description: Start an Instance, configure execution, and continue work in the Console.
---

## From source

Use Python 3.13, [uv](https://docs.astral.sh/uv/), Node.js 24, pnpm 10.30.3, and Make:

```bash
git clone https://github.com/Wh1isper/a13n-claw.git
cd a13n-claw
uv sync --locked
make console-build
mkdir -p "$HOME/claw-workspace"
uv run a13n-claw serve --workspace "$HOME/claw-workspace"
```

Open `http://127.0.0.1:8080`. The first startup creates `~/.a13n-claw/operator.token`; read that file locally and paste its value into the Console's **Access token** field. Do not put it in a URL, screenshot, shared terminal log, or configuration bundle. The browser keeps it only in this tab's memory; reloading requires reconnecting.

The default data root is `~/.a13n-claw`. The default workspace is the server's startup directory. Use `--data-root` / `CLAW_DATA_ROOT` and `--workspace` / `CLAW_WORKSPACE` to select explicit paths. The workspace must exist. Data and workspace must be separate, non-nested directories: credentials and saved application state must not sit inside the agent's ordinary file tree.

Use `--host 127.0.0.1 --port 9000` to change the listener and `--concurrency 4` to set the maximum concurrent Runs. One server process owns a data root; do not start multiple Uvicorn workers. Help and version commands do not start the server. Source builds report `0.0.0`.

## Configure the first Profile

In **Settings**:

1. Add a **model** with a resource ID, provider (`openai`, `anthropic`, or `google`), the provider's model name, and a credential reference such as `OPENAI_API_KEY`. An optional base URL selects a compatible endpoint; it is not a connectivity check. OpenAI uses the Chat Completions protocol.
2. In **Credentials**, save the reference's secret value. Alternatively set that variable in the server environment before startup. Managed credentials take precedence. Credential values cannot be read back through the API.
3. Add an **environment**. Start with Local, read-only files, and shell disabled. Docker requires an explicit image available to the server's Docker daemon. Review [environment authority](./operations.md#environment-authority) before enabling shell or write access.
4. Add a **profile** selecting that model and environment. Optional native AgentSpec JSON supplies instructions and supported model settings; Claw owns execution dependencies, output type, models, and installed capabilities.
5. Optionally set **defaults** → `instance` → `profile_id`. Otherwise choose a Profile explicitly when creating a Thread.

Definitions are versioned. Editing a definition affects later admissions; accepted Runs retain their captured composition. Missing references and conflicting edits are rejected, not silently repaired. Use **Atomic import / export** for reviewed JSON bundles; exports contain credential reference names, never credential values.

## Choose centralized coordination

The default conversation mode is `per_channel`. For one Main coordinator with persistent workers and a durable external Inbox, start with `--mode one_thread` (or `CLAW_CONVERSATION_MODE=one_thread`) and initialize Main in **Coordination** after configuring its Profile. See [One Thread coordination](./one-thread.md) for Channel bindings, pause controls, and safe delivery reconciliation. Mode changes are startup-only and do not migrate old histories or pending work.

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

The image runs as UID 10001. Named volumes preserve data across container replacement; bind mounts must be writable by that UID when writes are required. Read `/home/claw/data/operator.token` locally inside the server container to connect. The image prints help unless `serve` is supplied. It does not mount a Docker socket or create agent execution containers automatically. Read [Operations](./operations.md) before exposing or backing up an Instance.
