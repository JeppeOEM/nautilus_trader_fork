# ponytail: rebuild base rarely (nautilus core changes), this layer rebuilds in seconds:
# docker build --network=host -f .docker/nautilus_trader.dockerfile --target application -t nautilus-trader-base:1.229.0 .
FROM nautilus-trader-base:1.229.0

WORKDIR /app
RUN pip install --no-cache-dir plotly
COPY troll/dydx_collector ./dydx_collector
COPY troll/ml_signals ./ml_signals

CMD ["python3", "-m", "dydx_collector.collector"]
