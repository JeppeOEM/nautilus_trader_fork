# ponytail: own dockerfile (not troll/collector.dockerfile) so the Node build stage below
# doesn't force collector/ranking_engine/bot_tui to pay a Node build cost they
# don't need -- see the architecture spine's Deployment & Environments section.

# Stage 1: build the frontend (Node build-stage only -- never a runtime dependency of the
# final image; build output is static files, see spine Stack table).
FROM node:24-slim AS frontend-build
WORKDIR /frontend
COPY troll/frontend/package.json troll/frontend/package-lock.json ./
RUN npm ci
COPY troll/frontend ./
RUN npm run build

# Stage 2: application -- same base image and rebuild-order discipline as collector.dockerfile
# (rebuilt rarely; this thin layer rebuilds in seconds):
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
COPY troll/dydx_collector ./dydx_collector
COPY troll/ml_signals ./ml_signals
COPY troll/ranking_engine ./ranking_engine
COPY troll/data_api ./data_api
# Must land at ./frontend_dist -- data_api/app.py's FRONTEND_DIST_PATH default ("frontend_dist",
# resolved relative to this image's WORKDIR /app) depends on this exact destination; keep the
# two in sync if either changes (Story 15.1 AC #5).
COPY --from=frontend-build /frontend/dist ./frontend_dist

CMD ["uvicorn", "data_api.app:app", "--host", "127.0.0.1", "--port", "9100"]
