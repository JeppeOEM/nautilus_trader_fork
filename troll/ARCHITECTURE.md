# System Overview

Everything under `troll/` in one picture: what each module does, what data it owns, and
exactly how the pieces talk to each other (Redis channels, SQLite, Parquet, HTTP). This
is the "what got built" document — for setup/run instructions see `README.md`, for
coding rules see `CLAUDE.md`, for planning history see `_bmad-output/`.

---

## The one-paragraph version

A collector pulls live dYdX market data straight off the Rust adapter and writes it to
a Nautilus-native Parquet catalog, publishing a live 1-second snapshot feed to Redis as
it goes. A ranking engine reads that feed, scores every coin by volume or volatility,
and publishes the result back to Redis. A web dashboard and a terminal UI both read the
same two Redis feeds (never recomputing anything themselves) to show live charts and a
watchlist. Indicators written once in `ml_signals` get reused unmodified in Jupyter
research, Nautilus backtests, and a live paper-trading bot (`live_paper`), which
publishes its own status to Redis so the TUI can monitor and start/stop it. Nothing
downstream of the collector ever touches `nautilus_trader`'s live `TradingNode`/
`DataEngine` except `live_paper` — that's the one sanctioned exception.

---

## Module map

| Module | Role | Talks to |
|---|---|---|
| `dydx_collector/` | Captures live dYdX data → Parquet catalog + `snapshots:raw` Redis feed | dYdX WS/REST (via Rust `nautilus_pyo3` client), Redis (publish), Parquet catalog (write) |
| `ml_signals/` | Shared indicators + web dashboard + backtesting | Parquet catalog (read), Redis (`snapshots:raw`, `rankings:live` read; `ranking:control` publish), `metrics.db` (read) |
| `ranking_engine/` | Sole computer of coin ranking (volume + volatility) | Redis (`snapshots:raw` read; `rankings:live` publish; `ranking:control` read), `metrics.db` (write), dYdX REST (24h volume poll) |
| `live_paper/` | The actual trading bot — `TradingNode` + `Strategy` in paper (or gated real-money) mode | dYdX WS/HTTP (via `TradingNode`), Redis (`bots:status` publish, `bots:control` read) |
| `bot_tui/` | Keyboard-only terminal UI, interactive/on-demand | Redis (`rankings:live`, `snapshots:raw`, `bots:status` read; `ranking:control`, `bots:control` publish), dashboard (HTTP deep-link only) |

Module boundary rule enforced throughout (architecture AD-4): every module downstream
of the collector depends only on shared data types (`DydxSecondSnapshot`,
`DydxOpenInterest`, `ml_signals.indicators` classes) and Redis/HTTP contracts — never
another module's internal state. `dydx_collector` never imports from anything
downstream of it.

---

## Data flow

```
dYdX WS/REST (Rust nautilus_pyo3 client)
        │
        ▼
  dydx_collector  ──────────────► Parquet catalog (Nautilus-native, zero-conversion)
        │                                 │
        │ publish "snapshots:raw"         │ read (time-bounded / BacktestDataConfig)
        ▼                                 ▼
      Redis  ◄───────────────────  ranking_engine (volume + volatility scoring)
        │  ▲                             │
        │  │ publish "rankings:live"     │ write
        │  └─────────────────────────────┘
        │                            metrics.db (SQLite, ranking history)
        │
        ├──► data_api (web UI + REST, :9100)      — reads snapshots:raw + rankings:live
        │
        ├──► bot_tui (terminal, on-demand)        — reads snapshots:raw + rankings:live
        │                                            + bots:status; writes ranking:control
        │                                            + bots:control
        │
        └──► live_paper (TradingNode, paper/live) — writes bots:status; reads bots:control
                     │
                     ▼
              dYdX (paper fills, or real fills behind an explicit gate)
```

Everything downstream of the collector reads either the catalog (bounded/historical) or
Redis (live/current) — never both conflated, per NFR3's memory-bounded-access rule.

---

## 1. `dydx_collector/` — the data source

Standalone asyncio service. Bypasses `TradingNode`/`Strategy`/`DataEngine` entirely and
talks directly to `nautilus_pyo3.DydxHttpClient`/`DydxWebSocketClient` — this is
deliberate: `DataEngine` has a documented unbounded-queue-growth bug under sustained
load that OOM-crashed an earlier `Strategy`-based recorder.

- **`collector.py`** — owns the asyncio loop, buffer, and flush timer. Every 1s builds a
  `DydxSecondSnapshot` per subscribed instrument (top-20 bid/ask levels + per-side trade
  volume — nothing derivable is stored) and publishes it to Redis channel
  `snapshots:raw`. Flushes trades/deltas/bars/mark-index-funding/instruments to the
  Parquet catalog via `ParquetDataCatalog.write_data()` on `flush_interval_seconds`.
- **`client.py`** — thin wrapper around the Rust WS/HTTP clients; `_at_fixed_precision()`
  re-stamps mark/index prices to a single precision (dYdX's feed derives precision from
  each tick's own trailing-zero count, which corrupts catalog writes if left alone).
- **`second_snapshot.py`** — defines `DydxSecondSnapshot(Data)`, the one custom Arrow-
  registered type this whole system is built around.
- **`open_interest.py`** — `DydxOpenInterest(Data)` + `classify_liquidity()`; open
  interest is the one field the Rust bindings drop, so it's polled separately via
  stdlib `urllib` against dYdX's indexer REST endpoint every 5 min.
- **`prune_catalog.py`** — deletes `order_book_deltas` older than N days (`make prune`).
- **Hot-reload**: `config.toml`'s instrument list is re-read every `config_reload_seconds`
  — no restart needed to add/remove a coin.

**Publishes:** `snapshots:raw` (Redis pub/sub, one message per instrument per second).
**Writes:** Parquet catalog at `dydx_collector/catalog/`.

**Known benign WARN log lines** (from `nautilus_network::websocket::client`, seen via
`troll-logs`/Dozzle):
- `Connection closed by peer (no close frame), terminating` — dYdX's socket hung up
  without a WS close handshake (server restart, LB cycling the connection, network
  blip). The read loop breaks and the client's normal reconnect/resubscribe logic
  takes over automatically — expected, not a bug, as long as reconnect follows.
- `Received close frame, terminating: code=..., reason='...'` — same situation but a
  *graceful* close (peer sent a proper WS close frame first).

---

## 2. `ml_signals/` — shared signals, dashboard, backtesting

The one place indicators are implemented — everything else imports from here, never
reimplements.

- **`indicators.py`** — five Nautilus `Indicator` subclasses, each used identically in
  Jupyter, backtest, and live (`live_paper`):
  - `Microprice` — size-weighted mid from top-of-book
  - `OrderFlowImbalance` — top-of-book OFI
  - `MultiLevelOBI` — N-level order book imbalance
  - `MultiLevelOFI` — N-level order flow imbalance, replayed across snapshots
  - `OnlineLogisticTrend` — online-updating trend classifier fed from bars
- **`book_features.py` / `footprint.py` / `candles.py` / `chart_data.py`** — derived-view
  helpers for the dashboard (spread, microprice, footprint charts, OHLC aggregation) —
  none of it is stored, all computed on read per `troll/CLAUDE.md`'s 1s-based signal
  architecture rule.
- **`watchlist.py`** — `fetch_watchlist()` reads `data_api`'s `/api/rankings` to get the live, ranked coin set — this is how a backtest gets a dynamic instrument
  universe instead of a hardcoded list.
- **`strategies/backtest_dydx.py` / `strategies/backtest_ofi.py` / `strategies/backtest_snapshot.py`** — `BacktestNode` +
  `BacktestDataConfig` runs (no custom matching engine anywhere). `strategies/backtest_dydx.py`
  defaults to backtesting every coin in the live Watchlist, keyed results per symbol.
- **`strategies/example_strategy.py` / `strategies/ofi_strategy.py` / `strategies/snapshot_strategy.py`** — backtest-only
  reference strategies, referenced via `ImportableStrategyConfig` by string path.
- **`metrics_computer.py`** — pure computation used by `ranking_engine` to score coins;
  lives here (not in `ranking_engine`) so the same math is reachable from research code.
- **`rank_history.py` / `catalog_stats.py`** — supporting queries for the history view
  and catalog coverage/gap diagnostics.

**Reads:** Parquet catalog, Redis (`snapshots:raw`, `rankings:live`), `metrics.db`.
**Publishes:** `ranking:control` (mode-switch requests only).
**Serves:** nothing itself -- the web UI is `data_api` + `frontend/` (next section).

---

## 2a. Web UI — `data_api/` + `frontend/`

`ml_signals/dashboard.py` (the old aiohttp HTML app) was retired in Story 15.10. The web
UI is now the React SPA in `troll/frontend/` (Rankings, Chart, 31-day History, Docs),
served by the `data_api` FastAPI app on `:9100` (`127.0.0.1` only) alongside its REST +
WebSocket API: `/api/rankings`, `/api/candles/{id}`, `/api/snapshots/{id}`,
`/api/indicator-series/{id}`, `/api/coin/{id}/indicators`, `/api/metrics/history|nearest/{symbol}`,
and `/ws/live`. `data_api` reads the catalog/`metrics.db` read-only. Dropped without a
React equivalent: the `/debug` state dump and the server-rendered `/live` table.
---

## 3. `ranking_engine/` — the one place ranking gets computed

Extracted from what used to be inline logic in `dashboard.py` (Story 1.8) specifically
so the dashboard, TUI, and any future reader can never disagree on coin order.

- **`engine.py`** — subscribes to `snapshots:raw`, ingests each batch, computes both
  volume and volatility scores every cycle (both are always present in the output
  regardless of active mode), and publishes the merged result to `rankings:live` — on
  every rank change **and** on a fixed heartbeat (`RANKING_HEARTBEAT_SECONDS`), so
  readers can tell "stale" apart from "nothing changed." Listens on `ranking:control`
  for mode-switch requests (`"volume"` | `"volatility"`) — last-write-wins on a
  near-simultaneous double switch. Also polls dYdX REST for 24h volume.
- **`volatility.py`** — `VolatilityTracker`: stddev of price/returns over a configurable
  lookback (default 1h), ranked cross-sectionally against all other subscribed coins.
- **`metrics_store.py`** — SQLite (`metrics.db`) persistence for ranking history:
  `write()`, `latest()`, `history()`, `nearest()`. This is what answers "how did this
  coin's rank evolve over time" (Story 1.4/FR8) — `ranking_engine` is the sole writer,
  `dashboard`/others read-only.

**Reads:** `snapshots:raw`, `ranking:control`.
**Publishes:** `rankings:live`.
**Writes:** `metrics.db`.

---

## 4. `live_paper/` — the actual trading bot

The one place in `troll/` where `TradingNode`/`Strategy` usage is sanctioned
(architecture AD-8 amendment) — everywhere else in `troll/` treats `nautilus_trader` as
a library only.

- **`strategy.py`** — `DummyStrategy`: wires all five `ml_signals.indicators` into a
  live `TradingNode` run. Explicitly framed as an integration proof, not a tuned alpha
  strategy — it proves every signal stays alive end-to-end from research through
  backtest through live paper trading. Feeds `Microprice`/`OrderFlowImbalance` from
  `QuoteTick`, `MultiLevelOBI`/`MultiLevelOFI` from a 1-second clock timer snapshotting
  `cache.order_book()` (matched to the 1s cadence the indicators were calibrated
  against in backtest), and `OnlineLogisticTrend` from `Bar` via INTERNAL aggregation.
  Has an `orders_inflight()` guard to avoid duplicate submissions while a fill is
  pending — flagged as still needing a concurrency test, see below.
- **`config.py`** — `PaperConfig` vs `RealMoneyConfig`: two structurally separate
  dataclasses/loaders, not one schema with a mode flag, specifically so a stray `mode`
  key in the default config can never silently promote to real money. Real-money mode
  requires setting `LIVE_PAPER_REAL_MONEY_CONFIG` to a distinct file path that
  `load_paper_config()` doesn't even know how to parse.
- **`node.py`** — builds and runs the `TradingNode`; branches on `RealMoneyConfig` vs
  `PaperConfig` to pick paper vs live execution clients.
- **`bot_status.py`** — publishes `bots:status` (PnL, position, mode, heartbeat) on a
  timer; subscribes to `bots:control` for `{bot_id, action: "start"|"stop"}` commands.
  The control channel deliberately never carries a paper/live mode field — that gate
  lives solely in `config.py`.

**Reads:** dYdX WS/HTTP (via `TradingNode`), `bots:control`.
**Publishes:** `bots:status`.
**Known gap:** trade/position history is in-memory only (no `CacheConfig(database=...)`
wired up yet) — restart loses it, and nothing outside the process can query it. This is
tracked as backlog (Story 4.6/4.7), not yet built.

---

## 5. `bot_tui/` — terminal UI

`urwid`-based, keyboard-only, k9s-style navigation. Not a daemon — launched via
`docker compose run` (or on-host), never `restart: always`, since it's an interactive
SSH-launched tool, not a background service.

- **`app.py`** — `MainLoop` wiring, breadcrumb header, footer hint bar, `:` command bar
  (`:coins`, `:bots`, `:q`), `esc` pop-back-one-level, `/` fuzzy-filter.
- **`coins_pane.py` / `ranking_state.py`** — live-mirrors the dashboard's ranking table
  by reading `rankings:live` directly (no local recomputation); `m` toggles ranking mode
  by publishing to `ranking:control`.
- **`coin_detail.py` / `coin_detail_state.py`** — drill-down view: live indicators via
  the shared `ml_signals.indicators` code path, order book collapsed-by-default /
  `d`-to-expand (up to 20 levels), reads `snapshots:raw` directly. `o` deep-links to the
  dashboard's chart for the same coin.
- **`bots_pane.py` / `bots_state.py`** — live bot list from `bots:status`, per-row stale
  badges (independent of the coins-pane staleness badge), `s` to start/stop by
  publishing `{bot_id, action}` to `bots:control` — footer echoes "sent," never assumes
  success (no optimistic UI state).
- **Bot-detail trades blotter + PnL chart** — designed (Story 4.7) but not built; blocked
  on `live_paper`'s Cache persistence gap above.

**Reads:** `rankings:live`, `snapshots:raw`, `bots:status`.
**Publishes:** `ranking:control`, `bots:control`.
**Never imports:** `live_paper` internals (control-plane only, per AD-10) or
`dydx_collector`/`ml_signals` stateful internals (pure/shared-type imports only).

---

## Redis channel reference

| Channel | Publisher(s) | Subscriber(s) | Payload |
|---|---|---|---|
| `snapshots:raw` | `dydx_collector` | `ranking_engine`, `data_api`, `bot_tui` | One `DydxSecondSnapshot`-shaped message per instrument per second |
| `rankings:live` | `ranking_engine` | `data_api`, `bot_tui` | `{mode, updated_at, ranks: [{instrument_id, rank, volume24h, volatility_score}]}`, on change + heartbeat |
| `ranking:control` | `data_api`, `bot_tui` | `ranking_engine` | Mode-switch request (`"volume"` \| `"volatility"`), last-write-wins |
| `bots:status` | `live_paper` | `bot_tui` | Per-bot PnL/position/mode/heartbeat, on a timer |
| `bots:control` | `bot_tui` | `live_paper` | `{bot_id, action: "start"|"stop"}` — never a mode field |

## Storage reference

| Store | Writer | Readers | Contents |
|---|---|---|---|
| Parquet catalog (`dydx_collector/catalog/`) | `dydx_collector` | `ml_signals`, `data_api`, backtests, Jupyter | Trades, order book deltas, bars, mark/index price, funding rate, instruments, `DydxOpenInterest` — Nautilus-native, zero-conversion |
| `metrics.db` (SQLite) | `ranking_engine` | `data_api` (read-only mount) | Historical ranking snapshots (Story 1.4/FR8) |
| Nautilus `Cache` (in-memory, `live_paper`) | `live_paper` | nobody external yet | Orders/positions/fills for the running bot — **not yet Redis-backed**, lost on restart |

---

## Deployment topology (`docker-compose.yml`)

All services bind `127.0.0.1` only / `network_mode: host` — nothing is reachable
without an SSH tunnel over Tailscale (see README's remote-access section).

| Service | Started by default? | Restart policy | Why |
|---|---|---|---|
| `redis` | yes (`make up`) | `always` | Shared bus, no state to lose |
| `collector` | yes | `always` | Core data path, must self-heal |
| `ranking_engine` | yes | `always` | Sole ranking computer |
| `data_api` | yes | `always` | Web UI (React SPA) + read-only FastAPI over the catalog/`metrics.db`, `:9100` |
| `dozzle` | yes | `always` | Log viewer, `:8080` |
| `live-paper` | **no** — `profiles: ["live-paper"]`, `make up-live-paper` | `on-failure:5` | Explicit opt-in per Story 3.1; capped restarts so a bad config doesn't crash-loop against dYdX's API |
| `bot_tui` | **no** — `profiles: ["tui"]`, `docker compose run` | n/a (one-shot) | Interactive tool, never a background daemon |

Two-image split: `nautilus-trader-base` (rebuilt rarely, `make build-base`, ~15 min) →
`collector.dockerfile` (thin layer, rebuilds in seconds) reused by collector,
ranking_engine, `data_api`, and bot_tui; `live_paper.dockerfile` is `live-paper`'s own
thin layer on the same base.

### Running the UI/`bot_tui` off the VPS

The whole web UI is one tunneled surface: `data_api` (`9100`). `bot_tui` additionally
needs live Redis (`6379`). Tunnel both over the SSH-over-Tailscale mechanism README.md's
"Remote access via Tailscale + SSH tunnel" section sets up:

```bash
ssh -N -L 6379:127.0.0.1:6379 -L 9100:127.0.0.1:9100 you@<vps-tailscale-ip>
```

Then open `http://localhost:9100` for the UI, and run
`REDIS_URL=redis://127.0.0.1:6379 make tui` for the terminal UI. No local catalog or
`metrics.db` is needed.
---

## What's genuinely not finished

Cross-referenced against `_bmad-output/implementation-artifacts/sprint-status.yaml`
(Epic 4 is `in-progress`) and open retro action items:

- **`DummyStrategy` is a wiring proof, not a tuned strategy** — by design, but means
  nothing here is validated to make money.
- **Story 4.6/4.7 (backlog)** — `live_paper` Cache persistence + TUI trades
  blotter/PnL chart. Blocks real trade-history visibility across restarts.
- **`orders_inflight()` race guard has no test** proving it holds under a real
  concurrent-fill race (open action item, epic-3 retro).
- **EXTERNAL vs INTERNAL bar aggregation choice unverified live** — INTERNAL was
  picked defensively, never confirmed better against a real dYdX connection (open
  action item, epic-3 retro).
- **Real-money path (`RealMoneyConfig`) exists and is gated but untested** — never
  exercised even once.
