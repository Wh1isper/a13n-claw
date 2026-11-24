---
title: Schedules and heartbeat
description: Choose Main Inbox or a fresh Service Thread for scheduled work.
---

A schedule is recurring (cron and time zone) or one-time. **Main Inbox** creates a durable occurrence and admits its prompt as an Inbox item. Main reads it and handles it on its persistent Thread. Claw records retention, lateness, overlaps and the state of the associated processing Run. Edit the prompt or timing in Console, including the lateness allowance. Deleting a Main schedule stops future occurrences without removing accepted Inbox items or recorded occurrences.

A **fresh Thread** runs self-contained scheduled work in the Project Environment and shares the Project Memory Space. Main can create one with `claw_schedule` by choosing the `fresh` target; scheduled Threads report back through the Inbox. Claw owns the schedule and occurrence history. Open the schedule in Claw to inspect each occurrence and its exact Thread and Run.

Fresh Threads use the current Project Environment and optional Browser at each firing. You can change these bindings without deleting schedules; existing Runs retain their captured resources.

Select **Trigger now** to test either target. An accepted Main occurrence wakes Main after the transaction commits; a fresh occurrence starts a new Thread. Reuse an idempotency key only for the same effective schedule definition. This pre-release schema requires a clean installation.

The heartbeat runs only when Main is idle. Its interval is independent of schedules. When no follow-up is needed, Main can finish quietly and retain an inspectable successful Run.
