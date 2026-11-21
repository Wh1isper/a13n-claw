# a13n Claw

Claw is an App for A13n Service. It gives one persistent Main Thread an Inbox for GitHub, Lark, scheduled work, and worker reports. Main and its managed workers share a Project Environment and Memory Space; their execution history stays in native Service Threads and Runs.

## Get started

Install `a13n-claw` alongside the exact supported `a13n-service` version, or run the Claw image built from that Service image. Enable **Claw** in the organization's App settings, then configure a Claw in a workspace. See [Getting started](docs/content/getting-started.md) and [Container deployment](deploy/docker/README.md).

## Develop

```bash
make install
make check
make test
make build artifact-check
```

See the [specification](spec/README.md) for product contracts and [Contributing](CONTRIBUTING.md) for validation and release procedures. This App uses fresh Service state; keep existing standalone data in its original root. BSD 3-Clause, see [LICENSE](LICENSE).
