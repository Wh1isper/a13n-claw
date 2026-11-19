# Domain Model

## Instance and Channel

A **Claw Instance** is a named messaging assistant within a Service workspace. The workspace is its Service authorization boundary. The Instance is the product boundary for routing, policy, Main, outstanding work, and delivery. A deployment can host multiple Instances; selecting the same agent resource does not join their histories, Inboxes, or external authority.

A **Connection** identifies a platform account or installation. A **Channel** identifies an external conversation within a connection: a Lark chat or a GitHub issue or pull request, for example. A binding resolves eligible work to one Instance and its Channel context. Agent display names and unqualified external IDs are not routing identities.

## Business and Native Records

| Concept          | Meaning                                                                                      |
| ---------------- | -------------------------------------------------------------------------------------------- |
| External event   | Qualified platform identity, original author, and accepted or ignored input                  |
| Inbox item       | Retained obligation and its business disposition; distinct from Service's execution input    |
| Dispatch         | A product request and its native acceptance evidence, which may still be unresolved          |
| Delivery         | One explicit external publication request with its original destination and observed outcome |
| Schedule binding | Claw author, source, and notification policy associated with a native schedule               |

A Service **Thread** identifies a conversation, an **Entry** identifies an admitted input, and a **Run** identifies execution. A queued Entry need not yet have a Run; one Run can receive multiple inputs. Claw records native references in its own business data. It selects replies and controls from the exact business association rather than from a Thread's latest Run.

Routing identity does not confer permission. Service decides whether the principal may act on a native resource; Claw independently checks the sender, Instance binding, and particular action or destination. [Bridges](07-bridges-and-clients.md) own qualification at the external boundary.
