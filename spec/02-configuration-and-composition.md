# Configuration and Host Composition

## Design Position

Harness UI owns Agent configuration and execution composition. Claw uses its native resource formats, identities, validation, accepted generations, and selection rules. Claw owns only product settings and references to those resources. A simpler frontend does not imply a second configuration language or a second source of truth.

## Two Configuration Owners

| Configuration                                                                                                 | Owner                      |
| ------------------------------------------------------------------------------------------------------------- | -------------------------- |
| Models, authentication sources, Agents, MCP servers, Skills, extensions, Projects, and execution environments | Harness UI                 |
| Thread defaults, per-operation selections, and captured execution composition                                 | Harness UI                 |
| Memory and organization settings                                                                              | Harness UI                 |
| Listener and frontend settings, selected Host configuration/data locations                                    | Claw process bootstrap     |
| Messaging connections, sender filters, Channel bindings, conversation mode, and delivery policy               | Claw product configuration |
| Schedules and heartbeat definitions                                                                           | Claw automation            |

The operator explicitly selects a Host configuration root and data location for the embedded Instance. Claw does not accidentally open the operator's personal Harness UI store because both applications are installed. Sharing a configuration root, and therefore its configured resources and memory scopes, is an explicit choice. The embedded store has one live owner.

Host resources retain the Host's multi-file organization and source-mutation rules. Claw neither flattens them into the old single-file Profile catalog nor mirrors accepted resources into its business database. Product settings may reference Host Agent, Project, and Thread IDs but cannot redefine their content or use filesystem paths as substitute identities.

## Setup and Editing

Setup can offer reviewed Host-native starter resources and a default working context, plus Claw connection settings. Publication is explicit and uses the owning validation boundary. Reopening setup must not overwrite existing resources, credentials, or Thread selections.

Claw's CLI/API use supported Host operations for Host-owned settings and Claw operations for product settings. Setup and operation require no runtime Console. A file save, accepted configuration generation, current operation's captured configuration, and provider connectivity are different facts. Clients expose those distinctions and preserve a conflicting draft and its original revision. Host file observation and generation acceptance retain Host semantics; Claw adds no competing watcher or reload engine.

A change spanning both owners does not claim one atomic transaction. Each part reports its outcome, and affected routing remains unavailable until references resolve. Invalid or removed Host selections produce an actionable blockage rather than silent substitution. Product configuration publication validates references against an accepted Host view; use-time validation still handles later changes.

Headless setup remains supported. Offline operations require exclusive Instance ownership and supported owner APIs; failed communication with a running Instance never falls back to direct database edits. Host model credentials stay in Host authentication stores; platform connection credentials remain protected Claw adapter inputs. Ordinary exports and diagnostics contain references, not secret values.

## Product Publication

Product YAML exposes its exact UTF-8 source, bytes digest, accepted digest, and accepted live snapshot. Saving uses a digest precondition and atomic file replacement; it does not apply the candidate. Explicit reload validates the selected digest and Host Project references before replacing adapter intake. Invalid candidates preserve the previous live configuration. Arbitrary external editors must coordinate with service writers; a digest recheck is not an operating-system compare-and-swap across unrelated writers.

Reload stops old listeners, allows admitted sends to finish and retain their outcomes, then accepts the new product settings and adapters. New sends validate against the accepted settings. Changes to mode, Main Project, Host locations, or Console serving require restart rather than live publication. A different connection identity requires a new connection ID; retained source/destination identities prevent old work from being redirected. An existing per-Channel Thread cannot silently move to another Project.

## Defaults and Capture

Claw selects product defaults by referring to Host resources. Creating a Thread uses Host creation rules; changing a default does not rewrite existing selections. Main is a Host Coordinator Thread, and worker creation follows native Host ownership and Project rules.

Product conversation mode selects One Thread, per-Channel, or per-trigger routing. Group filtering is configured separately and supports mentions-only in each mode. Per-Channel and per-trigger bindings retain a stable Channel-to-Project reference; per-trigger creation uses that Project's ordinary Thread creation defaults. One Thread uses Main's fixed shared Project. Setup publishes required Projects through Host-native configuration operations, records product associations separately, and enables routing only after both resolve. These mappings follow [routing](07-bridges-and-clients.md#qualified-identity-and-routing) and [memory](09-memory.md#scopes-and-sharing), without a new Host memory setting.

An Inbox item or automation occurrence retains its business source and intended routing, not an invented frozen Agent composition. The Host captures effective execution configuration when it admits the actual operation. Therefore a product item waiting before dispatch can use newer accepted Host resource content. Clients must not present business-acceptance time as Host composition-capture time.

Steering and decision responses use the Host's captured operation semantics. Claw cannot change an active Model, environment, or capability set by attaching new product metadata. Requests combining configuration mutation and execution use an explicitly supported Host operation or remain separate; Claw does not synthesize an atomic combination.

## Capabilities and Trust

Claw supplies native product Capabilities for Inbox access, business settlement, and delivery through the [embedding contract](11-host-integration.md#product-capabilities). Native collaboration and coordinator auto-follow-up keep their Host configuration and meaning. Host composition determines their availability; Claw rechecks current product authority at the operation boundary. In One Thread mode, publication additionally requires the actual canonical Main identity.

Installed Python packages and trusted extensions are executable operator choices. Neither a Channel payload nor an Agent-selected configuration field can install code or expand the trusted catalog. A selected Agent, stored credential, or successful configuration save does not prove readiness or grant new product authority.

## Invariants

1. Host resources have one format, validation owner, and accepted-generation authority.
2. Claw retains product configuration, not a second Agent catalog or captured composition.
3. Business acceptance and Host configuration capture occur at separate boundaries.
4. Configuration editing remains usable without the Claw frontend and never implies credential or provider readiness.
