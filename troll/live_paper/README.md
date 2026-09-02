# live_paper — running and testing the bot locally

`live_paper` is a real `TradingNode` + `Strategy` (`DummyStrategy`, Story 3.2) that
subscribes to live dYdX market data and trades against it. **Paper mode is always 100%
simulated money** — it uses `SandboxExecutionClientConfig`, which has no wallet address
or private key anywhere in the path, so real funds are structurally unreachable
regardless of `network` (`mainnet` vs `testnet` only changes which market data feed is
used, never whether orders touch a real account). You cannot accidentally trade real
money by running the steps below.

For the fuller pre-production checklist (testnet dry-run, resilience checks, soak
testing) see [`DEPLOY_CHECKLIST.md`](./DEPLOY_CHECKLIST.md). This doc is the shorter
"get it running and see it work" version for local dev.

---

## Prerequisites

- Docker + Docker Compose
- The `nautilus-trader-base` image built once — from `troll/`:
  ```bash
  make build-base   # ~15 min, only needed once or after core nautilus_trader changes
  ```
- Redis running — `live_paper` needs it for `bots:status`/`bots:control` and the
  Cache-backed fill history. Starting the collector stack (`make up`) brings up Redis
  as a side effect; if you only want `live_paper` on its own:
  ```bash
  docker compose -f troll/docker-compose.yml up -d redis
  ```

---

## One-time: create the data directory

```bash
mkdir -p troll/live_paper/data
```

Do this **before** the first `docker compose up`/`run` for `live-paper`. If Docker
creates this bind-mount target itself (because it doesn't exist yet), it lands owned by
`root`, and the container runs as `user: "1000:1000"` — every fill write then fails
silently with `unable to open database file` (confirmed live 2026-09-02: this also
means **no trade history gets recorded at all**, so don't skip this step).

---

## Configure

Edit `troll/live_paper/config.toml`:

```toml
network = "mainnet"                 # or "testnet" -- only changes the market data feed
starting_balances = ["10_000 USDC"]
account_type = "MARGIN"
log_level = "INFO"
instrument_id = "BTC-USD-PERP.DYDX"
bot_id = "bot-01"
trade_size = "0.001"
trend_buy_threshold = 0.6
trend_sell_threshold = 0.4
ofi_confirm_threshold = 0.0
```

This file must never contain a `mode` key — `load_paper_config()` hard-errors if it
finds one (that's the point: real-money execution is a separate file/loader, never a
field toggle here — see `config.py`'s docstring).

**To see a trade fire quickly** for testing purposes, loosen the thresholds so the
strategy doesn't wait for a strong signal, e.g. `trend_buy_threshold = 0.51`,
`trend_sell_threshold = 0.49`, `ofi_confirm_threshold = -999` (never do this for a real
paper-P&L run — it makes the strategy trade on noise, which is fine when the only goal
is proving the order/fill/history pipeline works end to end).

---

## Run it

From `troll/`:

```bash
make up-live-paper
```

This builds the image (if needed) and starts the `live-paper` container in the
background. It is **not** part of plain `make up` — it's gated behind the `live-paper`
Compose profile so it's never started by accident.

---

## Watch it

**Logs:**
```bash
docker compose -f troll/docker-compose.yml logs -f live-paper
# or open Dozzle: make web  (http://localhost:8080)
```

What a healthy startup looks like, in order:
1. `CacheDatabaseAdapter: READY` / `Connected to redis` — Cache is Redis-backed.
2. `DataClient-DYDX: Connected` — WebSocket to dYdX is up.
3. `TradingNode: Execution state reconciled` / `Portfolio initialized`.
4. `DummyStrategy: RUNNING` / `TradingNode: RUNNING`.
5. `[CMD]--> Subscribe...` lines for quotes/order book/bars.

A burst of `OrderMatchingEngine(DYDX): Skipping stale trade` warnings right at startup
is normal — the sandbox exchange backfills recent historical trades to seed its book and
correctly discards ones older than the live book state. It stops once the backfill
drains.

**Status heartbeat** (published every 5s):
```bash
docker exec dydx-redis redis-cli SUBSCRIBE bots:status
```
Look for `"mode": "paper"`, `"running": true`, and `net_exposure`/`unrealized_pnl`
updating once a position opens.

**Trade history** (published every 30s once fills exist):
```bash
docker exec dydx-redis redis-cli GET bots:history:bot-01:all
```

### bot_tui — the interactive terminal dashboard

The fullest way to watch a running bot: live status, start/stop, a trades blotter, and
a PnL sparkline, without leaving the terminal.

```bash
cd troll
make tui
```

This runs `docker compose --profile tui run --rm bot_tui python3 -m bot_tui.app` — an
interactive full-screen app, so it must be launched from a real terminal (an SSH session
or a local shell), never piped or backgrounded. It's gated behind the `tui` Compose
profile and is never started by `make up` (it's a one-shot interactive process, not a
daemon). It reads `bots:status`/`bots:history:*` from Redis, so `redis` must already be
up (`make up` or `make up-live-paper` both bring it up) — `live-paper` itself doesn't
need to be running yet; the Bots pane will just show that bot as offline/stale until it
is.

**Navigating once it's open:**

| Key | Where | Does |
|---|---|---|
| `:` then type `bots` + Enter | anywhere | switch to the Bots pane |
| `:` then type `coins` + Enter | anywhere | switch to the Coins pane (collector/ranking view) |
| `:` then type `q` + Enter | anywhere | quit |
| `Enter` | Bots pane, row highlighted | open that bot's full-screen detail view |
| `s` | Bots pane or Bot-detail | start/stop the highlighted or open bot. Stopping a *running* bot opens a type-to-confirm prompt (type `stop` + Enter) — starting has no such guard |
| `t` | Bot-detail | cycle the trades blotter / PnL sparkline's range: day → week → month → all |
| `o` | Bot-detail | open this bot's chart in the web dashboard (browser) |
| `Esc` | any sub-view | go back one level |

The Bots pane shows `bot_id`, mode (`paper`/`live`), running state, position side,
`net_exposure`, PnL, and a stale badge if `bots:status` hasn't heartbeated recently.
Bot-detail additionally shows the trades blotter and PnL-over-time sparkline sourced
from `bots:history:{bot_id}:{range}` (Story 4.6/4.7) — empty until the bot has at least
one closed position and `trade_history.py`'s 30s Redis-publish cycle has run at least
once.

---

## Control it

```bash
# stop the strategy (does NOT flatten an open position -- no auto-flatten logic exists)
docker exec dydx-redis redis-cli PUBLISH bots:control '{"bot_id":"bot-01","action":"stop"}'

# resume
docker exec dydx-redis redis-cli PUBLISH bots:control '{"bot_id":"bot-01","action":"start"}'
```

A message with the wrong `bot_id` is silently ignored — safe to leave other bots' IDs
alone on a shared Redis instance.

---

## Stop it

```bash
docker compose -f troll/docker-compose.yml --profile live-paper down live-paper
```

Fill history in `troll/live_paper/data/fills.db` and Cache state in Redis both persist
across restarts — this is the point of Story 4.6's durable history.

---

## Run the automated test suite

```bash
cd troll
docker compose -f docker-compose.yml --profile live-paper run --rm --no-deps \
  -e HOME=/tmp -e USER=collector live-paper \
  python3 -m pytest live_paper/tests -q
```

(`-e HOME=/tmp -e USER=collector` works around a pre-existing `OSError: No username set`
from pytest's `tmp_path` fixture — the image runs as uid 1000 with no matching
`/etc/passwd` entry.) `make test-live-paper` from `troll/` runs the same command.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `bots:history refresh cycle failed, keeping stale data: unable to open database file` | `live_paper/data/` is root-owned | `sudo rm -rf troll/live_paper/data && mkdir troll/live_paper/data` (parent dir is user-owned, so no `sudo` needed for the `mkdir` itself if you can remove the root-owned one) |
| `bots:status/control loop error -- reconnecting in 2s: ... not NoneType` a couple of times right at startup | Benign race: the heartbeat loop's first tick can land before the portfolio has a first quote price. Self-heals within ~1-2 ticks. | Nothing — this no longer tears down the Redis connection (fixed 2026-09-02); a couple of skipped ticks at boot is expected. |
| Container restarts right after the very first fill | Old bug (fixed 2026-09-02): the on-fill handler had no error handling, so any `fills_store` write failure crashed the whole node | Rebuild the image if you're on an older one — `docker compose -f docker-compose.yml --profile live-paper build live-paper` |
| Code edits don't seem to take effect | `live_paper.dockerfile` `COPY`s source into the image at build time — it's not bind-mounted like `config.toml`/`data/` are | Rebuild: `docker compose -f docker-compose.yml --profile live-paper build live-paper` |
