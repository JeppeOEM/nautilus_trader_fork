# System Overview

Everything under `platform/` in one picture: what each module does, what data it owns, and
exactly how the pieces talk to each other (Redis channels, SQLite, Parquet, HTTP). This
is the "what got built" document — for setup/run instructions see `README.md`, for
coding rules see `CLAUDE.md`, for planning history see `_bmad-output/`.

---

## The one-paragraph version

Three collectors (dYdX, Bybit, Hyperliquid) share one venue-neutral engine
(`collector_core`): each pulls live market data straight off the Rust adapters and writes
it to a Nautilus-native Parquet catalog, publishing a live 1-second snapshot feed to
Redis as it goes. A ranking engine reads that feed, scores every coin by volume or volatility,
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
| `collector_core/` | The venue-neutral collector engine (Story 22.1) — ingest → 1s sample → flush → Parquet + `snapshots:raw` + the `SecondSink` port, and the capture lock `<catalog>/.capture-<VENUE>.lock` held for the process life (Story 25.1); the operator-run catalog tools moved to `archive/` in Story 25.1 (their old paths are deprecated re-exports) `[amended 2026-09-25: Story 25.1]` | Parquet catalog (read/write), Redis (publish), `collector_core/ports.py`'s `SecondSink` (the candle store, injected by the venue entrypoint) |
| `archive/` | The archive context (Story 25.1, DDD spine AD-D9/AD-D18): the nightly saga (`archive.nightly`: rebuild -> consolidate -> build_candles -> reconcile -> prune) over the `ArchiveDay` state machine, `RetentionPolicy` (the one deleter), `CatalogFiles` (the one in-place rewriter), and the operator CLIs `python -m archive.<tool>` | Parquet catalog (read/write, never today's files), `candles.application` (`VerifiedDays`, `queries`, `rebuild`), the venues' kline REST (`kernel.venue_http`), the dYdX plan file (read, prune only) |
| `dydx_collector/`, `bybit_collector/`, `hyperliquid_collector/` | Venue subclasses of `collector_core.Collector`: WS/HTTP client, config, venue quirks | Their venue's WS/REST (via Rust `nautilus_pyo3` clients) |
| `kernel/` | The shared kernel (Story 23.2, DDD spine AD-D3): the one copy of every type, fold, parser, constant, transport and read helper more than one context uses — `second_snapshot` (`DydxSecondSnapshot`, `SecondOHLC`), `open_interest`, `fold` (`fold_trades`), `indicators` (pure `Indicator`s and snapshot functions), `performance_metrics`, `venues` (the only `InstrumentId` parser: `venue_of`, `has_venue`, `venue_kind`, `market_kind`, `market_suffix`, `bybit_category`), `clocks` (`TwoClocks`, `CatalogFileSpan`, the one skew bound `MAX_TS_INIT_SKEW_NS` = 300 s), `archive_markers` (the `_archive_gaps/<iid>.jsonl` format), `venue_http` (every venue REST URL and request), `catalog_files` (read-only snapshot-file helpers), `parquet_compat` (the one zstd `write_table` default). Imports no context, holds no state, store, config loader or ledger call; every context may import it | venue REST endpoints (outbound GET/POST, via callers), the Parquet catalog (read-only) |
| `candles/` | The candles context (Story 24.1, DDD spine AD-D8): the one seconds → bars fold (`domain/fold.py`), the `CandleSeries` watermark aggregate, the query/forming/rebuild services, the retention process manager and `CandleStore` — the only read-write opener of a `candles_*.db`. Behind capture's `SecondSink` port, so nothing upstream imports it | Parquet catalog (read, the rebuild), `candles_*.db` (read/write) |
| `views/` | The views context (Story 24.2, DDD spine AD-D11): the read models both UIs show -- `ranking_columns` (the ranking table's columns, the Technicals tab's values), `coin_detail` (the single-coin metric set, the `snapshots:raw` decode via `DydxSecondSnapshot.from_dict`, the `metrics.db` history reads), `chart_series` (every chart page -- candles, Lines mode, indicator series/values -- plus book features, footprint and the gap-marker rendering rules), `indicator_picker` (the native + custom indicator catalogs and their dispatch), `preferences` (the one loader/saver of `chart_indicators.toml` and `screener_columns.toml`), `catalog_reads`, `live_candles` (`LiveCandleBus` and the `BarObserver` port) and `rankings_bus`. Framework-free, no module state; imports only `kernel`, `observability` and the candles/ranking query services. The reader never re-validates the capture gate | Parquet catalog (read), `candles_*.db` (read, via `candles.application.queries.open_store`), `metrics.db` (read), Redis (`snapshots:raw`, `rankings:live` subscribe, through the bus instances `data_api.buses` constructs) |
| `alerting/` | The alerting context (Story 24.3, DDD spine AD-D2/AD-D16): saved price alerts (`domain/`: `Alert`, `FiringPolicy`, the pure `evaluate`/`render`), `AlertEngine` (a structural `views.live_candles.BarObserver`, evaluated on the forming bar the chart draws for every pair an active alert watches, chart open or not) and `AlertService` (the `/api/alerts` use cases) in `application/`, and the `AlertStore`/`NotifyDeliverer` adapters in `infrastructure/`, constructed only by `data_api/alert_wiring.py`. An alert names channels, never transports. Imports only `kernel`, `observability`, stdlib and `tomli_w`; no module state | `alerts.toml` (read/write), webhook / Telegram URLs (outbound HTTP POST, through `observability.notify`) |
| `research/` | The research context (Story 24.4, DDD spine AD-D1 research row): a pure consumer with no aggregates -- the backtest strategies and runners (`strategies/`, referenced by `ImportableStrategyConfig` string path `research.strategies.<module>:<Class>`), `run_backtest.py`, the watchlist client (`watchlist.py`), the notebooks (`notebooks/`) and `BACKTESTING.md`. Reads market-data rows only through `kernel.catalog_files` or `BacktestDataConfig` (`research/tests/test_research_reads.py`), the live coin-set only over HTTP, and computes no rolling metric (pct-change and volatility are ranking's). In-repo it imports only `kernel` and `observability` (beside stdlib, `nautilus_trader` and pandas) | Parquet catalog (read; `snapshot_backtest` writes only a throwaway catalog in a temp dir), `data_api` `/api/rankings` (HTTP GET) |
| `observability/` | The generic observability context (Story 23.1, DDD spine AD-D16), standard library only and venue-free: `error_ledger` (every continue-past-failure site, DATA-07; in-memory per process, plus a durable per-service `<service>.jsonl` sink behind the same `record()` call, Story 23.3), `notify` (the one outbound transport: channels `operator` = ntfy/`WATCHDOG_NTFY_URL`, `telegram` = `TELEGRAM_*`, `webhook:<url>`), `watchdog` (the generic `(down_since, reminder)` alert transition), `incidents` (the WARNING+ incident-report handler, parameterised by the venue entrypoint's `IncidentConfig`). Every context except `kernel` may import it (spine AD-D2); it imports none | ntfy / Telegram / webhook URLs (outbound HTTP POST), `data/incident_reports/` (write, dYdX collector only), `data/errors/*.jsonl` (write, every service; Story 23.3) |
| `ml_signals/` | The ranking math `ranking_engine` still imports (`metrics_computer`, `catalog_stats`' price stats); the candle store moved to `candles/` in Story 24.1, the UI read models to `views/` in Story 24.2 and the backtest strategies, runners, watchlist client and notebooks to `research/` in Story 24.4 -- `ml_signals.{strategies.*,watchlist,run_backtest}` are deprecated re-exports (the Story 24.2 `ml_signals.{ranking_columns,screener_columns_config,chart_indicators,chart_indicator_config,custom_indicators,book_features,footprint,chart_data}` shims were deleted in Story 24.4, the Story 24.1 `ml_signals.{candle_store,candles}` shims were deleted in Story 24.3) `[amended 2026-09-25: Story 24.4]` | Parquet catalog (read), Redis (`snapshots:raw`, `rankings:live` read), `metrics.db` (read) `[amended 2026-09-25: Story 24.1 — no `candles_*.db` read remains: the store moved to `candles/` and the only `ml_signals` reference is the dead re-export shim, which `tests/test_namespace.py` proves nothing imports]` `[amended 2026-09-20: Epic 22 story 22.8, review pass — `ranking:control` publish removed: the sole producer is `bot_tui/ranking_state.py:128` since Story 15.10 retired `dashboard`]` |
| `data_api/` + `frontend/` | Web UI (React SPA) + read-only REST/WS on `:9100`. Format + transport only since Story 24.2: every value comes from `views/`; `data_api/buses.py` constructs the two Redis bus instances, `data_api/alert_wiring.py` the alerting instances (Story 24.3), and `app.py`'s lifespan attaches the alert engine to the live-candle bus -- the only such wiring; `routes/alerts.py` is a thin adapter over `alerting.application` (the deprecated `data_api/alerts.py` re-export was deleted in Story 25.1) `[amended 2026-09-25: Story 25.1]` | Redis (read), Parquet catalog + `candles_*.db` + `metrics.db` (read-only, through `views/`), `alerts.toml` (through `alerting/`) |
| `ranking_engine/` | Sole computer of coin ranking (volume + volatility) | Redis (`snapshots:raw` read; `rankings:live` publish; `ranking:control` read), `metrics.db` (write), dYdX REST (24h volume poll) |
| `live_paper/` | The actual trading bot — `TradingNode` + `Strategy` in paper (or gated real-money) mode | dYdX WS/HTTP (via `TradingNode`), Redis (`bots:status` publish, `bots:control` read) |
| `bot_tui/` | Keyboard-only terminal UI, interactive/on-demand. Format + transport only since Story 24.2: the coin-detail metric set, the rank-row lookup, the `snapshots:raw` decode and the ranking columns come from `views/` | Redis (`rankings:live`, `snapshots:raw`, `bots:status` read; `ranking:control`, `bots:control` publish), dashboard (HTTP deep-link only) |

Module boundary rule enforced throughout (architecture AD-4): every module downstream
of the collector depends only on shared data types (`DydxSecondSnapshot`,
`OpenInterest`, `kernel.indicators` classes) and Redis/HTTP contracts — never
another module's internal state. The collectors are *supposed* not to import from
anything downstream of them, and that is now almost true. It pre-dates Epic 22 and was tracked as
an open boundary question in the spine's Deferred section, not as a resolved rule
`[amended 2026-09-20: Epic 22 story 22.8, review pass — this sentence asserted the clean
version of the very clause AD-4 was amended to retract in the same commit]`.
Three of the four writer -> reader imports are gone. The ledger moved to `observability/`
(Story 23.1), which every context except `kernel` may import. The shared types, the fold, the
clocks (`_stamp_to_ns` is now `kernel.clocks.CatalogFileSpan`) and the catalog read helpers
(`kernel.catalog_files`) moved to `kernel/` in Story 23.2. And the candle store became its own
context in Story 24.1: `collector_core/collector.py` declares a `SecondSink` port
(`collector_core/ports.py`), each venue entrypoint injects `candles.application.sink.CandleSink`,
and the two archive tools take a `VerifiedDays` port instead of a database connection, so no
capture -> candles import exists at all `[amended 2026-09-25: Story 24.1]`.
The last one -- `repair_catalog`'s `views.catalog_reads.query_second_snapshots` -- is gone too:
the tool moved to `archive/` and reads with its own `ParquetDataCatalog.query` (Story 25.1), and
the dYdX collector's `prune_catalog` call went with its `_prune_loop`, so `test_boundaries.py`
lists neither the capture -> archive nor the archive -> views edge any more `[amended 2026-09-25:
Story 25.1]`.

**Readers of the chart data** (Story 24.2): the web UI's chart, its indicator panes and the
Technicals tab read through `views.chart_series`/`views.ranking_columns`; `bot_tui`'s coin detail
through `views.coin_detail`; the archive tools through `views.catalog_reads` until their own
story, and research through `kernel.catalog_files` (`query_top_of_book`, `query_second_ohlc`) or
`BacktestDataConfig` `[amended 2026-09-25: Story 24.4]`. None of them re-validates what the capture gate wrote: the reader-side
empty-top and crossed-book skips `data_api/routes/snapshots.py` used to apply (the parent spine's
AD-3 deviation) are deleted -- a crossed second now renders, an empty-top second fails its request
loudly (`views.snapshot_without_top` in the error ledger), and the only thing that changes what
is drawn is the gap-marker rendering rule in `views.chart_series` (the legacy
`compute_chart_series` behind `/catalog/chart-series` included).

---

## Data flow

<!-- [amended 2026-09-20: Epic 22 story 22.8, review pass] the diagram was still the
     single-venue system, contradicting this file's own three-collector summary above. -->
```
dYdX WS/REST      Bybit WS/REST      Hyperliquid WS
(Rust nautilus_pyo3 clients, one duck-typed client.py per venue)
        │                 │                 │
        └─────────────────┴─────────────────┘
        │
        ▼
  collector_core.Collector  ─────► Parquet catalog (Nautilus-native, zero-conversion)
  (one write gate; DydxCollector /                one shared catalog root
   BybitCollector / HyperliquidCollector)
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

## 1. `collector_core/` + the venue collectors — the data source

Three standalone asyncio services (one per venue) over one shared engine. All bypass
`TradingNode`/`Strategy`/`DataEngine` entirely and talk directly to the Rust
`nautilus_pyo3` WS/HTTP clients — this is deliberate: `DataEngine` has a documented
unbounded-queue-growth bug under sustained load that OOM-crashed an earlier
`Strategy`-based recorder.

- **`collector_core/collector.py`** — owns the asyncio loop, buffer, and flush timer for
  every venue. Every 1s builds a `DydxSecondSnapshot` per subscribed instrument (top-20
  bid/ask levels + per-side trade volume — nothing derivable is stored; the name is
  historical, the schema is venue-neutral) and publishes it to Redis channel
  `snapshots:raw`. Flushes trades/deltas/bars/mark-index-funding/instruments to the
  Parquet catalog via `ParquetDataCatalog.write_data()` on `flush_interval_seconds`, and hands
  the rows that were actually written to its `SecondSink` port — the candle store, injected by
  the venue entrypoint (Story 24.1), so a bar can never be ahead of the archive.
- **`kernel/second_snapshot.py`** — defines `DydxSecondSnapshot(Data)`, the one
  custom Arrow-registered type this whole system is built around (Story 23.2 moved it from
  `collector_core/`; the old path is a deprecated re-export).
- **`kernel.second_snapshot.ohlc_outside_book()`** (was `collector_core/integrity.py` until
  Story 25.1) — a second's trade high/low must lie inside that same second's own book. Live ERROR
  canary and offline detector (DATA-06).
- **Operator-run catalog tools** (`python -m archive.<tool>` since Story 25.1 -- the
  `collector_core.<tool>` paths are deprecated re-exports -- never automatic; the candle rebuild
  is `python -m candles.rebuild` since Story 24.1):
  `candles.rebuild` (rebuild a candle store from raw 1s), `consolidate_catalog`
  (`make consolidate`, nightly, every venue; `make backup-catalog` syncs the result off-box),
  `repair_catalog` (clear impossible trade OHLC; never on a day `rebuild_seconds` rebuilt),
  `migrate_open_interest` (one-shot layout migration). Story 22.13: `kernel.fold` (the one exact
  trades -> second fold, live and rebuild), `rebuild_seconds` (a closed day's trade columns from
  the raw `trade_tick` archive, on `ts_event`), `compare_klines` (1 m bars vs the venue's klines,
  exact, into `verified_days`, only for what the same saga run rebuilt), `prune_catalog` (age
  retention + verification-gated trade retention + the dYdX plan's dropped-instrument and delta
  retention; `make prune`) and `nightly` (`make nightly VENUE=...`: rebuild -> consolidate ->
  build_candles -> compare -> prune).
- **`{dydx,bybit,hyperliquid}_collector/`** — per-venue `Collector` subclass, `client.py`
  (thin wrapper around that venue's Rust clients) and `config.py`. dYdX-only:
  `client.py`'s `_at_fixed_precision()` re-stamps mark/index prices to a single precision
  (dYdX's feed derives precision from each tick's own trailing-zero count, which corrupts
  catalog writes if left alone) and `uncross.py` resolves crossed books (DATA-04).
- **`dydx_collector/open_interest.py`** — `classify_liquidity()` + the dYdX REST poll (the
  shared `OpenInterest(Data)` type lives in `kernel/open_interest.py`); open
  interest is the one field the Rust bindings drop, so it's polled separately via
  `kernel.venue_http` against dYdX's indexer REST endpoint every 5 min.
- **Hot-reload**: `config.toml`'s instrument list is re-read every `config_reload_seconds`
  — no restart needed to add/remove a coin.

**Publishes:** `snapshots:raw` (Redis pub/sub, one message per instrument per second).
**Writes:** the Parquet catalog at `data/catalog/` (shared by all three venues,
Story 19.2) and `data/candles/candles_{dydx,bybit,hyperliquid}.db`.

**Known benign WARN log lines** (from `nautilus_network::websocket::client`, seen via
`troll-logs`/Dozzle):
- `Connection closed by peer (no close frame), terminating` — dYdX's socket hung up
  without a WS close handshake (server restart, LB cycling the connection, network
  blip). The read loop breaks and the client's normal reconnect/resubscribe logic
  takes over automatically — expected, not a bug, as long as reconnect follows.
- `Received close frame, terminating: code=..., reason='...'` — same situation but a
  *graceful* close (peer sent a proper WS close frame first).

---

## 2. `ml_signals/` — shared signals, dashboard (backtesting moved to `research/`, 2b)

Indicators are implemented once, in the shared kernel (below) — everything imports them from
there, never reimplements; what remains in `ml_signals` is ranking's math and the research shims.

- **`kernel/indicators.py`** (moved from `ml_signals/indicators.py` in Story 23.2) — five
  Nautilus `Indicator` subclasses, each used identically in Jupyter, backtest, and live
  (`live_paper`):
  - `Microprice` — size-weighted mid from top-of-book
  - `OrderFlowImbalance` — top-of-book OFI
  - `MultiLevelOBI` — N-level order book imbalance
  - `MultiLevelOFI` — N-level order flow imbalance, replayed across snapshots
  - `OnlineLogisticTrend` — online-updating trend classifier fed from bars
- **`book_features.py` / `footprint.py` / `chart_data.py`** — gone: the derived-view helpers
  (spread, microprice, footprint charts) moved to `views/chart_series.py` in Story 24.2 and their
  re-export shims were deleted in Story 24.4 — none of it is stored, all computed on read per
  `platform/CLAUDE.md`'s 1s-based signal architecture rule.
- **`strategies/`, `watchlist.py`, `run_backtest.py`** — deprecated re-exports since Story 24.4:
  the backtest strategies, runners and watchlist client live in `research/` (section 2b).
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
UI is now the React SPA in `platform/frontend/` (Rankings, Chart, 31-day History, Docs),
served by the `data_api` FastAPI app on `:9100` (`127.0.0.1` only) alongside its REST +
WebSocket API: `/api/rankings`, `/api/candles/{id}`, `/api/snapshots/{id}`,
`/api/indicator-series/{id}`, `/api/coin/{id}/indicators`, `/api/metrics/history|nearest/{symbol}`,
and `/ws/live`. `data_api` reads the catalog/`metrics.db` read-only, and only through the
`views/` read models (Story 24.2): its routes clamp parameters, pass their env-derived paths in,
build the response models and map views' exceptions to HTTP codes. Dropped without a
React equivalent: the `/debug` state dump and the server-rendered `/live` table.

---

## 2b. `research/` — backtests, the watchlist client, notebooks

A pure consumer (Story 24.4): it reads the catalog, ranking's published output and the rankings
API, and computes nothing another context owns `[amended 2026-09-25: Story 24.4 -- moved from
`ml_signals/` and `dydx_collector/notebooks/`]`.

- **`watchlist.py`** — `fetch_watchlist()` reads `data_api`'s `/api/rankings` to get the live, ranked coin set — this is how a backtest gets a dynamic instrument
  universe instead of a hardcoded list. HTTP only: `research` never imports `data_api`.
- **`strategies/backtest_dydx.py` / `strategies/backtest_ofi.py` / `strategies/backtest_snapshot.py`** — `BacktestNode` +
  `BacktestDataConfig` runs (no custom matching engine anywhere). `strategies/backtest_dydx.py`
  defaults to backtesting every coin in the live Watchlist, keyed results per symbol.
  `strategies/snapshot_backtest.py` (behind `backtest_ofi.py` and `run_backtest.py`) seeds the
  simulated exchange's quotes from `kernel.catalog_files.query_top_of_book` into a throwaway
  catalog.
- **`strategies/example_strategy.py` / `strategies/ofi_strategy.py` / `strategies/snapshot_strategy.py`** — backtest-only
  reference strategies, referenced via `ImportableStrategyConfig` by string path
  (`research.strategies.<module>:<Class>`).
- **`notebooks/`** — `backtest.ipynb`, `dydx_catalog_pandas.ipynb` and
  `candlestick_pattern_scanner.ipynb` (a `Known limit:` notebook over the retired minute-bar
  directory, replaced by Story 27.7). `BACKTESTING.md` is the how-to.

**Reads:** Parquet catalog (market-data rows only via `kernel.catalog_files` or
`BacktestDataConfig`, enforced by `research/tests/test_research_reads.py`), `data_api`
`/api/rankings` (HTTP). **Publishes:** nothing.

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

The one place in `platform/` where `TradingNode`/`Strategy` usage is sanctioned
(architecture AD-8 amendment) — everywhere else in `platform/` treats `nautilus_trader` as
a library only.

- **`strategy.py`** — `DummyStrategy`: wires all five `kernel.indicators` into a
  live `TradingNode` run. Explicitly framed as an integration proof, not a tuned alpha
  strategy — it proves every signal stays alive end-to-end from research through
  backtest through live paper trading. Feeds `Microprice`/`OrderFlowImbalance` from
  `QuoteTick`, `MultiLevelOBI`/`MultiLevelOFI` from a 1-second clock timer snapshotting
  `cache.order_book()` (matched to the 1s cadence the indicators were calibrated
  against in backtest), and `OnlineLogisticTrend` from `Bar` via INTERNAL aggregation.
  Has an `orders_inflight()` guard to avoid duplicate submissions while a fill is
  pending — flagged as still needing a concurrency test, see below.
- **`config.py`** — `PaperConfig` vs `ExecConfig`: two structurally separate
  dataclasses/loaders, not one schema with a mode flag, specifically so a stray `mode`
  key in the default config can never silently promote to real money. Real-money mode
  requires setting `LIVE_PAPER_REAL_MONEY_CONFIG` to a distinct file path that
  `load_paper_config()` doesn't even know how to parse.
- **`node.py`** — builds and runs the `TradingNode`; branches on `ExecConfig` vs
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
  the shared `kernel.indicators` code path, order book collapsed-by-default /
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
| `snapshots:raw` | the three collectors | `ranking_engine`, `data_api`, `bot_tui` | One `DydxSecondSnapshot`-shaped message per instrument per second |
| `rankings:live` | `ranking_engine` | `data_api`, `bot_tui` | `{mode, updated_at, ranks: [{instrument_id, rank, volume24h, volatility_score}]}`, on change + heartbeat |
| `ranking:control` | `data_api`, `bot_tui` | `ranking_engine` | Mode-switch request (`"volume"` \| `"volatility"`), last-write-wins |
| `bots:status` | `live_paper` | `bot_tui` | Per-bot PnL/position/mode/heartbeat, on a timer |
| `bots:control` | `bot_tui` | `live_paper` | `{bot_id, action: "start"` \| `"stop"}` — never a mode field |

## Storage reference

| Store | Writer | Readers | Contents |
|---|---|---|---|
| Parquet catalog (`data/catalog/`) | all three collectors | `ml_signals`, `data_api`, `research` (backtests, notebooks) `[amended 2026-09-25: Story 24.4]` | Second-snapshots (`DydxSecondSnapshot`, trades folded in rather than stored raw — audit D-45), mark/index price, funding rate, `OpenInterest`, instrument definitions, plus `order_book_deltas` for the dYdX instruments that opt in, and the Story 22.13 raw `trade_tick/` archive (pruned nightly by `archive.prune_catalog --trade-retention-days 7`; `docs/DATA_DICTIONARY.md` §1.1). Minute bars retired 2026-09-20 (D-35). Nautilus-native, zero-conversion `[amended 2026-09-20: Epic 22 story 22.8, review pass]` |
| `candles_{dydx,bybit,hyperliquid}.db` (SQLite, `data/candles/`) | that venue's collector (through the `SecondSink` port its entrypoint injects, Story 24.1 — the collector core no longer opens the file), plus `compare_klines` for the `verified_days` table | `data_api`, `prune_catalog` (via the `VerifiedDays` port) | Finished 1m..1D bars derived from raw 1s (D-35), plus the `verified_days` day-status table the archive tools reach through the `VerifiedDays` port. Fully rebuildable: `python -m candles.rebuild` |
| `metrics.db` (SQLite) | `ranking_engine` | `data_api` (read-only mount) | Historical ranking snapshots (Story 1.4/FR8) |
| Nautilus `Cache` (in-memory, `live_paper`) | `live_paper` | nobody external yet | Orders/positions/fills for the running bot — **not yet Redis-backed**, lost on restart |
| `data/errors/<service>.jsonl` (JSON lines, Story 23.3) | that service (`collector`, `bybit_collector`, `hyperliquid_collector`, `ranking_engine`, `data_api`, `live-paper`, `bot_tui`) | `data_api` (`GET /api/errors`'s `services` block), `archive.crosscheck_errors` | Every `observability.error_ledger.record()` call, durably: `ts_ns`, `service`, `pid`, `site`, `detail`, `exc_type`, `suppressed`; plus one `process_start` line per boot. Rotates by size (`.1`..`.N`, default 20 MB, 10 backups kept alongside the live file); at most 60 lines/site/minute, exact `suppressed` carry |

---

## Deployment topology (`docker-compose.yml`)

All services bind `127.0.0.1` only / `network_mode: host` — nothing is reachable
without an SSH tunnel over Tailscale (see README's remote-access section).

| Service | Started by default? | Restart policy | Why |
|---|---|---|---|
| `redis` | yes (`make up`) | `always` | Shared bus, no state to lose |
| `collector` (dYdX) | yes | `always` | Core data path, must self-heal |
| `bybit_collector`, `hyperliquid_collector` | yes | `always` | Same engine, same catalog, other venues (Epic 22) |
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

## Target structure (DDD)

The module map above is the *current* layout. The target layout is a Domain-Driven Design
projection of the same Gatekeeper paradigm, fixed in the DDD spine
`_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`
(seed and decision record: `_bmad-output/planning-artifacts/ddd-redesign-seed-2026-09-21.md`).
It keeps the 2026-07-01 spine's AD-1..AD-11 unchanged and adds AD-D1..AD-D18: eleven bounded
contexts (`capture`, `collection_control`, `archive`, `candles`, `ranking`, `bots`, `alerting`,
`research`, `views`, `observability`) over one shared `kernel`, hexagonal layering inside each,
a boundary test instead of review as the enforcement, and a strangler migration one context per
story. Migration step 0 renames this directory `platform/` → `platform/` (after story 22.12 merges);
until a context's story lands, this file's map stays authoritative for it.

The migration is enforced by three guards in `platform/tests/`, run by `make test` against the
read-only checkout mount (Story 23.1): `test_boundaries.py` maps every module to its target
context and fails any import outside the AD-D2 graph or any `_private` import across contexts,
except the legacy edges it lists with the story that retires each (expired from
`sprint-status.yaml`); `test_images.py` fails when a dockerfile's `COPY` set misses a package an
entrypoint imports; `test_hotpath.py` replays a 30-instrument burst through
`Collector._process_data` against `tests/fixtures/hotpath_baseline.json` (AD-D5; the baseline is
recorded into the checkout by `make hotpath-baseline` only, and a missing one fails `make test`).

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
- **Real-money path (`ExecConfig`, `mode = "real_money"`) exists and is gated but untested** — never
  exercised even once.
