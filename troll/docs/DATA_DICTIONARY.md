# Data Dictionary: Collection → Signals → Ranking

What the dYdX collector stores, what `ml_signals`/`ranking_engine` compute from it, and
how a value traces from raw feed to the ranking table. All file:line references are
against the `bmad` branch as of 2026-09-05.

---

## 1. Raw data collected (`troll/dydx_collector/`)

The collector (`collector.py`) owns one WS connection per dYdX network and writes
everything through `ParquetDataCatalog.write_data()` — no hand-rolled schemas
(`troll/CLAUDE.md` NAUT-02). Nine distinct types land in the catalog. Six are native
Nautilus types decoded straight from the Rust adapter; two (`DydxSecondSnapshot`,
`DydxOpenInterest`) are custom `Data` subclasses this collector defines because the
PyO3 bindings don't expose the fields another way.

### 1.1 `TradeTick` (native Nautilus type) — **no longer persisted**

- **Source:** `v4_trades` WS channel, decoded by the Rust adapter, delivered via
  `DydxClient._handle_message`'s PyCapsule path (`client.py:138-140`).
- **Fields:** `instrument_id`, `price`, `size`, `aggressor_side` (`AggressorSide.BUYER`/
  `SELLER`), `trade_id`, `ts_event`, `ts_init`.
- **Cadence:** event-driven, one per executed trade.
- **No longer written to the catalog.** `Collector._process_data` explicitly excludes
  `TradeTick` from the buffer/flush path — raw trades accumulated forever for every
  pinned instrument with no retention cap (see §5's old Retention section history),
  and every downstream consumer only ever needed OHLC, not individual prints. Each
  `TradeTick` is still processed live to update a running per-second open/high/low/
  close price, folded into `DydxSecondSnapshot` (§1.7) instead of stored raw. The
  dashboard's "Ticks mode" (individual trade price/size/side scatter, `dashboard.py`)
  was removed in the same change since it has no data source anymore.
- **Subscription scope:** every pinned + liquid instrument (`_subscribe`,
  `collector.py:508-511`). Illiquid instruments are not subscribed to trades.
- **Historical data:** `TradeTick` Parquet files written before this cutover remain in
  the catalog and are still readable — this only stops *new* rows from being written.

### 1.2 `OrderBookDeltas` (native Nautilus type)

- **Source:** `v4_orderbook` WS channel, PyCapsule path, same as trades.
- **Fields:** a list of `OrderBookDelta` (side, price, size, `BookAction` ADD/UPDATE/
  DELETE/CLEAR), plus `instrument_id`/`ts_event`/`ts_init` on the wrapping
  `OrderBookDeltas`.
- **Cadence:** event-driven, one message per book change.
- **Written:** only for instruments with `store_order_book_deltas = true` in
  `config.toml` (`collector.py:486-487`, gated by `self._delta_store`) — this is a
  raw, high-volume type, opt-in per instrument. Independently of storage, every
  pinned/liquid instrument's deltas are always applied to an in-memory `OrderBook`
  (`_apply_deltas`, `collector.py:434-463`) used to build `DydxSecondSnapshot` (§1.7).
- **Retention:** per-instrument `retain_hours` in `config.toml`, pruned by
  `_prune_delta_retention` (`collector.py:276-288`); `None` = kept forever.
- **No sequence-gap detection:** dYdX's WS `sequence` field is connection-global, not
  per-market, so it can't be used to detect a dropped delta for one instrument — see
  `_apply_deltas`'s docstring (`collector.py:434-451`) and `troll/.planning/debug/
  crossed-book-root-cause.md` for the investigation this constraint drove.

### 1.3 `Bar` (native Nautilus type)

- **Source:** subscribed via `DydxClient.subscribe_bars` (`client.py:123-124`), PyCapsule
  path.
- **Note:** the collector's `run()` (`collector.py:771-819`) never calls
  `subscribe_bars` — no bar subscription is currently active. The capability exists in
  `client.py` but is unused; **no `Bar` data is currently written to the catalog by this
  collector.** (`ml_signals/candles.py`'s `aggregate_ohlc` instead derives candles from
  `DydxSecondSnapshot`'s per-second OHLC fields on read — see §2.3.)

### 1.4 `MarkPriceUpdate` (native Nautilus type)

- **Source:** `subscribe_markets()` markets-channel, global for all instruments
  (`client.py:129-131`, `collector.py:781`), plain pyo3-object path (`client.py:142-151`).
- **Fields:** `instrument_id`, `value` (`Price`), `ts_event`, `ts_init`.
- **Precision fix:** re-stamped through `_at_fixed_precision()` (`client.py:45-66`)
  before storage — dYdX's raw feed derives each tick's `Price.precision` from its own
  digit count, so consecutive ticks can carry different precision labels, which
  `ParquetDataCatalog` refuses to merge. See `troll/CLAUDE.md` NAUT-01.
- **Cadence:** event-driven, all subscribed + monitored instruments (markets channel
  covers everything, not just liquid/pinned).

### 1.5 `IndexPriceUpdate` (native Nautilus type)

- Same channel/path/precision-fix as mark price (`client.py:152-161`). Oracle index
  price, distinct from the venue's own mark price.

### 1.6 `FundingRateUpdate` (native Nautilus type)

- **Source:** markets channel, plain pyo3-object path (`client.py:162-163`), forwarded
  via `FundingRateUpdate.from_pyo3` with no transformation.
- **Fields:** `instrument_id`, funding rate value, `ts_event`, `ts_init`.
- **Downstream use:** **none found.** Stored to the catalog but no file in
  `ml_signals/` or `ranking_engine/` reads `FundingRateUpdate` — dead data as of this
  writing.

### 1.7 `DydxSecondSnapshot` (custom `Data` type, `second_snapshot.py`)

The core microstructure record — a 1-second-sampled L2 book snapshot, **not** raw
deltas. Per `troll/CLAUDE.md`'s Signal Architecture rule: store raw inputs, compute
signals on read (SIGNAL-01).

- **Fields** (`second_snapshot.py`, schema at `DydxSecondSnapshot.schema()`):
  - `instrument_id`
  - `bid_prices`, `bid_sizes`, `ask_prices`, `ask_sizes` — up to `BOOK_DEPTH = 20`
    levels each (`second_snapshot.py:36`), index 0 = best bid/ask
  - `buy_volume`, `sell_volume` — summed trade size per side since the last tick
  - `buy_count`, `sell_count` — trade count per side since the last tick
  - `open_price`, `high_price`, `low_price`, `close_price` — OHLC of actual executed
    trade prices within this second, `None` if no trade occurred. This is the
    collector's **only** record of traded price now that raw `TradeTick` is no longer
    persisted (§1.1) — tracked live in `Collector._process_data` as each `TradeTick`
    arrives, popped into the snapshot by `_second_loop`. `ml_signals/candles.py`'s
    `aggregate_ohlc` combines these across multiple seconds for coarser candles;
    `ml_signals/catalog_stats.py`'s `price_series` reads `close_price` as its primary
    price source (falling back to `MarkPriceUpdate` only when no snapshot ever
    recorded a trade for that instrument).
  - `ts_event`, `ts_init`
- **Built by:** `Collector._second_loop` (`collector.py:652-718`), every
  `snapshot_interval_seconds` (config default 0.5s, `config.py:66-68`, `config.toml`
  not shown here but overrideable).
- **Guards before emission:** skips crossed books (`_handle_crossed_book`,
  `collector.py:577-650` — also drives a forced resubscribe/resync after 3s of a
  persistent cross), and skips stale books with no `OrderBookDeltas` for
  `_STALE_BOOK_NS = 5s` (`collector.py:142,682-693`) — both are `troll/CLAUDE.md`
  DATA-01 "flag the gap, never fabricate" implementations.
- **Scope:** pinned + liquid instruments only (`collector.py:672`). Illiquid
  instruments get no snapshots.
- **Dual delivery:** written to the catalog via the normal buffer/flush path
  (`self._on_data(snapshot)`, `collector.py:716`) **and** published live to Redis
  channel `snapshots:raw` (`_publish_snapshot_batch`, `collector.py:291-304`) —
  this Redis stream is what `ranking_engine` actually consumes live (§3); the Parquet
  copy is for backtest/historical replay.

### 1.8 `DydxOpenInterest` (custom `Data` type, `open_interest.py`)

- **Why custom/separate:** open interest is parsed Rust-side but never forwarded to
  Python on either the REST or WS markets-channel path (`open_interest.py:16-24`
  docstring, citing `crates/adapters/dydx/src/python/{http,websocket}.rs`) — the one
  field this collector has to re-fetch itself.
- **Fields:** `instrument_id`, `open_interest` (`Decimal`, stored as a string in Arrow
  to avoid float round-tripping), `ts_event`, `ts_init`.
- **Source:** plain stdlib `urllib` poll of dYdX's public indexer
  `/v4/perpetualMarkets` REST endpoint (`_fetch_markets_json`, `open_interest.py:163-171`),
  every `open_interest_poll_seconds` (config default 300s, `config.py:77`).
- **Also drives liquidity tiering:** `classify_liquidity` (`open_interest.py:109-160`)
  reads the *same* markets JSON response but keys off `volume24H` (USD), not
  `openInterest` (base-token units) — `troll/CLAUDE.md` OBS-03 explicitly calls out
  the token-vs-USD confusion as a past production bug. This classification decides
  which instruments get trade/book subscriptions (pinned/liquid/illiquid tiers,
  `collector.py:21-40`), independent of storing `DydxOpenInterest` itself.
- **Downstream use of the stored `open_interest` field:** **none found** in
  `ml_signals/`/`ranking_engine/` — only the *volume*-based liquidity classification
  (a separate, parallel computation in the same module) is used live. The OI Parquet
  record itself is written and retained but not read back by any of the code
  inspected. Likely intended for future backtest/research use, not currently wired
  into any live signal or ranking.

### 1.9 `InstrumentStatus` (native Nautilus type)

- **Source:** markets channel, plain pyo3-object path (`client.py:164-165`).
- **Downstream use:** **none found** — stored, not read anywhere in `ml_signals/`/
  `ranking_engine/`.

### 1.10 Instrument definitions

- **Source:** one-time REST fetch (`DydxClient.fetch_instruments`, `client.py:91-101`)
  at collector startup, converted via `instruments_from_pyo3()` and written once
  (`collector.py:774-777`). Not a recurring stream — defines the instrument
  universe/precision metadata the catalog needs to interpret every other type
  correctly.

---

## 2. Computed signals / ML features (`troll/ml_signals/`)

Everything here is computed **on read** from the raw types in §1 — nothing in this
section is stored back to Parquet. Per SSOT-01/02 (`troll/CLAUDE.md`), stateless
single-snapshot formulas live as plain functions in `indicators.py`; stateful/rolling
indicators are classes, and for anything shown in a live UI, exactly one process
(`ranking_engine`) is allowed to own the running instance (§3).

### 2.1 Stateless, single-snapshot functions (`indicators.py:409-467`)

All take one `DydxSecondSnapshot`-shaped dict (§1.7) and return a value with no memory
of prior calls:

| Function | Formula | Raw fields used |
|---|---|---|
| `microprice(snapshot)` | `(bid_prices[0]*ask_sizes[0] + ask_prices[0]*bid_sizes[0]) / (bid_sizes[0]+ask_sizes[0])` | `bid_prices[0]`, `bid_sizes[0]`, `ask_prices[0]`, `ask_sizes[0]` |
| `spread(snapshot)` | `ask_prices[0] - bid_prices[0]` | `bid_prices[0]`, `ask_prices[0]` |
| `mid_price(snapshot)` | `(bid_prices[0] + ask_prices[0]) / 2` | `bid_prices[0]`, `ask_prices[0]` |
| `volume_delta(snapshot)` | `buy_volume - sell_volume` | `buy_volume`, `sell_volume` |
| `trade_aggregates(snapshots)` | sums `buy_volume`/`sell_volume`/`buy_count`/`sell_count` across a list — feeds CVD and `avg_trade_size` downstream | all four trade fields |

`Microprice` (`indicators.py:116-155`) is also available as a stateful `Indicator`
class fed one tick at a time (`update_raw`/`handle_quote_tick`) — used where a class
with `.initialized` semantics is more convenient (e.g. `chart_data.py` replay, §2.7),
but produces the identical formula.

### 2.2 Order Flow Imbalance (OFI)

Cont-Kukanov-Stoikov delta formula: at each level, compares this tick's bid/ask
price+size to the previous tick's; a price improvement counts the full new size, an
unchanged price counts the size *delta*, a worse price counts a full withdrawal on
that side. `contribution = bid_term - ask_term`, summed over a rolling window.

- **`OrderFlowImbalance`** (`indicators.py:157-232`) — top-of-book only (level 0),
  rolling sum over `window` updates (default 50). Fed from `QuoteTick` or raw
  bid/ask price+size. Used by `metrics_computer.py` (§2.6) and `chart_data.py` (§2.7).
- **`MultiLevelOFI`** (`indicators.py:269-397`) — same formula applied independently
  at each of the top `levels` price levels and summed, using the full
  `bid_prices`/`bid_sizes`/`ask_prices`/`ask_sizes` lists from `DydxSecondSnapshot`.
  Options: `usd_notional` (multiply each size term by its price level, for
  cross-instrument comparability — e.g. BTC vs. a low-priced altcoin), and
  `zscore_window` (normalize the rolling sum to a z-score over a longer history —
  `(value - mean) / std`). This is the version `ranking_engine` actually runs live
  (§3): three raw variants at levels 3/5/10 (window 300, no z-score) plus one
  z-scored variant at level 10 (window 50, z-score window 3600).

### 2.3 Order Book Imbalance (OBI)

`MultiLevelOBI` (`indicators.py:234-267`): `sum(bid_sizes[:levels]) / (sum(bid_sizes[:levels]) + sum(ask_sizes[:levels]))`.
1.0 = all depth on the bid side, 0.5 = balanced, 0.0 = all ask. `ranking_engine` runs
three instances per instrument at levels 3/5/10.

### 2.4 Footprint / order-book flow (`footprint.py`)

`build_footprint` buckets **resting order-book size changes** (not executed trades —
dYdX L2 deltas have no order IDs, so a shrinking level can't be told apart from a
cancel vs. a fill; see the module's own caveat, `footprint.py:16-29`) into
per-candle, per-price-band cells (`bands_per_candle`, default 4). Each cell tracks
gross `bid_added`/`bid_removed`/`ask_added`/`ask_removed` size (not just net, so a
churning level is visible). Input: `OrderBookDelta`s + `Candle`s (§2.5). Used by the
web dashboard's footprint chart only — no ranking/live-tick consumer.

### 2.5 Candles (`candles.py`)

Two builder functions, both recomputed fresh on every request, not stored — this is
where minute-bar candles come from since the collector itself never subscribes to
`Bar`s (§1.3):

- `build_candles` buckets a flat list of `(ts_event, price, size)` rows into OHLC
  candles at an arbitrary `period_seconds`. Legacy path, kept for any caller still
  working from individual trade prices.
- `aggregate_ohlc` — the one actually used by `dashboard.py`'s
  `_historical_candles_json` — re-buckets already-built 1-second OHLC rows (from
  `DydxSecondSnapshot.open/high/low/close_price`, §1.7/§1.1) into wider candles,
  combining them correctly (open of the first second in the bucket, high/low across
  all of them, close of the last, volume summed) rather than rederiving OHLC from a
  flat price list.

### 2.6 Book features (`book_features.py`)

A second, independent set of L2-derived features, computed by replaying raw
`OrderBookDelta`s (not `DydxSecondSnapshot`) — used by the chart page (§2.7), not by
`ranking_engine`.

| Feature | Formula | Notes |
|---|---|---|
| `depth_profile` | top-N bid/ask prices+sizes from a live `OrderBook` | levels 1-10 default |
| `book_imbalance` | per-level `bid/(bid+ask)`, plus an aggregate across all levels | same imbalance formula as OBI, computed from a live book object instead of stored list fields |
| `liquidity_distance` | price distance from best to where cumulative depth reaches `pct_threshold` (default 80%) of one side's total | small = dense support/resistance nearby; large = a liquidity vacuum |
| `CancellationTracker` / `cancel_pressure` | `(deleted_size - added_size) / (deleted_size + added_size)` at the best bid/ask, over a rolling event window (default 200) | +1 = all cancellations, -1 = all additions; only tracks ADD/DELETE at the *current* best price, UPDATE is ambiguous-direction and skipped |

### 2.7 Chart series (`chart_data.py`)

`compute_chart_series` is the web dashboard's per-coin chart backend: replays
`OrderBookDelta`s and `TradeTick`s for a time window and emits per-event series for
OFI (top-of-book `OrderFlowImbalance`), `Microprice`, spread, book imbalance/depth
(via `book_features.compute_features`), cancel pressure, 1-minute candles, a 5-minute
rolling cumulative trade delta, and fast/slow `ExponentialMovingAverage` trend lines
(Nautilus's own `ExponentialMovingAverage` indicator, EMA-8/EMA-21 defaults) computed
over those candles. Entirely a read-time replay — nothing here is persisted or fed
into ranking.

### 2.8 `metrics_computer.py` — periodic snapshot metrics

Bridges §1's Parquet catalog and the SQLite `metrics_store` (§3.4). Two entry points:

- `compute_book_metrics_all` — fast path, reads only the last `OFI_LOOKBACK_SECONDS`
  (60s) of `OrderBookDeltas` per instrument and computes `ofi` (top-of-book
  `OrderFlowImbalance`, window 5), `microprice`, and `spread` (`_book_metrics`,
  `metrics_computer.py:58-89`). **Note:** as of the current `ranking_engine.py`
  wiring, only `compute_all` (not `compute_book_metrics_all`) is actually invoked
  (`engine.py:419`) — this fast-path function currently has no caller found in
  `ranking_engine`/`dashboard`.
- `compute_all` — the slow, complete path: adds `price_stats` (`catalog_stats.py:190-224`)
  — latest price, `pct_change_1h`/`pct_change_24h`, and `volatility` (stdev of
  consecutive-return percentages over a 25-hour trailing window read straight from
  the Parquet trade/price history) — on top of the same book metrics. This is what
  `ranking_engine._slow_loop_task` calls every `DB_WRITE_INTERVAL_SECONDS` (60s,
  `engine.py:401-426`) to populate the `pct_1h`/`pct_24h`/`volatility` fields in the
  live ranking (§3).

### 2.9 `rank_history.py` / `watchlist.py`

Thin, dependency-light HTTP fetch helpers, not computations: `fetch_rank_history`
and `fetch_watchlist` pull already-computed data from the running dashboard's HTTP
API (`/api/rank_history`, `/api/watchlist`) for scripts/notebooks that don't want to
import the dashboard's full dependency set (aiohttp, plotly, redis).

### 2.10 `ranking_columns.py` — shared column definitions

Untracked in git but **actively wired up**, not a work-in-progress stub: it defines
`RANKING_COLS`, the ordered list of `(store_key, label, format_fn, color_fn)` tuples
that both `ml_signals/dashboard.py` (web) and `bot_tui`'s Coins pane render as the
ranking table (`troll/CLAUDE.md` SSOT-04). Every `store_key` in this list
(`ofi_10_z`, `obi_10`, `obi_5`, `obi_3`, `cvd`, `spread`, `microprice_lean`,
`volume_delta`, `price`, `pct_1h`, `pct_24h`, `volatility`, `volatility_score`,
`volume24h`) is a field name coming straight off a `rankings:live` rank entry —
i.e. every column this file defines maps 1:1 to a field `ranking_engine` publishes
(§3). It is display metadata (labels, `f"{v:+.2f}"`-style formatting, red/green
sign-coloring), not a new computation.

---

## 3. Ranking engine (`troll/ranking_engine/`)

`ranking_engine/engine.py` is the **sole computer and publisher** of the live coin
ranking (architecture decision AD-9) — `dashboard.py` and `bot_tui` are pure readers
of its output, never independent computers of the same indicators (`troll/CLAUDE.md`
SSOT-02). This exists specifically to prevent two processes independently running
the same rolling-window indicator class and silently diverging via differing startup
time / window contents / float accumulation order.

### 3.1 Inputs

- **`snapshots:raw`** — the Redis pub/sub stream of `DydxSecondSnapshot` dicts
  published live by the collector (§1.7). This is the engine's only per-tick market
  data input; it never reads Parquet directly for live-tick fields.
- **`volume24H`** — polled independently every 60s directly from dYdX's indexer
  `/v4/perpetualMarkets` (`_fetch_volume_24h_json`, `engine.py:152-167`) —
  deliberately *not* reused from `dydx_collector.open_interest._fetch_markets_json`
  even though it hits the same endpoint, because architecture rule AD-4 disallows
  cross-module reuse of anything that does network I/O (`engine.py:155-159`).
- **Parquet catalog** (via `metrics_computer.compute_all`, §2.8) — read once per
  minute for `price`/`pct_1h`/`pct_24h`/`volatility`.
- **`ranking:control`** — a Redis control channel that switches the active ranking
  mode between `"volume"` (default) and `"volatility"`.

### 3.2 What's computed, per instrument, on every `snapshots:raw` batch

`_ingest_snapshot_batch` (`engine.py:211-259`) feeds every incoming snapshot into
long-lived per-instrument indicator instances:

- **`VolatilityTracker`** (`volatility.py`) — a *fourth*, deliberately separate
  volatility computation from the other three in this codebase (the module's own
  docstring calls this out explicitly, `volatility.py:16-24`): cross-sectional stdev
  of consecutive mid-price percentage returns over an age-based (not fixed-length)
  rolling window, default 3600s. Age-based eviction specifically because a
  fixed-length deque would silently shrink its effective time span if the snapshot
  rate varies.
- **`MultiLevelOFI(levels=10, window=50, zscore_window=3600)`** → `ofi_10_z`
- **`MultiLevelOFI(levels=n, window=300)` for n in (3, 5, 10)** → `ofi_3`, `ofi_5`,
  `ofi_10` (raw, unscored)
- **`MultiLevelOBI(levels=n)` for n in (3, 5, 10)** → `obi_3`, `obi_5`, `obi_10`
- A 300-entry rolling window of raw snapshots per instrument (`_SECOND_ROLLING`) —
  feeds `trade_aggregates` (§2.1) for CVD/`avg_trade_size`, and a fast 300-tick
  `statistics.stdev` of mid-price returns (`volatility_fast`) — a *fifth*, separate
  volatility number, distinct from both `VolatilityTracker`'s and the catalog-derived
  one, by explicit design (`engine.py:285-292`).
- A reconnect-gap guard (`_OFI_GAP_NS = 3s`) clears OFI trackers' previous-tick state
  after a gap, so a stale pre-gap price never gets diffed against a fresh one.

### 3.3 The published `rankings:live` message

`_current_ranks()` (`engine.py:329-361`) builds one row per instrument that has had a
snapshot within the last 30 seconds (`_WATCHLIST_STALE_NS`, reusing OBS-01's
"pipeline failure, not quiet market" threshold verbatim). Each row combines:

- Live-tick fields from §3.2's indicators (`_fast_metrics_for`, `engine.py:262-314`):
  `ofi_10_z`, `ofi_3/5/10`, `obi_3/5/10`, `microprice`, `microprice_lean`
  (`microprice - mid`), `spread`, `cvd` (`buy_vol - sell_vol` from the rolling
  window), `volume_delta` (latest snapshot's `buy_volume - sell_volume`),
  `buy_count`/`sell_count`, `avg_trade_size`, `volatility_fast`, `price` (mid).
- Slow fields folded in from the last `compute_all` pass (§2.8): `pct_1h`, `pct_24h`,
  `volatility`.
- `volume24h` and `volatility_score` — always both present regardless of active mode.
- **`rank`** — 1-indexed position after sorting all rows by the active mode's score
  descending: `volatility_score` if mode is `"volatility"`, else `volume24h`
  (`engine.py:356-361`). This is the actual ranking: **default mode ranks
  instruments purely by 24-hour USD volume**; switching mode re-sorts the identical
  row set by the cross-sectional volatility stdev instead. No other field in the row
  affects sort order — OFI/OBI/CVD/etc. are informational columns on the ranked row,
  not ranking inputs themselves.

Published to Redis channel `rankings:live` whenever a representative subset of fields
changes (`RankingsPublisher._ranks_key`: instrument_id, rank, `ofi_10_z`, `spread`,
`cvd`, `microprice`, `price`) or at least every `RANKING_HEARTBEAT_SECONDS` (default
5s) regardless, so a quiet market still gets a heartbeat.

### 3.4 Persistence (`metrics_store.py`)

Every `DB_WRITE_INTERVAL_SECONDS` (60s), `_slow_loop_task` merges the current rank +
`volume24h` into that pass's snapshots and writes them to a SQLite table
(`ranking_engine/metrics_store.py`), columns: `price`, `pct_1h`, `pct_24h`,
`volatility`, `ofi`, `microprice`, `spread`, `rank`, `volume24h` — a 31-day rolling
history used by the dashboard's per-coin history page. Note this stored `ofi` column
is the **top-of-book-only** `OrderFlowImbalance` from `metrics_computer.compute_all`
(§2.8), not the multi-level `ofi_10_z`/`ofi_3/5/10` fields that only live in the
`rankings:live` Redis message — the SQLite history and the live ranking table
track genuinely different OFI computations.

### 3.5 What the ranking is used for

`dashboard.py`'s `/api/rankings`/`/api/watchlist` endpoints and `bot_tui`'s Coins pane
both render `rankings:live` directly, row order and column values unchanged
(`dashboard.py:854-933`, confirmed no independent computation on the read side). No
code path in `troll/live_paper/` (the actual trading-bot module) imports
`ranking_engine` or reads `rankings:live`/`/api/watchlist` — bots are configured
independently, not auto-selected from the live ranking. The ranking's current, only
confirmed consumer is the human-facing dashboard/TUI coin-picker UI, not an automated
trading decision.

---

## 4. Data lineage: raw field → signal → ranking

| Raw field (§1) | Computed signal (§2) | In `rankings:live` (§3) | Notes |
|---|---|---|---|
| `DydxSecondSnapshot.bid/ask_prices[0]`, `bid/ask_sizes[0]` | `microprice()`, `spread()`, `mid_price()` | `microprice`, `microprice_lean`, `spread`, `price` | pure functions, `indicators.py:409-446` |
| `DydxSecondSnapshot.bid/ask_prices[:N]`, `bid/ask_sizes[:N]` | `MultiLevelOFI`, `MultiLevelOBI` | `ofi_10_z`, `ofi_3/5/10`, `obi_3/5/10` | `ranking_engine` is the only live runner of these classes |
| `DydxSecondSnapshot.buy_volume`/`sell_volume`/`buy_count`/`sell_count` | `trade_aggregates()`, `volume_delta()` | `cvd`, `volume_delta`, `avg_trade_size`, `buy_count`, `sell_count` | |
| `DydxSecondSnapshot` mid-price sequence | `VolatilityTracker` (3600s cross-sectional) | `volatility_score` | **this is the sort key when mode = `"volatility"`** |
| `DydxSecondSnapshot` mid-price sequence (300-tick window) | `statistics.stdev` fast volatility | `volatility_fast` | separate from `volatility_score` and catalog `volatility` — 3 distinct volatility numbers by design |
| `DydxSecondSnapshot.close_price` (25h lookback; `TradeTick` pre-cutover) | `price_stats()` → `pct_change_1h/24h`, catalog `volatility` | `pct_1h`, `pct_24h`, `volatility` | via `metrics_computer.compute_all`, refreshed every 60s |
| dYdX indexer `volume24H` (independent poll) | — (used as-is) | `volume24h` | **this is the sort key when mode = `"volume"` (default)** |
| `OrderBookDeltas` | `book_features.py`, `chart_data.py`, `footprint.py` | *not present* | chart-page-only; never reaches `ranking_engine` |
| `MarkPriceUpdate` / `IndexPriceUpdate` | — | *not present* | stored, no downstream reader found |
| `FundingRateUpdate` | — | *not present* | stored, no downstream reader found |
| `InstrumentStatus` | — | *not present* | stored, no downstream reader found |
| `DydxOpenInterest` (stored) | — | *not present* | stored, no downstream reader found — only the *parallel* `volume24H`-based liquidity classification (not this field) affects anything live |

**Bottom line:** the live ranking table's actual sort key is either raw 24h USD
volume or a 1-hour cross-sectional volatility stdev — both computed from data outside
or adjacent to the book-level signal machinery. Every OFI/OBI/CVD/microprice column
visible on the ranking table is informational, derived from `DydxSecondSnapshot`
alone, and does not itself move an instrument's rank. Three raw types collected today
(`FundingRateUpdate`, `InstrumentStatus`, and the `open_interest` field of
`DydxOpenInterest`) have no confirmed downstream consumer anywhere in `ml_signals/`
or `ranking_engine/`.

---

## 5. Retention: how long is each type kept, and why the catalog keeps growing

Two independent pruning mechanisms exist. Neither one bounds the data that actually
accumulates day to day, which is why the catalog only ever grows.

**1. `Collector._prune_loop` (`collector.py:1136-1158`), running inside the collector
process itself.** Every `min(active retain_hours) * 900` seconds (≥ 900s floor,
`_prune_interval_seconds`), it deletes catalog files — across *every* data type, not
just one — for two groups of instrument only:

- Any instrument in `config.toml` with `pinned = false` (a legacy state — nothing
  creates one today; every instrument this collector actively adds is pinned by
  definition).
- Any market dYdX lists that *isn't* in `config.toml` at all, or was just dropped by a
  `stop`/`unpin` control action (`_prune_candidates`, `collector.py:266-280`) — this is
  why `crypto_perpetual`/`instrument_status`/etc. exist for ~140 markets on disk even
  though only the 29 in `config.toml` are actually subscribed: the markets channel is
  global, so mark/index price, funding rate, and instrument status/definitions get
  written for every market dYdX lists, and only the ~29 configured ones are exempt from
  this prune.

The retention window for that non-pinned/unknown group is `non_config_retain_hours` in
`config.toml` — **currently `4` hours**. It applies uniformly to every data type.

**2. Per-instrument raw `OrderBookDeltas` retention** (`retain_hours` on an
`InstrumentEntry`, only meaningful if that same entry also sets
`store_order_book_deltas = true`) — enforced by the same loop via
`_prune_delta_retention` (`collector.py:338-350`). **None of the 29 instruments in the
current `config.toml` set `store_order_book_deltas = true`**, so no raw deltas are
being written at all right now (`order_book_deltas/` is an empty directory) and this
mechanism currently has nothing to do.

**3. `prune_catalog.py` / `make prune`** — a separate, manual/cron-only script, not run
by the collector itself. By default it only targets `order_book_deltas` at a flat
**14-day** global cutoff, regardless of pinned status. Since nothing is stored there
today (see above), running it currently frees nothing.

### The actual retention per type, put plainly

| Data type | For your 29 pinned instruments | For any other dYdX market |
|---|---|---|
| `TradeTick` | **No longer written at all** (§1.1) — historical files pre-cutover remain but stop growing | 4h (`non_config_retain_hours`), also no longer growing |
| `OrderBookDeltas` | Not stored (no instrument opts in) | not stored |
| `MarkPriceUpdate` / `IndexPriceUpdate` / `FundingRateUpdate` / `InstrumentStatus` | **Unlimited** | 4h |
| `DydxSecondSnapshot` (now includes trade OHLC, §1.7) | **Unlimited** (pinned + liquid only, so this is always the pinned group) | not collected |
| `DydxOpenInterest` | **Unlimited** | 4h |
| Instrument definitions (`crypto_perpetual`) | **Unlimited** | 4h |
| `Bar` / `custom_dydx_minute_bar` | **Dead legacy data.** Written by an earlier pre-pivot architecture (§1.3 — the collector no longer calls `subscribe_bars` at all); nothing writes new files here and nothing prunes the old ones. Safe to delete manually if disk space matters; not wired into anything live. | — |

**Bottom line:** every instrument you've configured is `pinned = true`, and pinned
instruments are permanently exempt from `_prune_loop`. So for all 29 configured coins,
mark/index price, funding rate, open interest, instrument status, and the 1-second book
snapshots (which now also carry trade OHLC) still accumulate forever with no built-in
cap. Raw `TradeTick` was the one type that grew fastest for no real benefit — it's cut
over to `DydxSecondSnapshot`'s `open`/`high`/`low`/`close_price` fields (§1.1/§1.7),
which every downstream candle/price-series consumer (§2.5, §2.8) now reads instead.
That removes roughly 2-6 MB/day/instrument of unbounded growth (dominated by the most
liquid pairs) but does **not** address the other unlimited types above — those still
need `non_config_retain_hours`-style bounding if you want them capped too.
