---
title: Console
description: Inspect Claw activity and follow the associated Service execution.
---

## Overview

The Claw dashboard shows unread and retained items, current Main activity, its last Run, and managed workers. Follow a Thread or Run to inspect chat, execution history, and tool calls in Service. Models and usage stay in their native Console views.

## Deliveries

Filter arrivals by admission result. Open a record to inspect its message, source metadata, and Main Thread or Run. Opening a delivery record does not mark the Inbox item read; Main marks it read when it opens the item. Delivery metadata rolls over after seven days or 3,000 entries. Inbox message retention follows its own limits.

## Connectors

Add GitHub or Lark for the selected Claw. For Lark, enable the long connection in the platform and subscribe to `im.message.receive_v1`. Fill in the App and bot IDs, choose the region, and allow the intended senders and groups. Mount the Lark App secret at `$CLAW_SECRET_DIR/<connector-id>` in the Service process. Last received reports accepted activity, separately from configured and enabled state. For GitHub, see [GitHub intake](./github.md).

## Schedules

Main Inbox schedules show their occurrence history. Fresh-Thread schedules link to native Service definitions and occurrence history. See [Schedules and heartbeat](./schedules.md).
