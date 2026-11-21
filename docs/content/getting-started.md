---
title: Getting started
description: Enable Claw and connect a workspace in A13n Service.
---

## Enable Claw

Use the Claw image with your normal PostgreSQL, Redis, and object storage configuration. When Service creates its first organization, the image enables Claw during setup. For an existing organization, sign in as an organization administrator and enable **Claw** in **Organization settings → Apps**. For a Python installation, install the wheel alongside A13n Service 0.3.3 and enable Claw in the same settings. Service applies Claw's AppDatabase migrations.

## Set up a Project

In a workspace, open **Apps → Claw settings**. Choose a Service Agent with an available model and a workspace service account with execution access. Select an Environment now or finish setup later under **Settings → Project**. Choose an existing instance or reserve one from a template. Main and its managed workers can also share an optional Browser created or attached in the native Browsers page. Claw creates a native Memory Space when you save the Claw.

The Project Environment needs working shell execution. Claw installs the official `gh` and `lark-cli` binaries on supported Linux Project Environments. Configure platform credentials on the connector and authorize replies from the trusted Project Environment. See [Configuration](./configuration.md#reply-accounts).

## Connect a platform

Use [Claw connectors](./console.md#connectors) to add Lark or [GitHub](./github.md). For a fresh installation, leave any standalone Claw data root intact; the App starts with fresh Service state.
