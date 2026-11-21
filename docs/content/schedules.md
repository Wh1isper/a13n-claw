---
title: Schedules and heartbeat
description: Choose Main Inbox or a fresh Service Thread for scheduled work.
---

A schedule is recurring (cron and time zone) or one-time. **Main Inbox** creates a durable occurrence and admits its prompt as an Inbox item. Main reads it and handles it on its persistent Thread. Claw records retention, lateness, overlaps and the state of the associated processing Run.

A **fresh Thread** runs self-contained scheduled work in the Project Environment and shares the Project Memory Space. Main can create one with `claw_schedule` by choosing the `fresh` target; scheduled Threads report back through the Inbox. Service owns the schedule and its occurrence history. Follow the schedule link from Claw to inspect it in native Console; changes require the schedule's owning service account.

Fresh Threads use the Project Environment and optional Browser selected when the schedule is created. Archive enabled or paused fresh-Thread schedules before changing either binding, then create new schedules with the new selection. Existing Run and occurrence history remains intact.

The heartbeat runs only when Main is idle. Its interval is independent of schedules. When no follow-up is needed, Main can finish quietly and retain an inspectable successful Run.
