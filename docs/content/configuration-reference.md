---
title: Configuration field reference
description: YAML groups, supported fields, defaults, and examples for one Claw Instance.
---

Read [Configuration and CLI](./configuration.md) for file selection, save/load behavior, conflict recovery, and migration. This page describes authored YAML. Runtime resource versions and secret values are stored separately.

## Document structure

`format` is `1`. All groups are optional; an empty catalog is `format: 1`. Unknown fields and duplicate/non-string YAML keys are rejected. Use UTF-8; the complete file is limited to 2 MB. Quote strings that YAML 1.1 could interpret as booleans or numbers, especially IDs such as `"yes"` or `"123"`.

`models`, `environments`, `profiles`, `skills`, `mcp`, and `channels` are mappings keyed by stable IDs. IDs contain 1–128 letters, digits, dots, underscores, or hyphens and start with a letter or digit. `server` and `defaults` are singleton mappings. `retired` is a list of resource identities still defined in the document, such as `profile/old` or `defaults/instance`. Removing a definition also retires it on load; removing a Channel disables it.

A small complete configuration:

```yaml
format: 1
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
      instructions: Help the operator complete their work.
defaults:
  profile_id: default
```

## Server

These fields take effect at startup. Relative paths resolve from the file's directory. Flags and environment overrides follow the [selection rules](./configuration.md#selection-and-ownership).

| Field         | Default                                           | Meaning                                                              |
| ------------- | ------------------------------------------------- | -------------------------------------------------------------------- |
| `host`        | `127.0.0.1`                                       | Listener address                                                     |
| `port`        | `8080`                                            | Port from 1 through 65535                                            |
| `data_root`   | `~/.a13n-claw`                                    | Protected SQLite, credentials, and operator token directory          |
| `workspace`   | Startup working directory                         | Existing shared workspace, separate from protected data              |
| `concurrency` | `4`                                               | Concurrent Runs across Threads, from 1 through 64                    |
| `mode`        | Retain saved mode; new Instance uses `one_thread` | Explicit `one_thread` or `per_channel` startup override              |
| `console`     | `true`                                            | Serve packaged Console assets if available; `--no-console` overrides |

The supplied container sets `CLAW_CONFIG=/home/claw/data/config.yaml`, alongside its protected data volume, and selects `/home/claw/workspace` as its workspace. Preserve both volumes when replacing a container.

## Models

| Field            | Required/default | Meaning                                            |
| ---------------- | ---------------- | -------------------------------------------------- |
| `provider`       | Required         | `openai`, `anthropic`, or `google`                 |
| `model_name`     | Required         | Exact nonempty identifier supplied by the provider |
| `credential_env` | Required         | Credential reference, for example `OPENAI_API_KEY` |
| `base_url`       | Omitted          | Optional compatible HTTP(S) endpoint               |

OpenAI models use Chat Completions. Endpoints must omit embedded credentials, query parameters, and fragments. Managed credential values override the server environment. Definitions and credentials are validated independently of network connectivity.

## Environments

| Field       | Default | Meaning                                              |
| ----------- | ------- | ---------------------------------------------------- |
| `kind`      | `local` | `local` or `docker`                                  |
| `image`     | Omitted | Explicit image required for Docker; omit for Local   |
| `user`      | Omitted | Optional container user or UID:GID; omit for Local   |
| `files`     | `read`  | `none`, `read`, or `write` file-operation ceiling    |
| `shell`     | `false` | Enable shell operations                              |
| `retention` | `keep`  | `keep` target available or `stop` after its last use |

```yaml
environments:
  container:
    kind: docker
    image: python:3.13-slim-bookworm
    user: "10001:10001"
    files: write
    shell: true
    retention: stop
```

Use an image and user compatible with the shared workspace's permissions. Threads have separate target identities but share working files. Local shell executes with the server account's authority. Read [Environment authority](./operations.md#environment-authority) before enabling it or granting Docker access.

## Profiles

| Field            | Required/default                  | Meaning                                              |
| ---------------- | --------------------------------- | ---------------------------------------------------- |
| `model_id`       | Required                          | Active model ID                                      |
| `environment_id` | Required                          | Active environment ID                                |
| `agent`          | Native defaults, request limit 50 | Native Harness behavior settings described below     |
| `permissions`    | `default: inherit`, empty rules   | Native tool policy using stable permission IDs       |
| `skills`         | `[]`                              | Unique active skill IDs                              |
| `mcp_servers`    | `[]`                              | Unique active MCP IDs                                |
| `child_profiles` | `[]`                              | Unique active child Profile IDs; cycles are rejected |

References resolve against the complete resulting catalog. Child selection does not grant client authority automatically. Current grants and environment ceilings continue to apply to captured Run definitions.

```yaml
profiles:
  default:
    model_id: primary
    environment_id: local
    agent:
      instructions: |
        Inspect saved state before acting.
        Ask for approval when an operation requires it.
      usage_limits:
        request_limit: 50
        tool_calls_limit: 20
        input_tokens_limit: 100000
        output_tokens_limit: 10000
      model_settings:
        temperature: 0.3
        max_tokens: 4096
    permissions:
      default: inherit
      rules:
        claw.files.retain: ask
    skills: []
    mcp_servers: []
    child_profiles: []
```

### Native agent settings

The Console exposes common instructions, usage limits, and model tuning. Use YAML for additional supported native fields: `name`, `description`, `system_prompt`, `toolset_instructions`, `retries`, `end_strategy`, `tool_timeout`, `metadata`, `cold_start_filter`, and `model_characteristics`. Their value shapes are validated by the installed Harness `AgentSpec` contract. Claw selects `model`, `capabilities`, execution dependencies, and output shape; do not configure those infrastructure fields inside `agent`.

`usage_limits` uses native `request_limit`, `tool_calls_limit`, `input_tokens_limit`, `output_tokens_limit`, `total_tokens_limit`, `per_request_input_tokens_limit`, `cost_limit`, and `count_tokens_before_request`. The request default is 50. Set a limit to `null` to remove that budget; an omitted request limit retains the default. The form's **Limit model requests** switch represents this explicit null state.

`model_settings` accepts `max_tokens`, `temperature`, `top_p`, `timeout`, `parallel_tool_calls`, `seed`, `presence_penalty`, `frequency_penalty`, `stop_sequences`, `openai_reasoning_effort`, `anthropic_thinking`, and `google_thinking_config`. Provider-specific support still depends on the selected model. Header and credential configuration belongs to model/MCP resources.

Permission settings are `inherit`, `allow`, `ask`, `review`, or `deny`. `inherit` resolves the installed tool's default policy. Rules use stable selectors such as `claw.files.retain`, not the visible function name `retain_artifact`. See [Getting started](./getting-started.md#work-in-a-thread) for Claw tool IDs. Approval cannot enable an operation disabled by the environment or restore revoked access.

## Skills

| Field          | Required/default | Meaning                                                                     |
| -------------- | ---------------- | --------------------------------------------------------------------------- |
| `name`         | Required         | 1–64 lowercase letters, digits, or hyphens, starting with a letter or digit |
| `description`  | Required         | Nonempty description, at most 16,384 characters                             |
| `instructions` | Required         | Nonempty skill content, at most 1,000,000 characters                        |
| `files`        | `{}`             | Relative portable file paths mapped to text                                 |

```yaml
skills:
  review:
    name: careful-review
    description: Review changes against their intended behavior.
    instructions: |
      Read the relevant contract and tests. Report concrete defects with evidence.
    files:
      references/checklist.md: |
        Check ownership, persistence, and the observable result.
```

Supporting paths use `/`, remain relative, exclude `..` and `SKILL.md`, and must not collide across supported filesystems. Up to 256 supporting files are accepted. The whole configuration file's size limit also applies. Skill content is data; it does not register executable providers.

## MCP servers

| Field           | Required/default  | Meaning                                                          |
| --------------- | ----------------- | ---------------------------------------------------------------- |
| `url`           | Required          | Streamable HTTP endpoint without credentials, query, or fragment |
| `header_env`    | `{}`              | HTTP header names mapped to credential reference names           |
| `allowed_tools` | Omitted or `null` | All tools; a list selects exact names and `[]` allows none       |
| `description`   | Omitted           | Optional description                                             |

```yaml
mcp:
  tools:
    url: https://tools.example.com/mcp
    header_env:
      Authorization: TOOLS_AUTHORIZATION
    allowed_tools: [search_documents]
```

Store the complete header value, such as a bearer header, in the referenced managed credential or environment variable. Enable the MCP ID through a Profile's `mcp_servers` list.

## Defaults

`defaults.profile_id` is an optional active Profile ID used when admitting new work without an explicit selection. The singleton runtime identity is `defaults/instance`. Existing Threads retain their selection, and accepted Runs retain their captured composition.

## Channels

Channels bind an external conversation to Claw's ingress/routing/delivery policy. See [One Thread coordination](./one-thread.md#bind-an-external-conversation) for access setup and transport behavior.

| Field                         | Required/default | Meaning                                            |
| ----------------------------- | ---------------- | -------------------------------------------------- |
| `platform`, `account`         | Required IDs     | External identity namespaces                       |
| `channel`                     | Required         | External conversation ID, 1–500 characters         |
| `ingress_actor_id`            | Required         | Existing authenticated integration client ID       |
| `execution_actor_id`          | `operator`       | Actor whose current grants govern execution        |
| `allowed_senders`             | Required         | 1–1000 explicit external sender IDs                |
| `enabled`                     | `true`           | Enable this binding                                |
| `inbound`                     | `true`           | Accept inbound messages                            |
| `outbound`                    | `false`          | Allow external delivery                            |
| `group`                       | `false`          | Identify a group conversation                      |
| `require_addressed`           | `true`           | Require explicit addressing for group messages     |
| `shared_context_acknowledged` | `false`          | Required for enabled Channels in One Thread mode   |
| `profile_id`                  | Omitted          | Per-Channel mode's execution Profile               |
| `delivery_url`                | Omitted          | HTTP(S) endpoint required when outbound is enabled |
| `credential_env`              | Omitted          | Optional delivery credential reference             |

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

The example is for local operator testing. Create a dedicated scoped client under **Settings → Access** before connecting an external integration; do not share the operator token. A Channel's qualified destination cannot be retargeted under the same ID. Use a new ID and disable the old binding deliberately. One Thread mode shares Main's context across participating Channels and permits only Main to publish externally.
