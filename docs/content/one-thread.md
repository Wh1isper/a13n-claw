---
title: Main and workers
description: Coordinate Inbox work with one persistent Main Thread and independent workers.
---

Main receives a brief preview and opens the items it chooses. Opening marks an item read; processing and any platform reply are separate. Main owns user-facing replies and can delegate longer work to native asynchronous child Threads that share the captured Project resources.

In **Claw settings**, select named Workspace Agents and dynamic Worker models. Service always permits the Main Agent's own model for dynamic Workers in addition to the configured models. Named Workers retain their configured subagents. Child messages and results enter the parent Service Thread Inbox, not the Claw Inbox.

For inspection or execution controls, follow the native Thread and Run links in [Console](./console.md). Main and Workers deny interactive decisions rather than waiting for approval or user input.
