# Match the exact Service dependency in pyproject.toml. Inherit its runtime and health check.
FROM ghcr.io/converge-ai-labs/a13n-service:0.5.1@sha256:d9a3eab639aef3103f1a8cce9731b00b0405eeb1ccb874085bafcea730888eb2
ARG BUILD_VERSION=0.0.0
ENV A13N_PLUGINS__BOOTSTRAP_ENABLED_APPS='["a13n.claw"]'
LABEL org.opencontainers.image.title="a13n Claw" \
      org.opencontainers.image.source="https://github.com/Wh1isper/a13n-claw" \
      org.opencontainers.image.licenses="BSD-3-Clause"
USER root
COPY dist/*.whl /tmp/claw-wheel/
COPY build/image-requirements.txt /tmp/image-requirements.txt
RUN /usr/local/bin/python -m pip --python /app/.venv install --no-cache-dir --require-hashes -r /tmp/image-requirements.txt \
    && /usr/local/bin/python -m pip --python /app/.venv install --no-cache-dir --no-deps /tmp/claw-wheel/*.whl \
    && /usr/local/bin/python -m pip --python /app/.venv check \
    && /app/.venv/bin/python -c 'from importlib.metadata import version; import sys; assert version("a13n-claw") == sys.argv[1]; assert version("a13n-service") == "0.5.1"' "${BUILD_VERSION}" \
    && rm -r /tmp/claw-wheel /tmp/image-requirements.txt
USER app
