---
title: Getting started
description: Initialize an isolated Instance and connect an embedded assistant to Lark.
---

## From source

Use Python 3.13 and uv. Initialize a fresh Instance with a workspace outside its protected directory:

```bash
git clone https://github.com/Wh1isper/a13n-claw.git
cd a13n-claw
uv sync --locked
mkdir -p "$HOME/claw-workspace"
uv run a13n-claw init --workspace "$HOME/claw-workspace" --model openai:gpt-4o
```

Choose a model route available to your account. `--model-key-env` selects a credential environment variable (default `CLAW_MODEL_API_KEY`); `--model-base-url` optionally selects a compatible endpoint. Supply the secret to the server environment through your secret manager. Initialization validates native configuration, not provider connectivity. Omitting `--model` creates starter Agent and Project resources for later configuration.

The default Instance is `~/.a13n-claw`; `--root` or `CLAW_ROOT` selects another directory. Initialization writes `claw.yaml`, a native Host configuration tree under `host/`, and `operator.token`. It refuses existing configuration instead of overwriting it. Old standalone roots containing `claw.sqlite3` require a separate fresh root; preserve their data.

```bash
uv run a13n-claw serve --no-console
```

One server process owns the Instance. In another terminal, `uv run a13n-claw status` shows the canonical Main Thread, connection status, and product errors. `threads send THREAD_ID --text "Hello"` submits an explicit operator input. Inspect `operations show RECEIPT_ID` and `threads history THREAD_ID` for its outcome. Receipts establish admission, not saved completion.

The starter enables native file, shell, Skills, and Claw messaging tools. Native environment and permission policy still applies. Review authority before connecting participants: Local shell runs as the server account. See [Operations](./operations.md#environment-authority).

## Connect Lark or Feishu

Create a bot-enabled custom application in your platform's developer console. Configure long-connection event delivery for `im.message.receive_v1`, grant the application's message receiving/sending and resource upload/download permissions required by your use, and make the application available to the selected participants. Obtain the app ID, app secret, bot open ID, chat ID, and allowed users' open IDs from the platform. Display names are not identities.

Stop Claw, add a connection and binding to `~/.a13n-claw/claw.yaml`, and supply `CLAW_LARK_APP_SECRET` to the server environment:

```yaml
schema_version: 1
mode: one_thread
connections:
  team:
    platform: lark
    app_id: cli_your_app
    app_secret_env: CLAW_LARK_APP_SECRET
    bot_open_id: ou_your_bot
    domain: https://open.feishu.cn
    mentions_only: true
channels:
  team-room:
    connection: team
    chat_id: oc_your_chat
    allowed_senders: [ou_your_user]
    operators: [ou_your_user]
    shared_context_acknowledged: true
```

Use `https://open.larksuite.com` for Lark. Restart Claw and inspect `status`. A connected socket proves the connection, not successful model execution. Mention the bot in the configured group and ask for a reply. Direct messages still require an explicit binding and sender allowlist. Text, posts, images, and files are normalized; unsupported content and failed attachments are reported as unavailable.

One Thread shares Main's context and Project memory across all bound Channels. Channel operators can inspect Main decisions, which may contain shared context. Only grant this role to participants trusted for that context. See [routing and commands](./one-thread.md).

## Optional page and containers

The runtime page is Hello World. IM and CLI/API provide operational interaction. To serve the page from source, run `make console-build`, then omit `--no-console`. Installed wheels and sdist rebuilds need no Node.js. The `0.0.2` tagged artifacts contain the earlier informational placeholder; use a build of this source for these capabilities.

```bash
make build
docker build -f deploy/docker/Dockerfile -t a13n-claw:local .
docker run --rm -v claw-data:/home/claw/data \
  -v claw-workspace:/home/claw/workspace \
  a13n-claw:local init --workspace /home/claw/workspace --model openai:gpt-4o
# Edit claw.yaml and native resource files in the data volume before serving.
docker run --name claw -p 127.0.0.1:8080:8080 \
  -v claw-data:/home/claw/data -v claw-workspace:/home/claw/workspace \
  -e CLAW_MODEL_API_KEY -e CLAW_LARK_APP_SECRET \
  a13n-claw:local serve --host 0.0.0.0
```

The image runs as UID 10001 and selects `/home/claw/data` via `CLAW_ROOT`. Preserve both volumes. Bind mounts must have suitable ownership. The image prints help by default; startup and initialization are explicit. See [configuration](./configuration.md) and [backup](./operations.md#backup-and-restart).
