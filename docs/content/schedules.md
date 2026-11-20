---
title: Schedules and heartbeat
description: Choose Main Inbox or a fresh Service Thread for scheduled work.
---

A schedule is recurring (cron and time zone) or one-time. **Main Inbox** creates a durable occurrence and admits its prompt as an Inbox item. Main reads it and handles it on its persistent Thread. Claw records retention, lateness, overlaps and the state of the associated processing Run.

A **fresh Thread** runs self-contained scheduled work in the Project Environment. Service owns that schedule and its occurrence history. Follow the schedule link from Claw to manage it in native Console.

The heartbeat runs only when Main is idle. Its interval is independent of schedules. When no follow-up is needed, Main can finish quietly and retain an inspectable successful Run.
