# Two-layer build: the base image is rebuilt rarely (nautilus core changes), this layer rebuilds in seconds:
# docker build --network=host -f .docker/nautilus_trader.dockerfile --target application -t nautilus-trader-base:1.229.0 .
FROM nautilus-trader-base:1.229.0

WORKDIR /app
RUN chown 1000:1000 /app
# rclone for `archive.backup_catalog` (the `archive` service's nightly backup and `make
# backup-catalog`, Story 25.1b): Debian's package (the base is python:3.13-slim, Debian trixie), not
# a Python dependency. Its own layer before the pip install, so a requirements change reuses it.
RUN apt-get update && \
    apt-get install -y --no-install-recommends rclone && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
COPY platform/requirements.txt ./requirements.txt
# PIP_INSECURE_ARGS is empty by default (normal TLS-verified install). Set via
# --build-arg when behind a TLS-intercepting proxy (e.g. Zscaler) that breaks pip's
# cert verification against PyPI -- see `make build-insecure`.
ARG PIP_INSECURE_ARGS=
RUN pip install --no-cache-dir $PIP_INSECURE_ARGS -r requirements.txt
COPY platform/observability ./observability
COPY platform/kernel ./kernel
COPY platform/candles ./candles
COPY platform/archive ./archive
COPY platform/capture ./capture
# The four Story 26.2 re-export shim packages (collector_core, dydx_collector, bybit_collector,
# hyperliquid_collector) ship until Story 26.3 deletes them: `make test` imports each shim
# (tests/test_namespace.py), and the dYdX plan's bind mount keeps /app/dydx_collector/config.toml.
COPY platform/collector_core ./collector_core
COPY platform/collection_control ./collection_control
COPY platform/dydx_collector ./dydx_collector
COPY platform/bybit_collector ./bybit_collector
COPY platform/hyperliquid_collector ./hyperliquid_collector
COPY platform/views ./views
COPY platform/alerting ./alerting
COPY platform/research ./research
COPY platform/ranking ./ranking
COPY platform/bot_tui ./bot_tui
COPY platform/data_api ./data_api
COPY platform/tests ./tests

CMD ["python3", "-m", "capture.venues.dydx"]
