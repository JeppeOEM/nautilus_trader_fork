# Data Dictionary: Collection → Signals → Ranking

What the dYdX collector stores, what `ml_signals`/`ranking_engine` compute from it, and
how a value traces from raw feed to the ranking table. All file:line references are
against the `bmad` branch as of 2026-09-05.

Every "error ledger" site named below (`collector.late_trade`, `collector.trade_backfill`,
`collector.candle_store`, ...) is recorded through `observability.error_ledger.record`
(Story 23.1; formerly `ml_signals.error_ledger`, now a deprecated re-export). The sites, their
names and what they count are unchanged `[re-cited 2026-09-21: Story 23.1]`, with one addition:
`archive_gaps.inverted_span` counts a gap marker whose `from_ns > to_ns` — a backward wall-clock
step between a lost trade's arrival and the flush. The marker is written as the ordered span and
still protects its rows, so the count is the only signal that the clock stepped back
`[added 2026-09-22: Story 23.2]`.

---

## 1. Raw data collected (`platform/collector_core/` + the venue collectors)

Each collector (`collector_core/collector.py` plus a venue subclass) owns one WS
connection per venue network and writes
everything through `ParquetDataCatalog.write_data()` — no hand-rolled schemas
(`platform/CLAUDE.md` NAUT-02). Nine distinct types land in the catalog. Six are native
Nautilus types decoded straight from the Rust adapter; two (`DydxSecondSnapshot`,
`OpenInterest`) are custom `Data` subclasses this collector defines because the
PyO3 bindings don't expose the fields another way.

### 1.1 `TradeTick` (native Nautilus type) — raw trade archive (story 22.13)

- **Source:** every venue's trade channel, decoded by the Rust adapter and delivered to
  `Collector._on_data` (dYdX `v4_trades`, Bybit `publicTrade`, Hyperliquid `trades`).
- **Fields:** `instrument_id`, `price`, `size`, `aggressor_side` (`AggressorSide.BUYER`/
  `SELLER`), `trade_id`, `ts_event`, `ts_init`.
- **Two clocks, stored untouched:** `ts_event` is the venue's trade time, `ts_init` the
  Rust client's receive time (one per WS message, so a message's trades share it).
  Aggregates bucket on `ts_event` (the nightly rebuild); backtests replay on `ts_init`.
  Measured on a 200 s mainnet run (2026-09-21): arrival lag `ts_init - ts_event` median
  ~110 ms on Bybit, ~390 ms on Hyperliquid (max 8.7 s), ~1.5 s on dYdX (max 1.8 s).
- **Written to** `data/trade_tick/<instrument>/` through `ParquetDataCatalog.write_data()`
  by the normal flush, for every trade that passes the DATA-06 guards (stale-age filter,
  `trade_id` dedup) -- the same trades the live second folds. Loads with no conversion
  through `BacktestDataConfig(data_cls=TradeTick, instrument_ids=[...])` (NAUT-03).
- **Flush tie guard:** each flush sorts a batch by `ts_init` and keeps back the group
  sharing its newest `ts_init` while it is under 5 s old (the rest of that WS message may
  still be queued): `write_data` refuses a file whose `ts_init` interval touches an existing
  one, which would lose the next flush's whole batch. The shutdown flush writes everything.
- **Live use:** each accepted trade is also kept for the current sample and folded once by
  `kernel.fold.fold_trades` (exact: `Quantity.raw` sums, `Price.raw` comparisons)
  into that second's `DydxSecondSnapshot` (§1.7). Live is provisional -- arrival-timed on
  dYdX, exchange-timed on Bybit/Hyperliquid (story 22.12, §1.7) -- and
  `collector_core.rebuild_seconds` re-derives closed days from this archive (§6).
- **Late trades (venue mode):** a trade processed after its exchange second closed is still
  archived here, counted (`collector.late_trade` in the error ledger, once per instrument per
  flush) and left out of every live row; the rebuild places it in its own second. A trade
  stamped more than `hold_back_seconds + 5 s` after its arrival is handled the same way
  (`collector.venue_clock_ahead`).
- **Footprint:** expected ~1 MB/venue/day on dYdX (ETH ~2.9k, BTC ~1.4k trades/day);
  Bybit/Hyperliquid majors are far busier (the 200 s run archived ~7.2k BTCUSDT-LINEAR and
  ~1.4k Hyperliquid BTC trades). **Not measured** per venue-day yet (operator action, see
  `DEPLOY_CHECKLIST.md`).
- **Retention:** see §5 -- released only after the day reconciles `pass`.
- **History:** files written before the earlier retention cutover (when trades stopped being stored)
  are still readable; story 22.13 reversed that cutover.

#### Reconnect gap closure (story 22.14)

The Rust WS clients reconnect and resubscribe silently, so the core detects a reconnect per
connection ("feed") on evidence -- an `is_active()` flip (Bybit, Hyperliquid), a book feed
silent past `feed_stale_seconds or stale_book_seconds`, or the same feed replaying a trade id
after the startup grace -- and, 3 s later, fetches each affected instrument's trades of
`[last archived ts_event - 5 s, now]` over stdlib REST (`collector_core/trade_backfill.py`).
Unseen ids are archived with the venue's `ts_event` and `ts_init` = the time they were archived
(so `ts_init - ts_event` shows the recovery lag); they are **never** folded into the live second
-- the nightly rebuild places them. One `collector.trade_backfill` ledger entry per backfill
names the feed, the detections, and the counts: backfilled, already archived, refused (older
than the 300 s `kernel.clocks.MAX_TS_INIT_SKEW_NS`, which the rebuild and prune depend on), unrecoverable
seconds, no baseline, errors. dYdX's `collector:status` carries the per-instrument cumulative
`trade_backfill`; Bybit and Hyperliquid report it in the per-flush log line.

What a reconnect gap costs, per venue (endpoints and depths verified live 2026-09-21):

| Venue | Trade-history endpoint | Depth | What a reconnect gap costs |
|---|---|---|---|
| dYdX | `GET {indexer}/v4/trades/perpetualMarket/{ticker}?limit=1000&createdBeforeOrAt=<iso>` (paged backwards, newest first) | Full history, paged; capped by us at the 5-minute arrival margin and 20 pages | Nothing up to ~5 minutes: recovered exactly. Beyond that the older part is refused and reported `unrecoverable` |
| Bybit linear | `GET /v5/market/recent-trade?category=linear&symbol=&limit=1000` (no paging) | Last **1000** trades (28-64 s of BTCUSDT in two samples, 2026-09-21) | Recovered while the outage is shorter than the last 1000 trades; beyond, `unrecoverable` seconds |
| Bybit spot | same, `category=spot` | Last **60** trades, even with `limit=1000` (1.3-7 s of BTCUSDT) | Only very short outages on a busy pair; the rest `unrecoverable` (D-60) |
| Hyperliquid | `POST /info {"type": "recentTrades", "coin"}` (exists; `startTime` ignored) | Last **10** trades only | Effectively unrecoverable from one connection (D-48) |

Known limit: Bybit's and Hyperliquid's REST depth cannot cover a real outage on a busy
instrument. Upgrade path, built and off by default: `trade_feeds = 2` in the venue's
`config.toml` opens a second, independent trades-only WebSocket per feed group; both deliver
into the same bounded `trade_id` dedup, which keeps the first copy and counts the other as
`duplicate_feed` (distinct from `duplicate`, a same-feed replay). A per-flush INFO line "Trade
feed arbitration (cumulative)" gives each feed's first copies, its only-this-feed count and the
pairwise overlap; a feed whose last trade falls more than 30 s behind its sibling's raises an
OBS-01 one-sided-outage notification. Other limits: a crash/restart gap is not backfilled
(`_last_trade_ts` is in memory; D-61), and seconds the stale-book gate skipped during an outage
have no snapshot row, so their backfilled trades are rebuild orphans.

### 1.2 `OrderBookDeltas` (native Nautilus type)

- **Source:** `v4_orderbook` WS channel, PyCapsule path, same as trades.
- **Fields:** a list of `OrderBookDelta` (side, price, size, `BookAction` ADD/UPDATE/
  DELETE/CLEAR), plus `instrument_id`/`ts_event`/`ts_init` on the wrapping
  `OrderBookDeltas`.
- **Cadence:** event-driven, one message per book change.
- **Written:** only for instruments with `store_order_book_deltas = true` in
  `config.toml` (`collector.py`, gated by `self._delta_store`) — this is a
  raw, high-volume type, opt-in per instrument. Independently of storage, every
  pinned/liquid instrument's deltas are always applied to an in-memory `OrderBook`
  (`_apply_deltas`, `collector.py`) used to build `DydxSecondSnapshot` (§1.7).
- **Retention:** per-instrument `retain_hours` in `config.toml`, pruned by
  `_prune_delta_retention` (`collector.py`); `None` = kept forever.
- **No sequence-gap detection:** dYdX's WS `sequence` field is connection-global, not
  per-market, so it can't be used to detect a dropped delta for one instrument — see
  `_apply_deltas`'s docstring (`collector.py`) and `platform/.planning/debug/
  crossed-book-root-cause.md` for the investigation this constraint drove.

### 1.3 `Bar` (native Nautilus type)

- **Source:** subscribed via `DydxClient.subscribe_bars` (`client.py`), PyCapsule
  path.
- **Note:** the collector's `run()` (`collector.py`) never calls
  `subscribe_bars` — no bar subscription is currently active. The capability exists in
  `client.py` but is unused; **no `Bar` data is currently written to the catalog by this
  collector.** (`ml_signals/candles.py`'s `aggregate_ohlc` instead derives candles from
  `DydxSecondSnapshot`'s per-second OHLC fields on read — see §2.3.)

#### Backfilled `Bar`s (Story 22.9)

The one `Bar` path that *does* write to the catalog: `python -m collector_core.backfill_bars`, an
offline operator CLI that fetches the venues' own historical klines over REST for **Bybit and
Hyperliquid only** (dYdX bars are derived from its 1 s archive instead).

- **Bar type:** `<instrument id>-<step>-<aggregation>-LAST-EXTERNAL`, e.g.
  `BTCUSDT-LINEAR.BYBIT-1-MINUTE-LAST-EXTERNAL`. `EXTERNAL` is literal and load-bearing: these are
  the **venue's own aggregation**, not our 1 s fold, and the two must never be conflated in a
  backtest or a reconciliation.
- **Timestamps:** `ts_event == ts_init ==` the bar's **close** time, on both venues. Bybit is
  requested with `timestamp_on_close=True`; Hyperliquid's adapter stamps the candle's *open* time
  (`crates/adapters/hyperliquid/src/data.rs:1267`) and the CLI shifts it by one interval, carrying
  the decoded `Price`/`Quantity` across unchanged.
- **Day attribution:** a close-stamped bar belongs to the day it **opened** in, so `--start D1
  --end D2` covers closes `midnight(D1) + interval` through `midnight(D2) + 24 h`. The end is
  additionally clamped to the last closed bar.
- **Coverage:** Bybit serves multi-year kline history. Hyperliquid's `candleSnapshot` returns
  roughly the last 5000 candles (~3.5 days at 1 minute); older history is permanently unavailable
  and the CLI warns rather than pretending the range is archived (audit D-53).
- **Supported `--bar-spec`:** `1/3/5/15/30-MINUTE`, `1/2/4/12-HOUR`, `1-DAY`, all `-LAST`, on both
  venues. Deliberately narrower than either adapter's table: `WEEK`, `MONTH` and `3-DAY` are
  excluded because the tool's window bounds are absolute epoch multiples and the epoch is a
  Thursday, while both venues open weekly klines on Monday -- every bar fetched on such a grid
  would be discarded as off-grid. `6-HOUR` (Bybit-only) and `8-HOUR` (Hyperliquid-only) are
  excluded so one `--bar-spec` behaves identically across a mixed-venue run.
- **Idempotent re-run:** the windows come from the catalog's own
  `get_missing_intervals_for_request`, so re-running an archived range issues **no REST request at
  all** -- not merely no writes. A window the venue serves with an interior hole is written as one
  file per contiguous run, so the hole stays a visible, re-plannable gap.
- **Precision:** **both** venues' kline OHLCV passes through an `f64` round-trip upstream -- Bybit
  `value.parse::<f64>()` + `Price::new_checked`
  (`crates/adapters/bybit/src/common/parse.rs:1187-1198`) and Hyperliquid `Price::new(f64, …)`
  (`crates/adapters/hyperliquid/src/data.rs:1278`) differ only in range validation. **There is no
  Bybit/Hyperliquid precision asymmetry**; neither venue's backfilled bars carry a string-exact
  guarantee (audit D-52).
- **`--environment`:** picks which venue endpoint is queried (`mainnet`/`testnet`, plus `demo` on
  Bybit). It does **not** verify what the target catalog already holds -- nothing in the catalog
  records a row's environment -- so it cannot prevent a mainnet/testnet mix under one instrument id.
- **Report-only by default:** without `--apply` the CLI plans and logs only; it builds no venue
  client, issues no request and writes nothing.
- **Where they live:** Parquet only, under `data/bar/<bar type>/`. They are **never** written to
  `candles_<venue>.db` and **never** reach the chart, both of which are built from
  `DydxSecondSnapshot`.

### 1.4 `MarkPriceUpdate` (native Nautilus type)

- **Source:** `subscribe_markets()` markets-channel, global for all instruments
  (`client.py`, `collector.py`), plain pyo3-object path (`client.py`).
- **Fields:** `instrument_id`, `value` (`Price`), `ts_event`, `ts_init`.
- **Precision fix:** re-stamped through `_at_fixed_precision()` (`client.py`)
  before storage — dYdX's raw feed derives each tick's `Price.precision` from its own
  digit count, so consecutive ticks can carry different precision labels, which
  `ParquetDataCatalog` refuses to merge. See `platform/CLAUDE.md` NAUT-01.
- **Cadence:** event-driven, all subscribed + monitored instruments (markets channel
  covers everything, not just liquid/pinned).

### 1.5 `IndexPriceUpdate` (native Nautilus type)

- Same channel/path/precision-fix as mark price (`client.py`). Oracle index
  price, distinct from the venue's own mark price.

### 1.6 `FundingRateUpdate` (native Nautilus type)

- **Source:** markets channel, plain pyo3-object path (`client.py`), forwarded
  via `FundingRateUpdate.from_pyo3` with no transformation.
- **Fields:** `instrument_id`, funding rate value, `ts_event`, `ts_init`.
- **Downstream use:** **none found.** Stored to the catalog but no file in
  `ml_signals/` or `ranking_engine/` reads `FundingRateUpdate` — dead data as of this
  writing.

### 1.7 `DydxSecondSnapshot` (custom `Data` type, `kernel/second_snapshot.py`)

The core microstructure record — a 1-second-sampled L2 book snapshot, **not** raw
deltas. Per `platform/CLAUDE.md`'s Signal Architecture rule: store raw inputs, compute
signals on read (SIGNAL-01). Moved from `collector_core/` to the shared kernel in Story 23.2
with its class name, Arrow schema and `snapshots:raw` encoding unchanged (the catalog directory
`custom_dydx_second_snapshot` derives from the class name; proven by
`kernel/tests/test_pre_move_fixtures.py`). `DydxSecondSnapshot.from_dict` is the one parser of a
`snapshots:raw` entry. The seven candle columns are read without the book by
`kernel.catalog_files.query_second_ohlc` as `SecondOHLC` rows.

- **Fields** (`second_snapshot.py`, schema at `DydxSecondSnapshot.schema()`):
  - `instrument_id`
  - `bid_prices`, `bid_sizes`, `ask_prices`, `ask_sizes` — up to `BOOK_DEPTH = 20`
    levels each (`second_snapshot.py`), index 0 = best bid/ask
  - `buy_volume`, `sell_volume` — summed trade size per side since the last tick
  - `buy_count`, `sell_count` — trade count per side since the last tick
  - `open_price`, `high_price`, `low_price`, `close_price` — OHLC of actual executed
    trade prices within this second, `None` if no trade occurred. Live, each accepted
    `TradeTick` is kept in `Collector._second_trades` and folded once per sample by
    `kernel.fold.fold_trades` (the same exact fold the nightly rebuild uses; the
    float columns hold one conversion of an exact total). A closed day's rows are
    re-derived from the raw archive (§1.1) on exchange time by `rebuild_seconds` (§6);
    book columns and timestamps are never touched. `ml_signals/candles.py`'s
    `aggregate_ohlc` combines these across multiple seconds for coarser candles;
    `ml_signals/catalog_stats.py`'s `price_series` reads `close_price` as its primary
    price source (falling back to `MarkPriceUpdate` only when no snapshot ever
    recorded a trade for that instrument).
  - `ts_event`, `ts_init`
- **Clocks per venue** (`CoreConfig.book_time_source`, story 22.12):

  | | dYdX (`"arrival"`) | Bybit, Hyperliquid (`"venue"`) |
  |---|---|---|
  | Book columns | the book as **received** by the sample (dYdX deltas carry no venue time, `OrderBookDelta.ts_event = ts_init`, audit D-49) | the book as **the exchange stamped it**: every delta with `ts_event < S+1`, applied in `ts_event` order |
  | Trade columns, live | trades that **arrived** since the previous row | trades with `ts_event` in `[S, S+1)` that arrived before the row closed |
  | Trade columns, after the nightly rebuild | exchange-timed: `[S, S+1)` by `ts_event` | same |
  | `ts_event` | the sample time (mid-second) | `S + 0.5 s` |
  | `ts_init` | the sample time (`== ts_event`) | when the row was sampled, `>= S + 1 + hold_back_seconds` |

  Either way the row's floor second is its second (the rebuild, the candle store and the chart
  gap logic rely on that), and `ts_init` is when the row could first be known -- backtests
  replay on it. A Bybit/Hyperliquid row therefore reaches Redis and Parquet
  `1 + hold_back_seconds` after its second began; freshness readers stamp arrival
  (`ranking_engine._LAST_SEEN`), so that lag never reads as a stale feed. `hold_back_seconds`
  is set per venue from `python -m collector_core.measure_lag` (the per-kind distribution of
  `ts_init - ts_event`); it only makes fewer trades late, the rebuild is what makes a second
  correct (audit D-50).
- **Built by:** `collector_core/collector.py`'s `Collector._second_loop`, every
  `snapshot_interval_seconds` (config default 1.0s, overrideable in `config.toml`), on a
  drift-free wall-clock schedule at mid-interval (`_next_sample_at`): exactly one row per
  floor second, which the rebuild's trade-to-row mapping relies on. Venue mode
  (`_venue_second_loop`) instead closes each exchange second at wall
  `S + 1 + hold_back_seconds`; after a stall it closes every overdue second (at most 60) in
  order, each from the book as of its own end.
- **Guards before emission:** skips crossed books (the venue's `_handle_crossed_book` —
  on dYdX it also drives a forced resubscribe/resync after a persistent cross), and skips
  stale books with no `OrderBookDeltas` for `config.stale_book_seconds` (5s; in venue mode
  measured from the last applied delta's `ts_event` to the end of the second, the feed-dead
  test staying on arrival) — both are
  `platform/CLAUDE.md` DATA-01 "flag the gap, never fabricate" implementations.
- **Scope:** pinned + liquid instruments only. Illiquid instruments get no snapshots.
- **Dual delivery:** written to the catalog via the normal buffer/flush path
  (`self._on_data(snapshot)`, `collector.py`) **and** published live to Redis
  channel `snapshots:raw` (`_publish_snapshot_batch`, `collector.py`) —
  this Redis stream is what `ranking_engine` actually consumes live (§3); the Parquet
  copy is for backtest/historical replay.

### 1.8 `OpenInterest` (custom `Data` type, `kernel/open_interest.py`)

Moved from `collector_core/` to the shared kernel in Story 23.2; class name (hence
`custom_open_interest/`) and Arrow schema unchanged.

- **Spot has none, by design** (Story 22.4): Bybit `-SPOT.BYBIT` instruments produce only book+trade
  snapshots -- Bybit's spot ticker has no bid/ask, funding or open interest, and mark/index price are
  perp concepts -- so no `custom_open_interest`/mark/funding entries for a spot id is correct, not a gap.

- **One type for all venues** (Story 22.3): written by all three collectors -- dYdX and Bybit
  via REST poll, Hyperliquid via WebSocket (`OpenInterest.from_pyo3`). Catalog directory
  `data/custom_open_interest/`. Replaces `DydxOpenInterest`/`BybitOpenInterest`/
  `HyperliquidOpenInterest` (history in `custom_{dydx,bybit,hyperliquid}_open_interest/` moves
  with `python -m collector_core.migrate_open_interest`, audit D-40). The dYdX-specific
  notes below describe `dydx_collector/open_interest.py`, which keeps the poll and
  `classify_liquidity`.

- **Why custom/separate:** open interest is parsed Rust-side but never forwarded to
  Python on either the REST or WS markets-channel path (`open_interest.py`
  docstring, citing `crates/adapters/dydx/src/python/{http,websocket}.rs`) — the one
  field this collector has to re-fetch itself.
- **Fields:** `instrument_id`, `open_interest` (`Decimal`, stored as a string in Arrow
  to avoid float round-tripping), `ts_event`, `ts_init`.
- **Source:** plain stdlib `urllib` poll of dYdX's public indexer
  `/v4/perpetualMarkets` REST endpoint (`_fetch_markets_json`, `open_interest.py`),
  every `open_interest_poll_seconds` (config default 300s, `config.py`).
- **Also drives liquidity tiering:** `classify_liquidity` (`open_interest.py`)
  reads the *same* markets JSON response but keys off `volume24H` (USD), not
  `openInterest` (base-token units) — `platform/CLAUDE.md` OBS-03 explicitly calls out
  the token-vs-USD confusion as a past production bug. This classification decides
  which instruments get trade/book subscriptions (pinned/liquid/illiquid tiers,
  `collector.py`), independent of storing `OpenInterest` itself.
- **Downstream use of the stored `open_interest` field:** **none found** in
  `ml_signals/`/`ranking_engine/` — only the *volume*-based liquidity classification
  (a separate, parallel computation in the same module) is used live. The OI Parquet
  record itself is written and retained but not read back by any of the code
  inspected. Likely intended for future backtest/research use, not currently wired
  into any live signal or ranking.

### 1.9 `InstrumentStatus` (native Nautilus type)

- **Source:** markets channel, plain pyo3-object path (`client.py`).
- **Downstream use:** **none found** — stored, not read anywhere in `ml_signals/`/
  `ranking_engine/`.

### 1.10 Instrument definitions

- **Source:** one-time REST fetch (`DydxClient.fetch_instruments`, `client.py`)
  at collector startup, converted via `instruments_from_pyo3()` and written once
  (`collector.py`). Not a recurring stream — defines the instrument
  universe/precision metadata the catalog needs to interpret every other type
  correctly.

### 1.11 Error ledger (`platform/data/errors/<service>.jsonl`, story 23.3)

Not market data — the durable half of `observability.error_ledger.record()` (DATA-07). Not
Parquet: plain JSON lines, one file per service (`collector`, `bybit_collector`,
`hyperliquid_collector`, `ranking_engine`, `data_api`, `live-paper`, `bot_tui`; the compose
service name, via `ERROR_LEDGER_SERVICE`).

- **Fields per line:** `ts_ns` (int, arrival time), `service` (str), `pid` (int), `site` (str,
  e.g. `collector.book_sequence`), `detail` (str, truncated to 2000 chars in the file only),
  `exc_type` (str or `None`), `suppressed` (int, records dropped by the write cap since the
  previous line for this site — `lines + sum(suppressed)` is the true count **over a whole file
  set**; see the window-edge `Known limit` below). A `process_start`
  line (written once per boot by `observability.error_ledger.start()`) additionally carries
  `revision` (str or `None`, from `ERROR_LEDGER_REVISION`) — its own fields are otherwise empty
  (`detail=""`, `exc_type=None`, `suppressed=0`).
- **Rotation:** by size, `<service>.jsonl` → `.1` .. `.N` (`ERROR_LEDGER_MAX_BYTES` default
  20 MB, `ERROR_LEDGER_BACKUP_COUNT` default 10 **backups kept alongside the live file**, so
  the retained window is 11 files — the same shape as the compose `x-logging` policy).
- **Write cap:** at most `ERROR_LEDGER_MAX_LINES_PER_SITE_PER_MIN` (default 60) lines per site
  per UTC minute; every record past the cap is counted and folded into the next written line's
  `suppressed` for that site — never silently dropped from the total.
- **Known limit (window edges):** a `suppressed` carry is stamped with the timestamp of the line
  that *reports* it, not with when the suppressed records happened, so `lines + Σsuppressed` is
  exact over a whole file set but only approximate over a bounded window — a storm at 23:58:30
  whose next line for that site lands at 00:05 counts entirely in the following day. Ceiling:
  up to one cap-bucket per site can be attributed to the neighbouring window. Upgrade path:
  carry the bucket's own start timestamp on the line, which needs a version-detectable schema
  bump because the field set above is frozen by this story's AC1.
- **Known limit (unanchored counts):** `service_summary`'s `last_start_ns` is `None` when no
  `process_start` line survives in the retained files. `since_start` is then *not* anchored to
  a restart — it means "since retention", i.e. everything still on disk. Check
  `last_start_ns is not None` before reading it as "since start".
- **Downstream use:** `GET /api/errors`'s `services` block (per-service restart count and
  per-site totals since the last `process_start` / since an optional `?since_ns=`);
  `python3 -m collector_core.crosscheck_errors` (§6 of `docs/DEPLOY_CHECKLIST.md`), which
  matches a gap in §1.7's second-snapshot rows against these files within the 300 s skew bound
  `kernel.clocks.MAX_TS_INIT_SKEW_NS` **of one of the gap's own edges** — never across its
  interior — before calling it `UNEXPLAINED`.

---

## 2. Computed signals / ML features (`platform/ml_signals/`)

Everything here is computed **on read** from the raw types in §1 — nothing in this
section is stored back to Parquet. Per SSOT-01/02 (`platform/CLAUDE.md`), stateless
single-snapshot formulas live as plain functions in `kernel/indicators.py` (the shared kernel,
Story 23.2; `ml_signals.indicators` is a deprecated re-export); stateful/rolling
indicators are classes, and for anything shown in a live UI, exactly one process
(`ranking_engine`) is allowed to own the running instance (§3).

### 2.1 Stateless, single-snapshot functions (`kernel/indicators.py`)

All take one `DydxSecondSnapshot`-shaped dict (§1.7) and return a value with no memory
of prior calls:

| Function | Formula | Raw fields used |
|---|---|---|
| `microprice(snapshot)` | `(bid_prices[0]*ask_sizes[0] + ask_prices[0]*bid_sizes[0]) / (bid_sizes[0]+ask_sizes[0])` | `bid_prices[0]`, `bid_sizes[0]`, `ask_prices[0]`, `ask_sizes[0]` |
| `spread(snapshot)` | `ask_prices[0] - bid_prices[0]` | `bid_prices[0]`, `ask_prices[0]` |
| `mid_price(snapshot)` | `(bid_prices[0] + ask_prices[0]) / 2` | `bid_prices[0]`, `ask_prices[0]` |
| `volume_delta(snapshot)` | `buy_volume - sell_volume` | `buy_volume`, `sell_volume` |
| `trade_aggregates(snapshots)` | sums `buy_volume`/`sell_volume`/`buy_count`/`sell_count` across a list — feeds CVD and `avg_trade_size` downstream | all four trade fields |

`Microprice` (`indicators.py`) is also available as a stateful `Indicator`
class fed one tick at a time (`update_raw`/`handle_quote_tick`) — used where a class
with `.initialized` semantics is more convenient (e.g. `chart_data.py` replay, §2.7),
but produces the identical formula.

### 2.2 Order Flow Imbalance (OFI)

Cont-Kukanov-Stoikov delta formula: at each level, compares this tick's bid/ask
price+size to the previous tick's; a price improvement counts the full new size, an
unchanged price counts the size *delta*, a worse price counts a full withdrawal on
that side. `contribution = bid_term - ask_term`, summed over a rolling window.

- **`OrderFlowImbalance`** (`indicators.py`) — top-of-book only (level 0),
  rolling sum over `window` updates (default 50). Fed from `QuoteTick` or raw
  bid/ask price+size. Used by `metrics_computer.py` (§2.6) and `chart_data.py` (§2.7).
- **`MultiLevelOFI`** (`indicators.py`) — same formula applied independently
  at each of the top `levels` price levels and summed, using the full
  `bid_prices`/`bid_sizes`/`ask_prices`/`ask_sizes` lists from `DydxSecondSnapshot`.
  Options: `usd_notional` (multiply each size term by its price level, for
  cross-instrument comparability — e.g. BTC vs. a low-priced altcoin), and
  `zscore_window` (normalize the rolling sum to a z-score over a longer history —
  `(value - mean) / std`). This is the version `ranking_engine` actually runs live
  (§3): three raw variants at levels 3/5/10 (window 300, no z-score) plus one
  z-scored variant at level 10 (window 50, z-score window 3600).

### 2.3 Order Book Imbalance (OBI)

`MultiLevelOBI` (`indicators.py`): `sum(bid_sizes[:levels]) / (sum(bid_sizes[:levels]) + sum(ask_sizes[:levels]))`.
1.0 = all depth on the bid side, 0.5 = balanced, 0.0 = all ask. `ranking_engine` runs
three instances per instrument at levels 3/5/10.

### 2.4 Footprint / order-book flow (`footprint.py`)

`build_footprint` buckets **resting order-book size changes** (not executed trades —
dYdX L2 deltas have no order IDs, so a shrinking level can't be told apart from a
cancel vs. a fill; see the module's own caveat, `footprint.py`) into
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

Bridges §1's Parquet catalog and the SQLite `metrics_store` (§3.4). One entry point:

- `compute_all` — adds `price_stats` (`catalog_stats.py`) — latest price,
  `pct_change_1h`/`pct_change_24h`, and `volatility` (stdev of consecutive-return
  percentages over a 25-hour trailing window read straight from the Parquet
  trade/price history) — to the `ofi`/`microprice`/`spread` fields its required
  `book_metrics_fn` argument supplies (`ranking_engine._legacy_book_metrics_for`,
  reading the same live indicator state `rankings:live` uses — SSOT-02, no
  from-Parquet OFI replay). This is what `ranking_engine._slow_loop_task` calls every
  `DB_WRITE_INTERVAL_SECONDS` (60s, `engine.py`) to populate the
  `pct_1h`/`pct_24h`/`volatility` fields in the live ranking (§3).

### 2.9 `rank_history.py` / `watchlist.py`

Thin, dependency-light HTTP fetch helpers, not computations: `fetch_rank_history`
and `fetch_watchlist` pull already-computed data from the running `data_api`'s HTTP
API (`/api/metrics/nearest/{iid}`, `/api/rankings`) for scripts/notebooks that don't want to
import the full dependency set (fastapi, redis).

### 2.10 `ranking_columns.py` — shared column definitions

Untracked in git but **actively wired up**, not a work-in-progress stub: it defines
`RANKING_COLS`, the ordered list of `(store_key, label, format_fn, color_fn)` tuples
that `bot_tui`'s Coins pane renders as the ranking table (`platform/CLAUDE.md` SSOT-04).
The web UI no longer uses it: `ml_signals/dashboard.py` was retired in Story 15.10 and
`frontend/` defines its own columns. Every `store_key` in this list
(`ofi_10_z`, `obi_10`, `obi_5`, `obi_3`, `cvd`, `spread`, `microprice_lean`,
`volume_delta`, `price`, `pct_1h`, `pct_24h`, `volatility`, `volatility_score`,
`volume24h`) is a field name coming straight off a `rankings:live` rank entry —
i.e. every column this file defines maps 1:1 to a field `ranking_engine` publishes
(§3). It is display metadata (labels, `f"{v:+.2f}"`-style formatting, red/green
sign-coloring), not a new computation.

---

## 3. Ranking engine (`platform/ranking_engine/`)

`ranking_engine/engine.py` is the **sole computer and publisher** of the live coin
ranking (architecture decision AD-9) — `dashboard.py` and `bot_tui` are pure readers
of its output, never independent computers of the same indicators (`platform/CLAUDE.md`
SSOT-02). This exists specifically to prevent two processes independently running
the same rolling-window indicator class and silently diverging via differing startup
time / window contents / float accumulation order.

### 3.1 Inputs

- **`snapshots:raw`** — the Redis pub/sub stream of `DydxSecondSnapshot` dicts
  published live by the collector (§1.7). This is the engine's only per-tick market
  data input; it never reads Parquet directly for live-tick fields.
- **USD 24 h volume, per venue** (Story 22.10) — polled independently every 60s,
  all sources concurrently (`_volume_cycle`, `engine.py`): dYdX's indexer
  `/v4/perpetualMarkets` `volume24H` (`_fetch_volume_24h_json`), Bybit's
  `/v5/market/tickers?category=linear|spot` `turnover24h`
  (`_fetch_bybit_tickers_json`; spot kept only for USDT/USDC quotes) and Hyperliquid's
  `POST /info {"type":"metaAndAssetCtxs"}` `dayNtlVlm`
  (`_fetch_hyperliquid_meta_and_ctxs_json`). `DYDX_NETWORK`, `BYBIT_ENVIRONMENT` and
  `HYPERLIQUID_ENVIRONMENT` pick mainnet/testnet. Each source's last good result is
  kept on a failed poll and expires after 3 missed polls; failures, expiries and fresh
  instruments with no volume are counted at `ranking_engine.volume24h`. None of these
  fetchers is reused from the collectors even where the endpoint is the same, because
  architecture rule AD-4 disallows cross-module reuse of anything that does network
  I/O (`engine.py`).
- **Parquet catalog** (via `metrics_computer.compute_all`, §2.8) — read once per
  minute for `price`/`pct_1h`/`pct_24h`/`volatility`.
- **`ranking:control`** — a Redis control channel that switches the active ranking
  mode between `"volume"` (default) and `"volatility"`.

### 3.2 What's computed, per instrument, on every `snapshots:raw` batch

`_ingest_snapshot_batch` (`engine.py`) feeds every incoming snapshot into
long-lived per-instrument indicator instances:

- **`VolatilityTracker`** (`volatility.py`) — a *fourth*, deliberately separate
  volatility computation from the other three in this codebase (the module's own
  docstring calls this out explicitly, `volatility.py`): cross-sectional stdev
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
  one, by explicit design (`engine.py`).
- A reconnect-gap guard (`_OFI_GAP_NS = 3s`) clears OFI trackers' previous-tick state
  after a gap, so a stale pre-gap price never gets diffed against a fresh one.

### 3.3 The published `rankings:live` message

`_current_ranks()` (`engine.py`) builds one row per instrument that has had a
snapshot within the last 30 seconds (`_WATCHLIST_STALE_NS`, reusing OBS-01's
"pipeline failure, not quiet market" threshold verbatim). Each row combines:

- Live-tick fields from §3.2's indicators (`_fast_metrics_for`, `engine.py`):
  `ofi_10_z`, `ofi_3/5/10`, `obi_3/5/10`, `microprice`, `microprice_lean`
  (`microprice - mid`), `spread`, `cvd` (`buy_vol - sell_vol` from the rolling
  window), `volume_delta` (latest snapshot's `buy_volume - sell_volume`),
  `buy_count`/`sell_count`, `avg_trade_size`, `volatility_fast`, `price` (mid).
- Slow fields folded in from the last `compute_all` pass (§2.8): `pct_1h`, `pct_24h`,
  `volatility`.
- `volume24h` and `volatility_score` — always both present regardless of active mode.
  `volume24h` is the venue's own USD 24 h volume (Story 22.10) and is `null` when that
  venue has no current volume for the instrument; such a row is left out of volume mode
  entirely (never ranked at 0) and appears only in volatility mode. Exception, older than
  Story 22.10: dYdX's `parse_volume_24h` still reads a market whose `volume24H` field is
  absent or null as 0 (an unparseable string is left out); tracked in deferred work.
- **`rank`** — 1-indexed position after sorting all rows by the active mode's score
  descending: `volatility_score` if mode is `"volatility"`, else `volume24h`
  (`engine.py`). This is the actual ranking: **default mode ranks
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
(`ranking_engine/metrics_store.py`), columns: `price`, `pct_1h`, `pct_24h`, `pct_1w`, `pct_1m`,
`volatility`, `ofi`, `microprice`, `spread`, `rank`, `volume24h` — a 31-day rolling
history used by the dashboard's per-coin history page. Note this stored `ofi` column
is the **top-of-book-only** `OrderFlowImbalance` from `metrics_computer.compute_all`
(§2.8), not the multi-level `ofi_10_z`/`ofi_3/5/10` fields that only live in the
`rankings:live` Redis message — the SQLite history and the live ranking table
track genuinely different OFI computations.

### 3.5 What the ranking is used for

`data_api`'s `GET /api/rankings` (a verbatim passthrough, `data_api/routes/rankings.py`)
and `bot_tui`'s Coins pane both render `rankings:live` directly, row order and column
values unchanged — no independent computation on the read side. No code path in
`platform/live_paper/` (the actual trading-bot module) imports `ranking_engine` or reads
`rankings:live` — bots are configured independently, not auto-selected from the live
ranking. The ranking's current, only
confirmed consumer is the human-facing dashboard/TUI coin-picker UI, not an automated
trading decision.

---

## 4. Data lineage: raw field → signal → ranking

| Raw field (§1) | Computed signal (§2) | In `rankings:live` (§3) | Notes |
|---|---|---|---|
| `DydxSecondSnapshot.bid/ask_prices[0]`, `bid/ask_sizes[0]` | `microprice()`, `spread()`, `mid_price()` | `microprice`, `microprice_lean`, `spread`, `price` | pure functions, `indicators.py` |
| `DydxSecondSnapshot.bid/ask_prices[:N]`, `bid/ask_sizes[:N]` | `MultiLevelOFI`, `MultiLevelOBI` | `ofi_10_z`, `ofi_3/5/10`, `obi_3/5/10` | `ranking_engine` is the only live runner of these classes |
| `DydxSecondSnapshot.buy_volume`/`sell_volume`/`buy_count`/`sell_count` | `trade_aggregates()`, `volume_delta()` | `cvd`, `volume_delta`, `avg_trade_size`, `buy_count`, `sell_count` | |
| `DydxSecondSnapshot` mid-price sequence | `VolatilityTracker` (3600s cross-sectional) | `volatility_score` | **this is the sort key when mode = `"volatility"`** |
| `DydxSecondSnapshot` mid-price sequence (300-tick window) | `statistics.stdev` fast volatility | `volatility_fast` | separate from `volatility_score` and catalog `volatility` — 3 distinct volatility numbers by design |
| `DydxSecondSnapshot.close_price` (25h lookback; `TradeTick` pre-cutover) | `price_stats()` → `pct_change_1h/24h`, catalog `volatility` | `pct_1h`, `pct_24h`, `volatility` | via `metrics_computer.compute_all`, refreshed every 60s. `pct_1w`/`pct_1m` come from `metrics_store`'s persisted prices (`price_near_days_ago`), `None` until 7/30 days of history exist |
| dYdX indexer `volume24H`, Bybit v5 tickers `turnover24h` (linear; spot USDT/USDC-quoted only), Hyperliquid `metaAndAssetCtxs` `dayNtlVlm` (independent polls in `ranking_engine`) | — (used as-is, USD) | `volume24h` | **this is the sort key when mode = `"volume"` (default)**; an instrument with no volume is absent from that mode and counted at `ranking_engine.volume24h` |
| `OrderBookDeltas` | `book_features.py`, `chart_data.py`, `footprint.py` | *not present* | chart-page-only; never reaches `ranking_engine` |
| `MarkPriceUpdate` / `IndexPriceUpdate` | — | *not present* | stored, no downstream reader found |
| `FundingRateUpdate` | — | *not present* | stored, no downstream reader found |
| `InstrumentStatus` | — | *not present* | stored, no downstream reader found |
| `OpenInterest` (stored) | — | *not present* | stored, no downstream reader found — only the *parallel* `volume24H`-based liquidity classification (not this field) affects anything live |

**Bottom line:** the live ranking table's actual sort key is either raw 24h USD
volume or a 1-hour cross-sectional volatility stdev — both computed from data outside
or adjacent to the book-level signal machinery. Every OFI/OBI/CVD/microprice column
visible on the ranking table is informational, derived from `DydxSecondSnapshot`
alone, and does not itself move an instrument's rank. Three raw types collected today
(`FundingRateUpdate`, `InstrumentStatus`, and the `open_interest` field of
`OpenInterest`) have no confirmed downstream consumer anywhere in `ml_signals/`
or `ranking_engine/`.

---

## 5. Retention: how long is each type kept, and why the catalog keeps growing

Two independent pruning mechanisms exist. Neither one bounds the data that actually
accumulates day to day, which is why the catalog only ever grows.

**1. `Collector._prune_loop` (`collector.py`), running inside the collector
process itself.** Every `min(active retain_hours) * 900` seconds (≥ 900s floor,
`_prune_interval_seconds`), it deletes catalog files — across *every* data type, not
just one — for two groups of instrument only:

- Any instrument in `config.toml` with `pinned = false` (a legacy state — nothing
  creates one today; every instrument this collector actively adds is pinned by
  definition).
- Any market dYdX lists that *isn't* in `config.toml` at all, or was just dropped by a
  `stop`/`unpin` control action (`_prune_candidates`, `collector.py`) — this is
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
`_prune_delta_retention` (`collector.py`). **None of the 29 instruments in the
current `config.toml` set `store_order_book_deltas = true`**, so no raw deltas are
being written at all right now (`order_book_deltas/` is an empty directory) and this
mechanism currently has nothing to do.

**3. `collector_core/prune_catalog.py` / `make prune`** — a separate, manual/cron-only
script, not run by the collector itself (moved from `dydx_collector/` by story 22.13; report
only unless `--apply`). `make prune` targets `order_book_deltas` at a flat **14-day**
global cutoff, regardless of pinned status. Since nothing is stored there today (see
above), running it currently frees nothing. `trade_tick` is refused in `--types`, and
mechanism 1 above skips it too: trades have their own policy (4).

**4. Trade retention, gated on reconciliation** (`prune_catalog --candles-dir ...
--trade-retention-days 7`, run by `make nightly`): a `data/trade_tick/<iid>/` file is
deleted only when every UTC day its name spans is older than 7 days **and** that
instrument-day's `verified_days` row (`candles_<venue>.db`, written by `compare_klines`)
is `pass`. Otherwise it is kept and listed with its reason (`unverified` / `failed`).

**Known limit:** raw trades exist to correct and prove the aggregates (the rebuild and the
kline reconciliation), not as a tick-level research archive, so the window is 7 days after
a day is proven. Upgrade path: raise `--trade-retention-days` (or drop the trade policy from
`make nightly`) if tick-level features are ever wanted in backtests.

**Known limit:** a trade day that is `unverified` or `failed` is kept **indefinitely** -- the prune
never releases it -- so the archive keeps growing by every such day until 22.14 closes the
reconnect gaps behind most failures. The operator path for each listed day (the prune report
names them every night): find and fix the cause, then re-run `make nightly VENUE=<v> DAY=<day>`
so it verifies `pass` and is released; or, after deciding the day's raw trades are not needed
(the rebuilt snapshots and candles stay), delete its `trade_tick` files by hand and record why in
`docs/DATA_INTEGRITY_AUDIT.md`. Upgrade path: an explicit "accepted" verdict in `verified_days`,
set by the operator, that the prune honours.

### The actual retention per type, put plainly

| Data type | For your 29 pinned instruments | For any other dYdX market |
|---|---|---|
| `TradeTick` | Archived (§1.1); deleted 7 days after its day reconciles `pass` (mechanism 4); an unverified or failed day is kept | not collected (trades are subscribed for collected instruments only) |
| `OrderBookDeltas` | Not stored (no instrument opts in) | not stored |
| `MarkPriceUpdate` / `IndexPriceUpdate` / `FundingRateUpdate` / `InstrumentStatus` | **Unlimited** | 4h |
| `DydxSecondSnapshot` (now includes trade OHLC, §1.7) | **Unlimited** (pinned + liquid only, so this is always the pinned group) | not collected |
| `OpenInterest` | **Unlimited** | 4h |
| Instrument definitions (`crypto_perpetual`) | **Unlimited** | 4h |
| `Bar` / `custom_dydx_minute_bar` | **Dead legacy data.** Written by an earlier pre-pivot architecture (§1.3 — the collector no longer calls `subscribe_bars` at all); nothing writes new files here and nothing prunes the old ones. Safe to delete manually if disk space matters; not wired into anything live. | — |

**Bottom line:** every instrument you've configured is `pinned = true`, and pinned
instruments are permanently exempt from `_prune_loop`. So for all 29 configured coins,
mark/index price, funding rate, open interest, instrument status, and the 1-second book
snapshots (which now also carry trade OHLC) still accumulate forever with no built-in
cap. Raw `TradeTick` is archived again since story 22.13 (§1.1), but bounded: a proven day
is released after 7 days (mechanism 4). The other unlimited types above still need
`non_config_retain_hours`-style bounding if you want them capped too.

---

## 6. Nightly maintenance: rebuild, reconcile, release (story 22.13)

`make nightly VENUE=<DYDX|BYBIT|HYPERLIQUID> [DAY=YYYY-MM-DD]` (default: yesterday, UTC)
runs `collector_core.nightly`, each step its own process, stopping at the first failure:

1. `rebuild_seconds --apply` -- rewrites the day's snapshot trade columns from the raw
   archive on `ts_event` (late trades move to their exchange second; the arrival row is
   cleared). Rows before the instrument's first archived trade keep live values
   (`not covered`), and so do rows inside an archive-gap marker (`<catalog>/_archive_gaps/<iid>.jsonl`, format
   `kernel.archive_markers`:
   a trade write that failed while its snapshots landed, a quarantined or a pruned trade file) --
   the archive is known to miss trades their live values hold. A marker line that does not
   parse, lacks a key, or holds a non-integer or inverted span makes the rebuild refuse that
   instrument every night, naming the file and line, until the line is fixed by hand: guessing a
   span could overwrite exactly the rows the marker protects. Trades with no covered row are
   counted (`orphan trades`). An instrument-day with two rows in one second or mixed schemas is
   refused and left untouched (exit 2, the chain continues).
2. `consolidate_catalog --apply --venue --days 2` -- one file per recent closed day and data type.
3. `build_candles --day --venue --workers 1` -- refolds the day into `candles_<venue>.db`.
4. `compare_klines` -- every traded minute against the venue's own 1 m klines, exact
   integer units (no tolerance), parsed from the venue's decimal strings. Bybit's klines are
   seeded with the previous close, so our Bybit bars are put in that definition first
   (wire-verified; see the tool's docstring). Each mismatch is a
   `reconcile.kline_mismatch`; the verdict goes to `verified_days`. A per-instrument error (no
   definition, fetch error, no venue history for the day, unrepresentable value) is a
   `reconcile.error` with no verdict. Exit 2 = findings of either kind (the chain continues),
   1 = run-level failure (stops). `--kline-source catalog` never writes `verified_days`.
5. `prune_catalog --apply` -- the trade retention policy above.

One summary line per venue (per-step outcome and seconds, peak child RSS). The cron line
and the first-run measurements still owed are in `docs/DEPLOY_CHECKLIST.md`.
