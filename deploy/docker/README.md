# Claw image

The Claw image extends a Service image in the bounded minor line in `pyproject.toml`, installs the published Claw wheel, and inherits the Service entrypoint, health check, port 8000 and non-root account. Use it in place of the Service image in your Service deployment with the same configuration and backing PostgreSQL, Redis, and object storage. The image selects Claw during first-time Service setup. For an existing organization, enable Claw under **Organization settings → Apps**.

```bash
make build
make image-check
```

Project working context runs in the configured Service Environment. Save connector credentials through Claw in Console and keep them out of the image. See [Operations](../../docs/content/operations.md).

`make image-check` uses the locked Service patch and digest. Main-branch development images use the latest stable compatible patch.
