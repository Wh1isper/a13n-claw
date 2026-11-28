# Inherit the selected Service runtime and health check.
ARG SERVICE_IMAGE=ghcr.io/converge-ai-labs/a13n-service:0.7.0
FROM ${SERVICE_IMAGE}
ARG BUILD_VERSION=0.0.0
ARG SERVICE_VERSION=0.7.0
ENV A13N_PLUGINS__BOOTSTRAP_ENABLED_APPS='["a13n.claw"]'
LABEL org.opencontainers.image.title="a13n Claw" \
      org.opencontainers.image.source="https://github.com/Wh1isper/a13n-claw" \
      org.opencontainers.image.licenses="BSD-3-Clause"
USER root
COPY dist/*.whl /tmp/claw-wheel/
COPY build/image-requirements.txt /tmp/image-requirements.txt
RUN /app/.venv/bin/python -c 'from importlib.metadata import version; import sys; assert version("a13n-service") == sys.argv[1]' "${SERVICE_VERSION}" \
    && /app/.venv/bin/python -c 'import re, sys; assert re.search(r"^a13n-service==" + re.escape(sys.argv[1]) + r"(?:\s|\\|$)", open("/tmp/image-requirements.txt").read(), re.M)' "${SERVICE_VERSION}" \
    && /usr/local/bin/python -m pip --python /app/.venv install --no-cache-dir --require-hashes -r /tmp/image-requirements.txt \
    && /usr/local/bin/python -m pip --python /app/.venv install --no-cache-dir --no-deps /tmp/claw-wheel/*.whl \
    && /usr/local/bin/python -m pip --python /app/.venv check \
    && /app/.venv/bin/python -c 'from importlib.metadata import version; import sys; assert version("a13n-claw") == sys.argv[1]; assert version("a13n-service") == sys.argv[2]' "${BUILD_VERSION}" "${SERVICE_VERSION}" \
    && rm -r /tmp/claw-wheel /tmp/image-requirements.txt
USER app
