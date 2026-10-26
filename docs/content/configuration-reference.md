---
title: Configuration field reference
description: Product settings, connection and Channel fields, and native resource ownership.
---

Read [Configuration and CLI](./configuration.md) for selection and mutation behavior. `claw.yaml` is UTF-8 YAML with `schema_version: 1`; unknown fields are rejected. Quote numeric-looking identifiers. Secret values belong outside this file.

## Product settings

| Field                | Default                     | Meaning                                        |
| -------------------- | --------------------------- | ---------------------------------------------- |
| `schema_version`     | `1`                         | Product format                                 |
| `mode`               | `one_thread`                | `one_thread`, `per_channel`, or `per_trigger`  |
| `host_configuration` | `host/a13n-harness-ui.yaml` | Native configuration root                      |
| `host_data`          | `host/data`                 | Native Host state                              |
| `main_project_id`    | `project-claw-main`         | Fixed shared Project for Main/workers          |
| `connections`        | `{}`                        | Named messaging connections                    |
| `channels`           | `{}`                        | Qualified conversation bindings                |
| `reconcile_seconds`  | `2`                         | Periodic business scan, 0.1–60 seconds         |
| `retry_seconds`      | `30`                        | No-progress/rejection delay, 1–3600 seconds    |
| `console`            | `true`                      | Serve the packaged Hello World page if present |

Paths resolve relative to the Instance. Product changes use explicit save/reload; mode, Main Project, Host locations, and Console serving remain startup-only. `--no-console` overrides the page setting. Listener flags default to loopback port 8080. Changing `mode` takes effect on restart: new messages use the selected mode, while old-mode work remains stored and is excluded from current automatic dispatch and delivery. See [routing mode on restart](./operations.md#routing-mode-on-restart). Main Project remains fixed; selecting another Main Project requires a fresh Instance root.

## Connections

Connections select `lark` or `github`; the adapter boundary and normalized message model are platform-neutral.

### Lark

| Field            | Default / requirement    | Meaning                                       |
| ---------------- | ------------------------ | --------------------------------------------- |
| `platform`       | `lark`                   | Adapter type                                  |
| `enabled`        | `true`                   | Start this connection                         |
| `app_id`         | Required                 | Platform application identity                 |
| `app_secret_env` | `CLAW_LARK_APP_SECRET`   | Environment variable holding the app secret   |
| `bot_open_id`    | Required                 | Bot identity used for mention and echo checks |
| `domain`         | `https://open.feishu.cn` | Feishu or `https://open.larksuite.com`        |
| `mentions_only`  | `true`                   | Group default; independent of routing mode    |

`status` reports connected/disconnected state and adapter failures. Text and post input, images/files, replies, exact text commands, and JSON decision batches are supported. Optional bounded feedback and reactions are available; rich interactive cards remain outside this delivery. Upload/download availability also depends on actual platform permissions.

### GitHub

| Field                      | Default / requirement    | Meaning                                           |
| -------------------------- | ------------------------ | ------------------------------------------------- |
| `platform`                 | Required `github`        | Notification polling adapter                      |
| `account_login`            | Required                 | Authenticated account; checked against `/user`    |
| `token_env`                | `CLAW_GITHUB_TOKEN`      | Token environment reference                       |
| `api_url`                  | `https://api.github.com` | Supported API origin                              |
| `poll_seconds`             | `60`                     | Poll interval, 10–3600 seconds                    |
| `delay_seconds`            | `600`                    | Delay before admitting an updated notification    |
| `initial_lookback_seconds` | `0`                      | Initial cutoff; zero starts from activation time  |
| `mark_read`                | `true`                   | Mark settled eligible notification versions read  |
| `reasons`                  | `[]`                     | Optional GitHub notification reason filter        |
| `mentions_only`            | `false`                  | GitHub uses notification reasons, not IM mentions |

GitHub bindings use `chat_id: owner/repository`, no `topic_id`, and explicit allowed author logins. See [GitHub polling](./github.md) for event and read-state semantics.

## Channels

| Field                         | Default / requirement  | Meaning                                                              |
| ----------------------------- | ---------------------- | -------------------------------------------------------------------- |
| `connection`                  | Required               | Connection ID in this file                                           |
| `chat_id`                     | Required               | Exact platform chat identity                                         |
| `topic_id`                    | `null`                 | Exact topic/thread identity, when used                               |
| `enabled`                     | `true`                 | Allow current source use and delivery                                |
| `allowed_senders`             | Required nonempty list | Qualified platform user IDs                                          |
| `operators`                   | `[]`                   | Allowed senders permitted to inspect/control associated work         |
| `mentions_only`               | `null`                 | Override connection group filter                                     |
| `project_id`                  | `null`                 | Required distinct native Project per Channel outside One Thread mode |
| `shared_context_acknowledged` | Required `true`        | Explicit acceptance of shared Agent context and memory               |
| `progress_updates`            | `false`                | Bounded receipt/admission/terminal feedback                          |
| `reactions`                   | `false`                | Lark message reactions                                               |
| `final_reply`                 | `false`                | Automatic final reply in per-Channel/per-trigger mode only           |
| `decision_prompts`            | `false`                | Exact decision prompt for an unambiguous authorized source           |

One `(connection, chat_id, topic_id)` has one binding. Channel operators must also be allowed senders. Retained events keep their original qualified source; changing a binding cannot redirect saved replies. Removing/disabling it or revoking a sender blocks later reads and delivery but cannot erase already-consumed content.

## Native resources

The Host owns native schema and resolution. Use `config sources` and `config read` to inspect the selected tree; use native resource files under `models/`, `agents/`, `projects/`, `extensions/`, and `mcp/`. Skills remain native file/plugin resources, not a Claw database catalog.

For `per_channel` or `per_trigger`, provision a native Project for each Channel before enabling it. Example `host/projects/team.yaml`:

```yaml
schema_version: '1'
kind: project
id: project-team
name: Team
roots:
  - path: /absolute/claw-workspace
defaults:
  agent: agent-claw
```

Then select `project_id: project-team` in that Channel. Different Channels require distinct Projects, while Global memory remains shared. One Thread instead selects Main's fixed shared Project. See [coordination](./one-thread.md) and [environment authority](./operations.md#environment-authority).
