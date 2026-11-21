# Match the exact Service dependency in pyproject.toml. Inherit its runtime and health check.
FROM ghcr.io/converge-ai-labs/a13n-service:0.3.3@sha256:550748d035573e590a9c68e9582912cf213b9fe93ee3fca5f02ab3f84ce8c8d5
ARG BUILD_VERSION=0.0.0
ENV A13N_PLUGINS__BOOTSTRAP_ENABLED_APPS='["a13n.claw"]'
LABEL org.opencontainers.image.title="a13n Claw" \
      org.opencontainers.image.source="https://github.com/Wh1isper/a13n-claw" \
      org.opencontainers.image.licenses="BSD-3-Clause"
USER root
COPY dist/*.whl /tmp/claw-wheel/
RUN /usr/local/bin/python -m pip --python /app/.venv install --no-cache-dir /tmp/claw-wheel/*.whl \
    && /usr/local/bin/python -m pip --python /app/.venv check \
    && /app/.venv/bin/python -c 'from importlib.metadata import version; import sys; assert version("a13n-claw") == sys.argv[1]; assert version("a13n-service") == "0.3.3"' "${BUILD_VERSION}" \
    && rm -r /tmp/claw-wheel
USER app
