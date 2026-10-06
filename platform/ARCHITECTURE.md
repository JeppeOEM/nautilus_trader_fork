# System Overview

Everything under `platform/` in one picture: what each module does, what data it owns, and
exactly how the pieces talk to each other (Redis channels, SQLite, Parquet, HTTP). This
is the "what got built" document — for setup/run instructions see `README.md`, for
coding rules see `CLAUDE.md`, for planning history see `_bmad-output/`.

---

## The one-paragraph version

Three collectors (dYdX, Bybit, Hyperliquid) share one venue-neutral engine
(the `capture` context's `CaptureService`): each pulls live market data straight off the Rust adapters and writes
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
| `capture/` | The capture context (the collector core until Story 26.2, which moved it into the DDD spine's Structural Seed; the old path's re-export shim was deleted in Story 26.3 `[amended 2026-09-28: Story 26.2; Story 26.3]`): the venue-neutral collector engine (Story 22.1) — ingest → 1s sample → flush → Parquet + `snapshots:raw` + the `SecondSink` port, and the capture lock `<catalog>/.capture-<VENUE>.lock` held for the process life (Story 25.1); the operator-run catalog tools moved to `archive/` in Story 25.1 (their old paths' shims were removed in Story 25.3) `[amended 2026-09-25: Story 25.1]`. Since Story 26.1 the gate is explicit aggregates in `domain/` (`LiveBook`, `TradeIntake`, `FeedGroup`, the pure `SecondSampler`, `FlushBatch`) with venue variance as policy values (`CapturePolicies`); `application/capture_service.py`'s `CaptureService` (was `Collector`) is the application service (the loops, every resync, the only `error_ledger` caller, sites in `application/sites.py`, thresholds in `application/config.py`), and its I/O goes through `application/ports.py` (`VenueFeed`, `VenueTradeHistory`, `ArchiveWriter`, `LiveStream`, `Notifier`, `SecondSink`) with the adapters in `infrastructure/` (`parquet_writer.py`, `redis_stream.py`, `capture_lock.py`, `gap_markers.py`, and `config.py`, the one venue loader) `[amended 2026-09-26: Story 26.1]` | Parquet catalog (read/write, through `ArchiveWriter`), Redis (publish, through `LiveStream`), `capture/application/ports.py`'s `SecondSink` (the candle store, injected by the venue entrypoint) |
| `archive/` | The archive context (Story 25.1, DDD spine AD-D9/AD-D18): the nightly saga (`archive.nightly`: rebuild -> consolidate -> build_candles -> reconcile -> prune) over the `ArchiveDay` state machine, `RetentionPolicy` (the one deleter), `CatalogFiles` (the one in-place rewriter, writing every file with `compact_parquet.compact_write_options`, the one choice of Parquet write options `[amended 2026-09-29: Story 30.1]`), and the operator CLIs `python -m archive.<tool>` (`archive.tools.recompress` rewrites closed-day files written before those settings). Since Story 25.1b also the `archive` service (`python -m archive.scheduler`, `ArchiveScheduler` in `application/scheduler.py`): the one place nightly maintenance is scheduled -- each venue's saga, then consolidate, then `archive.backup_catalog` when `backup_enabled` `[amended 2026-09-26: Story 26.1b]`, per missed closed day, plus the closed-hour merge of the small types (`consolidate_catalog --closed-hours`) every `intraday_consolidate_hours` -- each step a child process, the maintenance lock only probed, never held `[amended 2026-09-26: Story 25.1b]` | Parquet catalog (read/write, never today's files; intraday, never the current hour), `candles.application` (`VerifiedDays`, `queries`, `rebuild`), the venues' kline REST (`kernel.venue_http`), the dYdX plan file (read, prune only), Redis (`archive:status` publish, `archive:control` read), `data/archive/state.json` (read/write), object storage (rclone, the backup step) |
| `capture/venues/dydx/`, `capture/venues/bybit/`, `capture/venues/hyperliquid/` | One package per venue (Story 26.2; three `Collector` subclasses, one per venue package, until then, whose re-export shims Story 26.3 deleted): `client.py` (the `VenueFeed` WS/HTTP client), `trade_history.py` (the `VenueTradeHistory` REST backfill), `policies.py` (dYdX's uncross ladder, Bybit's `u` canary; none for Hyperliquid), the optional `open_interest.py` (dYdX, Bybit) and `book_snapshot.py` (Bybit, Hyperliquid), `config.py` (the venue's config class, caps and `CONFIG_PATH`) and `__main__.py`, the composition root (`python3 -m capture.venues.<venue>`): `build_capture(config, plan_ids)` wires the client factory, the policy values, the adapters, the candle sink and retention loop, the REST open-interest poll (`CaptureService.poll_loop`) and, for dYdX, collection control's loops (`add_loops`) into one `CaptureService`, never a subclass; `build_capture_from_file` loads config + plan through `capture.infrastructure.config`'s one loader, `load_venue_config` `[amended 2026-09-28: Story 26.2]`. `CaptureService.apply(diff) -> Applied` makes the applied set the fact: the sampler, watchdog, cross-check and backfill iterate `applied ∩ plan`, and a book/trade message outside it is counted (`collector.unplanned_message`), never archived (Story 25.4) `[amended 2026-09-26: Story 25.4]` | Their venue's WS/REST (via Rust `nautilus_pyo3` clients) |
| `collection_control/` | The collection-control context (Story 25.4, DDD spine AD-D17): the plan is the intent, capture's applied set the fact. `CollectionPlan` (`domain/`: instruments, `exclude`, `cap` = 30 for dYdX and none (`None`) for Bybit and Hyperliquid, the liquidity threshold, the dropped-instrument retention; commands `add`/`remove`/`pin`/`unpin`/`exclude`/`reload` return a `PlanDiff`), the pure `classify_liquidity` that alone admits a pin; `ControlService` (`collector:control`: save, then apply, then publish), `StatusPublisher` (`collector:status`, `pending` for a planned-but-unapplied id) and the plan-file `reload_loop` in `application/`; `TomlPlanStore`, the Redis bus/channel and `DydxMarkets` in `infrastructure/`. Wired into every venue's capture service through `add_loops` by its `capture.venues.<venue>.__main__.build_capture_from_file` (dYdX Story 26.2; Bybit and Hyperliquid Story 29.4, after Story 29.2 wired only their `StatusPublisher`); no module state, deletes nothing | Redis (`collector:status` publish, `collector:control` read), each venue's plan file (`data/dydx_config.toml`, `capture/venues/{bybit,hyperliquid}/config.toml`, read/write), dYdX indexer `perpetualMarkets` (through `kernel.venue_http`) |
| `kernel/` | The shared kernel (Story 23.2, DDD spine AD-D3): the one copy of every type, fold, parser, constant, transport and read helper more than one context uses — `second_snapshot` (`DydxSecondSnapshot`, `SecondOHLC`), `open_interest`, `fold` (`fold_trades`), `indicators` (pure `Indicator`s and snapshot functions), `candle_patterns` (`CandlePattern`/`CandlePatternSet`: one pattern definition, shared by views, research and bots) `[amended 2026-09-28: Story 27.7]`, `performance_metrics`, `venues` (the only `InstrumentId` parser: `venue_of`, `has_venue`, `venue_kind`, `market_kind`, `market_suffix`, `bybit_category`, `asset_key`/`same_asset`, `base_symbol` `[amended 2026-09-28: Story 29.1]`), `clocks` (`TwoClocks`, `CatalogFileSpan`, the one skew bound `MAX_TS_INIT_SKEW_NS` = 300 s), `archive_markers` (the `_archive_gaps/<iid>.jsonl` format), `venue_http` (every venue REST URL and request), `catalog_files` (read-only snapshot-file helpers), `parquet_compat` (the one zstd `write_table` default). Imports no context, holds no state, store, config loader or ledger call; every context may import it | venue REST endpoints (outbound GET/POST, via callers), the Parquet catalog (read-only) |
| `candles/` | The candles context (Story 24.1, DDD spine AD-D8): the one seconds → bars fold (`domain/fold.py`), the `CandleSeries` watermark aggregate, the query/forming/rebuild services, the retention process manager and `CandleStore` — the only read-write opener of a `candles_*.db`. Behind capture's `SecondSink` port, so nothing upstream imports it | Parquet catalog (read, the rebuild), `candles_*.db` (read/write) |
| `views/` | The views context (Story 24.2, DDD spine AD-D11): the read models both UIs show -- `ranking_columns` (the ranking table's columns, the Technicals tab's values -- each at the latest closed bar, Story 27.7), `coin_detail` (the single-coin metric set, the `snapshots:raw` decode via `DydxSecondSnapshot.from_dict`, the `metrics.db` history reads), `chart_series` (every chart page -- candles, Lines mode, indicator values -- plus the volume footprint, the cancel-pressure tracker and the gap-marker rendering rules), `derivatives` (Story 33.4: the one read model of the archived funding, open interest, mark, index and liquidations, `docs/DATA_DICTIONARY.md` §2.16), `indicator_picker` (the native + custom indicator catalogs and their dispatch; native covers `nautilus_trader.indicators` plus the kernel's `CandlePattern`, and lists each enum param's `choices` for the picker's dropdown, Story 27.7), `preferences` (the one loader/saver of `chart_indicators.toml` and `screener_columns.toml`), `catalog_reads`, `live_candles` (`LiveCandleBus` and the `BarObserver` port), `live_derivs` (`LiveDerivsBus`, Story 33.4: the `/ws/live` `derivs:{iid}` and `liquidations:{iid}` relay) and `rankings_bus`. Framework-free, no module state; imports only `kernel`, `observability` and the candles/ranking query services. The reader never re-validates the capture gate | Parquet catalog (read), `candles_*.db` (read, via `candles.application.queries.open_store`), `metrics.db` (read), Redis (`snapshots:raw`, `rankings:live`, `liquidations:raw` and, since Story 33.4, `derivs:raw` subscribe, through the bus instances `data_api.buses` constructs) |
| `alerting/` | The alerting context (Story 24.3, DDD spine AD-D2/AD-D16): saved price alerts (`domain/`: `Alert`, `FiringPolicy`, the pure `evaluate`/`render`), `AlertEngine` (a structural `views.live_candles.BarObserver`, evaluated on the forming bar the chart draws for every pair an active alert watches, chart open or not) and `AlertService` (the `/api/alerts` use cases) in `application/`, and the `AlertStore`/`NotifyDeliverer` adapters in `infrastructure/`, constructed only by `data_api/alert_wiring.py`. An alert names channels, never transports. Imports only `kernel`, `observability`, stdlib and `tomli_w`; no module state | `alerts.toml` (read/write), webhook / Telegram URLs (outbound HTTP POST, through `observability.notify`) |
| `research/{domain,application,notebooks,strategies}` | The research context (Story 24.4, DDD spine AD-D1 research row): a consumer with no aggregates -- two layers since Story 27.1: `domain/` (the analysis values `ReturnSeries`, `EquityCurve`, `TradeLedger`, `MetricReport`, `CorrelationMatrix`, `MonteCarloResult` `[amended 2026-09-28: Story 27.6]`; stdlib, numpy, `kernel` and Nautilus value types only; statistics only through `kernel.performance_metrics`) and `application/` (the `typing.Protocol` ports `MarketFrames`, `RankingHistory`, `BacktestRunner` and their implementations `CatalogFrames`, `HttpRankingHistory`, `NodeRunner`) `[amended 2026-09-28: Story 27.1]` -- plus the backtest strategies and runners (`strategies/`, referenced by `ImportableStrategyConfig` string path `research.strategies.<module>:<Class>`), `run_backtest.py`, the watchlist client (`watchlist.py`), the notebooks (`notebooks/`) and `README.md` (the one research page: the notebook index, the recipes, the local launch and the backtest how-to) `[amended 2026-09-28: Story 27.9]`. Reads market-data rows only through `kernel.catalog_files`, `BacktestDataConfig`, a typed catalog `query` bounded by both `start=` and `end=`, or the candle store's query service (`research/tests/test_research_reads.py`), the live coin-set and ranking history only over HTTP, and computes no rolling metric (pct-change and volatility are ranking's). In-repo it imports only `kernel`, `observability`, the candles query services `open_store`/`window`/`oldest_t`/`newest_t`/`bucket_starts`/`verified_status` and `candles.domain.fold.BAR_SECONDS` (the allowlist `RESEARCH_CANDLES_SERVICES` in `tests/test_boundaries.py`), and the archive's pure gap heuristic `find_gaps` (`RESEARCH_ARCHIVE_SERVICES`) `[amended 2026-09-28: Story 27.2]` (beside stdlib, `nautilus_trader`, numpy, pandas and plotly) | Parquet catalog (read; `snapshot_backtest` and `NodeRunner` write only a throwaway catalog of derived quotes in a temp dir), `candles_*.db` (read-only, via `candles.application.queries.open_store`), `data/errors/*.jsonl` (read, `observability.error_ledger`'s readers, Story 27.2), `data_api` `/api/rankings` and `/api/metrics/*` (HTTP GET) |
| `observability/` | The generic observability context (Story 23.1, DDD spine AD-D16), standard library only and venue-free: `error_ledger` (every continue-past-failure site, DATA-07; in-memory per process, plus a durable per-service `<service>.jsonl` sink behind the same `record()` call, Story 23.3), `notify` (the one outbound transport: channels `operator` = ntfy/`WATCHDOG_NTFY_URL`, `telegram` = `TELEGRAM_*`, `webhook:<url>`), `watchdog` (the generic `(down_since, reminder)` alert transition), `incidents` (the WARNING+ incident-report handler, parameterised by the venue entrypoint's `IncidentConfig`). Every context except `kernel` may import it (spine AD-D2); it imports none | ntfy / Telegram / webhook URLs (outbound HTTP POST), `data/incident_reports/` (write, dYdX collector only), `data/errors/*.jsonl` (write, every service; Story 23.3) |
| `data_api/` + `frontend/` | Web UI (React SPA) + REST/WS on `:9100`, read-only except the saved preferences/alerts and the ranking-mode switch. Format + transport only since Story 24.2: every value comes from `views/`; `data_api/buses.py` constructs the Redis bus instances, one subscriber per channel per process (Story 33.4 added `live_derivs_bus`, the one `derivs:raw` subscriber), `data_api/alert_wiring.py` the alerting instances (Story 24.3), and `app.py`'s lifespan attaches the alert engine to the live-candle bus and, since Story 33.4, `live_derivs_bus` to its liquidation hook (the live-candle bus stays the one `liquidations:raw` subscriber) -- the only such wiring; `routes/alerts.py` is a thin adapter over `alerting.application` (the deprecated `data_api/alerts.py` re-export was deleted in Story 25.1) `[amended 2026-09-25: Story 25.1]` | Redis (read; `ranking:control` publish from `PUT /api/rankings/mode`, Story 25.1a), Parquet catalog + `candles_*.db` + `metrics.db` (read-only, through `views/`), `alerts.toml` (through `alerting/`) |
| `ranking/` | The ranking context (Story 25.2, DDD spine AD-D10): sole computer of coin ranking (volume + volatility) and of the pct-change/volatility math. `RankingBoard` (`domain/`) owns the mode, one `InstrumentMetrics` per instrument, the volume book and the publisher; `RankingEngine` (`application/`) drives it through the `VolumeSource`/`PriceHistory`/`RankingHistory`/`LivePublisher` ports, whose adapters (`infrastructure/`) only `__main__` wires; `application/queries.py` (`history`/`nearest`) is the `metrics.db` read service views calls. No module-level state. Runs as `python3 -m ranking` (compose service `ranking_engine`); the old package path's re-export shims were deleted in Story 25.4 `[amended 2026-09-26: Story 25.2]` `[amended 2026-09-26: Story 25.4]` | Redis (`snapshots:raw` read; `rankings:live` and, since Story 29.5, `markets:live` publish; `ranking:control` read; since Story 33.4 `derivs:raw` and `liquidations:raw` read), `metrics.db` (write), Parquet catalog (read, one-time backfill of prices with volume and, since Story 33.4, 25 h of open interest and 1 h of liquidations through the `DerivsHistory` port), dYdX/Bybit/Hyperliquid REST (24h USD volume poll, through `kernel.venue_http`) |
| `bots/` | The bots context (Story 25.3, DDD spine AD-D15; its old package path's re-export shims were deleted in Story 26.1 `[amended 2026-09-26: Story 26.1]`): the actual trading bots — one `TradingNode` + one strategy per bot (`DummyStrategy`, or since Story 27.8 a research strategy such as `CandlePatternStrategy` loaded by string path `[amended 2026-09-28: Story 27.8]`; since Story 33.14 `LiquidationCascadeStrategy`, paper only, fed `liquidations:raw` by the `LIQUIDATIONS` data client `liquidation_data_client.py`), in paper (or gated demo/real-money) mode. `PaperFleet`/`ExecBot` make the paper/non-paper split a type, `Bot` holds the incident log, `FillLedger` the per-fill PnL; `nautilus_host.py` is the only `TradingNode` importer (`liquidation_data_client.py` the only other `nautilus_trader.live` one, a `LiveMarketDataClient` subclass). Runs as `python3 -m bots` (compose service `live-paper`) `[amended 2026-09-26: Story 25.3]` | Venue WS/HTTP (via `TradingNode`), Redis (`bots:status` publish, `bots:history:*`/`bots:incidents:*` set, `bots:control` read, `liquidations:raw` read for a cascade bot (Story 33.14); the Nautilus Cache), `fills.db` (write) |
| `bot_tui/` | Keyboard-only terminal UI, interactive/on-demand: the control surface for the bots and the collector (two panes, Bots and Collector). Rankings, the ranking-mode switch and the single-coin view are web-only since Story 25.1a, so it imports no `views/` read model `[amended 2026-09-26: Story 25.1a]` | Redis (`bots:*`, `collector:status`, `archive:status` and `markets:live` -- market names only, Story 29.5 -- read; `bots:control`, `collector:control` publish), dashboard (HTTP deep-link only) |

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
context in Story 24.1: capture declares a `SecondSink` port
(`capture/application/ports.py`), each venue entrypoint injects `candles.application.sink.CandleSink`,
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
story, and research through `kernel.catalog_files` (`query_top_of_book`, `query_second_ohlc`,
`query_index_prices`), `BacktestDataConfig`, a `start=`/`end=`-bounded catalog `query` or the candle store's
`window` (`research.application.frames`) `[amended 2026-09-25: Story 24.4]` `[amended 2026-09-28: Story 27.1]`. None of them re-validates what the capture gate wrote: the reader-side
empty-top and crossed-book skips `data_api/routes/snapshots.py` used to apply (the parent spine's
AD-3 deviation) are deleted -- a crossed second now renders, an empty-top second fails its request
loudly (`views.snapshot_without_top` in the error ledger), and the only thing that changes what
is drawn is the gap-marker rendering rule in `views.chart_series` `[amended 2026-10-06: Story 33.4
-- the legacy chart-series endpoint and its builder were deleted, no caller remained]`.

---

## Data flow

<!-- [amended 2026-09-20: Epic 22 story 22.8, review pass] the diagram was still the
     single-venue system, contradicting this file's own three-collector summary above. -->
<!-- [amended 2026-09-28: Story 27.9] the research branch and the shared candlestick detector
     added: research had no place in the diagram. -->
```
dYdX WS/REST      Bybit WS/REST      Hyperliquid WS
(Rust nautilus_pyo3 clients, one duck-typed client.py per venue)
        │                 │                 │
        └─────────────────┴─────────────────┘
        │
        ▼
  capture/venues/<venue>/__main__.py  (composition roots: python3 -m capture.venues.<venue>;
        │                               client, policies, trade history, REST polls, adapters)
        ▼
  capture.CaptureService  ─────► Parquet catalog (Nautilus-native, zero-conversion)
  (capture/domain aggregates:            one shared catalog root
   LiveBook · TradeIntake · FeedGroup · SecondSampler = the one write gate)
        │                                 │
        │ publish "snapshots:raw"         │ read (time-bounded / BacktestDataConfig)
        ▼                                 ▼
      Redis  ◄───────────────────  ranking_engine (volume + volatility scoring)
        │  ▲                             │
        │  │ publish "rankings:live"     │ write
        │  └─────────────────────────────┘
        │                            metrics.db (SQLite, ranking history)
        │
        ├──► data_api (web UI + REST, :9100)      — reads snapshots:raw + rankings:live +
        │                                            liquidations:raw + derivs:raw;
        │                                            writes ranking:control (mode switch)
        │
        ├──► bot_tui (terminal, on-demand)        — reads bots:* + collector:status +
        │                                            markets:live (names only, Story 29.5);
        │                                            writes bots:control + collector:control
        │
        └──► bots (TradingNode, paper/live)       — writes bots:status; reads bots:control
                                                     (+ liquidations:raw for a cascade bot)
                     │
                     ▼
              the bot's own venue: paper fills on the node's Sandbox (dYdX, Bybit or
              Hyperliquid; a cascade bot Bybit LINEAR, paper only), or real fills behind an
              explicit gate

  Parquet catalog ──┐
                    ├──► research/{domain,application,notebooks,strategies}
  candles_*.db ─────┘      (read only, every read bounded by START/END; notebooks and
                            BacktestRunner; writes only throwaway backtest catalogs)

  kernel/candle_patterns.py  (CandlePattern: the one candlestick detector)
        ├──► views     (chart indicator picker, screener Technicals columns)
        ├──► research  (06_candlestick_scanner, CandlePatternStrategy)
        └──► bots      (a paper bot with strategy = "candle_pattern", by string path)
```

Everything downstream of the collector reads either the catalog (bounded/historical) or
Redis (live/current) — never both conflated, per NFR3's memory-bounded-access rule.

---

## 1. `capture/` + its venue packages — the data source

Three standalone asyncio services (one per venue) over one shared engine. All bypass
`TradingNode`/`Strategy`/`DataEngine` entirely and talk directly to the Rust
`nautilus_pyo3` WS/HTTP clients — this is deliberate: `DataEngine` has a documented
unbounded-queue-growth bug under sustained load that OOM-crashed an earlier
`Strategy`-based recorder.

Layout (Story 26.2, the DDD spine's Structural Seed; the old packages' re-export shims were deleted
in Story 26.3):

```
capture/
  domain/          live_book.py trade_intake.py feed_group.py sampler.py verdicts.py events.py
                   flush_batch.py policies.py trade_history.py   (pure: stdlib, kernel, Nautilus values)
  application/     capture_service.py (CaptureService) ports.py sites.py config.py (CoreConfig)
                   trade_backfill.py book_check.py feed.py
  infrastructure/  parquet_writer.py redis_stream.py capture_lock.py gap_markers.py
                   config.py (the one venue loader)   (imported only by the composition roots)
  venues/
    dydx/          client.py trade_history.py policies.py open_interest.py config.py __main__.py
    bybit/         client.py trade_history.py policies.py open_interest.py book_snapshot.py
                   config.py config.toml __main__.py
    hyperliquid/   client.py trade_history.py book_snapshot.py config.py config.toml __main__.py
  tests/           (and venues/<venue>/tests/)
```

- **`capture/application/capture_service.py`** — `CaptureService`, the application service
  (`Collector` until Story 26.2; never subclassed): owns the asyncio loops, the
  flush buffer (`domain/flush_batch.py`) and the flush timer for every venue, executes the
  resyncs the domain requests and is capture's only error-ledger caller (`sites.py`). The gate
  (Story 26.1) is `domain/`: a `LiveBook` per collected instrument, a `TradeIntake` per
  instrument (dedup window, arbitration, the live second's trades), one `FeedGroup` per venue
  (liveness, reconnect evidence, backfill requests) and the pure `SecondSampler`, the only place
  the four checks run (missing, empty top, crossed, stale) {A}. Every 1s builds a
  `DydxSecondSnapshot` per subscribed instrument (top-20
  bid/ask levels + per-side trade volume — nothing derivable is stored; the name is
  historical, the schema is venue-neutral) and publishes it to Redis channel
  `snapshots:raw`. Flushes trades/deltas/bars/mark-index-funding/instruments to the
  Parquet catalog via `ParquetDataCatalog.write_data()` on `flush_interval_seconds`, and hands
  the rows that were actually written to its `SecondSink` port — the candle store, injected by
  the venue entrypoint (Story 24.1), so a bar can never be ahead of the archive.
- **`kernel/second_snapshot.py`** — defines `DydxSecondSnapshot(Data)`, the one
  custom Arrow-registered type this whole system is built around (Story 23.2 moved it from
  the collector core; the old path's shim was deleted), and since Story 30.2 the one
  encoder/decoder of its exact integer layout (per-row precisions, integer units, gap-encoded
  book prices) for Parquet and the `snapshots:raw` payload alike; every reader decodes through
  it and a float-layout file is refused (`archive.tools.migrate_snapshot_ints` migrates it;
  `docs/DATA_DICTIONARY.md` §1.7) `[amended 2026-09-29: Story 30.2]`.
- **`kernel.second_snapshot.ohlc_outside_book()`** (was the collector core's integrity module until
  Story 25.1) — a second's trade high/low must lie inside that same second's own book. Live ERROR
  canary and offline detector (DATA-06).
- **Operator-run catalog tools** (`python -m archive.<tool>` since Story 25.1 -- the
  old paths' shims were removed in Story 25.3 -- never automatic; the candle rebuild
  is `python -m candles.rebuild` since Story 24.1):
  `candles.rebuild` (rebuild a candle store from raw 1s), `consolidate_catalog`
  (`make consolidate`, every venue; `--closed-hours` for the current day's closed hours of the
  small types), `backup_catalog` (`make backup-catalog`: the guarded `rclone sync` of the closed
  files off-box, rclone from the image),
  `repair_catalog` (clear impossible trade OHLC; never on a day `rebuild_seconds` rebuilt),
  `migrate_open_interest` (one-shot layout migration). Story 22.13: `kernel.fold` (the one exact
  trades -> second fold, live and rebuild), `rebuild_seconds` (a closed day's trade columns from
  the raw `trade_tick` archive, on `ts_event`), `compare_klines` (1 m bars vs the venue's klines,
  exact, into `verified_days`, only for what the same saga run rebuilt), `prune_catalog` (age
  retention + verification-gated trade retention + the dYdX plan's dropped-instrument and delta
  retention; `make prune`) and `nightly` (`make nightly VENUE=...`: rebuild -> consolidate ->
  build_candles -> compare -> prune). The `make` targets are manual tools: their schedule is the
  `archive` service (`python -m archive.scheduler`, Story 25.1b), which runs `nightly`'s chain per
  venue-day, then `consolidate_catalog --apply` and `backup_catalog` (the latter only when `backup_enabled`,
  `[amended 2026-09-26: Story 26.1b]`), every night, and replaced the host crontab line
  `[amended 2026-09-26: Story 25.1b]`.
- **`capture/venues/{dydx,bybit,hyperliquid}/`** — per-venue package: `client.py` (thin wrapper
  around that venue's Rust clients, built by the service from the root's factory so it can hand
  messages to `_on_data` and failures to `_ledger`) and the `__main__.py` composition root
  (`build_capture`, `build_capture_from_file`; a venue is wired, never subclassed, Story 26.2).
  Every venue's `config.toml` goes through the one loader `capture.infrastructure.config.load_venue_config`
  (a frozen `VENUE_SCHEMAS` row per venue; an unknown key refuses start), which returns the
  thresholds plus the venue's `CollectionPlan` (Story 25.4). dYdX-only:
  `client.py`'s `_at_fixed_precision()` re-stamps mark/index prices to a single precision
  (dYdX's feed derives precision from each tick's own trailing-zero count, which corrupts
  catalog writes if left alone) and `policies.py`'s `DydxLevelTagger`/`DydxUncrossPolicy`
  resolve crossed books (DATA-04); Bybit's `policies.py` is the `u` sequence canary (DATA-08).
  Each venue's `trade_history.py` is its REST reconnect backfill (`VenueTradeHistory`: fetch,
  exact parse, `BackfillCapability`, fixtures under its `tests/fixtures/`) `[amended 2026-09-26: Story 26.1]`.
- **`capture/venues/{dydx,bybit}/open_interest.py`** — the REST open-interest fetches (the
  shared `OpenInterest(Data)` type lives in `kernel/open_interest.py`); open interest is the
  one field the Rust bindings drop (dYdX everywhere, Bybit on the linear ticker), so it's polled
  via `kernel.venue_http` every `open_interest_poll_seconds` (5 min) by the service's generic
  `poll_loop` (Story 26.2: dYdX keeps every market, Bybit the plan's ids only; a failure is
  `collector.open_interest_poll`). `classify_liquidity` moved to
  `collection_control.domain.liquidity` (Story 25.4; its deprecated alias here was removed in
  Story 26.2).
- **The applied set (Story 25.4, AD-D17)**: capture subscribes only through
  `CaptureService.apply(diff) -> Applied(subscribed, unsubscribed, failed)`; `run()` applies the plan
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
  `CaptureService._subscribe_one`). A book exists only for `applied ∩ plan`, and a book or trade message
  for any other instrument is counted (`collector.unplanned_message`, once per flush), never
  booked, folded or archived; mark/index/funding/open interest stay ungated (venue-wide channels).
- **Control (every venue; `collection_control/`)**: the plan is changed only by a
  `collector:control` command addressed to the venue (`ControlService`: validate through the one
  loader, save, apply, publish; a message without `venue` is dYdX's) or a hand edit of its plan
  file, re-read every 30 s (dYdX `config_reload_seconds`, Bybit/Hyperliquid `PLAN_RELOAD_SECONDS`;
  only the plan hot-reloads, the thresholds are read at start). Every venue publishes its plan on
  `collector:status`. Bybit's and Hyperliquid's plans took commands in Story 29.4 (Story 29.2
  published them read-only): uncapped (`cap` null), no liquidity pin, saved to their committed
  `config.toml`; their clients hold one reference per channel and pace every wire call under a
  measured limit (`capture/application/wire_channels.py`, `docs/DATA_DICTIONARY.md` §1.14).

**Publishes:** `snapshots:raw` (Redis pub/sub, one message per instrument per second);
`liquidations:raw` (the Bybit collector, Story 33.1); `derivs:raw` (every collector, Story 33.4:
one array per sample tick of mark, index, funding and open-interest rows, `kernel/derivs_wire.py`).
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
never reimplements. The package that hosted them and later ranking's math and the research
shims was deleted in Story 25.2 `[amended 2026-09-26: Story 25.2 -- its last modules moved to
`ranking/` (the pct-change/volatility math, the catalog price read) and `research/`
(`rank_history`); its two preference TOMLs moved to `data/`]`.

- **`kernel/indicators.py`** (moved into the kernel in Story 23.2) — five
  Nautilus `Indicator` subclasses, each used identically in Jupyter, backtest, and live
  (`bots`):
  - `Microprice` — size-weighted mid from top-of-book
  - `OrderFlowImbalance` — top-of-book OFI
  - `MultiLevelOBI` — N-level order book imbalance
  - `MultiLevelOFI` — N-level order flow imbalance, replayed across snapshots
  - `OnlineLogisticTrend` — online-updating trend classifier fed from bars
- Plain functions sit beside them, among them the snapshot depth functions (`depth_within_bps`,
  `liquidity_distance`) and, since Story 33.4, `basis_bps` and `funding_annualised`, the one
  derivatives formulas `views.derivatives` and `ranking` both call.
- The derived-view helpers (spread, microprice, footprint charts) live in `views/chart_series.py`
  (Story 24.2) — none of it is stored, all computed on read per `platform/CLAUDE.md`'s 1s-based
  signal architecture rule.

---

## 2a. Web UI — `data_api/` + `frontend/`

The old aiohttp HTML dashboard was retired in Story 15.10. The web
UI is now the React SPA in `platform/frontend/` (Rankings, Chart, 31-day History, Docs),
served by the `data_api` FastAPI app on `:9100` (`127.0.0.1` only) alongside its REST +
WebSocket API: `/api/rankings`, `PUT /api/rankings/mode` (the ranking-mode switch, Story 25.1a:
publishes `{"mode": ...}` to `ranking:control`, 503 when no `ranking_engine` receives it),
`/api/candles/{id}`, `/api/snapshots/{id}`,
`/api/coin/{id}/indicators`, `/api/coin/{id}/indicator-values`, `/api/coin/{id}/footprint`,
`/api/coin/{id}/funding|open-interest|mark-index|liquidations|liquidation-bars` (Story 33.4,
`views.derivatives`), `/api/metrics/history|nearest/{symbol}`, and `/ws/live` (rankings, alerts,
and the subscribable `candles:{id}:{bar_seconds}`, `derivs:{id}` and `liquidations:{id}`
channels, at most 32 per connection) `[amended 2026-10-06: Story 33.4 -- the legacy
indicator-series route was deleted]`. `data_api` reads the catalog/`metrics.db` read-only, and only through the
`views/` read models (Story 24.2): its routes clamp parameters, pass their env-derived paths in,
build the response models and map views' exceptions to HTTP codes. Dropped without a
React equivalent: the `/debug` state dump and the server-rendered `/live` table.

---

## 2b. `research/` — backtests, the watchlist client, notebooks

A pure consumer (Story 24.4): it reads the catalog, ranking's published output and the rankings
API, and computes nothing another context owns `[amended 2026-09-25: Story 24.4 -- moved from
the old signals package and the dYdX collector's notebooks]`.

- **`watchlist.py`** — `fetch_watchlist()` reads `data_api`'s `/api/rankings` to get the live, ranked coin set — this is how a backtest gets a dynamic instrument
  universe instead of a hardcoded list. HTTP only: `research` never imports `data_api`.
- **`rank_history.py`** — `fetch_rank_history()` reads `data_api`'s `/api/metrics/nearest` for a
  past rank at a timestamp (moved here in Story 25.2). HTTP only, like the watchlist.
- **`strategies/backtest_dydx.py` / `strategies/backtest_ofi.py` / `strategies/backtest_snapshot.py`** — `BacktestNode` +
  `BacktestDataConfig` runs (no custom matching engine anywhere). `strategies/backtest_dydx.py`
  defaults to backtesting every coin in the live Watchlist, keyed results per symbol.
  `strategies/snapshot_backtest.py` (behind `backtest_ofi.py` and `run_backtest.py`) seeds the
  simulated exchange's quotes from `kernel.catalog_files.query_top_of_book` into a throwaway
  catalog.
- **`strategies/indicator_signal_strategy.py` / `strategies/ma_cross_strategy.py` / `strategies/ofi_strategy.py` / `strategies/snapshot_strategy.py`** — backtest-only
  reference strategies, referenced via `ImportableStrategyConfig` by string path
  (`research.strategies.<module>:<Class>`).
- **`strategies/candle_pattern_strategy.py` / `strategies/backtest_candle_pattern.py`** — the
  candlestick strategy (Story 27.8): `kernel.candle_patterns` detectors with the scanner's EMA
  filter, entered in `on_bar` of the closed pattern bar, out after `exit_bars`, on an opposite
  pattern or on a reduce-only ATR stop; imports only `kernel` and `nautilus_trader`, so the same
  string path runs in a `NodeRunner` backtest (`data="trades"`), `04_backtest_evaluation` and a
  paper bot (`strategy = "candle_pattern"`, section 4) `[amended 2026-09-28: Story 27.8]`.
- **`domain/`** (Story 27.1) — the analysis values every notebook and backtest report shows, each
  with its invariant in its docstring: `returns.ReturnSeries` (one period per series; `from_prices`
  never bridges a gap, `from_equity`, `resample` compounds, `rolling_sharpe` through
  `performance_metrics.return_stats`), `equity.EquityCurve` (drawdown episodes; `from_pnl_by_day`
  equal to `performance_metrics.equity_returns` bit for bit), `trades.TradeLedger`,
  `report.MetricReport` (`performance_metrics.all_metrics` frozen, never a second formula) and
  `correlation` (pairwise-complete Pearson, lead-lag, single-linkage clustering) and
  `microstructure` (Story 27.3: autocorrelation, volatility signature, realised volatility, a
  Kyle-lambda price-impact fit by `numpy.linalg.lstsq`, hit rate by bin; each docstring names its
  invariant and formula source) `[amended 2026-09-28: Story 27.3]` and `monte_carlo` (Story 27.6:
  `MonteCarloResult` -- seeded, reproducible paths that record their seed and path count; the
  trade-order bootstrap, the Politis-Romano stationary block bootstrap of returns, risk of ruin, a
  percentile Sharpe interval, and the probabilistic and deflated Sharpe ratios of Bailey & Lopez
  de Prado; every Sharpe through `performance_metrics.return_stats`, a zero variance a stated None)
  `[amended 2026-09-28: Story 27.6]` and `events` (Story 27.7: `forward_returns` after each event at
  fixed horizons, NaN past the end or at a missing close, and `hit_rate` -- the share in the event's
  direction, the mean and the count, NaN at none) `[amended 2026-09-28: Story 27.7]`. Stdlib, numpy,
  `kernel` and Nautilus value types only -- no pandas, no I/O.
- **`application/`** (Story 27.1) — the ports a notebook uses (`ports.py`: `MarketFrames`,
  `RankingHistory`, `BacktestRunner`, `RunSpec`, `RunResult`) and their implementations:
  `frames.CatalogFrames` (time-bounded frames: `seconds` with `kernel.indicators`' derived
  columns, `trades`, `funding`, `open_interest`, `mark_index` through bounded catalog queries and
  `kernel.catalog_files.query_index_prices`, `bars` from the candle store's `window` -- the
  research -> candles query-service edge), `ranking_history.HttpRankingHistory` (data_api's
  `/api/metrics/history`, HTTP), `backtest_runner.NodeRunner` (`BacktestNode` sweeps, results by
  config id, reports read from each engine before disposal) and `quotes.derived_quotes` (the
  snapshot-backtest quote derivation, shared with `strategies/snapshot_backtest.py`).
  `frames.CatalogFrames.objects` returns a window's typed rows (exact `Price`/`Quantity`) and
  `inspection` (Story 27.2) is the catalog inspection: instrument inventory, snapshot-file coverage,
  the gap report (likely outage / book gap / no mark coverage / quiet market, through the archive's pure
  `find_gaps` -- the research -> archive edge), day status (the candle store's
  `verified_status`), the trade-fold agreement (`kernel.fold.fold_trades`), snapshot sanity,
  per-file precision labels (`kernel.catalog_files.price_precision_labels`), the gap-preserving `mid_series`, and the error ledger's window
  (`observability.error_ledger.iter_records`/`site_counts`) `[amended 2026-09-28: Story 27.2]`.
  `inspection.second_grid` puts any seconds columns on the window's 1 s grid (NaN on a missing or
  shared second, and in the book-derived columns on a crossed second; `mid_series` delegates to
  it), and `microstructure` (Story 27.3) derives every frame `02_microstructure` plots from one
  bounded read per instrument (`read_instrument`): spread in ticks/bps and by UTC hour, depth
  from one snapshot a minute (`kernel.indicators.snapshot_depth`/`cumulative_depth`/
  `depth_within_bps`), OBI z-scores and the OFI replay mirroring `OFIStrategy.on_data`
  (`kernel.indicators.RollingZScore`, the one z-score formula `MultiLevelOFI` also delegates to),
  microprice edge, trade flow/CVD, impact inputs, basis, and the return tables
  `[amended 2026-09-28: Story 27.3]`. `walk_forward` (Story 27.5) splits a window into consecutive
  in-sample/out-of-sample `Fold`s, picks each fold's grid point on the in-sample `MetricReport`
  field (highest wins, undefined last, ties to grid order) through one `BacktestRunner.sweep` and
  runs it out of sample through one `run`, and joins the out-of-sample equity by the stated
  additive convention (each fold from the full balance, shifted by the earlier folds' PnL -- not
  one continuous run); `evaluation` (Story 27.5) turns a `RunResult`, a timed sweep and a
  `WalkForwardResult` into the frames `04_backtest_evaluation` shows (equity/underwater, drawdown
  episodes, the metric table, rolling Sharpe, trades, PnL by hour/weekday via
  `RunResult.pnl_by_hour_of_day`/`pnl_by_weekday`, the heatmap pivot, top runs, the fold table)
  plus the sentences printed where there is nothing to draw `[amended 2026-09-28: Story 27.5]`;
  `robustness` (Story 27.6) turns `monte_carlo` results and a sweep into what `05_monte_carlo`
  shows (fan-chart quantiles, the ruin table, titles naming seed and paths, the Sharpe-interval
  sentence, and `deflated_check`/`deflated_verdict`: the best grid point's `MetricReport` Sharpe
  deflated by the number of points tried) `[amended 2026-09-28: Story 27.6]`; `patterns` (Story
  27.7) is the scanner behind `06_candlestick_scanner`: the candle store's bars on the complete
  bucket grid (read span by span over `bar_coverage`; an absent or partial bucket a NaN row, a bar
  size the store does not keep skipped with a line, never resampled), `kernel.candle_patterns`'
  `CandlePatternSet` and Nautilus's `ExponentialMovingAverage` streamed over it and reset at every
  NaN row, the EMA/pattern filter, the window around a hit, and the forward-return table through
  `research.domain.events` within each hit's own run of adjacent bars `[amended 2026-09-28: Story 27.7]`.
- **`notebooks/`** — numbered jupytext pairs since Story 27.2 (`<nn>_<name>.py`, percent format,
  the source of truth, plus its output-free `.ipynb`; `jupytext.toml`, `make notebooks`), every
  one parameterised through `_params.py` and run against a fixture archive by
  `research/tests/test_notebooks.py`: `01_catalog_inspection` (what the archive holds over a
  window) and `02_microstructure` (spread, depth, OBI/OFI, microprice edge, trade flow and
  impact, funding/basis/OI, return autocorrelation, volatility signature and realised
  volatility per instrument over a window) `[amended 2026-09-28: Story 27.3]` and
  `03_correlation` (return correlation at 1 m/5 m/1 h/1 d from the candle store's bars, clustered
  heatmaps, rolling correlation against an anchor, single-linkage clusters with merge distances,
  funding and OI-change correlation, a cluster built into a `RunSpec` that it does not run, and per
  asset the cross-venue mid basis, lead-lag peak in words, funding differential and volume share;
  every number from `research.domain.correlation` and `research.application.aligned`, same-asset
  matching from `kernel.venues.asset_key`) `[amended 2026-09-28: Story 27.4]` and
  `04_backtest_evaluation` (one strategy by string path through `BacktestRunner`: equity,
  underwater and drawdown episodes, the `MetricReport` table, rolling Sharpe, trade
  distributions, PnL by UTC hour and weekday, the trade list, a two-parameter sweep heatmap with
  its top runs and runtime, and a walk-forward with the joined out-of-sample equity and metrics
  beside the in-sample ones; every number from `research.application.evaluation`/`walk_forward`
  and `research.domain`, no `BacktestNode` or PnL arithmetic in a cell; the one way to evaluate a
  strategy interactively; its archive-sized constants go through `_params.setting`, which the
  harness's `NOTEBOOK_ENV` shrinks to the fixture) `[amended 2026-09-28: Story 27.5]` and
  `05_monte_carlo` (04's strategy run and sweep through `BacktestRunner`, then the trade-order and
  block bootstraps' equity fans, terminal-wealth and path-drawdown histograms with the observed
  values marked, risk of ruin at three levels, the Sharpe confidence interval and the deflated
  Sharpe of the sweep's best point with a verdict sentence; every figure titled with its seed and
  path count, every number from `research.domain.monte_carlo`/`research.application.robustness`)
  `[amended 2026-09-28: Story 27.6]` and `06_candlestick_scanner` (the 22 `kernel.candle_patterns`
  patterns at every stored timeframe, an EMA filter and a pattern filter, one hits table, a plotly
  chart centred on a chosen hit with its EMA, and forward returns with the hit rate per pattern,
  direction and horizon; every number from `research.application.patterns` and
  `research.domain.events`, no TA-Lib) `[amended 2026-09-28: Story 27.7]`. No legacy notebook
  remains: `dydx_catalog_pandas.ipynb` was deleted in Story 27.2, `backtest.ipynb` in Story 27.5
  and `candlestick_pattern_scanner.ipynb` in Story 27.7.
  `README.md` is the one research page: the notebook index, the recipes (a notebook, a metric, a
  pattern), the local launch and the backtest how-to `[amended 2026-09-28: Story 27.9]`.

**Reads:** Parquet catalog (market-data rows only via `kernel.catalog_files`,
`BacktestDataConfig` or a catalog `query` bounded by both `start=` and `end=`, enforced by
`research/tests/test_research_reads.py`), `candles_*.db` (read-only, `candles.application.queries`),
`data/errors/*.jsonl` (read, `observability.error_ledger`), `data_api` `/api/rankings` and
`/api/metrics/*` (HTTP) `[amended 2026-09-28: Story 27.1]` `[amended 2026-09-28: Story 27.2]`.
**Publishes:** nothing.

---

## 3. `ranking/` — the one place ranking gets computed

Extracted from what used to be inline logic in `dashboard.py` (Story 1.8) specifically
so the web UI and any future reader can never disagree on coin order; a bounded context with
its own aggregate since Story 25.2 (it was one engine module with twelve module globals).

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
- **`domain/derivs.py`** (Story 33.4) — `DerivsState`, one per instrument: funding, the
  open-interest series, basis, the 1 h liquidation window, the hourly traded volume and the 24 h
  range behind the rank row's appended derivatives fields.
- **`application/engine.py`** — `RankingEngine`: the `snapshots:raw`/`ranking:control`/
  `derivs:raw`/`liquidations:raw` handler (`snapshots:raw` decoded only by
  `DydxSecondSnapshot.from_dict`, `derivs:raw` only by `kernel.derivs_wire.from_wire`), the volume poll, the slow
  metrics loop (one-time catalog backfill per instrument, then `metrics.db` every minute) and
  the heartbeat, through the ports in `application/ports.py`.
- **`application/queries.py`** — `history()`/`nearest()`, the read service for `metrics.db`
  (Story 1.4/FR8's "how did this coin's rank evolve over time"): read-only connections per call.
- **`infrastructure/`** — Redis (publisher + listener), `metrics_store.py`
  (`SqliteMetricsStore`, the sole writer), `catalog_prices.py` and `catalog_derivs.py` (over `kernel.catalog_files`) and
  one volume source per venue (`volume_{dydx,bybit,hyperliquid}.py`, requests built with
  `kernel.venue_http`), all constructed by `__main__.py`.

**Reads:** `snapshots:raw`, `ranking:control`, `derivs:raw` and `liquidations:raw` (Story 33.4),
the Parquet catalog (backfill only).
**Publishes:** `rankings:live`; `markets:live` (each venue's market names, every volume cycle,
Story 29.5).
**Writes:** `metrics.db`.

---

## 4. `bots/` — the actual trading bots

The one place in `platform/` where `TradingNode`/`Strategy` usage is sanctioned
(architecture AD-8 amendment) — everywhere else in `platform/` treats `nautilus_trader` as
a library only. Moved into its own context in Story 25.3 (its old path's re-export shims were deleted in
Story 26.1); every `bots:*` payload, `fills.db` row, env var and the `live-paper`
compose service are unchanged, proven by `bots/tests/test_replay.py` against payloads recorded
from the pre-move code.

- **`domain/`** — `config.py`: `PaperFleet` (many bots, one Sandbox pool per venue; its
  `PaperConfig` has no `mode` field; each `BotConfig` names its `strategy`, default `dummy`, and
  a read-only `params` table for it, both optional keys that default `[amended 2026-09-28: Story
  27.8]`) and `ExecBot` (one bot, `ExecConfig.mode` ∈
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
  `test_boundaries.py` fails any other `TradingNode` importer; each bot's strategy from
  `STRATEGIES` by its `strategy` key — `dummy` built directly, `candle_pattern` by the string
  path `research.strategies.candle_pattern_strategy:CandlePatternStrategy` through Nautilus's
  `StrategyFactory`, so bots has no research import (`test_boundaries.py`) while the image ships
  `research/` (`test_images.py`'s `_STRING_PATH_IMPORTS`); `check_strategy` refuses an unknown
  strategy, params on `dummy` and a params key the bot owns; an `ExecBot` always runs
  `DummyStrategy`, a Known limit `[amended 2026-09-28: Story 27.8]`; since Story 33.14 also
  `liquidation_cascade`, refused on an id without the liquidation feed, and for a fleet holding
  one the `LIQUIDATIONS` data client of `liquidation_data_client.py`, the Redis bridge of
  `liquidations:raw` whose connection status caps the bot's heartbeat), `cache_reader.py` (every
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
- **A research strategy as a paper bot** `[amended 2026-09-28: Story 27.8]` — a bot with
  `strategy = "candle_pattern"` runs `research/strategies/candle_pattern_strategy.py`'s
  `CandlePatternStrategy` (the scanner's `kernel.candle_patterns` detector, its EMA filter, a
  bar-count/opposite-pattern exit and a reduce-only ATR stop), the same class and string path a
  backtest runs; its `[bots.params]` are the strategy's config. `bot_tui`'s `v` key shows each
  bot's own strategy source from `/app/strategy_source/<Class>.py`.

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
- **`collector_pane.py` / `collector_state.py`** — the Collector pane (`:data`): one section per
  venue from `collector:status` (the row's venue derived from its id through `kernel.venues`,
  the plan facts from the venue's aggregate); `p` unpin / `x` stop behind a type-to-confirm
  prompt, published to `collector:control` with the id's venue, only on a venue whose plan
  accepts commands (every venue since Story 29.4) and whose status is fresh -- refused with the
  reason otherwise (Story 29.2).
- **`market_browser.py` / `markets_state.py`** — the Collector pane's `/` market browser (Story
  29.5): searches every venue's market names from `markets:live` (names only: no volume or price,
  so no rankings view comes back), grouped per venue under its Collector header, each row marked
  from `collector:status` (`collected`, `pending`, `failed: <reason>` from the aggregate's
  `last_refusal`, `excluded`, `no answer`); `a` adds the focused row with `collector:control`'s
  `start` behind the type-to-confirm prompt, refused with the reason before any send.

**Reads:** `bots:status`, `bots:history:*`, `bots:incidents:*`, `collector:status`,
`archive:status`, `markets:live`.
**Publishes:** `bots:control`, `collector:control`.
**Never imports:** `bots` internals (control-plane only, per AD-10) or
`capture`/`ranking` stateful internals (pure/shared-type imports only).

---

## Redis channel reference

| Channel | Publisher(s) | Subscriber(s) | Payload |
|---|---|---|---|
| `snapshots:raw` | the three collectors | `ranking_engine`, `data_api` | One `DydxSecondSnapshot`-shaped message per instrument per second |
| `rankings:live` | `ranking_engine` | `data_api` | `{mode, updated_at, ranks: [{instrument_id, rank, volume24h, volatility_score}]}`, on change + heartbeat |
| `markets:live` | `ranking_engine` (every volume cycle, Story 29.5) | `bot_tui` (market browser) | One message per venue with a fresh volume source, `{venue, ts, markets: [{instrument_id, symbol}]}` sorted by id -- names only |
| `ranking:control` | `data_api` (`PUT /api/rankings/mode`, Story 25.1a) | `ranking_engine` | Mode-switch request `{"mode": "volume"` \| `"volatility"}`, last-write-wins |
| `liquidations:raw` | the Bybit collector (Story 33.1) | `ranking_engine`, `data_api` (`LiveCandleBus`, which hands the rows to `LiveDerivsBus`), `live-paper` (only for a fleet with a `liquidation_cascade` bot: the `LIQUIDATIONS` data client, `bots/infrastructure/liquidation_data_client.py`, Story 33.14) | One JSON array of `Liquidation.to_dict` rows per decoded frame (integer units, both precisions) |
| `derivs:raw` | the three collectors (Story 33.4) | `ranking_engine`, `data_api` (`LiveDerivsBus`) | One JSON array per sample tick (none when empty) of `kernel.derivs_wire` rows `{instrument_id, kind: mark\|index\|funding\|oi, t, ts_init, value}` (+ `interval`, `next_funding_ns` on funding), values exact text; at most once |
| `bots:status` | `bots` | `bot_tui` | Per-bot PnL/position/mode/heartbeat, on a timer |
| `bots:control` | `bot_tui` | `bots` | `{bot_id, action: "start"` \| `"stop"}` — never a mode field |
| `collector:status` | `collection_control` (`StatusPublisher`, in every collector process: dYdX, Bybit, Hyperliquid) | `bot_tui` | One message per planned instrument (`id`, `liquid`, `last_trade_ts`, `trade_backfill`, plus `"pending": true` when capture has not applied it, Story 25.4), the per-venue aggregate (`unpinned_ids`, then `venue`, `cap`, `accepts_commands`, `min_liquidity_usd`, `last_apply`, Story 29.2; `last_refusal`, Story 29.5), and a `removed` tombstone on stop/unpin |
| `collector:control` | `bot_tui` | `collection_control` (`ControlService`, in every collector process, each acting on its own venue's messages) | `start`/`unpin`/`stop`/`pin_top_liquid` requests, `{action, id, venue}` (`venue` appended in Story 29.4; absent = dYdX) |
| `archive:status` | `archive` (`ArchiveScheduler`, Story 25.1b) | `data_api` (`GET /api/archive/status`), `bot_tui` (Collector pane) | `{next_run, next_intraday, running, last_run, last_intraday}`: each run `{run_id, kind, day, days, started, finished, steps: [{venue, name, exit, duration_s}]}` (`running` has no `finished`), after every step, on start and every 30 s |
| `archive:control` | `data_api` (`POST /api/archive/run`, Story 25.1b) | `archive` | `{"command": "run_now", "day": "YYYY-MM-DD"` \| `null}` (null: yesterday); anything else is ledgered `archive.control_rejected` and ignored |

## Storage reference

| Store | Writer | Readers | Contents |
|---|---|---|---|
| Parquet catalog (`data/catalog/`) | all three collectors; the `archive` service's steps (closed days, and closed hours of the small types, only; Story 25.1b) | `ranking` (price backfill; open-interest and liquidation backfill since Story 33.4), `data_api`, `research` (backtests, notebooks) `[amended 2026-09-26: Story 25.2]` | Second-snapshots (`DydxSecondSnapshot`, trades folded in rather than stored raw — audit D-45), mark/index price, funding rate, `OpenInterest`, instrument definitions, plus `order_book_deltas` for the dYdX instruments that opt in, and the Story 22.13 raw `trade_tick/` archive (pruned nightly by `archive.prune_catalog --trade-retention-days 7`; `docs/DATA_DICTIONARY.md` §1.1). Minute bars retired 2026-09-20 (D-35). Nautilus-native, zero-conversion `[amended 2026-09-20: Epic 22 story 22.8, review pass]` |
| `candles_{dydx,bybit,hyperliquid}.db` (SQLite, `data/candles/`) | that venue's collector (through the `SecondSink` port its entrypoint injects, Story 24.1 — the collector core no longer opens the file), plus `compare_klines` for the `verified_days` table | `data_api`, `prune_catalog` (via the `VerifiedDays` port) | Finished 1m..1D bars derived from raw 1s (D-35), plus the `verified_days` day-status table the archive tools reach through the `VerifiedDays` port. Fully rebuildable: `python -m candles.rebuild` |
| `metrics.db` (SQLite) | `ranking_engine` (`ranking.infrastructure.metrics_store`) | `data_api` (read-only mount, through `views` → `ranking.application.queries`) | Historical ranking snapshots (Story 1.4/FR8); the derivatives, liquidation and flow columns appended by Story 33.4, added in place as nullable by the store's own migration |
| Nautilus `Cache` (Redis-backed, `bots`) | `bots` | `bots` only (strategy-scoped reads, `bots.infrastructure.cache_reader`) | Orders/positions for the running bots (Story 4.6); never read outside `bots` — its Redis encoding is not a contract |
| `fills.db` (SQLite, `data/live_paper/`) | `bots` (`bots.infrastructure.fills_store`) | `bots` only; published as `bots:history:*` | One append-only row per fill, every bot (Story 4.6) |
| `data/archive/state.json` (JSON, Story 25.1b) | the `archive` service (`archive.infrastructure.state_store`, atomic via `CatalogFiles`' `write_json_atomic`) | the `archive` service only | The scheduler cursor: `last_run_day`, each venue's `last_success_day`, the last run and last intraday run. Never a data verdict: `verified_days` is the only day status (AD-D9), and reconcile and prune never read it |
| `data/errors/<service>.jsonl` (JSON lines, Story 23.3) | that service (`collector`, `bybit_collector`, `hyperliquid_collector`, `archive`, `ranking_engine`, `data_api`, `live-paper`, `bot_tui`) | `data_api` (`GET /api/errors`'s `services` block), `archive.crosscheck_errors` | Every `observability.error_ledger.record()` call, durably: `ts_ns`, `service`, `pid`, `site`, `detail`, `exc_type`, `suppressed`; plus one `process_start` line per boot. Rotates by size (`.1`..`.N`, default 20 MB, 10 backups kept alongside the live file); at most 60 lines/site/minute, exact `suppressed` carry |

---

## Deployment topology (`docker-compose.yml`)

All services bind `127.0.0.1` only / `network_mode: host` — nothing is reachable
without an SSH tunnel over Tailscale (see README's remote-access section).

| Service | Started by default? | Restart policy | Why |
|---|---|---|---|
| `redis` | yes (`make up`) | `always` | Shared bus, no state to lose |
| `collector` (dYdX) | **no** — `profiles: ["dydx"]`, `make up-dydx` (`make down-dydx` removes it) | `always` | Collection moved to Bybit and Hyperliquid (Story 29.3, decision 2026-09-26); kept runnable, its archive ages out through the nightly (`docs/DEPLOY_CHECKLIST.md` §8) |
| `bybit_collector`, `hyperliquid_collector` | yes | `always` | Same engine, same catalog, other venues (Epic 22) |
| `archive` | yes | `always` | Nightly maintenance scheduled in our own code (Story 25.1b): must survive reboots and redeploys; replaces the host crontab line |
| `ranking_engine` | yes | `always` | Sole ranking computer |
| `data_api` | yes | `always` | Web UI (React SPA) + read-only FastAPI over the catalog/`metrics.db`, `:9100` |
| `dozzle` | yes | `always` | Log viewer, `:8080` |
| `live-paper` | **no** — `profiles: ["live-paper"]`, `make up-live-paper` | `on-failure:5` | Explicit opt-in per Story 3.1; capped restarts so a bad config doesn't crash-loop against dYdX's API |
| `bot_tui` | **no** — `profiles: ["tui"]`, `docker compose run` | n/a (one-shot) | Interactive tool, never a background daemon |

One durable base image plus three thin layers (not a two-image split): `nautilus-trader-base` (rebuilt rarely, `make build-base`, ~15 min) →
`collector.dockerfile` (thin layer, rebuilds in seconds; it also installs Debian's `rclone` for
the backup step) reused by the three collectors, `archive`, ranking_engine and bot_tui (`data_api`
has its own `data_api.dockerfile`, with a frontend-build stage); `bots.dockerfile` (renamed after
its context in Story 26.3) is `live-paper`'s own thin layer on the same base (it
ships `bots`, `kernel`, `observability` and `tests`; the old path's shims it shipped were deleted
in Story 26.1).

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
entrypoint imports; `test_hotpath.py` replays two bursts -- `default` (30 instruments, 20 levels)
and `scale` (90 instruments, 200 levels; Story 28.2) -- through `CaptureService._process_data` and
through the queue (`_on_data` -> `_ingest_loop`), and times the flush encode, against
`tests/fixtures/hotpath_baseline.json` and `hotpath_baseline_scale.json` (AD-D5, audit D-65; the
baselines are recorded into the checkout by `make hotpath-baseline` only, and a missing one fails
`make test`).

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
