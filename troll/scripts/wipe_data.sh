#!/usr/bin/env bash
# Permanently deletes every persisted data store the troll/ stack has written:
# the Parquet market-data catalog (all coins), ranking_engine's metrics.db
# (history charts), collector incident reports, and live_paper's fills.db
# (bot trade/position history). Leaves config.toml and .gitkeep files alone.
#
# Stops the docker compose stack first so nothing is deleted out from under a
# container holding it open, then deletes, then leaves the stack down --
# `make up` / `docker compose up -d` to bring it back with a clean slate.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

TARGETS=(
    dydx_collector/catalog
    dydx_collector/metrics
    dydx_collector/incident_reports
    live_paper/data
)

echo "This will PERMANENTLY delete all data in:"
for t in "${TARGETS[@]}"; do
    printf '  %-32s %s\n' "$t" "$(du -sh "$t" 2>/dev/null | cut -f1)"
done
echo
echo "Everything collected/computed/traded is gone after this -- no undo."
read -r -p 'Type "yes" to proceed: ' confirm
if [[ "$confirm" != "yes" ]]; then
    echo "Aborted -- nothing deleted."
    exit 1
fi

echo "Stopping docker compose stack..."
docker compose -f docker-compose.yml down

for t in "${TARGETS[@]}"; do
    find "$t" -mindepth 1 -not -name .gitkeep -exec rm -rf {} +
    echo "Wiped $t"
done

echo "Done. Stack is stopped -- run 'make up' to start fresh."
