---
name: 'DDD redesign seed — platform/ (today troll/)'
type: architecture-seed
purpose: input for the bmad-architecture run that produces the DDD spine; record of the 2026-09-21 planning session
scope: 'all of troll/ (to be renamed platform/): collector_core + dydx/bybit/hyperliquid collectors, common, ml_signals, ranking_engine, data_api + frontend, live_paper, bot_tui'
status: final
created: '2026-09-21'
parent: '_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md'
---

# DDD redesign seed

Produced 2026-09-21 in an interactive planning session. It is the seed the
`bmad-architecture` skill distils into
`_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`,
with the 2026-07-01 Gatekeeper spine as the parent whose AD-1..AD-11 are inherited unchanged.

## Decisions taken in the session (do not re-ask)

- **Scope:** all of `troll/`.
- **Depth:** full tactical DDD (aggregates, value objects, domain events, repositories as
  ports, application services, anti-corruption layers), admitted only where each pattern
  names an invariant.
- **Deliverable of this pass:** the architecture spine only; no implementation stories yet.
- **Rename:** `troll/` becomes **`platform/`**. Executed as migration step 0, alone, only
  after story 22.12 has merged and the bmad-loop run is idle. `platform` is also a Python
  stdlib module: the directory must stay a namespace directory (no `platform/__init__.py`),
  guarded by a test; a namespace portion never shadows a regular stdlib module.
- **Migration:** incremental, strangler-style, one bounded context per story, every step
  deployable alone; catalog schema, Redis payloads, SQLite schemas, compose service names
  and env vars frozen for the whole migration.
- **Epic 22:** every story 22.1–22.14 and its code is covered, including 22.14 (merged
  `98bb8e493e`, adding `collector_core/{feed,venue_http,trade_backfill}.py`) and 22.12
  (in flight, modelled from its story file and tagged `[ASSUMPTION]`).

## Epic 22 coverage — every story, its code, its DDD home

This table goes into the seed and the spine's traceability section. "Home" is the bounded
context from the design seed below.

| Story | Status | New code | DDD home |
|---|---|---|---|
| 22.1 core extracted | awaiting-operator | `collector_core/collector.py` `Collector`, `config.py` `CoreConfig`, duck-typed client contract | Capture: `CaptureService`, `VenueFeed` port |
| 22.2 dYdX onto the core | awaiting-operator | `DydxCollector` hooks `_apply_deltas`/`_handle_crossed_book`, `uncross.py`, control plane as `extra_loops` | Capture policies (`LevelTagger`, `CrossedBookPolicy`); Collection Control |
| 22.3 shared types + one `OpenInterest` | awaiting-operator | `collector_core/{second_snapshot,open_interest}.py` | Shared Kernel |
| 22.4 Bybit spot + perp/spot explicit | awaiting-operator | `common/venues.py` `market_kind`, `-SPOT` ids, `market` field in API/UI | Kernel `venues`; Views |
| 22.5 order-book validation per venue | awaiting-operator | Bybit `u` canary, HL full-snapshot (no `resync_orderbook`), REST cross-check `book_check.py`, `feed_stale_seconds`, `fetch_book_levels` | Capture: `SequenceCanary` policy, `FeedLiveness` VO, `BookCrossCheck` service |
| 22.6 `live_paper` multi-venue paper | done | `live_paper/venues.py` `VenueSpec`/`VENUES`, one data+Sandbox client per venue | Bot Operations: `NautilusHost` ACL |
| 22.7 demo/testnet + real money Bybit/HL | awaiting-operator | `ExecConfig` `mode = "exchange_demo"`, creds env map | Bot Operations: `RealMoneyBot` aggregate type |
| 22.8 spine/rules/docs | done | doc amendments only | parent spine (inherited) |
| 22.9 historical bar backfill Bybit/HL | done | `collector_core/backfill_bars.py` (832 lines, REST klines → catalog `Bar`s, idempotent) | Archive Maintenance: `backfill_bars` app service + kline ACLs |
| 22.10 rankings across venues + filter | awaiting-operator | `ranking_engine/engine.py` `parse_{bybit,hyperliquid}_volume_24h`, `_volume_sources()`, `ranking_engine.volume24h` ledger site; venue chips in UI | Ranking: `VolumeSource` ports (one per venue); Views |
| 22.11 nightly consolidation every venue | awaiting-operator | `collector_core/consolidate_catalog.py`, `make consolidate`/`backup-catalog` (rclone) | Archive Maintenance: `consolidate_day`, `CatalogFiles` port |
| 22.13 raw trade archive, exact fold, rebuild, reconcile | awaiting-operator | `collector_core/{fold,rebuild_seconds,compare_klines,prune_catalog,nightly,archive_gaps}.py`, `data/trade_tick/` archive, `verified_days` table, `_TRADE_CARRY_NS` flush rule | Kernel `fold`; Archive Maintenance `ArchiveDay` state machine, `RetentionPolicy`, nightly saga; Candles `mark_verified` |
| 22.14 trade gap closure: REST backfill + dual feed | awaiting-operator (story file still says ready-for-dev; fix that header) | `collector_core/feed.py` (`Feed` name+group, `MAIN_FEED`, `REST_FEED_NAME`), `venue_http.py` (shared stdlib REST transport + venue URL maps), `trade_backfill.py` (`fetch_trades`, exact `Price.from_str` parsing, per-venue parsers, `BackfillError`, `Fetched`), `CoreConfig.trade_feeds`, collector `_feed_state_loop`/`_trade_backfill_loop`/`_first_copy_feed`/`_register_trade`/`_apply_backfill`/`_ledger_abandoned_backfills`, HL client twin socket | Capture: `Feed` VO, `FeedGroup`, per-feed `FeedLiveness`, `TradeArbitration` (first copy wins, second = duplicate), `BackfillRequest` VO, `TradeBackfill` domain service behind a `VenueTradeHistory` port (ACL per venue); `venue_http` → `capture/infrastructure` shared with `archive/` via kernel-level transport |
| 22.12 exchange-time bucketing (in flight) | ready-for-dev, dev-running | planned: `CoreConfig.book_time_source`, `hold_back_seconds`, `measure_lag.py`, `collector.late_trade` counter, `ts_event`-ordered delta application on Bybit/HL | Capture: `BookTimeSource` policy, `HoldBack` VO on the sampler; Archive: rebuild on `ts_event` (already 22.13). Modelled from the story file; marked `[ASSUMPTION]` until merged |

Also carried from Epic 22's audit rows: D-45..D-62 (`docs/DATA_INTEGRITY_AUDIT.md`) are the
invariants behind the Capture and Archive aggregates; the spine cites them by id.

## Design seed (saved verbatim as Step 1)

### 1. Design paradigm

**"Gatekeeper, expressed as bounded contexts."** The fail-closed single-writer rule stays
the load-bearing idea; DDD gives it a shape: the gate becomes an explicit aggregate
(`LiveBook`) plus a domain service (`SecondSampler`) inside one bounded context, and every
other module becomes a named context with a named relationship to it.

Tension stated up front: `platform/CLAUDE.md` DESIGN-01 (YAGNI, "no abstractions,
interfaces, factories") conflicts with full tactical DDD. Resolution: **a tactical pattern
is admitted only where it names an invariant** (the spine lists the invariant next to every
aggregate/port). Ports are `typing.Protocol`, not ABC hierarchies; no event-bus library, no
DI container, no repository base class (minimize-dependencies rule). A DESIGN-01 wording
amendment is proposed under Deferred, for the user to decide.

Hot-path rule (HFT standard): the DDD shape adds **zero per-message allocations** on the
ingest path. `on_data(data, feed)` stays an O(1) enqueue; `LiveBook.apply()` applies deltas
straight into the Nautilus `OrderBook`; domain events are emitted per sampled second
(`SecondSampled`) and per state transition (`BookCrossed`, `FeedReconnected`), never per
delta or per trade. The spine states this as an AD with a benchmark acceptance test
(existing `.benchmarks/` harness) so the refactor cannot silently regress latency.

### 2. Ubiquitous language

One table, term → definition → code identifier → forbidden synonyms. Highlights:

| Term | Meaning | Identifier | Retire |
|---|---|---|---|
| Venue | dYdX / Bybit / Hyperliquid; uppercase Nautilus token | `Venue` (kernel) | "exchange", package names |
| Instrument | one tradable id `SYMBOL-QUOTE-KIND.VENUE` | `InstrumentId` | "coin" (UI-only word, allowed in `views`) |
| Feed | one WebSocket connection; a Feed Group is a primary + its trades-only twin | `Feed`, `FeedGroup` (22.14) | "socket", "connection" |
| Live Book | the local L2 replica for one instrument | `LiveBook` aggregate | `_live_books[iid]` |
| Second Snapshot | the 1 s sampled record (top-20 + folded trades) | `DydxSecondSnapshot` — **class name pinned**: the catalog directory `custom_dydx_second_snapshot` is derived from `__name__` by `nautilus_trader.persistence.funcs.class_to_filename`. Known limit; upgrade path = one-off catalog directory migration. | "tick", "row" |
| Sample Tick | the once-per-second gate pass | `SecondSampler.sample()` | `_sample_tick` |
| Verdict | Accepted / Rejected(reason) from the gate | `SampleVerdict` VO | ad-hoc `continue`s |
| Fold | the exact trades→second aggregation | `fold_trades` (kernel) | "aggregate" (overloaded with DDD) |
| Two clocks | `ts_event` venue time, `ts_init` arrival time | `TwoClocks` VO | bare "timestamp" |
| Provisional / Verified day | day state after live capture / after rebuild + reconcile | `DayStatus` VO (`verified_days` table stays) | — |
| Backfill | REST recovery of trades (22.14) or bars (22.9) | `TradeBackfill`, `backfill_bars` | "replay" (reserved for venue subscribe-time history) |
| Collection Plan | which instruments a venue collects (+ exclude, pins) | `CollectionPlan` aggregate | "config instruments", "watchlist" |
| Coin Ranking, Ranking Mode | the ordered universe and volume/volatility switch | `RankingBoard`, `RankingMode` | "watchlist" (research-side name for the consumed ranking) |
| Bot | one strategy instance with a stable `bot_id` | `Bot` aggregate | "strategy" (reserved for Nautilus `Strategy`) |
| Feed-stale vs Instrument-stale vs Heartbeat-stale | three distinct staleness kinds | `FeedLiveness`, `BookStaleness`, `HeartbeatStaleness` VOs | bare "stale" |
| Error Ledger site | a named continue-past-failure point | `error_ledger.record(site)` | — |

### 3. Bounded contexts and the context map

| Context | Subdomain | Today | Target package |
|---|---|---|---|
| **Market Data Capture** | core | `collector_core/{collector,feed,trade_backfill,book_check,integrity}.py` + venue `client.py`/`uncross.py`/sequence canary | `capture/` (+ `capture/venues/{dydx,bybit,hyperliquid}/`) |
| **Collection Control** | supporting | `DydxCollector`'s control plane: hot-reload, pin/unpin/exclude, `collector:status/control`, liquidity tiering, prune | `collection_control/` |
| **Archive Maintenance** | supporting | `collector_core/{rebuild_seconds,consolidate_catalog,prune_catalog,repair_catalog,compare_klines,archive_gaps,nightly,backfill_bars,migrate_*}.py`, `dydx_collector/normalize_snapshot_schema` | `archive/` |
| **Candle Derivation** | supporting | `ml_signals/candle_store.py`, `collector_core/build_candles.py`, `ml_signals/candles.py` | `candles/` |
| **Coin Ranking** | core | `ranking_engine/*`, `ml_signals/{metrics_computer,rank_history}.py` | `ranking/` |
| **Bot Operations** | core (the eventual product) | `live_paper/*` | `bots/` |
| **Alerting** | supporting | `data_api/alerts.py`, `routes/alerts.py` | `alerting/` |
| **Research & Backtesting** | supporting | `ml_signals/strategies/*`, `run_backtest`, `watchlist`, `performance_metrics`, `catalog_stats` (query half) | `research/` |
| **Market Views** (CQRS query side) | supporting | `ml_signals/{ranking_columns,screener_columns_config,chart_indicators,chart_indicator_config,custom_indicators,book_features,footprint,chart_data}.py`, `data_api/{live_candles,redis_bus}.py` | `views/` |
| **Observability** | generic | `ml_signals/error_ledger.py`, `Collector._notify`, watchdog transition, dYdX incident handler | `observability/` |
| **Shared Kernel** | — | `collector_core/{second_snapshot,open_interest,fold}.py`, `common/venues.py`, `ml_signals/venue.py`, `ml_signals/indicators.py` (pure classes), `collector_core/venue_http.py` (stdlib REST transport + venue URL maps, used by capture **and** archive) | `kernel/` |
| Interface: `data_api/`, `bot_tui/`, `frontend/` | — | unchanged names; thin adapters over `views/` + context application services | same |

Context-map relationships (each an AD, pattern named):
- **Venue adapters (nautilus_pyo3) → Capture: Anti-Corruption Layer** per
  `capture/venues/<v>/client.py` (precision re-stamp, subscribe cap/throttle, OI poll,
  replay classification, HL `ts_event` re-stamp D-62, the trades-only twin socket). The
  duck-typed client contract becomes a `VenueFeed` `Protocol` in
  `capture/application/ports.py`.
- **Venue REST → Capture and Archive: ACLs** `VenueTradeHistory` (22.14 backfill) and
  `VenueKlines` (22.9/22.13), each with per-venue parsers that stay pure and
  fixture-tested (`tests/fixtures/*_trades_*_20260921.json`).
- **Capture → downstream: Published Language / Open Host Service** — the Parquet archive
  (Arrow schemas of kernel types, plus `trade_tick/` raw archive) and Redis `snapshots:raw`.
  Downstream contexts are Conformist.
- **Capture ↔ Archive Maintenance: Shared Kernel + Partnership** (`fold`, snapshot types,
  `venue_http`, catalog layout; changed only together).
- **Capture → Candle Derivation: Customer/Supplier via a port** — Capture defines
  `SecondSink`; `candles/` implements it; the venue `main()` injects it. Resolves the
  writer→reader import in the parent spine's Deferred list.
- **Ranking → UIs: Open Host Service** (`rankings:live`) + command channel
  `ranking:control`.
- **Bot Operations ↔ Nautilus runtime: ACL** (`NautilusHost`, strategy-scoped `Cache`
  reads). AD-8's sanction preserved verbatim.
- **Bot Operations → UIs: Open Host Service** (`bots:status`, `bots:history:*`) + command
  channel `bots:control` (AD-10 unchanged).
- **Observability: generic, imported by everyone, imports nothing.**
- **Collection Control → Capture: Customer/Supplier in-process** (`CollectionPlan` →
  `InstrumentAdded/Removed` → subscribe/unsubscribe).

### 4. Tactical model per context

Template per context: aggregates (with invariants) → entities → value objects → domain
events → domain services → repositories (ports) → application services → infrastructure
adapters → deliberately not modelled.

**Market Data Capture (`capture/`)**
- `LiveBook` aggregate (per instrument): wraps a Nautilus `OrderBook` by reference; last
  delta ns (per side on dYdX), crossed-since, resync-pending, per-level message-id tags
  (dYdX `LevelTagger` policy), last `u` (Bybit `SequenceCanary` policy), and (22.12)
  the `BookTimeSource` policy that orders deltas by `ts_event` on Bybit/HL. Invariants:
  never yields a top-of-book while crossed/stale/empty (`snapshot_top` → `SampleVerdict`);
  a delta applies at most once, only after a baseline; resync is a fallback that clears
  state and emits `BookResynced`. Events: `BookCrossed`, `BookUncrossed`,
  `BookResyncRequested`, `SequenceBroken`.
- `TradeIntake` aggregate (per instrument): bounded `seen_trade_ids`, stale-history filter
  (DATA-06), **per-feed first-copy arbitration** (22.14: first copy wins, second counted as
  duplicate, per-feed baseline), late-trade counting (22.12: never dropped, archived,
  excluded from the live second). Counters are state, reported at flush (DATA-05).
- `FeedGroup` aggregate (per venue): `Feed` VOs (name, group, `trades_only`), per-feed
  `FeedLiveness`, reconnect detection (silence gap then messages), one-sided-outage alert
  across the group, `BackfillRequest` scheduling with abandonment ledgering. Events:
  `FeedReconnected`, `FeedSilent`, `BackfillAbandoned`.
- `SecondSampler` domain service: the gate for every instrument at a tick →
  `list[DydxSecondSnapshot]` + `list[SampleRejected]`; pure; honours `HoldBack` (22.12).
- `TradeBackfill` domain service (22.14): given a `BackfillRequest` and a
  `VenueTradeHistory` port, returns exact `TradeTick`s (`Price.from_str` of proven-exact
  text, never float) for `TradeIntake` to dedup and the archive to append with
  `ts_init = now`. Per-venue capability (dYdX recoverable; Bybit last 1000/60; HL last 10)
  is a `BackfillCapability` VO in the venue package, cited in the data dictionary.
- Value objects: `SampleVerdict`, `FeedLiveness`, `BookStaleness`, `TwoClocks`,
  `FlushBatch` (ts_init carry rule `_TRADE_CARRY_NS` as a method), `HoldBack`,
  `BackfillRequest`, `BackfillCapability`.
- Policies supplied by the venue package (replacing hook overrides): `CrossedBookPolicy`,
  `LevelTagger`, `SequenceCanary`, `BookTimeSource`. AD-1's "a venue never overrides the
  gate" becomes structural.
- Ports: `VenueFeed`, `VenueTradeHistory`, `ArchiveWriter`, `LiveStream`, `SecondSink`,
  `Notifier`. Adapters in `capture/infrastructure/` (zstd patch, quarantine, instrument
  definitions write, REST cross-check, `venue_http` transport).
- Application service `CaptureService` (today `Collector`): owns the loops as process
  managers (`ingest`, `sample`, `flush`, `crosscheck`, `watchdog`, `feed_state`,
  `trade_backfill`, `candle_prune`); `run_forever` stays the supervisor.
- Not modelled: no `Instrument` entity (Nautilus's), no event bus.

**Collection Control (`collection_control/`)**
- `CollectionPlan` aggregate: instruments (delta-storage + retention entries), `exclude`,
  pins, cap (`_MAX_COLLECTED_INSTRUMENTS = 30` as an invariant). Invariants: excluded ⟂
  collected; every listed id collected; cap never exceeded; a pin is liquid by USD volume
  (AD-7/OBS-03). `LiquidityTier` VO + `classify_liquidity`. Events `InstrumentAdded/
  Removed/Excluded`. Port `CollectionPlanStore` (TOML; comment-loss Known limit kept).
  Application: `ControlService` (`collector:control`), `StatusPublisher`
  (`collector:status`), prune on `InstrumentRemoved`. Known limit: only dYdX has a live
  plan; Bybit/HL plans are static.

**Archive Maintenance (`archive/`)**
- `ArchiveDay` aggregate (venue, instrument, day): `provisional → rebuilt → verified |
  mismatched` (`verified_days`). Invariants: rebuild uses the kernel fold on `ts_event`;
  raw trades pruned only when older than `trade_retention_days` **and** verified.
- VOs `RetentionPolicy`, `ReconciliationResult` (exact, never tolerance),
  `ArchiveGap`. Services `rebuild_day`, `reconcile_day`, `consolidate_day`,
  `backfill_bars` (22.9). Application: the `nightly` chain as a saga (`Step`/`StepResult`
  kept); operator CLIs `python -m archive.<tool>`; `make consolidate`/`backup-catalog`.
- Ports: `CatalogFiles` (the only in-place `pq.write_table` rewriter, AD-6's exception),
  `VenueKlines` ACL per venue.

**Candle Derivation (`candles/`)**
- `CandleSeries` aggregate per (instrument, bar_seconds): watermark, `seconds_observed`/
  `partial`. Invariant: each second applied exactly once; rebuildable from seconds alone.
  `fold_arrays` (seconds→bars) is this context's fold, distinct from the kernel's.
  Repository `CandleStore` (SQLite). Application `apply_seconds` (implements Capture's
  `SecondSink`), `rebuild_day`, `mark_verified`, `window`/`latest` for `views`.

**Coin Ranking (`ranking/`)**
- `RankingBoard` aggregate root replacing the module globals: `mode`, per-instrument
  `InstrumentMetrics` entity (indicator instances, rolling windows, price series,
  last-seen, `VolumeReading`), `RankingsPublisher` (kept). Invariants (AD-9 + 22.10): both
  scores always present; a volume-less row is absent from volume mode and present in
  volatility mode, ledgered at `ranking_engine.volume24h`; stale instruments age out; mode
  is global; publish on change and heartbeat.
- VOs `RankingMode`, `VolumeReading(value_usd, observed_ns)`, `VolatilityScore`. Events
  `RankChanged`, `ModeSwitched`. Ports `VolumeSource` (one per venue; `parse_*` pure),
  `RankingHistory` (SQLite), `LivePublisher`. `metrics_computer.py` moves here.

**Bot Operations (`bots/`)**
- `Bot` aggregate: `BotId` (== `order_id_tag`, AD-11), execution mode derived from the
  config *type* (`PaperFleet` vs `RealMoneyBot`, incl. 22.7's `exchange_demo`), running
  flag, bounded `Incident` entities, heartbeat. `FillLedger` aggregate per bot (AD-10 PnL
  semantics). Ports `NautilusHost` (22.6 per-venue clients via `VENUES`), `CacheReader`,
  `FillsStore`, `StatusPublisher`, `ControlListener`. Nautilus `Strategy` subclasses in
  `bots/strategies/` as framework code.

**Alerting (`alerting/`)** — `Alert` aggregate, `FiringPolicy` VO, `RunState`,
`evaluate` service, `Deliverer` port (webhook/Telegram), `AlertStore` (TOML).

**Research & Backtesting (`research/`)** — consumer only; `Watchlist` ACL over
`/api/rankings`; backtests conform to AD-6; `catalog_stats` split by owner (diagnostics →
`archive/`, series reads → `research/`/`views/`).

**Market Views (`views/`)** — CQRS query side shared by both UIs (SSOT-01/04/05): ranking
columns incl. venue/market chips (22.4/22.10), coin-detail metric set, chart series,
indicator picker, live candle bus, rankings bus. AD-3's reader-side crossed-book skip
(`data_api/routes/snapshots.py:132`) listed as the deviation `views/` must not carry.

**Observability (`observability/`)** — `error_ledger`, `notify`, incident reports,
watchdog transition. Imports nothing from any context.

**Shared Kernel (`kernel/`)** — `DydxSecondSnapshot`, `OpenInterest`, `fold`, venue
helpers (merged `common/venues.py` + `ml_signals/venue.py`), pure `Indicator` classes and
stateless snapshot functions, `TwoClocks`/ns helpers, `venue_http`. Rule: a change touches
every consumer's tests in the same story; nothing stateful or I/O-bearing enters (the
`venue_http` transport is the one stateless I/O helper, admitted because two contexts
must produce byte-identical REST requests).

### 5. Layering rule

Inside every context: `domain/` (no I/O, no asyncio, no Nautilus runtime; may import
`kernel/` and Nautilus model types) → `application/` (ports as `Protocol`, services,
process managers) → `infrastructure/` (Parquet, SQLite, Redis, REST, Nautilus runtime).
Interface layers import only `application/` services and `views/`. Composition roots are
the entrypoints (`python -m capture.venues.dydx`, `ranking`, …). Enforced by
`platform/tests/test_boundaries.py` (plain pytest over `ast` imports; no new dependency),
closing the parent spine's "class hierarchy plus review, not tooling" caveat.

### 6. Traceability

AD-1..AD-11 → aggregate/port/context, and whether the rule became structural; one row per
parent-spine Deferred item resolved (writer→reader imports, private-symbol imports,
`data_api` image packages — moot once packages are cut per context) or left open (buffer
durability, gate-version skew); plus the Epic 22 table above.

### 7. Structural seed

Target tree as in §3 (file level for `capture/` and `ranking/`), plus the surfaces that pin
names and move in lock-step with each package move: the three dockerfiles' `COPY` lines,
`docker-compose.yml` `command:` lines, `Makefile` test list, `CANDLES_DB_PATH`/
`CATALOG_PATH` defaults, and the catalog directory names that must **not** change.

### 8. Migration (strangler, one context per story)

0. The `troll/` → `platform/` rename (Step 3 above) is migration step 0 and ships alone.
1. A move creates the new package and leaves a re-export shim at the old path with a
   `DeprecationWarning` (TEST-04 forces callers to chase it); a shim lives at most two
   stories.
2. Every move is deployable alone: catalog schema, Redis payloads, SQLite schemas,
   compose service names and env vars are frozen for the whole migration
   (published-language freeze list in the spine).
3. Order: `observability/` → `kernel/` → `views/` → `candles/` → `alerting/` →
   `research/` → `archive/` → `ranking/` → `bots/` → `collection_control/` →
   `capture/` last, with the §1 benchmark test as its gate.
4. Each move updates `platform/CLAUDE.md` citations, `ARCHITECTURE.md` and
   `docs/DATA_DICTIONARY.md` in the same commit.

### 9. Deferred / user decisions

- Amend DESIGN-01 to "no abstraction without a named invariant", or keep and accept the
  documented tension.
- Docker packaging per context (recommend: unchanged until the migration is done).
- Renaming `DydxSecondSnapshot` (needs a catalog directory migration) — recommended never.
- Bybit/HL control loops in `collection_control` (allowed, not required).
- Story 22.14's file header says `Status: ready-for-dev` while sprint-status says
  `awaiting-operator`; fix the header when 22.12's merge is handled.

