# Claw image

The Claw image extends the exact Service image selected in `pyproject.toml`, installs the published Claw wheel, and inherits the Service entrypoint, health check, port 8000 and non-root account. Use it in place of the Service image in your Service deployment with the same configuration and backing PostgreSQL, Redis, and object storage. The image selects Claw during first-time Service setup. For an existing organization, enable Claw under **Organization settings → Apps**.

```bash
make build
make image-check
```

Project working context runs in the configured Service Environment. Mount connector secrets into the Service process and keep them out of the image. See [Operations](../../docs/content/operations.md).
