# Security Policy

Only the latest published release is supported. Source builds provide an IM-first service embedding Harness UI; published artifacts provide the capabilities of their tagged source. See [source and release availability](spec/repository-model.md#source-implementation-and-release-availability).

## Runtime trust boundary

Claw is intended for trusted operators and explicitly allowed messaging participants, not hostile multi-tenancy. The operator API token grants administrative access. Keep the listener on loopback or behind a trusted TLS reverse proxy, protect the Instance directory and credentials, and configure Channel sender and operator grants before enabling intake.

Native Local execution uses the server OS account. A Docker socket grants control of its Docker host. Shared Main history, Global/Project memory, working directories, and browser profiles can expose information across conversations; separate Thread IDs do not isolate those resources. Review the [operations guide](docs/content/operations.md#access-and-secrets) and [Docker deployment boundary](deploy/docker/README.md) before granting access.

## Reporting a vulnerability

Report suspected vulnerabilities privately through this repository's GitHub **Security → Report a vulnerability** form. Do not include credentials or sensitive details in public issues. If private reporting is unavailable, contact @Wh1isper privately before disclosing technical details.
