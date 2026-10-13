# Configuration and Run Composition

## Design Position

Claw owns the configuration consumed by its API-driven product. A Profile is a reusable application definition, not an executable runtime object. The backend validates and accepts configuration; the console edits and selects it through the same application boundary used by other authorized clients.

Mutable defaults and fixed Run compositions have separate lifecycles. One operator-selected configuration file owns desired definitions and launch settings. The database retains the last successfully loaded definitions and the compositions captured for accepted work; it is a derived execution view, not a separately editable configuration catalog.

## Headless Configuration

An operator can configure and administer an Instance without a browser. Startup validates and loads the selected file before dispatching work. Runtime file edits take effect through explicit reload, without a file watcher. Launch settings select the listener, data root, workspace, concurrency, Console availability, and optional conversation-mode override; changing these fields requires restart.

The file describes the complete desired catalog. Omitted resources retire and omitted Channels disable, preserving history and captured compositions. An explicit retirement marker can retain an inactive definition in the file. Runtime-generated resource versions stay in the database; operators do not author them in configuration. Exports preserve the selected source and credential references, never managed secret values. An existing database without a configuration file requires explicit migration; startup cannot silently erase its catalog.

Saving the file and loading it have separate completion boundaries. A managed save validates the candidate and checks the exact source revision the editor read before replacing the file. It preserves comments and unrelated fields during structured edits. Identical retries are harmless; conflicting writes preserve the draft and report the conflict. External text editors need not honor application locks, so source drift remains observable. Reload validates the resulting active Profile graph and Channel policies, then atomically publishes their loaded state. A failed reload leaves the previous loaded state intact even when the file has already changed. A source revision identifies file bytes; it is distinct from resource versions and from provider readiness.

Offline initialization and configuration require exclusive Instance ownership. They use the same application validation and persistence boundary without dispatching work or reconciling interrupted execution. Operations against a running owner use authenticated APIs and cannot fall back to offline database access on a network failure. Definition validation does not assert provider connectivity. Credentials use a separate protected input path rather than command-line values or ordinary configuration exports.

## Definition Boundaries

A Profile expresses intended agent behavior and selections for models, instructions, capabilities, tools, skills, delegation, and execution environments. Shared resources retain their own identity and lifecycle. Claw uses Harness public concepts where they already own execution semantics rather than inventing parallel model, tool, or capability types.

Definition, availability, authorization, and readiness are separate:

- A resource can be known without being enabled for this Instance.
- A valid definition can refer to a service that is temporarily unavailable.
- A caller may read a resource without being allowed to use or change it.
- Saving credentials or a connection does not prove successful connectivity.

Provider credentials and privileged integration configuration remain protected backend state. Captured compositions retain the necessary references and provenance, not reusable secret values or live clients.

## Defaults and Effective Behavior

Instance and Profile defaults initialize Thread selections. Changing a default affects newly resolved work; it does not silently reset an existing Thread's explicit selections. Applying changed defaults to an existing Thread is an explicit operation.

The Instance selects its exclusive conversation mode at startup under [One Thread mode](10-one-thread-mode.md#instance-mode-and-lifetime). This is an Instance-level routing and authority choice, not a per-Channel, Profile, or model-selected override. Main identity, worker ownership, and automatic-processing pause are durable application state, not prompt conventions.

The Instance owns its one workspace root and the memory/automatic-organization settings. Thread or Profile selections cannot introduce another workspace root or another Thread's private memory. A memory-enabled ordinary Run captures Global and its own private scope according to [memory](09-memory.md); environment selection exposes the [shared workspace](05-workspaces-and-environments.md), not a Project selection.

Claw distinguishes editing a shared resource from selecting a different resource. Work that resolves that shared resource after an accepted edit can use the new content; work already accepted retains its captured content. Historical Runs remain explainable even if a resource is renamed or retired.

A submitted configuration change checks the source revision the editor actually read. Resource-oriented compatibility operations also check their resource version and route through the same file owner. Conflicting edits fail visibly without discarding local input. A combined configuration change and work submission either accepts the intended combination or rejects it; it never executes against an accidental mixture.

## Capture Flow

```mermaid
sequenceDiagram
    participant Caller
    participant Claw
    participant Definitions as Accepted definitions
    participant Work as Accepted work
    Caller->>Claw: Submit input and explicit selections
    Claw->>Definitions: Resolve one coherent effective composition
    Claw->>Claw: Validate scope, authority, and dependencies
    Claw->>Work: Retain input and captured composition
    Claw-->>Caller: Accepted work reference
```

A Run's captured composition is fixed when that Run is durably admitted, before queueing or environment preparation. Ordinary messages that join or steer an existing Run use its captured composition; they do not capture a second configuration or apply edited defaults to active work. A request combining input with a different composition must explicitly request a separate Run or be rejected, never disguised as a configuration-changing steer. Waiting in a queue does not silently adopt later Profile edits. Selecting the latest permitted Thread checkpoint at execution start is a separate operation owned by [execution](03-execution-lifecycle.md); configuration capture must not freeze a stale history for queued work.

Current permission and credential validity are checked again when work uses them. A captured definition is not a permanent authorization grant. If its required component becomes unavailable or disallowed, Claw reports the blockage or failure instead of substituting a different model, tool, or environment.

## Change and Retirement

Retiring a resource prevents new selection according to its policy while preserving historical descriptions. It does not rewrite accepted compositions. Destructive removal must not leave retained checkpoints or accepted work silently pointing to missing dependencies.

Provider implementations are trusted installation choices. A Profile, imported document, chat message, or bridge payload cannot register executable code or expand the installed provider catalog.

## Invariants

1. Every accepted Run has an inspectable effective composition independent of later edits.
2. Mutable defaults, shared resource content, and captured execution behavior remain distinguishable.
3. Configuration changes never replace or reset Thread history implicitly.
4. All clients use one file authority and validation boundary; file save, runtime load, launch settings, and current readiness are separate facts.
