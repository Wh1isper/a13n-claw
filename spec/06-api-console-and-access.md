# API, Console, and Access

## Operator Surfaces

Claw's frontend is a contribution to A13n Console, with the host's login, workspace selection, and navigation. Operators can select an Instance and distinguish its business records from native resources shared by the workspace. IM remains the conversation surface.

| Page              | Operator workflow                                                                                                                                                                                                       |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Overview          | Inspect token consumption, model distribution, and reported costs by time and available groupings; see subscription allowances with freshness and availability. Missing cost or quota is not zero.                      |
| Runs              | Investigate Runs read-only with filters and exact native detail links; distinguish input acceptance, execution, Inbox settlement, and delivery when they differ.                                                        |
| Models & Accounts | Manage supported models and groups and their effective selection; manage provider login, account selection, quota and reset, and supported redemption. A confirmed account operation is not undone by a failed refresh. |
| Schedules         | Inspect and edit definitions, preview the next firing, pause or remove schedules, trigger once, and inspect occurrences, native decisions, and notification outcomes.                                                   |

Consumption totals cover the filtered range, not only the current page, and distinguish usage from subscription charges. Allowance has its own observation time; a failed refresh can leave the last value visible as stale. Codex and Grok managed accounts, Copilot selected-account allowance, and ChatGPT login retain their distinct supported workflows. Model edits preserve the operator’s draft on a conflict; uncertain redemption retains its original request identity.

Service is authoritative for native resources and provider facts. Claw may compose them with business data without creating a second model catalog, credential vault, or usage ledger.

## Headless and Access

Headless clients can configure and validate Instances, inspect connections, channels and targets, operate conversations and exact controls, inspect Inbox items, attempts and deliveries, manage schedules and occurrences, and perform diagnostics, backup and retention workflows. These operations remain available without browser presence. Operator interfaces may change with the new runtime; workflow parity does not require preserving the old server's URLs or wire format.

Service authenticates API and Console callers and applies workspace and resource permissions. Claw checks access to the selected Instance and business action. External sender grants do not grant Console membership or equivalent access to a Service workspace. A user who can read an Instance does not thereby acquire authority to mutate its bindings, run work, or publish on its behalf.
