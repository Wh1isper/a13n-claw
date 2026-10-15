# Product and Architecture Overview

## Product Position

Claw turns an embedded Harness UI Host into an operator-controlled assistant for ongoing work. Its frontend, messaging connections, Inbox, and automation are product surfaces over one Host, not another Agent runtime. The frontend is optional for background progress and independent of the stock Harness UI WebUI.

Claw deploys as one process with one embedded `HarnessUiApp`. Direct Python calls connect product services to the App. The supported dependency is the `a13n-harness-ui` package; Harness remains its execution foundation. There is no internal HTTP hop or separately managed Harness UI service.

## Architecture

```mermaid
flowchart TB
    Frontend[Claw frontend and API clients] --> API
    Platforms[Messaging platforms] <--> Bridges
    subgraph Process[One Claw process]
        API[Claw HTTP and realtime API] --> Product
        API --> Host
        Bridges[Embedded adapters] --> Product
        Product[Inbox, routing, automation, delivery] --> Host
        Host[Embedded HarnessUiApp] --> Harness
        Harness[Harness execution] --> Tools[Claw product Capabilities]
        Tools --> Product
        Product --> Bridges
    end
    Host --> HostState[Host-owned configuration and saved state]
    Product --> BusinessState[Claw-owned business state]
    Host --> Environment[Host-managed environments and files]
```

The diagram describes responsibility, not a required class hierarchy. Product tools invoke bounded Claw operations with Host-bound caller context. They do not start a nested Host, access private execution objects, or synchronously wait for another Agent to complete.

## Ownership

| Concern                                                              | Owner                        | Claw's role                                                   |
| -------------------------------------------------------------------- | ---------------------------- | ------------------------------------------------------------- |
| Model, Agent, MCP, Skill, extension, and Project resources           | Harness UI                   | Present and edit through supported Host operations            |
| Thread identity, composition, execution, decisions, and continuation | Harness UI                   | Select destinations, request operations, and present results  |
| Coordinator roles, worker ownership, and subagents                   | Harness UI                   | Select Main and integrate product attention                   |
| Environment selection, preparation, state, and lifecycle             | Harness UI and its providers | Offer product defaults and authorized controls                |
| File-memory scopes, projection, and organization                     | Harness UI and Harness       | Expose the supported settings and views                       |
| Channel identity, sender filtering, and routing                      | Claw                         | Normalize external context and apply product policy           |
| Inbox dispositions and automation occurrences                        | Claw                         | Retain business intent and decide when to request execution   |
| External message delivery                                            | Claw and platform adapters   | Retain explicit destinations and reconcile transport outcomes |
| Product API, frontend, and external access policy                    | Claw                         | Authenticate clients and expose only intended operations      |

Claw stores references to Host Threads and observable operations. It does not mirror checkpoints, capture a second execution composition, implement a second worker graph, or manage a parallel environment pool. [Host integration](11-host-integration.md) owns the required embedding contract.

## Product Modes

[One Thread mode](10-one-thread-mode.md) is the default for new Instances: eligible external messages enter a durable Inbox for one canonical Main Host Thread. Main uses Host-native workers and explicitly publishes external messages through Claw. Per-Channel mode reuses one conversation Thread per Channel; per-trigger mode creates a fresh task Thread for each eligible event. Both are explicit alternatives. Mentions-only is an independent admission filter for all three modes.

Host Projects and configuration selections are retained as Host concepts. In per-Channel and per-trigger modes, each Channel maps to a stable Host Project so its Threads share Channel memory plus Global memory. One Thread mode uses Main's shared Project and Global memory across all participating Channels, without Channel memory partitions or a Host memory extension. [Memory](09-memory.md) owns the mapping and transition rules; Claw adds no separate memory store.

## End-to-End Work Path

1. An adapter authenticates an external event; Claw checks sender, group filter, and qualified event identity.
2. Claw retains the eligible message and routing context before acknowledging business acceptance.
3. The product scheduler checks pending work, current policy, and Host availability. It records a dispatch attempt before calling the Host.
4. The Host owns admission, start-or-steer behavior, composition, decisions, execution, and checkpoint publication. Claw observes through supported queries and subscriptions.
5. In One Thread mode, Main reads and explicitly settles Inbox work through Claw Capabilities. Host completion alone does not settle an item.
6. An authorized send creates a separate delivery record. Adapter failure is reconciled from that record, without rerunning the Agent.
7. After restart, Claw rediscovers business backlog and checks Host state. Ambiguous prior dispatch or effects remain blocked rather than being replayed.

Direct human conversation uses the Host interaction model without a durable Claw prompt queue. Automation retains occurrences as business intent and uses the same product-to-Host boundary. The different acceptance promises are explicit to clients.

## Lifetime

Claw owns process startup and shutdown. It opens its business store and the embedded Host through each owner's lifecycle, validates the selected product bindings, and enables dispatch only after the required capabilities are ready. A failed Host startup prevents new execution; it does not erase retained Inbox or deliveries.

Shutdown stops new ingress and automated dispatch, settles product mutations and outbound attempts, then drains or closes Host execution through its supported lifecycle before closing stores. Product services used by active tools remain available during that drain. Browser disconnection is unrelated to shutdown. Only one Claw process owns an Instance; another interactive Host process must not concurrently manage the same embedded store.

## Non-Goals

Claw does not implement distributed scheduling, tenant isolation, a general Host SDK framework, interchangeable local/HTTP backends, a separate execution engine, or recovery of arbitrary operating-system processes. It does not require changing Harness UI into a durable workflow service. The [source boundary](repository-model.md) distinguishes this target from current implementation and publication.
