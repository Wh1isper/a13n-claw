# Host File Memory

## Design Position

Claw adopts Harness UI's file-memory model instead of implementing another memory subsystem. Harness supplies file-memory tools, bounded untrusted projection, and conflict-aware mutations. Harness UI owns binding, configuration, cursor persistence, files, and automatic organization. Claw exposes supported settings and views and explains their sharing consequences.

The former Claw Global plus Thread-private design is replaced by Host Global plus Project scopes. There is no Claw memory database, private scope per Channel or Thread, independent projection cursor, or separate organizer.

## Scopes and Sharing

| Host scope            | Product meaning                                                    |
| --------------------- | ------------------------------------------------------------------ |
| Global                | Shared by conversations using the selected Host configuration root |
| Project               | Shared by conversations capturing the same Host Project identity   |
| Projectless execution | Uses Global memory without a Project scope                         |

Thread history and memory have different identities. Main and workers in the same Project share Project memory under Host rules; distinct Thread IDs do not establish private knowledge stores. A Channel binding does not create a memory scope or authorize another Project's memory. Claw does not manufacture Projects merely to emulate its retired Thread-private model.

The operator sees the actual memory sharing before enabling a Channel or selecting Main context. One Thread mode deliberately shares Main knowledge across participating Channels. Selective Inbox reading limits what is retrieved now; it cannot isolate knowledge already read, remembered, or copied into conversation history.

Memory is historical context, not policy or current proof. It cannot authorize a sender, grant a tool, change routing, or override current instructions. Secrets do not belong in memory. Broad Host file authority and trusted extensions retain their existing scope; file-memory scoping is not a tenant sandbox.

## Configuration and Updates

Host-native memory settings determine enablement, bound scopes, projection limits, and organization. Claw uses supported Host source operations; it does not translate them into a second product schema. A configuration change affects execution according to Host capture rules and does not silently delete retained files.

Foreground edits use the Host/Harness conflict-aware operations. They persist independently of Agent checkpoint success and are not rolled back by cancellation. Claw neither stages replacement directories nor retries an uncertain mutation without checking the owner's current evidence.

Memory storage and lifecycle remain separate from working directories, execution containers, and Claw Inbox records. A change to a Channel, workspace, or container is not a memory migration. New Thread creation, fork, and Project selection follow the Host's memory binding semantics.

## Background Organization

Where supported by the selected embedded Host mode, automatic organization remains a Host-owned feature with its existing scope locks, budgets, opportunity triggers, retry rules, and fresh-context behavior. Claw does not start a duplicate organizer, sweep scopes with its scheduler, or supply conversation transcripts as an extraction pipeline.

Embedding must explicitly enable the intended Host capabilities through supported lifecycle options. A feature that is currently mode-specific in Harness UI is not assumed to exist merely because Claw has a web frontend. If the supported package cannot expose organization in embedding mode, the feature is visibly unavailable until the Host integration supports it; foreground memory remains independently described.

Organization history and files are read through supported Host queries. Viewing memory does not trigger inference or turn an internal maintenance Thread into an ordinary chat. Maintenance failure is shown without retroactively failing unrelated Inbox processing.

## Retention

Deleting a Channel, archiving a Thread, removing a Project, and deleting memory have separate effects under their owners. Claw must not delete shared knowledge as part of business-record cleanup. A product reference cannot keep an otherwise removed Host scope valid by itself; missing references are reported.

Backups include the selected Host configuration's memory locations and required Host metadata, not only Claw's business database. Removing memory cannot retract knowledge already present in saved history or external messages.

## Invariants

1. Host scope identity and memory lifecycle remain authoritative.
2. Main, worker, and Channel labels do not create private-memory guarantees.
3. Claw adds no duplicate projection, persistence, or maintenance machinery.
4. Frontend availability and memory-feature availability are separate facts.
