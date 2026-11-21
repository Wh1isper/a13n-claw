---
title: Operations
description: Inspect delivery state, configure access, and preserve Project resources.
---

## Access and secrets

Use a workspace service account with grants scoped to the resources each Claw needs. Save built-in connector credentials in Claw. Configure accounts for any other tools separately inside the Project Environment. A Project shares working files, Memory Space and optionally a Browser across Main and its workers. Review grants before using an existing resource.

## Diagnostics and retention

Use [Console](./console.md) to inspect connector intake, Inbox status, and exact Runs. An accepted arrival, a read Inbox item, a finished Run, and a platform reply are separate observations. For an uncertain external reply, inspect platform history before retrying.

## Backup and restore

Back up the AppDatabase with the corresponding Service PostgreSQL state. Preserve the associated Environment files, Browser state, and credentials according to the owning provider's procedures. After restoring, verify resource references and connector identities before reconnecting.
