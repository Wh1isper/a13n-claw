---
title: Console
description: Inspect Claw activity and follow the associated Service execution.
---

## Overview

The Claw dashboard shows unread and retained items, current Main activity, its last Run, and managed workers. Follow a Thread or Run to inspect chat, execution history, and tool calls in Service. Models and usage stay in their native Console views.

## Project setup

Open **Claw settings → Project** to see the names and lifecycle states of the bound Environment and optional Browser. If the Environment is missing, the dashboard links administrators to setup. An unconfigured Claw still accepts Inbox messages. Select an Environment to let Main start processing them.

Replacing a binding affects later Runs on the same Thread. Use the native resource pages to manage the instances or move their data separately.

## Deliveries

Filter arrivals by admission result. Open a record to inspect its message, source metadata, and Main Thread or Run. Opening a delivery record does not mark the Inbox item read; Main marks it read when it opens the item. Delivery metadata rolls over after seven days or 3,000 entries. Inbox message retention follows its own limits.

## Connectors

Add GitHub or Lark for the selected Claw. On Lark or Feishu, enable bot capability, enable the long connection, subscribe to `im.message.receive_v1`, grant bot send permissions, and publish the app version. Enter the App ID, App secret, bot open ID, region, and allowed senders and groups in Claw. Add the bot to each allowed group where it should respond.

Save the connector, reopen it, and select **Check credential** to verify authentication. For Lark, **Listener** reports the observed long-connection state; **Last received** records the last eligible event. Test the full path with an allowlisted message that Main should reply to. The credential check does not send anything. To rotate the credential, enter a replacement; a blank field preserves the saved value. Enable replies only for the trusted Project Environment. For GitHub, see [GitHub intake](./github.md).

## Schedules

Main Inbox schedules show their occurrence history. Fresh-Thread schedules link to native Service definitions and occurrence history. See [Schedules and heartbeat](./schedules.md).
