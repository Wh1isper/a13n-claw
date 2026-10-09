FROM python:3.13-slim
ARG BUILD_VERSION=0.0.0
LABEL org.opencontainers.image.source="https://github.com/Wh1isper/a13n-claw"
LABEL org.opencontainers.image.description="a13n Claw placeholder (not an agent service)"
LABEL org.opencontainers.image.licenses="BSD-3-Clause"
COPY dist/*.whl /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl \
    && python -c "from importlib.metadata import version; assert version('a13n-claw') == '${BUILD_VERSION}'" \
    && rm -rf /tmp/wheels \
    && useradd --create-home --uid 10001 claw
USER 10001:10001
WORKDIR /home/claw
EXPOSE 8080
ENTRYPOINT ["a13n-claw"]
CMD ["--help"]
