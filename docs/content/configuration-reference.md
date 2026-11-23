---
title: Configuration field reference
description: Native resource references and Claw's Inbox settings.
---

| Setting                   | Purpose                                                                |
| ------------------------- | ---------------------------------------------------------------------- |
| Agent                     | Service Agent and pinned revision used to run Main and delegate work   |
| Environment               | A persistent Service instance; select one or create it from a template |
| Browser                   | Optional native Browser for Main; native Workers do not inherit it     |
| Workspace service account | Dedicated `builder` principal shared by Claw Control and execution     |
| Heartbeat interval        | Wait before waking idle Main; zero disables it                         |
| Unread limit              | Maximum outstanding Inbox capacity                                     |
| Read history limit        | Maximum number of read items retained, also bounded by age             |

Claw pins the Agent revision when it is created. After changing the Agent's default revision, explicitly sync the revision in Claw settings or with the [sync API](./configuration.md#headless-operation) to apply it to later Runs.

Claw records the Service Memory Space created for each Project. Platform fields are scoped by connector: GitHub has an integration principal, repositories and senders; Lark has an App ID, bot ID, region, allowed senders and optional group restrictions. See [Console](./console.md#connectors) and [GitHub](./github.md).
