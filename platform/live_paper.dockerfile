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
# Story 3.2 imports the pure indicator classes (AD-4); since Story 23.2 they, the venue-id
# parser and the performance metrics come from the shared kernel (`kernel.indicators`,
# `kernel.venues`, `kernel.performance_metrics`), so live_paper no longer imports ml_signals and
# the image no longer ships it. Still deliberately does NOT copy dydx_collector -- live_paper
# never imports it (AD-8 keeps the module structurally separate from the collector's write path).
COPY platform/kernel ./kernel
COPY platform/live_paper ./live_paper
# Story 23.1: the generic observability context (every context may import it; nothing here does
# yet, but `make test-live-paper` runs its tests) and the cross-cutting guards in platform/tests,
# which read the read-only source mount (PLATFORM_SOURCE_DIR), not this image.
COPY platform/observability ./observability
COPY platform/tests ./tests

CMD ["python3", "-m", "live_paper.node"]
