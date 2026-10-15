# Embedded Host Integration Contract

## Design Position

Claw depends on the `a13n-harness-ui` Python package and composes one `HarnessUiApp` through supported construction and lifecycle operations. The integration is a concrete application dependency, not a generic Host abstraction or a remote transport. Claw does not require the stock WebUI listener, terminal renderer, or frontend bundle to serve its own API.

This document defines the library behavior required by the IM product. Implementation starts with existing public App operations and native Capabilities, validated against a published package. An upstream change is justified only by a concrete required interaction that cannot be implemented correctly through that surface. Inbox, platform presentation, source attribution, scheduling, and delivery remain Claw concerns.

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
| Input                           | Submit attributed content and steer an exact active operation; preserve Host conflict and pending-decision semantics                                      |
| Controls                        | Inspect and act on exact available operations and decisions; expose unavailable control rather than reacquiring old authority                             |
| Observation                     | Obtain detached saved/current views and bounded subscriptions with a refetch path after loss; preserve available correlation and unknown state            |
| Files, environments, and memory | Expose supported operations without granting access through private storage or provider objects                                                           |
| Product extension               | Bind native Claw Capabilities to real execution identity and trusted product services, with a supported lifetime                                          |

Subscriptions are hints for refetch and reconciliation, not durable queues. If a saved view cannot prove the outcome of a specific product dispatch, the correct result is unknown. No required interface promises durable Host input acceptance, cross-store atomicity, exactly-once execution, or recovery of active OS processes.

## Product Capabilities

Claw owns the implementations of bounded Inbox reads, disposition changes, delivery requests, and product automation management. The Host owns their installation into a captured composition and supplies authoritative caller context. At minimum, product operations must distinguish the actual executing Thread, applicable role/owner, and operation correlation where available.

Model arguments specify the requested business target, never the caller's identity or authority. Product services validate current policy, Main-only publication, authorized content scope, and mutation preconditions. Trusted service references are not exposed as serialized credentials or native execution objects to the model or frontend.

Native Host collaboration, subagent, file, and memory tools retain their owners. Claw does not wrap them with competing copies. Mode-specific memory uses existing Global and Project scopes: Channel Projects for per-Channel/per-trigger routing, and one shared Main Project for One Thread. This mapping requires no Host memory extension or cross-Project memory access. Trusted service binding can use the public Capability registration and execution context; it need not introduce a new generic service-injection framework. Business tool execution commits only its own records; Host checkpoint publication is a separate boundary.

## Input and Wake Semantics

Claw records the actual source and renders it clearly in submitted content. Human IM messages use ordinary human input semantics. A product notice or automation request identifies its business cause; it does not fabricate a user instruction. Source attribution can live in Claw records and supported input content without becoming a new Host resource or authority field. Claw needs no browser draft, presence, or fabricated WebUI surface for a wake.

Product notices and automation use public root submission where its observable behavior preserves the actual source and Host controls. Automation targets Main or an independent root; managed-worker creation uses native collaboration. Automated first input must not produce an owner notification falsely attributing that task to a human. This compatibility check applies to that entry path, not to unrelated IM input or product wakes.

The Host remains responsible for the atomic execution-admission decision. Claw may gate its requests and serialize its product dispatch attempts; it cannot acquire a private Host lock or create a second queue of Host Runs. Active steering and idle admission expose their own actual outcomes, including races and rejection.

Native coordinator follow-up and worker messages retain their Host behavior. Claw serializes its Inbox dispatch attempts and checks business eligibility at product operations; it adds neither a global pause switch nor a universal admission gate. Native starts may proceed while particular business items await reconciliation. Product tools expose those items as blocked, and no start path grants permission to repeat their uncertain effects. Host decisions and execution controls remain authoritative.

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
06. Native worker reports and auto-follow-up coexist with Inbox drain. Competing submissions receive actual Host outcomes, and unresolved business work remains excluded from actionable Inbox even in a natively started Main operation.
07. Subscriptions can be lost and recreated using supported views; missing receipt or insufficient correlation is reported as unknown, not replayed.
08. Crashes before and after a Host call preserve business intent and conservatively block unresolved attempts; a committed product settlement is not undone by failed Host publication.
09. Host-native worker creation/ownership, environment selection, and Global/Project memory are reused without private persistence access.
10. Installed package execution works outside both repositories, and missing required integration capability fails explicitly without resetting data or switching backends.
11. Two distinct eligible triggers in one Channel create distinct fresh task Threads in the same Project; a duplicate event and an exact decision callback create no additional task. Unknown creation/admission blocks recreation.
12. Per-Channel routing reuses its conversation Thread; per-trigger routing preserves the Channel's Project memory across fresh Threads. Different Channels select distinct Project scopes and the same Global scope.
13. One Thread routes Channels to Main with shared Global/Main Project memory and native same-Project workers, without Channel memory partitions or cross-Project memory APIs. Mode changes preserve retained memory without implicit transfer.
14. Mentions-only filtering works independently in all three routing modes; ignored events create neither task Threads nor execution context.
15. Targeted IM continuation steers the selected active operation or submits to the selected idle Thread. Stale stop/steer actions never control a replacement operation, and an ambiguous target requires selection.
16. Approvals and structured questions retain exact Host correlation, response kinds, and batch semantics. Duplicate, stale, wrong-actor, and cross-Channel callbacks cannot answer a different request.
17. Progress, failures, results, and files reach only authorized destinations. Shared Main history and worker output are not streamed to every participating Channel; interrupted or unknown delivery is reconciled without inference replay.
18. Messaging, controls, setup, and recovery work without a runtime browser UI. Unsupported adapter interactions have a concrete CLI/API alternative or an explicit unavailable result, not a link to a placeholder Console.

Runtime acceptance establishes compatibility for the selected package and implemented adapter features. Optional Host and platform features are reported independently so their absence does not block unrelated IM interactions.
