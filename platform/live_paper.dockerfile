# Two-layer build: the base image is rebuilt rarely (nautilus core changes), this layer rebuilds in seconds:
# docker build --network=host -f .docker/nautilus_trader.dockerfile --target application -t nautilus-trader-base:1.229.0 .
FROM nautilus-trader-base:1.229.0

WORKDIR /app
RUN chown 1000:1000 /app
COPY platform/requirements.txt ./requirements.txt
# PIP_INSECURE_ARGS is empty by default (normal TLS-verified install). Set via
# --build-arg when behind a TLS-intercepting proxy (e.g. Zscaler) that breaks pip's
# cert verification against PyPI -- see `make build-insecure`.
ARG PIP_INSECURE_ARGS=
RUN pip install --no-cache-dir $PIP_INSECURE_ARGS -r requirements.txt
# The bots context (Story 25.3, was live_paper/): `python3 -m bots` imports the shared kernel
# (indicators, venue-id parser, performance metrics) and the generic observability context (the
# error ledger). Still deliberately does NOT copy capture (or its shims) -- bots never
# imports capture (AD-8 keeps the trading runtime structurally separate from the write path).
COPY platform/kernel ./kernel
COPY platform/observability ./observability
COPY platform/bots ./bots
# The cross-cutting guards in platform/tests, which read the read-only source mount
# (PLATFORM_SOURCE_DIR), not this image.
COPY platform/tests ./tests

CMD ["python3", "-m", "bots"]
