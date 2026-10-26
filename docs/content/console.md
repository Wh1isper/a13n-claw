---
title: Operator Console
description: Inspect consumption and runs, manage model accounts, and operate schedules.
---

## Open the Console

Build source assets with `make console-build`, start the initialized Instance with `a13n-claw serve`, and open `http://127.0.0.1:8080`. Enter the token from the Instance's `operator.token`. Installed builds include the assets and need no Node.js. `serve --no-console` keeps the headless API available without the frontend.

The token and unsaved forms stay in tab memory. Disconnect clears protected cached results; reloading requires the token again. Light, dark, and system themes are available. The four pages share the same authenticated operator API as headless operation. IM remains the conversation surface; Runs is read-only.

## Overview

Select a UTC period, Project, and model to inspect the Host's recorded consumption. Custom ranges span 1–366 days; the end date is exclusive. Summary totals cover the whole filtered range even when the distribution table is paginated. Select a chart day or a compact number for exact values. Group by model, Project, or Thread and follow Thread links to investigate.

- Total tokens are input plus output. Cache and audio counters are included subsets, not additional totals.
- Estimated model cost retains partial or unknown pricing. It is not a subscription charge.
- Project attribution follows the Thread's current Project assignment.
- Provider allowance has its own observation time and includes use outside Claw. A failed refresh keeps the last successful observation visibly marked as stale.

Codex and Grok use managed account snapshots; Copilot reports its selected account's available buckets, including unlimited allowances. ChatGPT exposes login status rather than numeric allowance. Missing data is shown as unavailable, never as a fabricated zero.

## Runs

Search and filter saved Threads by Project, including archived Threads when needed. Open a Thread to inspect its summary, paginated saved history, usage, retained tools, configuration, and linked business sources. The view separates current activity from the latest saved execution's outcome. It also separates next-run selections from captured configuration.

Usage distinguishes root execution, descendants, and combined totals. Recent Run details are bounded. Latest MAIN account selection is provenance for that execution, not historical attribution of all its tokens to an account. Sources link exact Inbox, attempt, schedule occurrence, and delivery records rather than inferring them from titles.

The focused Thread observes authenticated events and refreshes saved views. A disconnected stream does not mean execution completed. Background tabs pause observation; returning reconnects. Conclusive observation errors leave manual refresh available. Transcript text is rendered as text, with tool/thinking details expandable and unavailable media identified explicitly.

## Models & Accounts

### Models and stored credentials

Add or edit models using the Host's connection recipes, provider model IDs, settings presets, and native validation. Custom provider IDs are supported. Authentication references either an environment variable, a write-only stored API key, or the appropriate subscription account/group. Model resources never contain the API key itself.

Editing preserves unedited native fields and the original source revision. A conflicting or invalid external file edit rejects the save and leaves the form draft visible. Close the form and refresh the inventory before deliberately applying a draft to newer configuration. Custom connections not supported by the form remain file-editable. A successful configuration save proves validation, not provider connectivity.

### Subscription accounts and groups

Register a Codex or Grok account and explicitly start sign-in. Device authorization is preferred where offered; browser callback submission remains available. Only one authorization is active per process. Closing its dialog leaves authorization running; resume it from the account page or explicitly cancel it. Restarting the server loses process-local login sessions.

Copilot and ChatGPT retain their native selected-source behavior. Inspect a source before selecting or signing it out, particularly when shared with a CLI. Groups route across managed accounts with ordered or weighted selection and an explicit paid-credit policy. Disabling an account preserves its registration; deleting retires it and removes credentials, subject to native reference validation.

Codex reset vouchers require an eligible unexpired voucher and explicit confirmation of the local account, upstream identity, and voucher. If the response is uncertain, retry the same retained request; the original redeem UUID is reused while the tab remains connected. A failed quota refresh does not undo confirmed redemption. After losing tab state, inspect the provider/account before attempting another redemption.

## Schedules

Create, edit, pause, enable, or delete schedules using the same accepted catalog as the CLI. Forms retain the original catalog revision and preserve drafts on conflict. Cron, interval, and one-time timing use the server's canonical rules. Preview shows explicit timezone offsets. The retained next-due clock appears only after discovery of the matching accepted definition; paused, pending discovery, and finished are distinct.

Run now requires confirmation. An ambiguous response retains its request key across dialog closure and navigation within the connected tab; retry uses that same key. Acceptance creates an occurrence, not a completed result. Paused schedules reject manual triggering.

Inspect occurrence history for execution outcome, retained result, Thread, and independent notification delivery. Deleting a definition keeps history and does not cancel already accepted work. See [Schedules](./schedules.md) for notification destinations, missed-run policies, and headless recovery.

## File-driven operations

Platform connections, environments, Agent/Project defaults, and advanced operational recovery remain in native files, environment settings, or the CLI. See [Configuration](./configuration.md) and [Operations](./operations.md). The Console neither changes Host ownership nor adds a parallel execution store.
