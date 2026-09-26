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
and publishes the result back to Redis. A web dashboard reads those two Redis feeds (never
recomputing anything itself) to show live charts, the rankings and the ranking-mode switch; a
terminal UI controls the bots and the collector (rankings are web-only since Story 25.1a). Indicators written once in the shared `kernel` get reused unmodified in Jupyter
research, Nautilus backtests, and a live paper-trading bot (the `bots` context), which
publishes its own status to Redis so the TUI can monitor and start/stop it. Nothing
downstream of the collector ever touches `nautilus_trader`'s live `TradingNode`/
`DataEngine` except `bots` — that's the one sanctioned exception, and inside it only
`bots/infrastructure/nautilus_host.py` imports `TradingNode`.

---

## Module map

| Module | Role | Talks to |
|---|---|---|
| `collector_core/` | The venue-neutral collector engine (Story 22.1) — ingest → 1s sample → flush → Parquet + `snapshots:raw` + the `SecondSink` port, and the capture lock `<catalog>/.capture-<VENUE>.lock` held for the process life (Story 25.1); the operator-run catalog tools moved to `archive/` in Story 25.1 (their old paths are deprecated re-exports) `[amended 2026-09-25: Story 25.1]` | Parquet catalog (read/write), Redis (publish), `collector_core/ports.py`'s `SecondSink` (the candle store, injected by the venue entrypoint) |
| `archive/` | The archive context (Story 25.1, DDD spine AD-D9/AD-D18): the nightly saga (`archive.nightly`: rebuild -> consolidate -> build_candles -> reconcile -> prune) over the `ArchiveDay` state machine, `RetentionPolicy` (the one deleter), `CatalogFiles` (the one in-place rewriter), and the operator CLIs `python -m archive.<tool>` | Parquet catalog (read/write, never today's files), `candles.application` (`VerifiedDays`, `queries`, `rebuild`), the venues' kline REST (`kernel.venue_http`), the dYdX plan file (read, prune only) |
| `dydx_collector/`, `bybit_collector/`, `hyperliquid_collector/` | Venue subclasses of `collector_core.Collector`: WS/HTTP client, venue quirks, and each entrypoint's `build_collector` (the composition root: config + plan through `collector_core.config`'s one loader, `load_venue_config`). `Collector.apply(diff) -> Applied` makes the applied set the fact: the sampler, watchdog, cross-check and backfill iterate `applied ∩ plan`, and a book/trade message outside it is counted (`collector.unplanned_message`), never archived (Story 25.4) `[amended 2026-09-26: Story 25.4]` | Their venue's WS/REST (via Rust `nautilus_pyo3` clients) |
| `collection_control/` | The collection-control context (Story 25.4, DDD spine AD-D17): the plan is the intent, capture's applied set the fact. `CollectionPlan` (`domain/`: instruments, `exclude`, `cap` = 30 for dYdX, the liquidity threshold, the dropped-instrument retention; commands `add`/`remove`/`pin`/`unpin`/`exclude`/`reload` return a `PlanDiff`), the pure `classify_liquidity` that alone admits a pin; `ControlService` (`collector:control`: save, then apply, then publish), `StatusPublisher` (`collector:status`, `pending` for a planned-but-unapplied id) and the plan-file `reload_loop` in `application/`; `TomlPlanStore`, the Redis bus/channel and `DydxMarkets` in `infrastructure/`. Wired into the dYdX collector's `extra_loops` by `dydx_collector.collector.build_collector`; no module state, deletes nothing | Redis (`collector:status` publish, `collector:control` read), the dYdX plan file `data/dydx_config.toml` (read/write), dYdX indexer `perpetualMarkets` (through `kernel.venue_http`) |
| `kernel/` | The shared kernel (Story 23.2, DDD spine AD-D3): the one copy of every type, fold, parser, constant, transport and read helper more than one context uses — `second_snapshot` (`DydxSecondSnapshot`, `SecondOHLC`), `open_interest`, `fold` (`fold_trades`), `indicators` (pure `Indicator`s and snapshot functions), `performance_metrics`, `venues` (the only `InstrumentId` parser: `venue_of`, `has_venue`, `venue_kind`, `market_kind`, `market_suffix`, `bybit_category`), `clocks` (`TwoClocks`, `CatalogFileSpan`, the one skew bound `MAX_TS_INIT_SKEW_NS` = 300 s), `archive_markers` (the `_archive_gaps/<iid>.jsonl` format), `venue_http` (every venue REST URL and request), `catalog_files` (read-only snapshot-file helpers), `parquet_compat` (the one zstd `write_table` default). Imports no context, holds no state, store, config loader or ledger call; every context may import it | venue REST endpoints (outbound GET/POST, via callers), the Parquet catalog (read-only) |
| `candles/` | The candles context (Story 24.1, DDD spine AD-D8): the one seconds → bars fold (`domain/fold.py`), the `CandleSeries` watermark aggregate, the query/forming/rebuild services, the retention process manager and `CandleStore` — the only read-write opener of a `candles_*.db`. Behind capture's `SecondSink` port, so nothing upstream imports it | Parquet catalog (read, the rebuild), `candles_*.db` (read/write) |
| `views/` | The views context (Story 24.2, DDD spine AD-D11): the read models both UIs show -- `ranking_columns` (the ranking table's columns, the Technicals tab's values), `coin_detail` (the single-coin metric set, the `snapshots:raw` decode via `DydxSecondSnapshot.from_dict`, the `metrics.db` history reads), `chart_series` (every chart page -- candles, Lines mode, indicator series/values -- plus book features, footprint and the gap-marker rendering rules), `indicator_picker` (the native + custom indicator catalogs and their dispatch), `preferences` (the one loader/saver of `chart_indicators.toml` and `screener_columns.toml`), `catalog_reads`, `live_candles` (`LiveCandleBus` and the `BarObserver` port) and `rankings_bus`. Framework-free, no module state; imports only `kernel`, `observability` and the candles/ranking query services. The reader never re-validates the capture gate | Parquet catalog (read), `candles_*.db` (read, via `candles.application.queries.open_store`), `metrics.db` (read), Redis (`snapshots:raw`, `rankings:live` subscribe, through the bus instances `data_api.buses` constructs) |
| `alerting/` | The alerting context (Story 24.3, DDD spine AD-D2/AD-D16): saved price alerts (`domain/`: `Alert`, `FiringPolicy`, the pure `evaluate`/`render`), `AlertEngine` (a structural `views.live_candles.BarObserver`, evaluated on the forming bar the chart draws for every pair an active alert watches, chart open or not) and `AlertService` (the `/api/alerts` use cases) in `application/`, and the `AlertStore`/`NotifyDeliverer` adapters in `infrastructure/`, constructed only by `data_api/alert_wiring.py`. An alert names channels, never transports. Imports only `kernel`, `observability`, stdlib and `tomli_w`; no module state | `alerts.toml` (read/write), webhook / Telegram URLs (outbound HTTP POST, through `observability.notify`) |
| `research/` | The research context (Story 24.4, DDD spine AD-D1 research row): a pure consumer with no aggregates -- the backtest strategies and runners (`strategies/`, referenced by `ImportableStrategyConfig` string path `research.strategies.<module>:<Class>`), `run_backtest.py`, the watchlist client (`watchlist.py`), the notebooks (`notebooks/`) and `BACKTESTING.md`. Reads market-data rows only through `kernel.catalog_files` or `BacktestDataConfig` (`research/tests/test_research_reads.py`), the live coin-set only over HTTP, and computes no rolling metric (pct-change and volatility are ranking's). In-repo it imports only `kernel` and `observability` (beside stdlib, `nautilus_trader` and pandas) | Parquet catalog (read; `snapshot_backtest` writes only a throwaway catalog in a temp dir), `data_api` `/api/rankings` (HTTP GET) |
| `observability/` | The generic observability context (Story 23.1, DDD spine AD-D16), standard library only and venue-free: `error_ledger` (every continue-past-failure site, DATA-07; in-memory per process, plus a durable per-service `<service>.jsonl` sink behind the same `record()` call, Story 23.3), `notify` (the one outbound transport: channels `operator` = ntfy/`WATCHDOG_NTFY_URL`, `telegram` = `TELEGRAM_*`, `webhook:<url>`), `watchdog` (the generic `(down_since, reminder)` alert transition), `incidents` (the WARNING+ incident-report handler, parameterised by the venue entrypoint's `IncidentConfig`). Every context except `kernel` may import it (spine AD-D2); it imports none | ntfy / Telegram / webhook URLs (outbound HTTP POST), `data/incident_reports/` (write, dYdX collector only), `data/errors/*.jsonl` (write, every service; Story 23.3) |
| `data_api/` + `frontend/` | Web UI (React SPA) + REST/WS on `:9100`, read-only except the saved preferences/alerts and the ranking-mode switch. Format + transport only since Story 24.2: every value comes from `views/`; `data_api/buses.py` constructs the two Redis bus instances, `data_api/alert_wiring.py` the alerting instances (Story 24.3), and `app.py`'s lifespan attaches the alert engine to the live-candle bus -- the only such wiring; `routes/alerts.py` is a thin adapter over `alerting.application` (the deprecated `data_api/alerts.py` re-export was deleted in Story 25.1) `[amended 2026-09-25: Story 25.1]` | Redis (read; `ranking:control` publish from `PUT /api/rankings/mode`, Story 25.1a), Parquet catalog + `candles_*.db` + `metrics.db` (read-only, through `views/`), `alerts.toml` (through `alerting/`) |
| `ranking/` | The ranking context (Story 25.2, DDD spine AD-D10): sole computer of coin ranking (volume + volatility) and of the pct-change/volatility math. `RankingBoard` (`domain/`) owns the mode, one `InstrumentMetrics` per instrument, the volume book and the publisher; `RankingEngine` (`application/`) drives it through the `VolumeSource`/`PriceHistory`/`RankingHistory`/`LivePublisher` ports, whose adapters (`infrastructure/`) only `__main__` wires; `application/queries.py` (`history`/`nearest`) is the `metrics.db` read service views calls. No module-level state. Runs as `python3 -m ranking` (compose service `ranking_engine`); the `ranking_engine/` re-export shims were deleted in Story 25.4 `[amended 2026-09-26: Story 25.2]` `[amended 2026-09-26: Story 25.4]` | Redis (`snapshots:raw` read; `rankings:live` publish; `ranking:control` read), `metrics.db` (write), Parquet catalog (read, one-time price backfill), dYdX/Bybit/Hyperliquid REST (24h USD volume poll, through `kernel.venue_http`) |
| `bots/` | The bots context (Story 25.3, DDD spine AD-D15; was `live_paper/`, now a deprecated re-export shim until Story 26.1): the actual trading bots — one `TradingNode` + one `DummyStrategy` per bot, in paper (or gated demo/real-money) mode. `PaperFleet`/`ExecBot` make the paper/non-paper split a type, `Bot` holds the incident log, `FillLedger` the per-fill PnL; `nautilus_host.py` is the only `TradingNode` importer. Runs as `python3 -m bots` (compose service `live-paper`) `[amended 2026-09-26: Story 25.3]` | Venue WS/HTTP (via `TradingNode`), Redis (`bots:status` publish, `bots:history:*`/`bots:incidents:*` set, `bots:control` read; the Nautilus Cache), `fills.db` (write) |
| `bot_tui/` | Keyboard-only terminal UI, interactive/on-demand: the control surface for the bots and the collector (two panes, Bots and Collector). Rankings, the ranking-mode switch and the single-coin view are web-only since Story 25.1a, so it imports no `views/` read model `[amended 2026-09-26: Story 25.1a]` | Redis (`bots:*` and `collector:status` read; `bots:control`, `collector:control` publish), dashboard (HTTP deep-link only) |

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
Technicals tab read through `views.chart_series`/`views.ranking_columns` (`bot_tui`'s coin detail,
which read `views.coin_detail`, was deleted in Story 25.1a); the archive tools through `views.catalog_reads` until their own
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
        ├──► data_api (web UI + REST, :9100)      — reads snapshots:raw + rankings:live;
        │                                            writes ranking:control (mode switch)
        │
        ├──► bot_tui (terminal, on-demand)        — reads bots:* + collector:status;
        │                                            writes bots:control + collector:control
        │
        └──► bots (TradingNode, paper/live)       — writes bots:status; reads bots:control
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
  (thin wrapper around that venue's Rust clients) and the `build_collector` composition root.
  Every venue's `config.toml` goes through the one loader `collector_core.config.load_venue_config`
  (a frozen `VENUE_SCHEMAS` row per venue; an unknown key refuses start), which returns the
  thresholds plus the venue's `CollectionPlan` (Story 25.4). dYdX-only:
  `client.py`'s `_at_fixed_precision()` re-stamps mark/index prices to a single precision
  (dYdX's feed derives precision from each tick's own trailing-zero count, which corrupts
  catalog writes if left alone) and `uncross.py` resolves crossed books (DATA-04).
- **`dydx_collector/open_interest.py`** — the dYdX REST poll (the
  shared `OpenInterest(Data)` type lives in `kernel/open_interest.py`); open
  interest is the one field the Rust bindings drop, so it's polled separately via
  `kernel.venue_http` against dYdX's indexer REST endpoint every 5 min. `classify_liquidity`
  moved to `collection_control.domain.liquidity` (Story 25.4; served here, deprecated).
- **The applied set (Story 25.4, AD-D17)**: capture subscribes only through
  `Collector.apply(diff) -> Applied(subscribed, unsubscribed, failed)`; `run()` applies the plan
  once, then the control plane applies each change. A wire-failed subscribe is `pending`
  (`collector.subscribe_failed`), a wire-failed unsubscribe keeps the id subscribed but never
  sampled (`collector.unsubscribe_failed`); both are retried every 30 s by
  `_subscription_retry_loop`, serialized with `apply` -- and with every forced book resync, itself
  an unsubscribe plus a subscribe -- under one lock. dYdX's two-channel subscribe and unsubscribe
  are idempotent per channel: `DydxClient` mirrors the Rust client's per-topic reference and the
  topics a failed subscribe leaves for its reconnect replay, so a retry sends only what is
  missing, a possibly replayed orderbook is re-subscribed for a fresh snapshot, and removing a
  pending id ends whatever part of it reached the wire. An id still subscribed after a
  failed unsubscribe is reported `lingering` and keeps its wire slot out of control's `start`/
  `pin_top_liquid`/`reload` budget, so the wire never exceeds the cap. "Applied" means the
  subscribe frames were sent: an asynchronous venue rejection is not seen (a `Known limit:` in
  `Collector._subscribe_one`). A book exists only for `applied ∩ plan`, and a book or trade message
  for any other instrument is counted (`collector.unplanned_message`, once per flush), never
  booked, folded or archived; mark/index/funding/open interest stay ungated (venue-wide channels).
- **Control (dYdX only; `collection_control/`)**: the plan is changed only by a `collector:control`
  command (`ControlService`: validate through the one loader, save, apply, publish) or a hand edit
  of `config.toml`, re-read every `config_reload_seconds` (only the plan hot-reloads; the
  thresholds are read at start). Bybit and Hyperliquid apply a static plan once at start.

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

## 2. Shared indicators — `kernel/indicators.py`

Indicators are implemented once, in the shared kernel — everything imports them from there,
never reimplements. `ml_signals/`, which hosted them and later ranking's math and the research
shims, was deleted in Story 25.2 `[amended 2026-09-26: Story 25.2 -- its last modules moved to
`ranking/` (the pct-change/volatility math, the catalog price read) and `research/`
(`rank_history`); its two preference TOMLs moved to `data/`]`.

- **`kernel/indicators.py`** (moved from `ml_signals/indicators.py` in Story 23.2) — five
  Nautilus `Indicator` subclasses, each used identically in Jupyter, backtest, and live
  (`bots`):
  - `Microprice` — size-weighted mid from top-of-book
  - `OrderFlowImbalance` — top-of-book OFI
  - `MultiLevelOBI` — N-level order book imbalance
  - `MultiLevelOFI` — N-level order flow imbalance, replayed across snapshots
  - `OnlineLogisticTrend` — online-updating trend classifier fed from bars
- The derived-view helpers (spread, microprice, footprint charts) live in `views/chart_series.py`
  (Story 24.2) — none of it is stored, all computed on read per `platform/CLAUDE.md`'s 1s-based
  signal architecture rule.

---

## 2a. Web UI — `data_api/` + `frontend/`

`ml_signals/dashboard.py` (the old aiohttp HTML app) was retired in Story 15.10. The web
UI is now the React SPA in `platform/frontend/` (Rankings, Chart, 31-day History, Docs),
served by the `data_api` FastAPI app on `:9100` (`127.0.0.1` only) alongside its REST +
WebSocket API: `/api/rankings`, `PUT /api/rankings/mode` (the ranking-mode switch, Story 25.1a:
publishes `{"mode": ...}` to `ranking:control`, 503 when no `ranking_engine` receives it),
`/api/candles/{id}`, `/api/snapshots/{id}`,
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
- **`rank_history.py`** — `fetch_rank_history()` reads `data_api`'s `/api/metrics/nearest` for a
  past rank at a timestamp (moved from `ml_signals/` in Story 25.2). HTTP only, like the watchlist.
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

## 3. `ranking/` — the one place ranking gets computed

Extracted from what used to be inline logic in `dashboard.py` (Story 1.8) specifically
so the web UI and any future reader can never disagree on coin order; a bounded context with
its own aggregate since Story 25.2 (it was `ranking_engine/engine.py`, twelve module globals).

- **`domain/board.py`** — `RankingBoard`, the aggregate: the one global mode (last-write-wins),
  one `InstrumentMetrics` per instrument (the OFI/OBI trackers, the 300-snapshot rolling window,
  arrival-time freshness, the slow-loop metrics), the per-venue USD volumes and the
  `RankingsPublisher` (publish on every rank/mode change **and** on a fixed heartbeat,
  `RANKING_HEARTBEAT_SECONDS`, so readers can tell "stale" apart from "nothing changed"). Both
  volume and volatility scores are always present in every row; a row with no fresh USD volume
  leaves volume mode and stays, `volume24h: null`, in volatility mode. An instrument silent for
  an hour is aged out.
- **`domain/metrics.py`, `price_series.py`, `volatility.py`** — the pct-change/volatility formula
  (`price_stats_from_series`, ranking's alone: views and research read the published values),
  the in-memory 25 h price series and `VolatilityTracker` (the cross-sectional 1 h stdev).
- **`application/engine.py`** — `RankingEngine`: the `snapshots:raw`/`ranking:control` handler
  (`snapshots:raw` decoded only by `DydxSecondSnapshot.from_dict`), the volume poll, the slow
  metrics loop (one-time catalog backfill per instrument, then `metrics.db` every minute) and
  the heartbeat, through the ports in `application/ports.py`.
- **`application/queries.py`** — `history()`/`nearest()`, the read service for `metrics.db`
  (Story 1.4/FR8's "how did this coin's rank evolve over time"): read-only connections per call.
- **`infrastructure/`** — Redis (publisher + listener), `metrics_store.py`
  (`SqliteMetricsStore`, the sole writer), `catalog_prices.py` (over `kernel.catalog_files`) and
  one volume source per venue (`volume_{dydx,bybit,hyperliquid}.py`, requests built with
  `kernel.venue_http`), all constructed by `__main__.py`.

**Reads:** `snapshots:raw`, `ranking:control`, the Parquet catalog (backfill only).
**Publishes:** `rankings:live`.
**Writes:** `metrics.db`.

---

## 4. `bots/` — the actual trading bots

The one place in `platform/` where `TradingNode`/`Strategy` usage is sanctioned
(architecture AD-8 amendment) — everywhere else in `platform/` treats `nautilus_trader` as
a library only. Moved from `live_paper/` in Story 25.3 (`live_paper` is a deprecated re-export
shim until Story 26.1); every `bots:*` payload, `fills.db` row, env var and the `live-paper`
compose service are unchanged, proven by `bots/tests/test_replay.py` against payloads recorded
from the pre-move code.

- **`domain/`** — `config.py`: `PaperFleet` (many bots, one Sandbox pool per venue; its
  `PaperConfig` has no `mode` field) and `ExecBot` (one bot, `ExecConfig.mode` ∈
  {`real_money`, `exchange_demo`}, agreeing with `environment`) are distinct aggregates built by
  distinct loaders, so no config key, control message or list reorder can promote a paper bot
  (AD-D15; Known limit: demo vs real is a validated value, not a type). `bot.py`: `Bot`
  (id == the strategy's `order_id_tag`, the bounded `bots:incidents:*` log). `fill_ledger.py`:
  `FillLedger` (per-fill realized PnL, the rolling day/week/month/all windows of AD-10).
- **`application/`** — the ports (`BotRuntime`, `FillsStore`, `BusConnection`),
  `supervise.py` (`bots:status` on a 5 s heartbeat, `bots:control` start/stop — the channel never
  carries a mode) and `history.py` (fills recorded on-fill, `bots:history:*` every 30 s).
- **`infrastructure/`** — the Nautilus anti-corruption layer: `nautilus_host.py` (the one
  `TradingNode` per process, one data + one Sandbox exec client per venue from `VENUES`;
  `test_boundaries.py` fails any other `TradingNode` importer), `cache_reader.py` (every
  position read scoped to the bot's own `strategy_id`), plus `fills_store.py`, `redis.py` and
  `config.py` (the two loaders; real money needs `LIVE_PAPER_REAL_MONEY_CONFIG` naming a file
  the paper loader cannot parse).
- **`strategies/dummy.py`** — `DummyStrategy`: wires all five `kernel.indicators` into a
  live `TradingNode` run. Explicitly framed as an integration proof, not a tuned alpha
  strategy — it proves every signal stays alive end-to-end from research through
  backtest through live paper trading. Feeds `Microprice`/`OrderFlowImbalance` from
  `QuoteTick`, `MultiLevelOBI`/`MultiLevelOFI` from a 1-second clock timer snapshotting
  `cache.order_book()` (matched to the 1s cadence the indicators were calibrated
  against in backtest), and `OnlineLogisticTrend` from `Bar` via INTERNAL aggregation.
  Has an `orders_inflight()` guard to avoid duplicate submissions while a fill is
  pending — flagged as still needing a concurrency test, see below.

**Reads:** venue WS/HTTP (via `TradingNode`), `bots:control`.
**Publishes:** `bots:status`; sets `bots:history:*` and `bots:incidents:*`.
**Durable state:** orders/positions in the Redis-backed Nautilus Cache (Story 4.6), fills in
`fills.db` (append-only, since the Cache loses a NETTING position's closed history on reopen).

---

## 5. `bot_tui/` — terminal UI

`urwid`-based, keyboard-only, k9s-style navigation. Not a daemon — launched via
`docker compose run` (or on-host), never `restart: always`, since it's an interactive
SSH-launched tool, not a background service.

The control surface for the bots and the collector, and nothing else: rankings, the
ranking-mode switch and the single-coin view are web-only since Story 25.1a (the Coins pane,
Coin-detail and their `rankings:live`/`snapshots:raw` listeners were deleted;
`bot_tui/tests/test_no_rankings_feed.py` keeps them out).

- **`app.py`** — `MainLoop` wiring, breadcrumb header, footer hint bar, `:` command bar
  (`:bots`, `:data`, `:help`, `:q`, plus the Collector actions `:start <ID>`/`:pintop`),
  `esc` pop-back-one-level. Opens on the Bots pane.
- **`bots_pane.py` / `bots_state.py`** — live bot list from `bots:status`, per-row stale
  badges, `s` to start/stop by publishing `{bot_id, action}` to `bots:control` — footer
  echoes "sent," never assumes success (no optimistic UI state). Enter opens Bot-detail
  (live snapshot, trades blotter + PnL sparkline from `bots:history:*`
  (`bot_history_state.py`), incidents log from `bots:incidents:*`
  (`bot_incidents_state.py`); `o` deep-links to the dashboard's bot page).
- **`collector_pane.py` / `collector_state.py`** — the Collector pane (`:data`): every
  collected dYdX instrument from `collector:status`; `p` unpin / `x` stop behind a
  type-to-confirm prompt, published to `collector:control`.

**Reads:** `bots:status`, `bots:history:*`, `bots:incidents:*`, `collector:status`.
**Publishes:** `bots:control`, `collector:control`.
**Never imports:** `bots` internals (control-plane only, per AD-10) or
`dydx_collector`/`ranking` stateful internals (pure/shared-type imports only).

---

## Redis channel reference

| Channel | Publisher(s) | Subscriber(s) | Payload |
|---|---|---|---|
| `snapshots:raw` | the three collectors | `ranking_engine`, `data_api` | One `DydxSecondSnapshot`-shaped message per instrument per second |
| `rankings:live` | `ranking_engine` | `data_api` | `{mode, updated_at, ranks: [{instrument_id, rank, volume24h, volatility_score}]}`, on change + heartbeat |
| `ranking:control` | `data_api` (`PUT /api/rankings/mode`, Story 25.1a) | `ranking_engine` | Mode-switch request `{"mode": "volume"` \| `"volatility"}`, last-write-wins |
| `bots:status` | `bots` | `bot_tui` | Per-bot PnL/position/mode/heartbeat, on a timer |
| `bots:control` | `bot_tui` | `bots` | `{bot_id, action: "start"` \| `"stop"}` — never a mode field |
| `collector:status` | `collection_control` (`StatusPublisher`, in the dYdX collector process) | `bot_tui` | One message per planned instrument (`id`, `liquid`, `last_trade_ts`, `trade_backfill`, plus `"pending": true` when capture has not applied it, Story 25.4), the `unpinned_ids` list, and a `removed` tombstone on stop/unpin |
| `collector:control` | `bot_tui` | `collection_control` (`ControlService`, in the dYdX collector process) | `start`/`unpin`/`stop`/`pin_top_liquid` requests |

## Storage reference

| Store | Writer | Readers | Contents |
|---|---|---|---|
| Parquet catalog (`data/catalog/`) | all three collectors | `ranking` (price backfill), `data_api`, `research` (backtests, notebooks) `[amended 2026-09-26: Story 25.2]` | Second-snapshots (`DydxSecondSnapshot`, trades folded in rather than stored raw — audit D-45), mark/index price, funding rate, `OpenInterest`, instrument definitions, plus `order_book_deltas` for the dYdX instruments that opt in, and the Story 22.13 raw `trade_tick/` archive (pruned nightly by `archive.prune_catalog --trade-retention-days 7`; `docs/DATA_DICTIONARY.md` §1.1). Minute bars retired 2026-09-20 (D-35). Nautilus-native, zero-conversion `[amended 2026-09-20: Epic 22 story 22.8, review pass]` |
| `candles_{dydx,bybit,hyperliquid}.db` (SQLite, `data/candles/`) | that venue's collector (through the `SecondSink` port its entrypoint injects, Story 24.1 — the collector core no longer opens the file), plus `compare_klines` for the `verified_days` table | `data_api`, `prune_catalog` (via the `VerifiedDays` port) | Finished 1m..1D bars derived from raw 1s (D-35), plus the `verified_days` day-status table the archive tools reach through the `VerifiedDays` port. Fully rebuildable: `python -m candles.rebuild` |
| `metrics.db` (SQLite) | `ranking_engine` (`ranking.infrastructure.metrics_store`) | `data_api` (read-only mount, through `views` → `ranking.application.queries`) | Historical ranking snapshots (Story 1.4/FR8) |
| Nautilus `Cache` (Redis-backed, `bots`) | `bots` | `bots` only (strategy-scoped reads, `bots.infrastructure.cache_reader`) | Orders/positions for the running bots (Story 4.6); never read outside `bots` — its Redis encoding is not a contract |
| `fills.db` (SQLite, `data/live_paper/`) | `bots` (`bots.infrastructure.fills_store`) | `bots` only; published as `bots:history:*` | One append-only row per fill, every bot (Story 4.6) |
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
thin layer on the same base (it ships `bots`, `kernel`, `observability`, the `live_paper` shims
and `tests`).

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
- ~~**Story 4.6/4.7 (backlog)** — Cache persistence + TUI trades blotter/PnL chart.~~ Built:
  the Redis-backed Cache, `fills.db` and `bots:history:*` (§4) `[amended 2026-09-26: Story 25.3]`.
- **`orders_inflight()` race guard has no test** proving it holds under a real
  concurrent-fill race (open action item, epic-3 retro).
- **EXTERNAL vs INTERNAL bar aggregation choice unverified live** — INTERNAL was
  picked defensively, never confirmed better against a real dYdX connection (open
  action item, epic-3 retro).
- **Real-money path (`ExecConfig`, `mode = "real_money"`) exists and is gated but untested** — never
  exercised even once.
