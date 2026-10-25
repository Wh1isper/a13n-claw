# Persistent execution deployment

Compose owns three long-lived containers: the Claw service, an authenticated Envd executor, and a visible Chromium browser. Harness UI owns execution Sessions and native tools. Restarting Claw does not destroy the executor workspace or browser profile.

## Start from source

Build the ordinary Claw wheel/image and the executor image:

```bash
make image-check
docker build -f deploy/docker/Executor.Dockerfile -t a13n-claw-executor:local .
export CLAW_EXECUTOR_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
docker compose -f deploy/docker/compose.yaml run --rm --no-deps claw init \
  --workspace /home/claw/workspace --executor http://executor:8888 \
  --model openai:your-model
docker compose -f deploy/docker/compose.yaml up -d --wait
```

Keep the executor token in a protected deployment environment file for subsequent invocations. Model credentials belong in `deploy/docker/claw.env`, for example the `CLAW_MODEL_API_KEY` environment variable referenced by initialization. Set `CLAW_IMAGE` and `CLAW_EXECUTOR_IMAGE` to select reviewed custom images. Initialization refuses an existing Instance; edit existing native resources through the configuration API instead of repeating it. Starting a container proves service availability, not model connectivity.

Initialization creates a native HTTP Device, a remote-only Main Project, and a custom native local-file profile. That profile retains `copy` alongside shell tools without an Agent tool allowlist, so Claw tools, collaboration, and additional configured Capabilities remain available. Local Thread files still carry host-account authority; this profile is not a sandbox. Configure other Channel Projects with equivalent Device defaults when using per-Channel or per-trigger mode.

`device.yaml` and `project.yaml` illustrate the generated resources. The generated `extensions/claw-files.yaml` is required before selecting the Project's `environment-claw-files` profile. The executor's private HTTP endpoint uses the `CLAW_EXECUTOR_TOKEN` environment reference. Do not publish port 8888 to an untrusted network.

## Files and tools

The default executor working directory is `/workspace`. Native file tools address it as `/environment/executor/workspace`; shell commands use the guest path `/workspace`. Use native `copy` between Thread attachments/results and the executor. A shell in the executor cannot access Claw's local Thread files.

Named volumes retain the workspace, `/home/sandbox` including caches and CLI authentication, browser profile, downloads, and Claw data. Execution and the browser run as UID/GID 1000; the Claw service runs as UID/GID 10001. The executor supervisor starts as root to initialize Envd and drops execution identity through Envd. It removes its token from child environment variables.

The executor image includes Python/uv, Node/pnpm, Rust/Clippy, build tools, Git/Git LFS, GitHub CLI, rclone, Docker CLI, AnyCap CLI, Lark CLI and bundled skill entrypoints. Authentication is an explicit operator task; credentials are not included in the image. Customize the Dockerfile or select another compatible image for additional tools.

## Browser and human takeover

Open `http://127.0.0.1:6080/vnc.html` on the Docker host, or forward that loopback port over SSH. The executor reaches the same browser through CDP at `127.0.0.1:9222`. Use a named `agent-browser --session <name> --cdp 9222` session. Browser downloads appear at `/workspace/downloads` in the executor and `/downloads` in the browser.

The browser owns its dedicated profile exclusively. Its supervisor holds a kernel lock and recovers its Chromium and X display locks after a crash. Do not attach another independently launched Chromium process to that profile volume. Human takeover and automation share browser state; coordinate their actions. noVNC has no public authentication layer here and is bound to loopback; add authenticated transport before remote exposure.

## Optional Docker host access

`compose.host-docker.yaml` explicitly mounts the host Docker socket. This grants host-level Docker authority, including the ability to mount host files. Enable it only for trusted workloads.

Pre-create a writable directory for UID 1000, set `CLAW_HOST_WORKSPACE` to its absolute path and `CLAW_DOCKER_GID` to the socket's numeric group. Use both Compose files for every command, and initialize with `--executor-workspace "$CLAW_HOST_WORKSPACE"`. Equal host/container paths make nested Docker bind mounts resolve correctly. The override changes the execution primary GID to the socket group while retaining UID 1000. Existing Projects need the matching native `working_directory`; editing Compose alone does not rewrite Thread selections.

## Lifecycle and validation

`docker compose ... down` preserves volumes. `down --volumes` deletes them and is appropriate only for disposable tests or explicitly authorized removal. Back up Claw state and executor/browser volumes separately at a stopped boundary; neither restores external effects.

`make executor-check` builds both images and tests real native Host file transfer and shell execution, credential rejection, service startup, container replacement, browser state persistence and crash recovery. Tests use random project names and remove only their own containers and volumes. They need Docker but no platform accounts or paid models.
