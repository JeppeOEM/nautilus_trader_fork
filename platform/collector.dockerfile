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
COPY platform/observability ./observability
COPY platform/kernel ./kernel
COPY platform/candles ./candles
COPY platform/collector_core ./collector_core
COPY platform/dydx_collector ./dydx_collector
COPY platform/bybit_collector ./bybit_collector
COPY platform/hyperliquid_collector ./hyperliquid_collector
COPY platform/common ./common
COPY platform/ml_signals ./ml_signals
COPY platform/ranking_engine ./ranking_engine
COPY platform/bot_tui ./bot_tui
COPY platform/data_api ./data_api
COPY platform/tests ./tests

CMD ["python3", "-m", "dydx_collector.collector"]
