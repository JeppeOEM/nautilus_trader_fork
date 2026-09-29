#!/usr/bin/env bash
# `make bots-churn-check` (Story 29.6): prove the bracket-exit chain live, end to end.
#
# Runs the `live-paper` image once with bots/tests/fixtures/config.churn.toml (one paper bot on
# dYdX BTC-USD-PERP mainnet data, Sandbox execution, tuned to churn through 5 bps exits) mounted
# over /app/bots/config.toml, a scratch fills.db inside the container and host networking (the
# dev box's default Docker bridge stalls venue TLS handshakes), then watches its bots:status
# with bots/tests/churn_check.py until it has seen a protected long, a clean flat and a second
# protected long -- or CHURN_TIMEOUT_SECONDS (900) pass.
#
# The bot talks to its own Redis on CHURN_REDIS_PORT (default 6399), a dedicated port so no other
# stack's Nautilus Cache or bots:* keys are loaded or touched. A Redis is started there (the
# compose file's image tag, host network, no persistence) only when nothing answers yet, and is
# removed afterwards only when this script started it; one left behind by an interrupted run is
# removed first, never reused. The bot container is removed either way.
# Exits with the checker's status: 0 passed, 1 a check failed or was not reached, 2 Redis
# unreachable; on failure the bot's last log lines are printed.
set -euo pipefail

PLATFORM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose -f "$PLATFORM_DIR/docker-compose.yml" --profile live-paper)
FIXTURE="$PLATFORM_DIR/bots/tests/fixtures/config.churn.toml"
PORT="${CHURN_REDIS_PORT:-6399}"
REDIS_URL="redis://127.0.0.1:$PORT"
TIMEOUT="${CHURN_TIMEOUT_SECONDS:-900}"
OUT="${CHURN_OUT:-$PLATFORM_DIR/data/bots_churn_check/payloads.json}"
BOT_CONTAINER="bots-churn-check"
REDIS_CONTAINER="bots-churn-check-redis"
# The broker tag compose pins (redis-py 8 speaks RESP3, which a 7.x server does not).
REDIS_IMAGE="$(sed -n 's/^ *image: *\(redis:[^ ]*\).*/\1/p' "$PLATFORM_DIR/docker-compose.yml" | head -n 1)"
STARTED_REDIS=false

redis_answers() {
    python3 - "$REDIS_URL" <<'PY'
import sys

import redis

try:
    redis.Redis.from_url(sys.argv[1], socket_timeout=2).ping()
except redis.RedisError:
    sys.exit(1)
PY
}

cleanup() {
    docker rm -f "$BOT_CONTAINER" >/dev/null 2>&1 || true
    if [[ "$STARTED_REDIS" == true ]]; then
        docker rm -f "$REDIS_CONTAINER" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# The watcher and the Redis probe run on the host: without redis-py every probe would read as
# "Redis not answering" and hide the real cause.
python3 -c "import redis" 2>/dev/null || {
    echo "host python3 lacks redis-py (pip install redis): the check watches bots:status from here" >&2
    exit 2
}

echo "building the live-paper image"
"${COMPOSE[@]}" build live-paper

# A Redis this script left behind (an interrupted run whose trap never fired) still holds that
# run's Nautilus Cache: the bot would inherit its position, with no exits. Never reuse it.
docker rm -f "$REDIS_CONTAINER" >/dev/null 2>&1 || true
if redis_answers; then
    echo "using the Redis already answering on port $PORT"
else
    echo "starting ${REDIS_IMAGE:?no redis image in docker-compose.yml} on port $PORT"
    docker run -d --rm --name "$REDIS_CONTAINER" --network host "$REDIS_IMAGE" \
        redis-server --port "$PORT" --bind 127.0.0.1 --save "" --appendonly no >/dev/null
    STARTED_REDIS=true
    for _ in $(seq 1 30); do
        redis_answers && break
        sleep 1
    done
    redis_answers || { echo "Redis on port $PORT never answered" >&2; exit 2; }
fi

docker rm -f "$BOT_CONTAINER" >/dev/null 2>&1 || true
# The service's own bind-mount sources: one that does not exist would be created root-owned by
# dockerd, and the uid-1000 bot could not write its error ledger there.
mkdir -p "$PLATFORM_DIR/data/errors" "$PLATFORM_DIR/data/live_paper"
echo "starting $BOT_CONTAINER with $FIXTURE"
"${COMPOSE[@]}" run -d --no-deps --name "$BOT_CONTAINER" \
    -v "$FIXTURE:/app/bots/config.toml:ro" \
    -e REDIS_URL="$REDIS_URL" \
    -e FILLS_DB_PATH=/tmp/bots-churn/fills.db \
    -e ERROR_LEDGER_SERVICE=bots-churn-check \
    live-paper >/dev/null

status=0
(cd "$PLATFORM_DIR" && python3 bots/tests/churn_check.py \
    --redis-url "$REDIS_URL" --timeout "$TIMEOUT" --out "$OUT") || status=$?

if [[ -f "$OUT" ]]; then
    echo "captured payloads ($OUT) as Bots-pane rows:"
    (cd "$PLATFORM_DIR" && python3 - "$OUT" <<'PY'
import json
import sys

from bot_tui.bots_pane import bots_header_line
from bot_tui.bots_pane import format_bot_line

captured = json.loads(open(sys.argv[1]).read())
print(bots_header_line())
for name, status in captured["checks"].items():
    print(format_bot_line(status, stale=False, now=status["updated_at"]), f"<- {name}")
PY
    ) || echo "could not render the captured payloads" >&2
fi

if [[ "$status" -ne 0 ]]; then
    echo "churn check FAILED (exit $status); last bot log lines:" >&2
    docker logs --tail 80 "$BOT_CONTAINER" >&2 || true
fi
exit "$status"
