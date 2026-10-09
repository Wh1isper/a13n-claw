---
title: Operations
description: Operate a single-node Instance without confusing saved state, authority, and external effects.
---

## Access and secrets

The Instance is for a trusted operator and explicitly granted participants, not hostile multi-tenant workloads. Keep the default loopback listener or place it behind a trusted TLS reverse proxy. The Console and `/api` must share an origin. Forward the original Host and scheme correctly; do not enable arbitrary CORS or put access tokens in query strings. API calls require an Authorization Bearer header; browser requests with a foreign Origin are rejected. No cookies grant authority.

The operator can configure resources, credentials, client grants, recovery, and target management. A client gets explicit Thread IDs, Profile IDs, and actions; participation does not imply administration. Grants are checked again during execution and decision handling. Removing authority can stop further work but cannot undo an external effect already performed. Shared workspace data may be visible to any granted agent with file access, regardless of private conversation history.

The data root contains `claw.sqlite3`, its SQLite journal files, `operator.token`, and the process lease file. Managed provider/MCP credentials are stored as plaintext values in the protected database; this is not encrypted secret storage. Restrict filesystem access and protect backups. POSIX-created data roots use private permissions; Windows operators must enforce equivalent ACLs. Credential environment references can avoid storing a managed value. API exports and captured compositions exclude values, but prompts and tool results can contain user-supplied sensitive content and must also be protected.

To replace a lost or compromised operator token, stop the server, then run:

```bash
a13n-claw reset-operator --data-root /absolute/private/claw-data
```

The command requires exclusive ownership of that data root, writes a new private token file, and revokes the old operator token in the database. A crash between file replacement and database update produces an explicit startup mismatch; repeat the offline command to repair it. Do not edit the token file and expect an implicit rotation. Scoped client tokens remain separate; disable compromised clients in Settings. Client IDs must be single identifiers (letters, digits, dots, underscores, and hyphens, starting with a letter or digit). Client updates carry the viewed `version`; a stale form is rejected so it cannot restore revoked access. Creating or forking a Thread also advances the creator's grant version.

## Environment authority

All Threads share one workspace directory. The agent sees its selected binding as `/workspace`; Docker's backing mount uses a different container path. A Thread's history and target identity do not isolate shared files. Concurrent Threads may overwrite each other's files. Capturing a definition does not snapshot working data.

Local execution runs with the server account's OS authority. Logical file routing and disabled operations are useful boundaries, but enabling Local shell is **not sandboxing** and can access files outside the workspace. Do not grant it to untrusted participants. Docker provides a separate execution context, not a guarantee of isolation from intentionally shared writable data or the network.

Docker execution requires a reachable daemon and an explicit compatible image. Targets are Thread-owned and reused across Runs and server restarts when the captured environment generation is compatible. Changing model settings does not recreate a container; changing container-relevant settings selects another generation. Retention `keep` leaves the target available, while `stop` stops it after the last use. Prepare starts or reconciles a target; stop/remove reject busy targets. Removing a target discards its writable layer, not the shared workspace, retained artifacts, or conversation history.

There is no silent Docker-to-host fallback. After uncertain target operations, inspect before retrying. A server running in Docker does not automatically have daemon access. If deliberately granting that access, treat it as host-level privilege and ensure the workspace bind source resolves to the same absolute directory in both the server and Docker daemon's host namespace. The server's own private data directory must never be mounted into agent targets.

## Decisions, cancellation, and recovery

Ordinary message acceptance does not mean model consumption. The Console distinguishes queued, held, delivered, incorporated, blocked, and uncertain input. An ambiguous HTTP submission keeps its original identity/body in tab memory and offers reconciliation before an identical retry. Reloading discards unsaved drafts; it does not discard accepted backend work.

Cancellation requests stop owned execution and retain the latest proven checkpoint. They do not roll back shell commands, MCP calls, or provider effects. A server restart does not infer that an interrupted tool succeeded, failed, or is safe to replay. Pending decision state can be restored from its exact checkpoint; answering it requires current authority and the complete original batch.

For failed, interrupted, or cancelled work:

1. Inspect saved history, inputs, and any actual external effects.
2. Record the external-effects review in **Execution details**.
3. Resolve uncertain inputs explicitly. Acknowledging an input as unapplied is an operator assertion, not evidence generated by the application.
4. Start a linked recovery with a concrete instruction, or release known-unapplied blocked inputs when appropriate.

Recovery creates a new Run linked to the old one, from the still-current checkpoint. It does not replay unknown approved tool effects. Accepted decision facts survive and are correlated to their original requests. A stale recovery checkpoint is rejected rather than replacing newer history. Cleanup failures cannot rewrite an already committed successful result.

## Backup and restart

This delivery has no online backup scheduler, automatic retention, or cross-version migration command. Before upgrades or manual backups, stop the server cleanly and stop or otherwise quiesce all writers to the shared workspace, including retained shell processes. Preserve the **whole data directory**, including any WAL files, plus the workspace separately. Retained attachments/artifacts live in the database; workspace files and Docker writable layers do not. A resource JSON export alone is not a backup.

Restore with the server stopped, restrictive ownership/permissions, the matching application/dependency versions, and the same workspace binding where accepted work still depends on it. Start exactly one server. Verify saved Threads, pending decisions, recovery flags, and targets before accepting new work. Database schema versions newer than the application are rejected. Do not clone an active data root into two running owners with access to the same managed targets.

The authenticated Instance view reports dispatcher readiness and configuration facts. It does not probe model or MCP connectivity. A meaningful connectivity check is an explicit Run with the selected Profile; retain its saved outcome and investigate a failure rather than treating a configured credential as proof of service health.
