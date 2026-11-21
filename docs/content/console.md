---
title: Console
description: Inspect Claw activity and follow the associated Service execution.
---

## Overview

Overview shows actionable Main and Project notices, Inbox counts, compact usage by model, recent deliveries, and managed workers. Hover over a compact total for its exact value. Open native **Usage** for broader inspection, or follow the exact Thread or Run to inspect execution and answer pending decisions. To submit a message, use a connector or the headless input API.

## Project setup

Workspace administrators open **Workspace Settings → Claw settings**. Create a Claw, configure its Inbox and heartbeat, or bind a Project Environment and optional Browser. A stopped instance should be started in native Console; replace a missing or permanently failed instance in Project settings. An unconfigured Claw accepts messages but cannot start Main.

Archive blocking Fresh Schedules before changing a binding. Changing the Environment revokes reply authorization; reauthorize the connectors for the new Environment. Files and browser profiles stay with the original resources. **Sync revision** picks up the Agent's current default revision for subsequent Claw admissions. Existing Run and native Schedule captures are unchanged. **Archive Claw** stops intake and automation without deleting its native Threads, Runs, schedules, or Project resources.

## Deliveries

Filter arrivals by admission result or Inbox state: unread, read, or expired. Open a record to inspect its message, source metadata, and Main Thread or Run. Opening a delivery record does not mark the Inbox item read; Main marks it read when it opens the item. Delivery metadata rolls over after seven days or 3,000 entries. Inbox message retention follows its own limits.

## Connectors

Add GitHub or Lark for the selected Claw. On Lark or Feishu, enable bot capability, enable the long connection, subscribe to `im.message.receive_v1`, grant bot send permissions, and publish the app version. Enter the App ID, App secret, bot open ID, region, and allowed senders and groups in Claw. Add the bot to each allowed group where it should respond.

Save the connector, reopen it, and select **Check saved connection** to verify authentication. For Lark, **Listener** reports the observed long-connection state or a conflict with another connector using the same App ID; **Last received** records the last eligible event. Test the full path with an allowlisted message that Main should reply to. The credential check does not send anything. To rotate the credential, enter a replacement; a blank field preserves the saved value. Use **Remove credential** to clear it or **Delete connector** to stop its intake and replies. Enable replies only for the trusted Project Environment. For GitHub, see [GitHub intake](./github.md).

## Schedules

Main schedules can be edited, paused, resumed, or deleted. Their occurrence history links to retained deliveries and exact Main Runs. Fresh-Thread schedules link to native Service definitions and occurrence history. See [Schedules and heartbeat](./schedules.md).
