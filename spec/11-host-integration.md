# Embedded Host Integration Contract

## Design Position

Claw depends on the `a13n-harness-ui` Python package and composes one `HarnessUiApp` through supported construction and lifecycle operations. The integration is a concrete application dependency, not a generic Host abstraction or a remote transport. Claw does not require the stock WebUI listener, terminal renderer, or frontend bundle to serve its own API.

This document defines the library capabilities Claw requires. It does not declare that every required extension point already exists in a released package. A selected dependency version must demonstrate these contracts before the embedded implementation is supported. Missing general-purpose embedding hooks belong at the Host boundary; Inbox, scheduling, and delivery remain Claw concerns.

## Dependency and Compatibility

Claw uses bounded published package requirements and a tested lockfile. It does not depend on a sibling checkout, import private coordinators, write Host tables, subclass the App to replace execution internals, or monkey-patch methods. A small Claw integration module may adapt calls and detached views without owning an alternate runtime model.

There is one initial backend: the embedded App. HTTP connection to an independently running Harness UI instance, a local/remote backend interface, and a separately extracted universal Host package are outside this contract. A future transport requires its own concrete need and compatibility decision.

Library compatibility includes input provenance, lifecycle, operation outcomes, extension context, observations, and saved-state compatibility, not only Python signatures. Claw tests the selected dependency against the acceptance scenarios below. Importability or an internal method name alone is not a support promise.

## Required Surface

These are semantic requirements, not proposed Python signatures or HTTP endpoints.

| Capability                      | Required contract                                                                                                                                         |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| App construction and lifetime   | Open the selected configuration and data locations, initialize supported embedding features, enter and close one App without starting a stock UI listener |
| Configuration                   | Discover resources, inspect accepted state and diagnostics, and mutate source through the owning validation and revision rules                            |
| Threads and roles               | Create, inspect, select, fork, and archive through supported operations; expose Coordinator/worker ownership and partial creation outcomes                |
| Input                           | Submit human input and product-origin input with truthful provenance; preserve Host start/steer, conflict, and pending-decision semantics                 |
| Controls                        | Inspect and act on exact available operations and decisions; expose unavailable control rather than reacquiring old authority                             |
| Observation                     | Obtain detached saved/current views and bounded subscriptions with a refetch path after loss; preserve available correlation and unknown state            |
| Files, environments, and memory | Expose supported operations without granting access through private storage or provider objects                                                           |
| Product extension               | Bind native Claw Capabilities to real execution identity and trusted product services, with a supported lifetime                                          |
| Automatic admission policy      | Allow Claw's Main pause and unresolved-work gate to cover product-triggered and integrated collaboration starts without replacing the Host coordinator    |

Subscriptions are hints for refetch and reconciliation, not durable queues. If a saved view cannot prove the outcome of a specific product dispatch, the correct result is unknown. No required interface promises durable Host input acceptance, cross-store atomicity, exactly-once execution, or recovery of active OS processes.

## Product Capabilities

Claw owns the implementations of bounded Inbox reads, disposition changes, delivery requests, and product automation management. The Host owns their installation into a captured composition and supplies authoritative caller context. At minimum, product operations must distinguish the actual executing Thread, applicable role/owner, and operation correlation where available.

Model arguments specify the requested business target, never the caller's identity or authority. Product services validate current policy, Main-only publication, authorized content scope, and mutation preconditions. Trusted service references are not exposed as serialized credentials or native execution objects to the model or frontend.

Native Host collaboration, subagent, file, and memory tools retain their owners. Claw does not wrap them with competing copies. Missing resource catalog or lifecycle hooks are addressed in the Host before dependent Claw behavior is enabled. Business tool execution commits only its own records; Host checkpoint publication is a separate boundary.

## Input and Wake Semantics

The embedding distinguishes direct human input, attributed external messages, automation, and content-light product notices. Reusing a human submission method with a fabricated human label is not compliant. Claw does not need browser drafts, presence, or MCP App UI context for a product wake.

The Host remains responsible for the atomic execution-admission decision. Claw may gate its requests and serialize its product dispatch attempts; it cannot acquire a private Host lock or create a second queue of Host Runs. Active steering and idle admission expose their own actual outcomes, including races and rejection.

For Main, there is one effective automatic admission gate. Claw controls product pause and uncertainty. Any enabled native coordinator follow-up or worker-to-Main automatic start must pass an equivalent supported gate; otherwise that start path stays disabled. Explicit authorized human controls remain available through their own semantics and cannot falsify prior business evidence.

## Lifetime and Isolation

One embedded App serves all authorized Claw adapters for the Instance. HTTP requests and bridge reconnections do not construct additional Apps. Startup does not enable native computer sharing, Live voice, stock browser push, or other privileged surfaces merely because an embedding mode shares a name with WebUI mode.

Claw opens product services before enabling tools that depend on them and keeps those services alive until Host-owned execution has drained or stopped. Shutdown prevents new product submissions, closes external intake, settles delivery attempts, and closes the Host through its supported lifecycle. Hard process termination remains a supervisor concern and can leave uncertain effects.

An embedding mode must explicitly identify the supported feature set. For example, Host collaboration or memory maintenance can be mode-dependent without requiring a browser listener. Claw reports unsupported features rather than recreating their implementation.

## Integration Acceptance

Before an embedded implementation is considered available, tests demonstrate:

01. One App starts and closes under Claw without a stock HTTP listener, terminal, browser, or duplicate execution owner.
02. Host-native resource edits and Thread selections are reflected without a second catalog; current captured behavior remains distinct from future defaults.
03. Direct human submission and product wake have truthful source attribution and preserve decision/control restrictions.
04. Busy, preparing, waiting, completion-race, and shutdown paths preserve actual admission outcomes without an uncertain fallback start.
05. Injected product tools receive trusted caller identity; a worker, child, or supplied ID cannot acquire Main publication rights.
06. Product pause and dispatch-uncertainty gates cover all automatic Main-start paths enabled by the integration.
07. Subscriptions can be lost and recreated using supported views; missing receipt or insufficient correlation is reported as unknown, not replayed.
08. Crashes before and after a Host call preserve business intent and conservatively block unresolved attempts; a committed product settlement is not undone by failed Host publication.
09. Host-native worker creation/ownership, environment selection, and Global/Project memory are reused without private persistence access.
10. Installed package execution works outside both repositories, and missing required integration capability fails explicitly without resetting data or switching backends.

These tests establish the boundary, not compatibility with arbitrary future Host releases. The spec-only redesign changes neither the Host package nor Claw runtime code; concrete upstream work and the supported version are determined during implementation against this contract.
