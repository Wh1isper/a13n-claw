# syntax=docker/dockerfile:1
# Match the a13n-envd-client release in uv.lock; this is not the Host version.
ARG ENVD_IMAGE=ghcr.io/converge-ai-labs/a13n-sandbox:0.5.1
FROM ${ENVD_IMAGE}
USER root
ARG ANYCAP_VERSION=0.6.4
ARG LARK_VERSION=1.0.98
RUN apt-get update && apt-get install -y --no-install-recommends \
      cargo rustc rustfmt rust-clippy cmake libssl-dev gh git-lfs rclone docker-cli \
      xvfb openbox x11vnc novnc websockify x11-utils \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global "@anycap/cli@${ANYCAP_VERSION}" "@larksuite/cli@${LARK_VERSION}" \
    && npm cache clean --force \
    && install -d -o sandbox -g sandbox /downloads /workspace/downloads /browser-profile /home/sandbox/.cache
COPY deploy/docker/install-skills.py /tmp/claw-install-skills.py
RUN python3 /tmp/claw-install-skills.py \
    && anycap skill install --target /usr/local/share/a13n/skills \
    && rm /tmp/claw-install-skills.py
COPY deploy/docker/executor-start.py /usr/local/bin/claw-executor-start
COPY deploy/docker/browser-start.py /usr/local/bin/claw-browser-start
ENV HOME=/home/sandbox \
    XDG_CACHE_HOME=/home/sandbox/.cache \
    LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 \
    LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1
WORKDIR /workspace
ENTRYPOINT ["tini", "--", "python3", "/usr/local/bin/claw-executor-start"]
