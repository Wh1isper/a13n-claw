---
title: Getting started
description: Enable Claw and connect a workspace in A13n Service.
---

## Enable Claw

Use the Claw image with your normal PostgreSQL, Redis, and object storage configuration. When Service creates its first organization, the image enables Claw during setup. For an existing organization, sign in as an organization administrator and enable **Claw** in **Organization settings → Apps**. For a Python installation, install the wheel alongside A13n Service 0.7 (`>=0.7.0,<0.8.0`) and enable Claw in the same settings. Service applies Claw's AppDatabase migrations.

## Set up a Project

In a workspace, open **Workspace Settings → Claw settings**. Choose a Service Agent with an available model and a dedicated workspace service account with the `builder` role. Select an Environment now or finish setup later under **Claw settings → Project**. Choose an existing instance or reserve one from a template. For the optional Browser, select an existing instance or reserve one from a Browser provider; native Worker Threads do not inherit that Browser. Claw creates a native Memory Space when you save the Claw.

The Project Environment needs working shell execution. Claw installs the official `gh` and `lark-cli` binaries on supported Linux Project Environments. Configure platform credentials on the connector for intake and opt in to Project egress for CLI-backed replies. See [Configuration](./configuration.md#connector-credentials).

## Connect a platform

Use [Claw connectors](./console.md#connectors) to add Lark or [GitHub](./github.md). For a fresh installation, leave any standalone Claw data root intact; the App starts with fresh Service state.
