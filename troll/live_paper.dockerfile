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
# Story 3.2 imports ml_signals.indicators directly (AD-4: shared, pure indicator classes,
# no I/O, no stateful internals) -- ml_signals must be present in this image now. Still
# deliberately does NOT copy dydx_collector -- live_paper never imports it (AD-8 keeps the
# module structurally separate from the collector's write path either way).
COPY troll/ml_signals ./ml_signals
COPY troll/live_paper ./live_paper

CMD ["python3", "-m", "live_paper.node"]
