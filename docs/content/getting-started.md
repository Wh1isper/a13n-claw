---
title: Getting started
description: Install and inspect the placeholder package.
---

## From source

Install Python 3.13 or later and [uv](https://docs.astral.sh/uv/), then:

```bash
git clone https://github.com/Wh1isper/a13n-claw.git
cd a13n-claw
uv sync --locked
uv run a13n-claw --version
uv run a13n-claw --help
```

Source installs report `0.0.0`. Running the command without arguments prints help and explicitly identifies the placeholder status. No provider credentials are needed and no network calls are made by the CLI.

## From a release

Install the BSD-3-Clause licensed placeholder (`0.0.2` or later):

```bash
uv tool install a13n-claw==0.0.2
a13n-claw --version
```

The module entry point is also available with `python -m a13n_claw --version` in an environment containing the package.

## Container

Run the matching release image:

```bash
docker run --rm ghcr.io/wh1isper/a13n-claw:0.0.2 --version
```

The non-root image runs the informational CLI and exits. It has no HTTP port, volumes, API token, or service health endpoint. Do not deploy it as a long-running service.
