"""Compose-owned daemon bootstrap; never change ownership of existing user files."""

import os
import subprocess
from pathlib import Path

# A deployment injects the same token into the Host and this daemon. Keep it out of
# child command environments and YAML resources. Do not generate a different one.
token = os.environ.pop("CLAW_EXECUTOR_TOKEN", "").strip()
if not token or any(c.isspace() for c in token):
    raise SystemExit("CLAW_EXECUTOR_TOKEN must be a nonempty token without whitespace")
credential = Path("/var/lib/a13n-envd/claw-token")
credential.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
fd = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write(token)
os.environ.update(
    A13N_ENVD_TRANSPORT="http",
    A13N_ENVD_HTTP_BIND="0.0.0.0:8888",
    A13N_ENVD_HTTP_CREDENTIAL_FILE=str(credential),
    A13N_ENVD_HTTP_PLAINTEXT_SCOPE="provider_private_link",
    A13N_ENVD_DEVICE_ID=os.environ.get("CLAW_EXECUTOR_ID", "claw-executor"),
    A13N_ENVD_DEFAULT_WORKING_DIRECTORY=os.getcwd(),
)
subprocess.run(["a13n-sandbox-init"], check=True)
os.execvp("a13n-envd", ["a13n-envd"])
