---
title: Getting started
description: Enable Claw and connect a workspace in A13n Service.
---

## Enable Claw

Use the Claw image with your normal PostgreSQL, Redis, and object storage configuration. When Service creates its first organization, the image enables Claw during setup. For an existing organization, sign in as an organization administrator and enable **Claw** in **Organization settings → Apps**. For a Python installation, install the wheel alongside A13n Service 0.3.1 and enable Claw in the same settings. Service applies Claw's AppDatabase migrations.

## Set up a Project

In a workspace, open **Apps → Claw settings**. Choose a Service Agent with an available model, a workspace service account with execution access, and an existing Environment or a managed Environment template. An optional Browser can be shared by Main and its managed workers. Claw creates a native Memory Space for the Project.

The Project Environment needs working shell execution. Claw installs the official `gh` and `lark-cli` binaries on supported Linux Project Environments. Configure account access separately with each CLI. See [Configuration](./configuration.md).

## Connect a platform

Use [Claw connectors](./console.md#connectors) to add Lark or [GitHub](./github.md). For a fresh installation, leave any standalone Claw data root intact; the App starts with fresh Service state.
