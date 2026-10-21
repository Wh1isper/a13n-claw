---
title: Configuration and CLI
description: Keep product settings separate from native Host resources and operate without a browser.
---

## Selection and ownership

`--root`, then `CLAW_ROOT`, then `~/.a13n-claw` selects the Instance. `claw.yaml` owns connections, Channel bindings, routing, and product reconciliation settings. Save and explicit reload are separate operations; startup-only fields require restart. [The field reference](./configuration-reference.md) lists its fields.

The native tree under `host/` owns Models, Agents, Projects, extensions, Skills, MCP servers, and execution policy. Claw embeds the published Harness UI App and uses its validation and configuration mutations; it has no second Profile catalog. `host_configuration` and `host_data` can select alternate paths, resolved relative to the Instance unless absolute. Back up those paths if they are outside the Instance.

```bash
a13n-claw config path
a13n-claw config show
a13n-claw config sources
a13n-claw config read --path agents/claw.yaml
```

`path` and `show` read local product settings. Other configuration commands use the running service. API requests use `--url`, `CLAW_URL`, or `http://127.0.0.1:8080`; authentication reads `--token-file` or the selected root's `operator.token`. An explicitly selected URL receives that token, so select only a trusted endpoint. Redirects and automatic request retries are disabled; network failure never invokes an offline fallback.

## Product configuration edits

```bash
a13n-claw product-config show
a13n-claw product-config write --file candidate-claw.yaml --expected-digest SOURCE_DIGEST
a13n-claw product-config reload --expected-digest SAVED_DIGEST
```

`show` returns exact source, its digest, the accepted digest, and the accepted live snapshot. `write` validates references and atomically saves the UTF-8 file without applying it. `reload` accepts the selected saved digest, replaces listeners, and waits for already admitted sends before closing old clients. A conflict preserves the live generation; refetch and merge your draft. Direct external file edits require explicit reload and coordination with service writers.

Mode, Main Project, Host locations, and Console serving are startup settings. A connection changing platform/account identity needs a new ID. Retained per-Channel Threads keep their Project. Native resource edits and product edits are separate transactions; provision referenced Projects first.

## Native configuration edits

To update an existing native resource, retain `source_digest` from `config read`, edit a local candidate, then submit it:

```bash
a13n-claw config write --path agents/claw.yaml \
  --file candidate.yaml --expected-digest ORIGINAL_DIGEST
```

For a new resource use `--expected-digest new`. The Host validates and accepts the mutation for later compositions. Claw serializes its own editors and checks the observed digest; arbitrary filesystem editors do not participate in this lock. After a conflict, fetch current source and merge deliberately. Inspect accepted sources after an uncertain response before writing again.

You can also edit native files while stopped, or edit and call `config reload` while running. Native accepted state and diagnostics govern subsequent Runs; active Runs keep captured configuration. A changed Agent default does not rewrite an existing Thread's selections. `PATCH /api/threads/{id}/configuration` uses the native configuration version precondition; metadata has its own version.

A minimal native Model resource:

```yaml
schema_version: '1'
kind: model
id: model-claw
name: Claw Model
route: openai:gpt-4o
authentication:
  kind: api_key
  env: CLAW_MODEL_API_KEY
```

Keep secrets in credential environment references or native Host authentication facilities, never in configuration text. The generated `agent-claw` selects `ClawMessaging`, `dynamic_environment`, and `skills`. Removing `ClawMessaging` blocks product dispatch with a visible reason. Use native Agent/environment policy for tool permissions, model settings, and limits. The pinned Host 0.13 public Model route does not support `openai-responses-ws`; Claw does not silently substitute HTTP Responses. Deployments requiring that transport need a separately validated supported Host path before cutover.

## Command reference

All commands support `--help`. Results are JSON and rejected commands exit nonzero. `--root`, `--url`, and `--token-file` work before or after the command group.

| Group                                                    | Operations                                                                                       |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `init`                                                   | Create fresh files with `--workspace`, optional `--model`, `--model-key-env`, `--model-base-url` |
| `serve`                                                  | Open one Host, adapters, and API; `--host`, `--port`, `--no-console`                             |
| `status`, `diagnostics`                                  | Instance, adapters, errors; content-free aggregate diagnostic counts                             |
| `product-config`                                         | `show`, `write`, `reload` with digest preconditions                                              |
| `schedules`                                              | `list`, `reload`, `put`, `remove`, `trigger`                                                     |
| `occurrences`                                            | `list`, `show`, `cancel`, `resolve`, `attach`                                                    |
| `retention`                                              | Preview aged settled content; `--apply` prunes one bounded batch                                 |
| `backup`, `restore`                                      | Offline checksummed snapshots with `--acknowledge-external-state`                                |
| `config`                                                 | `path`, `show`, `sources`, `read`, `write`, `reload`                                             |
| `threads`                                                | `list`, `create`, `show`, `history`, `send`, `active`, `decisions`, `answer`                     |
| `operations`                                             | `show`, `steer --text`, `cancel` for an exact receipt                                            |
| `inbox`, `attempts`, `deliveries`, `channels`, `targets` | Inspect product state                                                                            |
| `api`                                                    | Explicit `GET`, `POST`, `PUT`, or `PATCH` path under `/api/`; `--file` supplies YAML/JSON        |
| `reset-operator`                                         | Rotate the token under the stopped Instance's lease                                              |

Examples:

```bash
a13n-claw threads send THREAD_ID --text "Inspect the current task"
a13n-claw threads active THREAD_ID
a13n-claw operations steer RECEIPT_ID --text "Keep the change focused"
a13n-claw threads decisions THREAD_ID
a13n-claw threads answer THREAD_ID --file complete-response.json
a13n-claw api GET '/api/threads?limit=100'
a13n-claw api GET '/api/inbox?offset=50&limit=50'
```

Thread creation supports `--project` and `--title`. A worker is created through the native collaboration tools or `POST /api/threads` with `coordinator_thread_id`; direct operator submission and steering to Main's workers retain Main attention. `threads send --file` reads text. `threads answer --file` sends a complete native `DecisionResponseBatch`, including the exact expected continuation ID and response kinds returned by decision inspection.

## Recovery operations

Use the generic API command for versioned settlement and evidence-bearing reconciliation. [Operations](./operations.md#decisions-cancellation-and-recovery) documents request bodies. There is no Claw-owned input replay queue or global pause; stop/steer and decisions retain native Host semantics. An unavailable receipt cannot be replaced with whichever operation is now active.
