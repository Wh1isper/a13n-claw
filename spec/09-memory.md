# Host File Memory

## Design Position

Claw adopts Harness UI's file-memory model instead of implementing another memory subsystem. Harness supplies file-memory tools, bounded untrusted projection, and conflict-aware mutations. Harness UI owns binding, configuration, cursor persistence, files, and automatic organization. Claw exposes supported settings and views and explains their sharing consequences.

The former Claw Global plus Thread-private design is replaced by Host Global plus Project scopes. Channel-scoped knowledge uses ordinary Host Projects in per-Channel and per-trigger modes; One Thread mode uses a shared Main Project without Channel partitions. There is no Claw memory database, independent projection cursor, or separate organizer.

## Scopes and Sharing

| Host scope            | Product meaning                                                    |
| --------------------- | ------------------------------------------------------------------ |
| Global                | Shared by conversations using the selected Host configuration root |
| Project               | Shared by conversations capturing the same Host Project identity   |
| Projectless execution | Uses Global memory without a Project scope                         |

Thread history and memory have different identities. The routing mode selects the following native Host mapping:

| Routing mode | Thread and Project relationship                                                | Memory available to execution                                             |
| ------------ | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------- |
| One Thread   | Main and its workers stay in one shared Main Project                           | Global plus Main Project memory, shared across all participating Channels |
| Per-Channel  | One persistent conversation Thread in each Channel's stable Project            | Global plus that Channel's Project memory                                 |
| Per-trigger  | A fresh task Thread for each eligible trigger, in the Channel's stable Project | Global plus the same Channel Project memory across tasks                  |

In per-Channel and per-trigger modes, each qualified Channel has a distinct stable Host Project reference. Renaming a Channel or replacing its conversation Thread does not change that reference. A new task starts without earlier task history but retains access to the Channel's long-term memory. Host-native Project creation and memory binding remain authoritative; Claw stores the association, not another memory namespace. Channel-scoped facts belong in Project memory; Global holds deliberately shared cross-Channel facts and preferences.

One Thread mode has no Channel-specific memory partition or access filter. Main and its workers use the full shared Global and Main Project scopes through ordinary Host memory tools and budgets. No Channel-specific Projects are required for this mode, and processing another Channel never switches Main's Project. This shared scope does not mean automatically mounting every unrelated or previously configured Host Project. No cross-Project memory access extension, all-Project scan, or custom memory Capability is required.

The operator sees the actual memory sharing before enabling a Channel or selecting Main context. Selective Inbox reading limits what is retrieved now; it cannot isolate knowledge already read, remembered, or copied into conversation history.

Memory is historical context, not policy or current proof. It cannot authorize a sender, grant a tool, change routing, or override current instructions. Secrets do not belong in memory. Broad Host file authority and trusted extensions retain their existing scope; file-memory scoping is not a tenant sandbox.

## Configuration and Updates

Host-native memory settings determine enablement, bound scopes, projection limits, and organization. Claw uses supported Host source operations; it does not translate them into a second product schema. A configuration change affects execution according to Host capture rules and does not silently delete retained files.

Foreground edits use the Host/Harness conflict-aware operations. They persist independently of Agent checkpoint success and are not rolled back by cancellation. Claw neither stages replacement directories nor retries an uncertain mutation without checking the owner's current evidence.

Memory storage and lifecycle remain separate from working directories, execution containers, and Claw Inbox records. A change to a Channel, workspace, or container is not a memory migration. New Thread creation, fork, and Project selection follow the Host's memory binding semantics. Switching between per-Channel and per-trigger modes preserves each Channel's Project reference. Switching to or from One Thread mode selects the destination mode's scopes without automatically merging, copying, or deleting retained memory; any knowledge transfer is an explicit operator action.

## Background Organization

Where supported by the selected embedded Host mode, automatic organization remains a Host-owned feature with its existing scope locks, budgets, opportunity triggers, retry rules, and fresh-context behavior. Claw does not start a duplicate organizer, sweep scopes with its scheduler, or supply conversation transcripts as an extraction pipeline.

Embedding must explicitly enable the intended Host capabilities through supported lifecycle options. A feature that is currently mode-specific in Harness UI is not assumed to exist merely because Claw has a web frontend. If the supported package cannot expose organization in embedding mode, the feature is visibly unavailable until the Host integration supports it; foreground memory remains independently described.

Organization history and files are read through supported Host queries. Viewing memory does not trigger inference or turn an internal maintenance Thread into an ordinary chat. Maintenance failure is shown without retroactively failing unrelated Inbox processing.

## Retention

Deleting a Channel, archiving a Thread, removing a Project, and deleting memory have separate effects under their owners. Claw must not delete shared knowledge as part of business-record cleanup. A product reference cannot keep an otherwise removed Host scope valid by itself; missing references are reported.

Backups include the selected Host configuration's memory locations and required Host metadata, not only Claw's business database. Removing memory cannot retract knowledge already present in saved history or external messages.

## Invariants

1. Host scope identity and memory lifecycle remain authoritative.
2. Channel memory identity outlives task Threads in per-Channel and per-trigger modes; One Thread deliberately shares its Main Project across Channels.
3. Claw adds no duplicate projection, persistence, or maintenance machinery.
4. Frontend availability and memory-feature availability are separate facts.
