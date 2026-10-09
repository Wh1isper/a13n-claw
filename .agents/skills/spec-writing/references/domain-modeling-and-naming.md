# Domain Modeling and Naming

Adapted for a13n Claw from Agent Foundation; replaced external platform conventions with self-contained modeling guidance. See [attribution and license](../../README.md#provenance).

Use when a specification adds or reshapes concepts, schemas, identities, revisions, lifecycle boundaries, or shared terminology. The examples below explain modeling choices; they do not establish product behavior. Read the owning contract before assigning semantics to an existing resource or operation.

## Derive Models from Flows

Walk through representative create, continue, change, cancel, read, and resume flows before selecting schemas or tables. For each operation, identify whether it creates an identity, continues one, or records an observation; specify the owner and lifecycle boundary.

Define concepts, relationships, and authority before field lists. Separate independently changing values at the boundary that owns their lifecycle. Keep values together when they have no independent identity, lifecycle, authority, compatibility, or query value.

For example, if one configuration value may change while another must remain frozen, placing both in a single immutable snapshot is too coarse. Determine which owner accepts the change before splitting the model. Do not infer from this example that an existing Run permits configuration mutation.

Managed references, exact revisions, overrides, inline definitions, triggers, and child entry paths should converge on the same core concepts when they represent the same semantics. An additional entry path does not by itself justify a parallel model.

Persisted and public types describe domain facts. Resolution, preparation, loading, or projection stages warrant separate models only when their results have independent contract meaning. Retain snapshots when historical reconstruction or compatibility makes them meaningful.

## Canonical Terms and Types

Use one owning specification, canonical model, and term for each concept; other documents link to it. Compare meaning before consolidating names: similar fields may represent distinct authority or compatibility boundaries.

Names state what a concept is without repeating the project or module namespace. Add a qualifier only when it distinguishes real concepts at the same boundary. For implementation naming, follow [DEVELOPMENT.md](../../../../DEVELOPMENT.md#code-quality-and-design).

Choose names that expose meaning: a reference identifies a resource, a request asks for an operation, a selection chooses among definitions, state records a condition, and an event records an occurrence. Define revision, lock, and receipt semantics in their owning contract before using those terms. A reference or receipt does not confer authority unless its contract says so.

Preserve distinct identity domains in conceptual schemas even when wire encodings are strings:

```python
# Conceptual identity types, not a wire-format declaration.
class RunEvent:
    run_id: RunId
    thread_id: ThreadId
```

Do not rename stable wire fields merely to improve internal names. A terminology change that crosses public, durable, or independently released boundaries requires the owning compatibility decision.

## Version and Revision Semantics

Distinguish resource identity, mutable definition versions, captured Run composition, and continuation checkpoint identity. Follow [configuration capture](../../../../spec/02-configuration-and-composition.md) and [persistence](../../../../spec/04-persistence-and-recovery.md) for their owners and lifecycles.

When a contract needs versioning, define what changes the version, which values it covers, and how stale updates or incompatible readers are handled. Do not invent another counter or freeze independently mutable metadata to satisfy a naming pattern. The high-level design does not prescribe a wire format or concurrency token. Protocols, artifacts, packages, and external systems retain their own version semantics.

## Model Review

Check that representative flows fit the identities, each separate model has independent meaning, and values change at the correct lifecycle boundary. Search for existing owners and alternate terms before adding concepts. Verify that terminology remains clear in context and that any public/durable rename preserves the accepted compatibility contract.
