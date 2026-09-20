# ponytail: rebuild base rarely (nautilus core changes), this layer rebuilds in seconds:
# docker build --network=host -f .docker/nautilus_trader.dockerfile --target application -t nautilus-trader-base:1.229.0 .
FROM nautilus-trader-base:1.229.0

WORKDIR /app
RUN chown 1000:1000 /app
COPY troll/troll-requirements.txt ./troll-requirements.txt
# PIP_INSECURE_ARGS is empty by default (normal TLS-verified install). Set via
# --build-arg when behind a TLS-intercepting proxy (e.g. Zscaler) that breaks pip's
# cert verification against PyPI -- see `make build-insecure`.
ARG PIP_INSECURE_ARGS=
RUN pip install --no-cache-dir $PIP_INSECURE_ARGS -r troll-requirements.txt
COPY troll/collector_core ./collector_core
COPY troll/dydx_collector ./dydx_collector
COPY troll/bybit_collector ./bybit_collector
COPY troll/hyperliquid_collector ./hyperliquid_collector
COPY troll/common ./common
COPY troll/ml_signals ./ml_signals
COPY troll/ranking_engine ./ranking_engine
COPY troll/bot_tui ./bot_tui
COPY troll/data_api ./data_api

CMD ["python3", "-m", "dydx_collector.collector"]
