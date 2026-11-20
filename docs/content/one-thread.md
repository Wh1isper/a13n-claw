---
title: Main and workers
description: Coordinate Inbox work with one persistent Main Thread and independent workers.
---

Main receives a brief preview and opens the items it chooses. Opening marks an item read; processing and any platform reply are separate. Main alone owns user-facing replies. It can delegate longer work to independent worker Threads that share its Project, or continue an existing worker explicitly with another assignment. Workers report progress through Inbox.

For inspection or exact execution controls, follow the native Thread and Run links in [Console](./console.md). Use Service's native decision controls for a Main Run waiting on a decision.
