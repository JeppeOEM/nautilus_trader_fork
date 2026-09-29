# Database Setup

Four separate persistence mechanisms exist in `platform/`, each with a distinct job. None
of them overlap in purpose — see [Data Dictionary](DATA_DICTIONARY.md) for the full
schema of what actually flows *through* Redis/the catalog, this doc is about the
storage layer itself: where each store lives, who owns writes, and how to reach it.

| Mechanism | What it's for | Durable? | Network-reachable? |
|---|---|---|---|
| **Redis** | Live pub/sub + small "latest value" cache between services | No (in-memory, no persistence configured) | Yes — `127.0.0.1:6379` only |
| **`metrics.db`** (SQLite) | 31-day rolling per-instrument metrics history | Yes | No (file only) |
| **`fills.db`** (SQLite) | Append-only live-paper trade/fill ledger | Yes | No (file only) |
| **Parquet catalog** | Raw market data archive (trades, book deltas, bars, ...) | Yes | No (file only) |
| **`config.toml`** | Collector's live instrument registry (pin/unpin state) | Yes | No (file only) |

All of this is described by `platform/docker-compose.yml`, and `platform/CLAUDE.md`'s **SEC-01**
governs every port here: *localhost-only, always* — nothing in this stack is ever bound
to a public interface. Remote access is via SSH tunnel only (§6).

---

## 1. Redis — the live message bus

**Image:** `redis:8-alpine`, container `dydx-redis`, bound `127.0.0.1:6379:6379`
(`docker-compose.yml`). Every service connects via `redis.asyncio` using
`REDIS_URL=redis://127.0.0.1:6379`.

**No persistence, no TTLs, no complex types.** Nothing here uses Redis's own
persistence (RDB/AOF) — if the container restarts, everything in it is gone and
services simply republish on their next cycle. No key anywhere has an expiry set
(no `.expire()`/`.setex()`); staleness is instead judged consumer-side (age of the
payload's own timestamp field). Only plain string `GET`/`SET` and `PUBLISH`/`SUBSCRIBE`
are used — no hashes, sorted sets, streams, or lists.

Think of Redis here purely as **a live wire between processes**, not a database you'd
ever back up or migrate.

<!-- [amended 2026-09-20: Epic 22 story 22.8] `dashboard` was retired by Story 15.10; every
     reader named below is `data_api` (FastAPI + React SPA), reading the same Redis keys. -->

### 1.1 Pub/Sub channels

| Channel | Publisher | Subscribers | Payload |
|---|---|---|---|
| `snapshots:raw` | `capture/application/capture_service.py`'s `_second_loop` — every ~1s tick, in **all three** collector containers (dYdX, Bybit, Hyperliquid) | `ranking_engine`, `data_api` | JSON list of `DydxSecondSnapshot` dicts (book top-20 + trade volume/count), one per collected instrument. Each publisher sends only its own venue's instruments, so entries stay disjoint by `instrument_id` — this is the architecture spine's "one producer per (channel, venue)" convention |
| `rankings:live` | `ranking/` (the `ranking_engine` service; **sole publisher**, AD-9) | `data_api` | JSON: `{mode, updated_at, ranks: [...], stale_instrument_ids: [...]}` — every rank row carries volume/volatility/OFI/OBI/microprice/spread/CVD/price/pct-change fields |
| `markets:live` | `ranking/` (`RankingEngine.publish_markets`, every 60 s volume cycle; Story 29.5) | `bot_tui` (the Collector pane's `/` market browser) | One message per venue with a fresh volume source: `{"venue", "ts", "markets": [{"instrument_id", "symbol"}]}`, sorted by id — names only, no volume/price/metric (`docs/DATA_DICTIONARY.md` §3.6) |
| `ranking:control` | `data_api` (`PUT /api/rankings/mode`, the web rankings page's mode control; Story 25.1a) | `ranking_engine` | `{"mode": "volume"\|"volatility"}` |
| `collector:control` | `bot_tui` | `collection_control`'s `ControlService`, in every collector (`capture/venues/{dydx,bybit,hyperliquid}/__main__.py`; Bybit and Hyperliquid since Story 29.4), each acting only on its own venue's messages | `{"action": "start"\|"unpin"\|"stop"\|"pin_top_liquid", "id": "<instrument_id>", "venue": "DYDX"\|"BYBIT"\|"HYPERLIQUID"}` (`id` omitted for `pin_top_liquid`; `venue` appended in Story 29.4, a message without it is dYdX's; `docs/DATA_DICTIONARY.md` §1.12) |
| `collector:status` | `collection_control`'s `StatusPublisher`, in all three collectors (`capture/venues/{dydx,bybit,hyperliquid}/__main__.py`; Bybit and Hyperliquid since Story 29.2) | `bot_tui` | Per-instrument `{id, liquid, last_trade_ts, trade_backfill[, pending]}`, the per-venue plan aggregate `{unpinned_ids, venue, cap, accepts_commands, min_liquidity_usd, last_apply, last_refusal}` (`last_refusal` appended in Story 29.5), or a `{id, removed}` tombstone (`docs/DATA_DICTIONARY.md` §1.12) |
| `bots:control` | `bot_tui` | `bots/application/supervise.py` | `{"bot_id": "...", "action": "start"\|"stop"}` |
| `bots:status` | `bots/application/supervise.py` — every 5s heartbeat and on each of the bot's order events (Story 29.6) | `bot_tui` | `{bot_id, strategy, symbol, mode, running, position_side, net_exposure, realized_pnl, unrealized_pnl, win_rate, closed_trades, started_at, updated_at, stop_loss, take_profit, entry_price, mark_price, position_qty, stop_loss_orders, take_profit_orders, open_orders, last_fill_at}` — the last nine appended in Story 29.6 (fields are only ever appended): the five price/quantity fields are `str(Price)`/`str(Quantity)` strings or `null` (flat, no such protective order, no mid yet), the three counts ints, `last_fill_at` UNIX ns or `null` (`docs/BOT_OPERATIONS.md` §1) |

### 1.2 Plain keys (GET/SET, no TTL)

| Key | Writer | Reader | Shape |
|---|---|---|---|
| `bots:incidents:{bot_id}` | `bots/application/supervise.py` (the log itself: `bots/domain/bot.py`) | `bot_tui` (polled) | JSON list of `{type: "data_stale"\|"process_start", started_at, ended_at}`, capped at 50 entries |
| `bots:history:{bot_id}:{day\|week\|month\|all}` | `bots/application/history.py` | `bot_tui` (polled) | JSON blob of performance stats derived from `fills.db` |

### 1.3 Who owns what

- **`ranking_engine`** is the sole computer of every rolling-window ranking indicator
  (OFI/OBI z-scores, volatility). `data_api`/`bot_tui` only ever parse
  `rankings:live` — neither runs its own copy of these indicators (`platform/CLAUDE.md`
  SSOT-02).
- **The three collectors** (compose services `collector`, `bybit_collector`,
  `hyperliquid_collector`, i.e. `python3 -m capture.venues.{dydx,bybit,hyperliquid}`, all
  publishing through the shared `capture` context) are the only writers of
  `snapshots:raw` — one producer per venue, each publishing only its own instruments.
  Each also publishes its own plan on `collector:status` (one aggregate per venue, Story 29.2)
  and consumes `collector:control`, acting only on the messages addressed to its own venue
  (Story 29.4; a message without `venue` is dYdX's).
- **`bots`** (the bots context, `python3 -m bots`; was `live_paper` until Story 25.3) is the sole writer of `bots:status`/`bots:incidents:*`/
  `bots:history:*`, and the sole actor on `bots:control`.
- **`bot_tui`** never writes status data — it only publishes control messages and
  reads everything else. It never touches SQLite or the catalog directly either;
  every value it shows arrives via Redis.

### 1.4 Nautilus's own internal Cache also lives here

Separately from everything above, `bots/infrastructure/nautilus_host.py` configures Nautilus's built-in
`CacheConfig(database=DatabaseConfig(type="redis", ...))` for its `TradingNode` —
this is `nautilus_trader`'s own order/position/account state persistence, not
`platform/`-authored code, and it's wired to the *same* `dydx-redis` container rather
than a separate store. `DatabaseConfig` generically supports Postgres too (it's
listed in the top-level tech stack for that reason), but nothing in `platform/` ever
connects to Postgres — there's no `postgres` service in `docker-compose.yml` and no
Postgres client anywhere in the code. If you're looking for a Postgres instance to
tunnel to or inspect, there isn't one.

---

## 2. SQLite databases

Both use raw `sqlite3` (no ORM) with `PRAGMA journal_mode=WAL`. WAL mode is why both
are bind-mounted as **directories**, not single files, in `docker-compose.yml` — WAL
creates `<name>.db-wal`/`<name>.db-shm` sidecar files next to the main one, which a
single-file mount can't expose.

### 2.1 `metrics.db` — owned by `ranking/infrastructure/metrics_store.py`

- **Path:** `./data/metrics/metrics.db` on the host (`METRICS_DB_PATH` env,
  mounted `/app/metrics_dir/metrics.db` in the `ranking_engine`/`data_api` containers).
- **Table:** `snapshots(ts, instrument_id, price, pct_1h, pct_24h, volatility, ofi,
  microprice, spread, rank, volume24h)`, primary key `(ts, instrument_id)`.
- **Purpose:** 31-day rolling per-instrument history, upserted every 60s, pruned to
  `retain_days=31` on every write. Powers the web UI's per-coin history chart.
- **Writer:** `ranking_engine` exclusively. **Reader:** `data_api` only (mounted
  `:ro`).
- Note: the `ofi` column here is top-of-book-only `OrderFlowImbalance` — a *different*
  metric from the multi-level `ofi_10_z`/`ofi_3/5/10` fields that live only in
  `rankings:live`. See [Data Dictionary](DATA_DICTIONARY.md) if that distinction
  matters for what you're building.

### 2.2 `fills.db` — owned by `bots/infrastructure/fills_store.py`

- **Path:** `./data/live_paper/fills.db` on the host, `/app/data/live_paper/fills.db` in the
  container (`FILLS_DB_PATH` env; `/app/live_paper/data/fills.db` until Story 26.3 mirrored the
  host path).
- **Table:** `fills(ts, bot_id, side, price, qty, realized_pnl,
  position_realized_pnl)`.
- **Purpose:** append-only, event-sourced fill log — one row per `OrderFilled` event,
  written directly off the strategy's message bus. This exists specifically because
  Nautilus's `Cache.positions_closed()` silently discards prior closed positions on a
  NETTING-mode position reopen — `fills.db` is the durable source of truth trade
  history is rebuilt from, not the Nautilus cache.
- **Writer:** `bots/application/history.py` (through `bots.domain.fill_ledger.FillLedger`).
  **Readers:** `bots/application/supervise.py` (win-rate stats) and `history.py` itself, to
  build the `bots:history:*` Redis blobs above. Nothing outside `bots/` reads this file
  directly — `bot_tui`/
  `data_api` only ever sees it via Redis.

---

## 3. Parquet catalog

`ParquetDataCatalog`, mounted at `./data/catalog` (read-write for all three
collector services — `collector`, `bybit_collector`, `hyperliquid_collector` — read-only
for `data_api`/`ranking_engine`). This is the append-only
archive of what the three collectors actually write: second-snapshots
(`DydxSecondSnapshot`, which folds that second's trades in rather than storing them
raw — audit D-45), mark/index price, funding rate, open interest and instrument
definitions, plus `order_book_deltas` for the dYdX instruments that opt in via
`store_order_book_deltas` (off by default, dYdX-only). There is no `trade_tick/`
directory and minute bars were retired 2026-09-20 (audit D-35). Written exclusively
via `ParquetDataCatalog.write_data()`
(`platform/CLAUDE.md` NAUT-02: no hand-rolled schemas). Its full schema, per type, is
already documented in [Data Dictionary](DATA_DICTIONARY.md) §1 — this doc won't
repeat it.

---

## 4. `config.toml` — the collector's instrument registry

Easy to mistake for static config, but `platform/data/dydx_config.toml` (bind-mounted over
`/app/dydx_collector/config.toml`) is actually **mutable,
persisted runtime state**, rewritten by the running system on its own. Since Story 29.4 so are
Bybit's and Hyperliquid's committed `platform/capture/venues/{bybit,hyperliquid}/config.toml`
(bind-mounted read-write over `/app/{bybit,hyperliquid}_config.toml`): their flat `instruments`
list plus an optional `exclude` list, written only when non-empty. Being committed files, a
runtime command leaves the VPS checkout modified (`docs/DEPLOY_CHECKLIST.md`'s 29-4 entry).

- **Owner:** the `collection_control/` context (Story 25.4): `TomlPlanStore`
  (`collection_control/infrastructure/plan_store.py`) loads and saves the plan through the one
  venue loader, `capture.infrastructure.config.load_venue_config` (`tomllib`/`tomli_w`). A save
  re-reads the file, replaces only the plan keys, validates the result and rewrites the file in
  place, not a patch — hand-added comments won't survive a control action.
- **Read:** `collection_control`'s `reload_loop` re-reads it every `config_reload_seconds`
  (30 s; Bybit/Hyperliquid `PLAN_RELOAD_SECONDS`, also 30 s), so a hand-edit of the plan is picked up without a restart (the thresholds are read
  once per start).
- **Write:** every accepted `collector:control` action addressed to that venue
  (start/unpin/stop, and dYdX's pin_top_liquid) triggers a rewrite.
- **Content:** network, catalog path, flush/snapshot intervals, liquidity threshold,
  the `instruments` array (`id`, `store_order_book_deltas`, `retain_hours`) and `exclude`
  — the plan (the intent). What capture actually subscribed is the applied set, reported on
  `collector:status` (a planned id not yet applied carries `"pending": true`).
- **Docker mount:** the three plan files are the only collector config mounts that are `rw` in
  `docker-compose.yml` (each read-write only in its own collector's container). Outside them,
  only the preference files the UI saves are `rw` (`chart_indicators.toml`,
  `screener_columns.toml`, `data_api/alerts.toml`); every other config file (`bots/config.toml`
  included) is mounted `:ro` and never written back by the running process.
- `bot_tui` never edits these files directly — it only publishes `collector:control`
  messages, keeping each file's filesystem access to its one collector container.

---

## 5. Local layout on disk

```
platform/
├── bots/
│   └── config.toml                # static per-bot config (ro)
├── data/
│   ├── catalog/                   # Parquet catalog (§3)
│   ├── metrics/
│   │   └── metrics.db             # + -wal/-shm sidecars (§2.1)
│   ├── dydx_config.toml           # collector instrument registry (rw, §4)
│   └── live_paper/
│       └── fills.db               # + -wal/-shm sidecars (§2.2)
```

Redis has no on-disk footprint here (no RDB/AOF configured) — it's purely in-memory,
per §1.

---

## 6. Viewing this remotely

Every port above is bound `127.0.0.1`-only on the host (SEC-01) — none of it is
reachable from outside the box directly. The way in is an SSH tunnel, via the
`nifelheim` alias already set up in `~/.ssh/config` and wired into `platform/Makefile`:

| Command | What it does |
|---|---|
| `make remote-db` | Tunnels Redis (6379) to `localhost:6379` so a GUI client (RedisInsight, TablePlus, Another Redis Desktop Manager, ...) on your machine can connect to it as if it were local. |
| `make remote-db-stop` | Kills that tunnel. |
| `make remote-web` | Tunnels the dashboard (9100) and opens it in your browser. |
| `make remote-web-stop` | Kills that tunnel. |
| `make remote-tui` | SSHes in with a real TTY and runs `bot_tui` interactively — no port involved, it's a terminal app, not a network service. |

The two SQLite files and the Parquet catalog have no server to tunnel to — they're
just files on the remote host. To inspect them locally, `scp`/`rsync` a copy down
(e.g. `rsync -avz nifelheim:~/nautilus_trader_fork/platform/data/metrics/ ./metrics/`)
rather than trying to tunnel a port for them.
