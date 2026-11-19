# Automation

## Ownership

Service owns the clock and occurrence lifecycle. Claw associates a schedule with an Instance, its originating author, selected working context, source, and notification destination. Heartbeat is an interval schedule. Each occurrence runs in an independent conversation and has no implicit authority to publish as Main.

## Operator Behavior

Claw supports zoned cron, anchored intervals, and one-time schedules. Timing preview and the next due time are distinct; the latter belongs to the current accepted definition. Operators can preview timing, create or edit definitions, pause or remove a definition, trigger a manual occurrence, and inspect or control the exact occurrence. Definitions can also be managed headlessly. Updates govern future work and do not rewrite the accepted occurrence's request, author, or destination.

Missed-fire policy can skip late work or retain the latest eligible firing within its lateness window. The overlap policy either skips a firing or queues it behind unfinished work, including a pending decision or unresolved submission. Pausing or removing a schedule prevents pending dispatch and subsequent result delivery; stopping an accepted Run is separate. Replaying a manual trigger does not create a second occurrence of the same request.

## Decisions and Notifications

Where the source permits it, operators can manage a schedule and answer its exact native waits through IM. Agent-initiated schedule changes require a retained request by a currently authorized Lark operator and apply only to that author and qualified Channel. Operators can reconcile uncertain dispatch or attach matching continuation evidence without creating a new occurrence. Notification may be always, on error, or never. A waiting decision, Run outcome, and result notification each have their own disposition. A failed or uncertain notification does not rerun the occurrence. Where notifications are enabled, waiting work may request attention once per exact wait. A recovered occurrence keeps its original identity and applicable authority.
