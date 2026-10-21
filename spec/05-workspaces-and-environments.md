# Host Workspaces and Environments

## Design Position

Claw adopts Harness UI's working-context and Environment model. The Host resolves configured Projects, explicit roots, execution modes, provider selections, and saved Environment state. Claw offers authorized configuration and inspection surfaces, not a separate target manager or a general Docker proxy.

Running the Claw process inside a container is a deployment choice independent of the environments in which Agents operate.

## Working Context

Setup can initialize a common working directory using native Host configuration. A selected Host Project or explicit Host-supported root selection remains authoritative. The old requirement that every Thread use one immutable Claw workspace is replaced by Host selection semantics; Claw does not introduce its own workspace identity or silently override a Thread's captured roots.

Channels reference Threads, not filesystem directories. Main and workers follow Host Project and creation-default rules. An ordinary Channel cannot choose arbitrary host paths, install providers, or turn an external message into a new mount. Changing working context is an authorized Host configuration operation, separate from message routing.

Thread histories do not isolate shared working files. Threads with common roots can affect the same files. A Project is a working context, not a hostile-tenant isolation boundary. Setup and inspection surfaces disclose selected roots, execution mode, and the sharing implied by a binding.

## Lifecycle Ownership

| Responsibility                                                                       | Owner                                                      |
| ------------------------------------------------------------------------------------ | ---------------------------------------------------------- |
| Resolve and capture working context for execution                                    | Harness UI                                                 |
| Prepare and close Run access, retain provider state, and expose supported management | Harness UI and its providers                               |
| Execute file and command operations                                                  | Harness Environment boundary                               |
| Decide which product caller may select or inspect a context                          | Claw access policy, within Host capabilities               |
| Retain or explicitly remove backing data                                             | The resource's owning Host/provider operation and operator |

Claw does not copy container IDs into a second target catalog, implement its own generation algorithm, race Host idle cleanup, or recover shell processes from old observation handles. Reuse, readiness, replacement, and state publication follow the selected Host provider's actual contract. The previous standalone Claw Thread-container reuse guarantee is not retained as an independent layer.

Unsupported management actions are unavailable rather than approximated with direct Docker or filesystem commands. A required unavailable environment blocks the operation; Claw never falls back to host-account execution. Historical selected state and current readiness remain distinct in product views.

## Persistent Docker Deployment

The supplied Compose deployment owns a long-lived authenticated Envd executor and a visible browser alongside the Claw service. A native HTTP Device and remote-only Host Project select that executor through public Host configuration. Compose owns container allocation and persistent workspace/home/cache/browser/download volumes; the Host owns Run access, native file transfer, process controls, and captured selections. Neither Claw nor Compose recreates a Host-private provider state machine.

Thread attachments and results cross the Host/Device boundary through native copy operations. Guest shell paths and logical Environment file paths remain distinct. Missing remote resources fail explicitly without local fallback. Browser automation and human takeover share one persistent profile with exclusive browser ownership.

Host Docker socket access is an explicit deployment option granting host-level Docker authority. Equal host/guest workspace paths preserve nested bind-mount meaning, and the executor uses the socket's numeric group. It is a trusted-workload option, not a sandbox boundary. Stopping a Run or replacing a service does not delete persistent files or reverse side effects.

## Data and Access Boundaries

Host configuration, credentials, saved continuation, Thread files, memory, Claw business state, and working directories have separate owners and lifetimes. A workspace selection does not automatically expose protected application directories. Attachments enter Host execution through supported file/input operations or bounded Claw Inbox reads with explicit provenance and authorization, not by mounting the entire product store.

IM attachment transfer and CLI/API file access follow the selected Host boundary and product grants. A remote participant receives only the specifically authorized file, not ambient access to the Host's filesystem browser. Embedding does not implicitly enable the stock WebUI's native Host file browser, Git access, PTY, or computer sharing. Those are distinct privileged surfaces, not requirements of the Claw frontend.

Local or Full Control execution is host-account authority, not a sandbox. Product Channel checks and Main-only messaging tools cannot isolate an Agent that has separately been granted broad filesystem, shell, or network credentials. Configure such authority deliberately and disclose it accurately.

## Reconfiguration and Removal

Next-operation selections and active captured context retain Host semantics. Claw cannot replace an active environment by rewriting its configuration or infer that cancelling an Agent undid filesystem changes. Forking a Thread follows Host history semantics and is not a filesystem clone.

Deleting a business binding neither removes a Host Thread nor destroys its files. Host resource deletion reports affected product references through [retention](04-persistence-and-recovery.md#retention-and-backup). Claw blocks dependent business work rather than selecting another environment or empty directory under the old identity.

## Invariants

1. The Host is the sole owner of execution-environment selection, state, and lifecycle.
2. Product routing cannot create filesystem authority or override captured context.
3. Working files, saved conversation, memory, and provider liveness are independent facts.
4. Claw exposes only supported Host operations and never supplies a hidden execution fallback.
