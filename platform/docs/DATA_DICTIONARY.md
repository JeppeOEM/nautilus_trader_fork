# Data Dictionary: Collection → Signals → Ranking

What the dYdX collector stores, what `views`/`ranking` compute from it, and
how a value traces from raw feed to the ranking table. All file:line references are
against the `bmad` branch as of 2026-09-05.

Every "error ledger" site named below (`collector.late_trade`, `collector.trade_backfill`,
`collector.candle_store`, ...) is recorded through `observability.error_ledger.record`
(Story 23.1; formerly `ml_signals.error_ledger`, whose shim Story 24.1 deleted). The sites, their
names and what they count are unchanged `[re-cited 2026-09-21: Story 23.1]`, with one addition:
`archive_gaps.inverted_span` counts a gap marker whose `from_ns > to_ns` — a backward wall-clock
step between a lost trade's arrival and the flush. Since Story 26.1 every capture site is a constant in `capture/application/sites.py` and `CaptureService._ledger` is capture's only `record` call; it added `collector.empty_top` (a book with no best bid or ask: seconds skipped, one WARNING and one ledger line per instrument per minute) `[amended 2026-09-26: Story 26.1]`, and Story 30.2 `collector.unencodable` (a second whose row cannot be stored exactly at the instrument definition's precisions: skipped, one ERROR and one ledger line per instrument per minute). Story 31.2 ledgered every site that only logged or skipped before. Per flush: `collector.stale_trade` (§1.1) and `collector.second_rejected` (gate rejections per instrument and reason, `not_collected` excluded). Per event: `collector.skipped_seconds`, `collector.restart_gap` and `collector.coverage_write` (§1.16), `collector.ohlc_outside_book` (every occurrence, the row kept), `collector.snapshot_publish` (a failed `snapshots:raw` publish, Parquet unaffected), `collector.crash` (`run()` raised; restarted with backoff), `collector.unknown_message` (a client message no branch decodes, repr bounded to 300 chars; dYdX's `block_height` and `new_instrument_discovered` dicts are ignored by name), `collector.candle_store_behind` and `collector.book_crosscheck_unconfirmed`. A REST poll's unparseable rows (a missing symbol or `openInterest`, a non-decimal value) go to its own poll site, `collector.open_interest_poll` `[amended 2026-09-29: Story 31.2]`. Story 28.1 added one per-flush site, `collector.hotpath_publish` (a failed `capture:hotpath` publish of the flush's hot-path figures, §1.23: once per failed publish, after a periodic flush or the final flush of a stop or crash, Parquet unaffected) `[amended 2026-09-30: Story 28.1]`. The marker is written as the ordered span and
still protects its rows, so the count is the only signal that the clock stepped back
`[added 2026-09-22: Story 23.2]`.

---

## 1. Raw data collected (`platform/capture/` + its venue packages `capture/venues/<venue>/`)

Each collector (one `CaptureService`, `capture/application/capture_service.py`, wired by its
venue's composition root `capture/venues/<venue>/__main__.py` and run as `python3 -m
capture.venues.<venue>`; no subclass since Story 26.2 `[amended 2026-09-28: Story 26.2]`) owns one WS
connection per venue network and writes
everything through `ParquetDataCatalog.write_data()` — no hand-rolled schemas
(`platform/CLAUDE.md` NAUT-02). Nine distinct types land in the catalog. Six are native
Nautilus types decoded straight from the Rust adapter; two (`DydxSecondSnapshot`,
`OpenInterest`) are custom `Data` subclasses this collector defines because the
PyO3 bindings don't expose the fields another way.

**The collected set** (the committed plans, venue cutover Story 29.3, operator decision
2026-09-26: BTC/ETH on Bybit, SOL on Hyperliquid; reversible by config). Since Story 29.4 every
venue's plan also changes at runtime (`bot_tui`'s `p`/`x`/`:start`, §1.12), which rewrites that
venue's plan file, so on a running box the file, not this table, is the current set:

| Venue | Instrument ids | Market | What each yields |
|---|---|---|---|
| Bybit (`capture/venues/bybit/config.toml`) | `BTCUSDT-LINEAR.BYBIT`, `ETHUSDT-LINEAR.BYBIT` | linear perp | trades, book (`DydxSecondSnapshot`), mark/index price, funding rate, open interest (REST poll) |
| Bybit | `BTCUSDT-SPOT.BYBIT`, `ETHUSDT-SPOT.BYBIT` | spot | trades and book only: no mark/index price, no funding rate (the ticker is subscribed for `LINEAR` alone), no open interest (spot has none) |
| Hyperliquid (`capture/venues/hyperliquid/config.toml`) | `SOL-USD-PERP.HYPERLIQUID` | perp | trades, book, mark/index price, funding rate, open interest (over the WebSocket) |
| dYdX (`platform/data/dydx_config.toml`) | operator data (the live plan file, hot-reloaded) | perp | trades, book, mark/index price, funding rate, open interest (indexer REST poll); raw `OrderBookDeltas` per opt-in |

dYdX's collector (compose service `collector`) is gated behind the `dydx` compose profile since
the cutover: `make up` does not start it, `make up-dydx` does and `make down-dydx` removes it.
Its archived days stay in the catalog and the `archive` service keeps verifying and pruning them
(`DYDX` stays in `archive/config.toml`'s `venues`) until they age out
(`docs/DEPLOY_CHECKLIST.md` §8).

### 1.1 `TradeTick` (native Nautilus type) — raw trade archive (story 22.13)

- **Source:** every venue's trade channel, decoded by the Rust adapter and delivered to
  `CaptureService._on_data` (dYdX `v4_trades`, Bybit `publicTrade`, Hyperliquid `trades`).
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
- **Stale filter, judged on arrival** (DATA-06, `capture/domain/trade_intake.py`
  `TradeIntake.accept`): a first copy whose age *when it arrived*, `ts_init - ts_event`, exceeds
  `stale_trade_seconds` (default 10 s) is subscribe-time history, counted `stale` and neither
  archived nor folded. A replay (same id, same feed) is checked first and stays `duplicate`. Age is
  never judged at processing time: an ingest backlog does not turn a live trade into history. A
  trade with no receipt stamp (`ts_init == 0`, only a hand-built one) keeps the old
  processing-time age, `now - ts_event`. Evidence for the change: the committed Bybit
  `linear.publicTrade` fixture replays into 573 distinct trades (187 BTCUSDT, 386 ETHUSDT),
  received 0.10–0.24 s after venue time. Processed after a 15 s ingest stall, the old rule drops
  all 573 and the arrival rule archives all 573 (`platform/tests/test_stale_trade_burst.py`).
  Each flush writes one `collector.stale_trade` ledger line naming every instrument that dropped
  a trade, with its count and oldest/youngest arrival age. It also writes one coverage
  `trades_dropped` line per such instrument, spanning the dropped trades' `ts_event` (§1.16). The
  INFO line "Dropped subscribe-time trade history" is gone `[amended 2026-09-29: Story 31.2]`.
- **Dedup window, time-bounded and seeded** (DATA-06, MEM-02): an id is evicted only while the
  window holds more than `seen_trade_ids` ids **and** that id was registered more than
  `DEDUP_HORIZON_NS` (= `MAX_TS_INIT_SKEW_NS` 300 s + `READ_SPAN_MARGIN_NS` 60 s = 6 min) before
  the newest registration. The registration time is the arrival `ts_init` for a live copy, the
  fetch time for a REST backfill copy and the archived `ts_init` for a seeded id. No copy that
  could still be archived arrives after the horizon: a live copy older than
  `stale_trade_seconds` on arrival is stale, and a backfill admits nothing older than
  `MAX_TS_INIT_SKEW_NS`. So a count-bounded window can no longer evict an id and then archive it
  a second time. Registration times are kept as one mark per registration second, not one per
  id, to hold the hot-path allocation budget (`tests/test_hotpath.py`), so eviction is at most
  1 s late and never early. At `run()`, before the first subscribe, each plan id's window is seeded from the
  archive's own trades with `ts_init` in `[now - horizon, now]` (`ParquetArchiveWriter
  .recent_trade_ids`: only the `trade_id`/`ts_init` columns of the files whose name span meets
  it). Seeded ids carry the first feed `archive`, so a live or REST copy of an id the previous
  process archived is a `duplicate_feed`, never archived or folded again, and not counted as a
  feed-arbitration overlap. Seeding runs in `apply()` for every added id, before it is subscribed. A file whose name is not
  a catalog span, or that vanishes or cannot be read, is ledgered at `collector.dedup_seed` and
  skipped; the other files still seed. Known limit (in `trade_intake.py`): within the horizon the
  window has no count bound. At a sustained 1000 trades/s on one instrument it holds about
  360 000 ids (~47 MB per instrument, ~84 MB with a second feed; it scales per instrument). Upgrade path: a compact per-feed id encoding, or a bloom-backed tier
  past `seen_trade_ids` `[amended 2026-09-29: Story 31.2]`.
- **Flush tie guard:** each flush sorts a batch by `ts_init` and keeps back the group
  sharing its newest `ts_init` while it is under 5 s old (the rest of that WS message may
  still be queued): `write_data` refuses a file whose `ts_init` interval touches an existing
  one, which would lose the next flush's whole batch. The shutdown flush writes everything.
- **Live use:** each accepted trade is also kept for the current sample and folded once by
  `kernel.fold.fold_trades` (exact: `Quantity.raw` sums, `Price.raw` comparisons)
  into that second's `DydxSecondSnapshot` (§1.7). Live is provisional -- arrival-timed on
  dYdX, exchange-timed on Bybit/Hyperliquid (story 22.12, §1.7) -- and
  `archive.rebuild_seconds` re-derives closed days from this archive (§6).
- **Late trades (venue mode):** a trade processed after its exchange second closed is still
  archived here, counted (`collector.late_trade` in the error ledger, once per instrument per
  flush) and left out of every live row; the rebuild places it in its own second. A trade
  stamped more than `hold_back_seconds + 5 s` after its arrival is handled the same way
  (`collector.venue_clock_ahead`).
- **NO_AGGRESSOR on the wire** (Story 31.4): the fold counts every non-BUYER trade as a sell.
  That convention matters only if a venue sends a side token other than its two documented ones.
  Bybit's adapter maps `S: ""` to `NoAggressor` (`BybitOrderSide::Unknown`); Hyperliquid's has no
  such variant (`HyperliquidSide` is `B`/`A` only), so another token would fail the message in
  the adapter, not reach the fold. Measured on the Story 31.2 soak, 2026-09-29T13:00-15:00Z
  (`verification.trades`, §1.17): **0** of 789,692 Bybit reference trades (BTCUSDT/ETHUSDT linear
  and spot) and **0** of 11,464 Hyperliquid SOL trades carried a token other than `Buy`/`Sell` or
  `B`/`A`. The convention is kept and stays checked: the tool counts `wire_no_aggressor` with its
  tokens per instrument, and folds such a trade to the sell side exactly as production does (audit
  D-93).
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
`[last archived ts_event - 5 s, now]` over stdlib REST (each venue's `capture/venues/<venue>/trade_history.py`, a `VenueTradeHistory` `[amended 2026-09-26: Story 26.1]`).
Unseen ids are archived with the venue's `ts_event` and `ts_init` = the time they were archived
(so `ts_init - ts_event` shows the recovery lag); they are **never** folded into the live second
-- the nightly rebuild places them. One `collector.trade_backfill` ledger entry per backfill
names the feed, the detections, and the counts: backfilled, already archived, refused (older
than the 300 s `kernel.clocks.MAX_TS_INIT_SKEW_NS`, which the rebuild and prune depend on), unrecoverable
seconds, no baseline, errors. Every venue's `collector:status` carries the per-instrument
cumulative `trade_backfill` (Bybit and Hyperliquid since Story 29.2), and the per-flush log line
reports it too.

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
have no snapshot row, so their backfilled trades are rebuild orphans. Since Story 31.2 every
backfill also writes to the coverage record (§1.16): the ids it archived go to a
`trades_backfilled` line. A window it could not check goes to a `trades_unrecoverable` line:
`depth` when the venue's history did not reach back to the baseline, `fetch_failed` when the
fetch raised or had no instrument definition or no `VenueTradeHistory`, or shutdown interrupted
or abandoned the backfill before its fetch. A `no baseline` backfill
(no archived trade known in memory) records nothing `[amended 2026-09-29: Story 31.2]`.

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
- **Retention:** per-instrument `retain_hours` in `config.toml`, pruned by the nightly
  `archive.prune_catalog --dydx-plan` (`RetentionPolicy`'s `delta_retention` rule, §5; Story 25.1
  deleted the in-collector `_prune_delta_retention`); `None` = kept forever.
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
  collector.** (the candles context's one seconds → bars fold instead derives candles from
  `DydxSecondSnapshot`'s per-second OHLC fields — see §2.5.)

#### Backfilled `Bar`s (Story 22.9)

The one `Bar` path that *does* write to the catalog: `python -m archive.backfill_bars`, an
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
- **Bybit and Hyperliquid** (the bullets above are dYdX's) `[amended 2026-09-29: Story 31.6]`, proven by `verification.derivs`
  (§1.19):
  - **Bybit** (linear only; `capture/venues/bybit/client.py` subscribes `tickers.<symbol>` for
    `LINEAR`): one row per ticker frame carrying `markPrice` -- Bybit sends a field in a `delta`
    frame only when it changed, so this is change-driven too. `ts_event` = the frame's `ts` (whole
    ms), `ts_init` = the adapter's receipt. Precision: the instrument's `price_precision` (Bybit
    publishes at its tick, no re-stamping); label measured `2` on BTCUSDT/ETHUSDT.
  - **Hyperliquid** (`subscribe_mark_prices`): one row per change of the `activeAssetCtx`
    `markPx` wire string (the adapter's per-coin string cache,
    `crates/adapters/hyperliquid/src/websocket/handler.rs:868-970`); a push that repeats the value
    stores nothing. The wire carries no time: `ts_event == ts_init`, the adapter's receive clock.
    Label = the instrument's `price_precision` (`6 - szDecimals`, `4` for SOL).
  - **Change-only storage (audit D-107):** a quiet second has no row, so a reader wanting the
    value at time t forward-fills the last row at or before t; a gap in rows is not a gap in
    data (compare the coverage record, §1.16, for when the collector was absent).

### 1.5 `IndexPriceUpdate` (native Nautilus type)

- Same channel/path/precision-fix as mark price (`client.py`). Oracle index
  price, distinct from the venue's own mark price.
- **Reading it back:** `ParquetDataCatalog.query(IndexPriceUpdate, ...)` raises
  `NotImplementedError` in the pinned nautilus_trader (no Arrow decoder, and the Rust backend has
  no such data type), so the one reader is `kernel.catalog_files.query_index_prices` (column
  projection, `Price.from_raw` at the file's `price_precision`), used by
  `research.application.frames.CatalogFrames.mark_index` `[amended 2026-09-28: Story 27.1]`.
- **Bybit and Hyperliquid** `[amended 2026-09-29: Story 31.6]`: as §1.4 with Bybit's `indexPrice` (one row per ticker frame
  carrying it; the busiest stream collected, ~7.6k rows an hour on BTCUSDT) and Hyperliquid's
  `oraclePx` (one row per change of its wire string, `ts_event == ts_init`). Same `ts_event`
  rules, same labels, same forward-fill rule (D-107).

### 1.6 `FundingRateUpdate` (native Nautilus type)

- **Source:** markets channel, plain pyo3-object path (`client.py`), forwarded
  via `FundingRateUpdate.from_pyo3` with no transformation.
- **Fields:** `instrument_id`, funding rate value, `ts_event`, `ts_init`.
- **Downstream use:** research only: `research.application.frames.CatalogFrames.funding`
  reads it (time-bounded `catalog.query`) for notebooks; no file in `views/` or `ranking/`
  reads `FundingRateUpdate` `[amended 2026-09-28: Story 27.1 -- was "none found ... dead data"]`.
- **Bybit** `[amended 2026-09-29: Story 31.6]`: from the linear ticker. The adapter keeps a per-symbol `funding_cache`
  (`crates/adapters/bybit/src/python/websocket.rs:1549-1570`) of the last `fundingRate` and
  `nextFundingTime` *strings*; a frame carrying either one that differs from the cached string
  yields one row, parsed from that frame alone. So:
  - `ts_event` = the frame's `ts` (whole ms);
  - `interval` = `fundingIntervalHour` x 60 and `next_funding_ns` = `nextFundingTime` x 10^6 **only
    when the frame carries them** -- a delta frame carries only what changed, so almost every row
    has both **null** (the soak, 12:59-17:00Z: of BTCUSDT's 100 rows only the collector's startup
    snapshot row carries `interval`, and only it and the 16:00Z settlement frame carry
    `next_funding_ns` -- 99 and 98 null; ETHUSDT 62 and 61 null of 63). Null means "not in this frame", not "none": a reader
    takes the interval and the next funding time from the last row that carried them (audit D-103);
  - a frame whose `nextFundingTime` changed but which carries no `fundingRate` is dropped:
    the parse needs the rate, fails, and the adapter only `log::debug!`s it. Its new next time
    reaches the catalog only with the next rate change (`next_time_only`, a Known limit, audit
    D-104; measured 0 such frames on the soak).
- **Hyperliquid** `[amended 2026-09-29: Story 31.6]`: one row per change of the `activeAssetCtx` `funding` wire string;
  `interval` 60 (hourly funding, venue docs), `next_funding_ns` null, `ts_event == ts_init`.
- **Stored text** `[amended 2026-09-29: Story 31.6]`: `rate` is JSON-quoted decimal text written by `rust_decimal`, which uses
  scientific notation for small magnitudes (`"-9.368E-7"` for Hyperliquid's wire
  `-0.0000009368`; 1 of 1,684 stored rates on the soak). The value is exact; a reader must parse
  it as a `Decimal` (or accept the exponent form), never assume plain digits (audit D-108).

### 1.7 `DydxSecondSnapshot` (custom `Data` type, `kernel/second_snapshot.py`)

The core microstructure record — a 1-second-sampled L2 book snapshot, **not** raw
deltas. Per `platform/CLAUDE.md`'s Signal Architecture rule: store raw inputs, compute
signals on read (SIGNAL-01). Moved from `collector_core/` to the shared kernel in Story 23.2
with its class name (the catalog directory `custom_dydx_second_snapshot` derives from it). Since
Story 30.2 its book and trade fields are **exact integers** (the layout below), in Parquet and in
the `snapshots:raw` payload alike: `kernel/second_snapshot.py` is the one encoder/decoder of that
layout, `DydxSecondSnapshot.from_dict` the one parser of a stored row or a `snapshots:raw` entry,
and no other module reads the gap-encoded book columns (`platform/tests/test_boundaries.py`) except
the migration that writes them and the book oracle's own independent decoder
(`verification/infrastructure/snapshot_book.py`, §1.18) `[amended 2026-09-29: Story 31.5]`. The
trade columns are read without the book by `kernel.catalog_files.query_second_ohlc` as
`SecondOHLC` rows (decoded floats), level 0 by `query_top_of_book` as exact `Price`/`Quantity`.

- **Stored layout (Story 30.2; `DydxSecondSnapshot.schema()`):**

  | Column | Arrow type | Holds |
  |---|---|---|
  | `instrument_id` | `dictionary<int8, string>` | the id |
  | `price_precision`, `size_precision` | `uint8` | the instrument definition's precisions, **per row** (a mid-day precision change can never make two files disagree) |
  | `bid_prices`, `ask_prices` | `list<int64>` | element 0 = the best price in units of `10^-price_precision`; every later element = the strictly positive **gap** to the level above (bids: previous − this; asks: this − previous) |
  | `bid_sizes`, `ask_sizes` | `list<int64>` | each level's size in units of `10^-size_precision` |
  | `buy_volume`, `sell_volume` | `int64` | size units (0 when that side did not trade) |
  | `buy_count`, `sell_count` | `uint32` | trade counts |
  | `open_price`, `high_price`, `low_price`, `close_price` | nullable `int64` | price units, null when nothing traded |
  | `ts_event`, `ts_init` | `uint64` | the two clocks (below) |

  Units come from Nautilus's exact `Price.raw`/`Quantity.raw` scaled down by
  `10^(FIXED_PRECISION − precision)` with integer division asserted exact (`units_of`), at the
  precision of the instrument definition capture holds -- never through `float`, never from a
  value's own digits. A value that is not exact at its precision, outside int64, or a book level
  that is not strictly below (bids) / above (asks) the one before is refused
  (`SnapshotEncodingError`): the sampler rejects that second as `Unencodable` (logged and
  ledgered `collector.unencodable`, one line per instrument per minute), never rounds it. Known
  limit: int64 units cap a value at 9.22e18 units (≈ 9.2e9 at size precision 9); upgrade path: a
  per-row size exponent or decimal128.

  **Decode by hand** (any language, no Nautilus): `value = units / 10^precision`, exactly as a
  decimal (`858919` at precision 1 is `85891.9`). Book prices: `p[0] = stored[0]`, then
  `p[i] = p[i−1] − stored[i]` for bids and `p[i] = p[i−1] + stored[i]` for asks. Example at
  price precision 1: bids 100.5 / 100.3 / 99.9 are stored `[1005, 2, 4]`, asks 100.7 / 101.0
  `[1007, 3]`. In Python, `DydxSecondSnapshot.from_dict(row)` does it and exposes the pre-30.2
  attribute names as floats computed once by `unit_float` = `float(units) / 10.0**precision`
  (`bid_prices`, `buy_volume`, `close_price`, ...), the integers (`bid_price_units`, ...), and
  exact `Price`/`Quantity` values (`.exact`); `as_floats()` is the dict `kernel.indicators`' pure
  functions take (the decoded floats plus the row's two precisions, Story 31.3: `spread` rounds its
  difference to `price_precision`, §2.1). The web formats units only in `frontend/src/lib/units.ts` (exact, string-based).

  **The `snapshots:raw` payload** is the same row: a JSON list of `DydxSecondSnapshot.to_dict()`
  results -- the integers, both precisions and the gap-encoded book -- published by
  `capture/infrastructure/redis_stream.py`. Every consumer (`ranking`, `views.live_candles` and
  through it `alerting` and `data_api`'s live candles) decodes it with `from_dict` and may use
  floats only inside its own computation; `from_dict` refuses a float, a bool or a missing
  precision (a pre-30.2 entry). `data_api` passes integers through to the web
  (`/api/snapshots/{iid}`: `bid_units`/`ask_units`/`price_precision`; `/catalog/snapshots/{iid}`:
  the wire dict itself). A float-layout file is refused by every reader
  (`LegacySnapshotLayoutError`) until `archive.tools.migrate_snapshot_ints` has rewritten it (§6).

- **Fields** (their meaning; decoded values):
  - `instrument_id`
  - `bid_prices`, `bid_sizes`, `ask_prices`, `ask_sizes` — up to `BOOK_DEPTH = 20`
    levels each (`second_snapshot.py`), index 0 = best bid/ask
  - `buy_volume`, `sell_volume` — summed trade size per side since the last tick
  - `buy_count`, `sell_count` — trade count per side since the last tick
  - `open_price`, `high_price`, `low_price`, `close_price` — OHLC of actual executed
    trade prices within this second, `None` if no trade occurred. Live, each accepted
    `TradeTick` is kept in `CaptureService._second_trades` and folded once per sample by
    `kernel.fold.fold_trades` (the same exact fold the nightly rebuild uses; the
    columns hold the exact totals in units, `SecondTradeFields.snapshot_units`). A closed day's rows are
    re-derived from the raw archive (§1.1) on exchange time by `rebuild_seconds` (§6);
    book columns and timestamps are never touched. `candles.domain.fold.fold_arrays`
    combines these across multiple seconds for coarser candles (§2.5);
    `ranking/infrastructure/catalog_prices.py` (the ranking price backfill) reads `close_price` as its only
    price source: a window with no trade is an empty series, never mark prices (Story 31.3 deleted
    that fallback, which mixed a second quantity into the trade-close series).
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
  (`ranking`'s `InstrumentMetrics.last_seen_ns`), so that lag never reads as a stale feed. `hold_back_seconds`
  is set per venue from `python -m archive.tools.measure_lag` (the per-kind distribution of
  `ts_init - ts_event`); it only makes fewer trades late, the rebuild is what makes a second
  correct (audit D-50).
- **Built by:** `capture/application/capture_service.py`'s `CaptureService._second_loop` (the gate itself is `capture/domain/sampler.py`'s `SecondSampler` `[amended 2026-09-26: Story 26.1]`), every
  `snapshot_interval_seconds` (config default 1.0s, overrideable in `config.toml`), on a
  drift-free wall-clock schedule at mid-interval (`_next_sample_at`): exactly one row per
  floor second, which the rebuild's trade-to-row mapping relies on. Venue mode
  (`_venue_second_loop`) instead closes each exchange second at wall
  `S + 1 + hold_back_seconds`; after a stall it closes every overdue second (at most 60) in
  order, each from the book as of its own end.
- **Guards before emission:** skips a missing book and an empty top of book (no best bid or
  ask; rate-limited WARNING + `collector.empty_top` since Story 26.1), skips a row that cannot be
  encoded exactly (no instrument definition, or a value finer than it: rate-limited ERROR +
  `collector.unencodable`, Story 30.2), skips crossed books (the
  venue's `CrossedBookPolicy` -- on dYdX the active uncross first, then a forced resync after a
  persistent cross; on Bybit a forced resync past `crossed_resync_seconds`) `[amended 2026-09-26: Story 26.1]`, and skips
  stale books with no `OrderBookDeltas` for `config.stale_book_seconds` (5s; in venue mode
  measured from the last applied delta's `ts_event` to the end of the second, the feed-dead
  test staying on arrival) — both are
  `platform/CLAUDE.md` DATA-01 "flag the gap, never fabricate" implementations.
- **Scope:** pinned + liquid instruments only. Illiquid instruments get no snapshots.
- **Dual delivery:** written to the catalog via the normal buffer/flush path
  (`CaptureService._sample_tick` appends it to the flush buffer, `capture_service.py`) **and** published
  live to Redis channel `snapshots:raw` (`publish_snapshot_batch`,
  `capture/infrastructure/redis_stream.py`) `[amended 2026-09-26: Story 26.1]` —
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
  with `python -m archive.tools.migrate_open_interest`, audit D-40). The dYdX-specific
  notes below describe `capture/venues/dydx/open_interest.py`, which keeps the poll
  (`classify_liquidity` moved to `collection_control.domain.liquidity` in Story 25.4).

- **Why custom/separate:** open interest is parsed Rust-side but never forwarded to
  Python on either the REST or WS markets-channel path (`open_interest.py`
  docstring, citing `crates/adapters/dydx/src/python/{http,websocket}.rs`) — the one
  field this collector has to re-fetch itself.
- **Fields:** `instrument_id`, `open_interest` (`Decimal`, stored as a string in Arrow
  to avoid float round-tripping), `ts_event`, `ts_init`.
- **Source:** plain stdlib `urllib` poll of dYdX's public indexer
  `/v4/perpetualMarkets` REST endpoint (`fetch_markets_json`, `open_interest.py`),
  every `open_interest_poll_seconds` (config default 300s, `capture/venues/dydx/config.py`'s
  `DydxConfig`), run by `CaptureService.poll_loop` from the dYdX composition root (Story 26.2;
  every market kept, a failure ledgered `collector.open_interest_poll`).
- **Also drives liquidity tiering:** `classify_liquidity` (`collection_control/domain/
  liquidity.py`, Story 25.4) reads the *same* markets JSON response but keys off `volume24H`
  (USD), not `openInterest` (base-token units) — `platform/CLAUDE.md` OBS-03 explicitly calls out
  the token-vs-USD confusion as a past production bug. It labels each collected instrument
  liquid/illiquid on `collector:status` and alone admits a `pin_top_liquid` pin (§1.12),
  independent of storing `OpenInterest` itself.
- **Downstream use of the stored `open_interest` field:** research only
  (`research.application.frames.CatalogFrames.open_interest`, a time-bounded `catalog.query`,
  for notebooks) `[amended 2026-09-28: Story 27.1]`; none in `views/`/`ranking/` — only the
  *volume*-based liquidity classification (a separate, parallel computation in the same module)
  is used live. Not wired into any live signal or ranking.
- **Bybit** `[amended 2026-09-29: Story 31.6]`: dropped by the bindings on the linear ticker path, so polled over REST
  (`capture/venues/bybit/open_interest.py`, the category-wide `/v5/market/tickers?category=linear`
  `openInterest`) every `open_interest_poll_seconds` (committed `config.toml`: 300; the loader's
  default when the key is absent is `BybitConfig.open_interest_poll_seconds = 300`,
  `capture/venues/bybit/config.py:36`). One row per poll per linear id, whether the value changed
  or not. **`ts_event == ts_init` = the collector's `time.time_ns()` after the response
  (`open_interest.py:44`), a local wall clock, never a venue time** (audit D-105): the value is
  the venue's state when Bybit answered, up to the poll's latency before `ts_event` (measured
  <= 623 ms). Linear only: `-LINEAR.BYBIT` ids are built from the response, spot has none.
- **Hyperliquid** `[amended 2026-09-29: Story 31.6]`: forwarded over the WebSocket (`subscribe_open_interest`), one row per
  change of the `activeAssetCtx` `openInterest` wire string, stored verbatim as decimal text
  (e.g. `5558985.1799999969`, the venue's own float artefact, kept exactly), `ts_event ==
  ts_init`. Change-only: forward-fill (D-107).
- **Stored text** `[amended 2026-09-29: Story 31.6]`: `open_interest` is `str(Decimal)`, which may use scientific notation for
  small or large magnitudes; parse it as a `Decimal`.

### 1.9 `InstrumentStatus` (native Nautilus type)

- **Source:** markets channel, plain pyo3-object path (`client.py`).
- **Downstream use:** **none found** — stored, not read anywhere in `views/`/`ranking/`/
  `research/`.

### 1.10 Instrument definitions

- **Source:** one-time REST fetch (`DydxClient.fetch_instruments`, `client.py`)
  at collector startup, converted via `instruments_from_pyo3()` and written once
  (`collector.py`). Not a recurring stream — defines the instrument
  universe/precision metadata the catalog needs to interpret every other type
  correctly.
- **Bybit and Hyperliquid** `[amended 2026-09-29: Story 31.6]`: written once per collector start by `CaptureService.run`
  (`self._archive.write_instruments(converted)`) into `crypto_perpetual/<iid>/` (perps) or
  `currency_pair/<iid>/` (Bybit spot), `ts_event == ts_init` = that start. A venue change during a
  run (a new tick size) is not written until the next start; `verification.derivs` then reports
  every later venue poll `differs` and the change as a `venue_change` (§1.19). The adapters map:
  - Bybit linear (`instruments-info`): `price_increment` = `tickSize`, `price_precision` = its
    written decimals (`0.10` -> 2, equal to `priceScale`), `size_increment` = `lot_size` =
    `qtyStep` (its decimals the `size_precision`), `min_quantity` = `minOrderQty`, `multiplier` 1;
  - Bybit spot: the same with `basePrecision` for `qtyStep`;
  - Hyperliquid perp (`meta.universe`): `size_precision` = `szDecimals`, `price_precision` =
    `max(0, 6 - szDecimals)`, increments 10^-precision, no `min_quantity`, `multiplier` 1, and
    **`lot_size` 1: Nautilus's default, not a venue value** (the venue declares none; its minimum
    order is a notional). Never read `lot_size` as Hyperliquid's (audit D-106).

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
  `python3 -m archive.crosscheck_errors` (§6 of `docs/DEPLOY_CHECKLIST.md`), which
  matches a gap in §1.7's second-snapshot rows against these files within the 300 s skew bound
  `kernel.clocks.MAX_TS_INIT_SKEW_NS` **of one of the gap's own edges** — never across its
  interior — before calling it `UNEXPLAINED`.

### 1.12 `collector:status` / `collector:control` (the `collection_control/` context, Story 25.4)

Not market data: every venue's collection plan as `bot_tui`'s Collector pane shows it (Story 6.1;
every venue since Story 29.2), and every venue's live control surface (dYdX's since Story 6.1,
Bybit's and Hyperliquid's since Story 29.4). Published language, frozen
(AD-D12): fields are only ever appended. The bytes are replay-tested
(`collection_control/tests/test_status_replay.py` against a pre-move recording,
`bot_tui/tests/test_collector_status_replay.py` through the TUI's reader, and
`collection_control/tests/fixtures/control_payloads.json` for `collector:control`).

- **`collector:status`** — published by `StatusPublisher` in each collector process (dYdX, Bybit,
  Hyperliquid) at start, then every `liquidity_check_seconds` (dYdX) or
  `PLAN_STATUS_SECONDS` = 1800 s (Bybit, Hyperliquid: a plan with no liquidity refresh; named
  `STATIC_PLAN_STATUS_SECONDS` before Story 29.4), right after every control action or plan
  reload that changed the plan (every venue), and within 30 s of a row's `pending` state changing
  (capture's retry applied it) or of a new apply. One `json.dumps` message per planned instrument, in plan order,
  keys in this order (no `venue`: a reader derives it from the id, SIGNAL-01):
  - `id` — the instrument id;
  - `liquid` — `true` when its USD `volume24H` is at or above the plan's `liquidity_min_oi_usd`
    (the last classification; `false` until the first one);
  - `last_trade_ts` — capture's last book update for it (wall-clock ns; `0` = never);
  - `trade_backfill` — trades recovered over REST after reconnects since start (§1.1);
  - `pending` — present, and `true`, only when the instrument is planned but capture has **not**
    applied it (its subscribe failed on the wire and is being retried, or the venue does not list
    it). Absent on an applied instrument, so every pre-25.4 row shape is unchanged.
  Then the venue's plan aggregate, keys in this order (all but `unpinned_ids` appended in
  Story 29.2; an aggregate without `venue` is a pre-29.2 dYdX producer's):
  - `unpinned_ids` — every `exclude` id, sorted (Bybit and Hyperliquid: their optional `exclude`
    list, written by an `unpin`, `[]` while they have none);
  - `venue` — the plan's `kernel.venues` code (`DYDX`, `BYBIT`, `HYPERLIQUID`);
  - `cap` — the plan's cap: dYdX 30; `null` for an uncapped plan (Bybit and Hyperliquid since
    Story 29.4, operator decision 2026-09-26: no coin cap; before it a static plan's cap was its
    own size);
  - `accepts_commands` — `true` when a `ControlService` consumes `collector:control` for this
    plan: every venue since Story 29.4 (Bybit and Hyperliquid published `false` from Story 29.2
    until then). Absent (older producer): `true` only for dYdX;
  - `min_liquidity_usd` — the plan's liquidity threshold (a float), `null` when the plan
    classifies no liquidity (then every row's `liquid` stays `false` and means nothing);
  - `last_apply` — `null` before capture's first `CaptureService.apply`, then
    `{"ts": <wall-clock ns>, "subscribed": [...], "unsubscribed": [...], "failed": [...]}` for the
    most recent apply (startup or command), each id list sorted. It is history: a failed id
    capture's retry has since subscribed loses its row's `pending` while this still lists it;
  - `last_refusal` — `null` until the plan refuses a command (`PlanRejected`) after the collector
    started, then `{"ts": <wall-clock ns, time.time_ns()>, "action": ..., "id": ..., "reason":
    <the refusal's text>}` for the most recent one (`id` `null` for `pin_top_liquid`). Held in
    memory only, so a restarted collector publishes `null` again. `bot_tui` shows it under the
    venue's last apply and, for its own market-browser adds, as the row's `failed: <reason>`: a
    newly arrived refusal of a `start` naming an add it has outstanding is copied onto that add at
    once (the field holds one refusal per venue, so a later refusal or a restart's `null` would
    otherwise replace it), ordered by arrival, never by `ts` (another host's clock). Each refusal
    is also republished within `STATUS_CHANGE_POLL_SECONDS` if its own publish failed.
    `[amended 2026-09-29: Story 29.5 -- appended after `last_apply`; every earlier key and byte is
    unchanged, `collection_control/tests/test_status_replay.py`'s `_APPENDED_KEYS`]`
  And on `stop`/`unpin` a `{"id": ..., "removed": true}` tombstone.
- **`collector:control`** — `{action, id, venue}` published by `bot_tui` (only for a plan whose
  aggregate says `accepts_commands` and is fresh), consumed by every venue's collector.
  `venue` (the `kernel.venues` code) was appended in Story 29.4 after the existing keys, so the
  pre-29.4 `{action, id}` bytes stay the payload's prefix (replay-tested against the fixture);
  each venue's `ControlService` acts only on its own venue's messages, a message **without**
  `venue` is dYdX's (every sender before 29.4 drove only dYdX), and a non-string `venue` is
  ledgered `collector.control` and ignored by every venue. Actions: `start` (plan `add`), `unpin`
  (stop and exclude), `stop` (plan `remove`), `pin_top_liquid` (fill the free slots under dYdX's
  30-instrument cap with the top USD-volume liquid ids, never an excluded one; refused by Bybit's
  and Hyperliquid's plans, which have no liquidity threshold: "admits no pins"). A refused command
  -- including an id of another venue than the receiving plan's -- or an unknown action logs a
  WARNING and changes nothing; a refused command (not an unknown action) is also recorded as the
  aggregate's `last_refusal` and published at once `[amended 2026-09-29: Story 29.5]`. The
  market browser's `a` sends the existing `start`, addressed with `venue`: the channel gained no
  action. A valid one is saved to the venue's plan file (dYdX
  `data/dydx_config.toml`; Bybit and Hyperliquid the committed
  `capture/venues/<venue>/config.toml`, mounted read-write, whose optional `exclude` list is
  written only when non-empty), validated through the one loader first, then applied through
  `CaptureService.apply` (a removed id's book is forgotten, its rows stop and its catalog files
  stay; a failed subscribe is `pending` with one `collector.subscribe_failed` per attempt), then
  published. Every venue's file is also re-read every 30 s (dYdX `config_reload_seconds`,
  Bybit/Hyperliquid `PLAN_RELOAD_SECONDS`), so a hand edit is applied without a restart; a file
  that fails validation is ledgered `collector.config_reload` and the current plan is kept.

### 1.13 `archive:status` / `archive:control` (the `archive/` context's scheduler, Story 25.1b)

Not market data: the nightly-maintenance scheduler's status and its one command. The publisher is
`archive.scheduler` (`python3 -m archive.scheduler`, compose service `archive`), the one place
maintenance is scheduled (§6). No reader imports `archive`: `views/archive_status_bus.py`,
`data_api/routes/archive.py` and `bot_tui/archive_state.py` each keep their own copy of the
channel names and shape, as for `ranking:control`/`collector:status`
`[amended 2026-09-26: Story 25.1b -- new channels]`.

- **`archive:status`** — published by the scheduler after every step, on start and on a 30 s
  heartbeat. One `json.dumps` object holding the whole status, keys in this order:
  - `next_run` — the next nightly slot, ISO-8601 UTC (`2026-09-27T03:07:00Z`);
  - `next_intraday` — the next intraday closed-hour merge slot, same format;
  - `running` — `null`, or the job in progress: `{run_id, kind, day, days, started, steps}`;
  - `last_run` — `null`, or the last finished nightly-sequence run (`kind` `nightly`,
    `catch_up` or `run_now`): `{run_id, kind, day, days, started, finished, steps}`;
  - `last_intraday` — `null`, or the last finished intraday merge (`kind` `intraday`), same shape,
    kept apart so a 4-hourly merge never hides the nightly's `last_run`;
  - `backup` — `"enabled"` or `"disabled"`: whether the scheduler's full runs end in the off-site
    backup (`backup_enabled` in `archive/config.toml`, committed `false`). `"disabled"` means the
    catalog has no copy off the host (audit D-33) and no run carries a `backup_catalog` step
    `[amended 2026-09-26: Story 26.1b -- new key]`.

  `kind` is one of `nightly`, `catch_up`, `run_now`, `intraday`; `day` is the run's (first) UTC
  day and `days` every day it covers (a catch-up runs several, oldest first); `started`/`finished`
  are ISO-8601 UTC. Each `steps` entry is `{venue, name, exit, duration_s}`: `venue` is `null` for
  the venue-less steps (consolidate, backup), `exit` the step subprocess's exit code (0 clean,
  2 findings, anything else failed -- the archive tools' convention), `duration_s` its wall
  seconds. Readers require an object carrying `next_run` and `last_run` (neither may be
  omitted; `last_run` may be `null`), treat `next_intraday`/`running`/`last_intraday`/`backup` as
  optional (a present `backup` other than `"enabled"`/`"disabled"` is malformed) and ignore keys
  they do not know, so a key can be added without breaking them.
  Consumers:
  - `data_api`'s `GET /api/archive/status` (through `views.archive_status_bus.ArchiveStatusBus`,
    one subscriber per process): the latest valid message, 503 until one has arrived. A message
    failing the shape check keeps the previous one and is ledgered `views.archive_status`;
  - the web UI's maintenance status in the top bar (`frontend/src/components/ArchiveStatus.tsx`,
    polled every 30 s): last run day and outcome -- `ok` when every step exited 0, `findings` when
    the only non-zero exits are 2, else `FAILED` with the non-zero steps named -- its finish time,
    the next run, the running job, a failed intraday merge, and `backup off` in the warn colour
    when `backup` is `"disabled"`;
  - `bot_tui`'s Collector pane, its last line (`collector_pane.format_archive_line`), the same
    verdicts, `backup off` appended when `backup` is `"disabled"`; `~` marks it stale after 120 s
    without a message.
- **`archive:control`** — `{"command": "run_now", "day": "YYYY-MM-DD" | null}` (`null` =
  yesterday, UTC). Publisher: `data_api`'s `POST /api/archive/run` (body `{"day": ...}`, extra
  keys, a malformed or non-existent date, today or a future day a 422), driven by the web UI's
  "Run now" button behind a confirm dialog; it publishes exactly
  `json.dumps({"command": "run_now", "day": day})`, and a publish no subscriber received (the
  `archive` service is down) or a Redis error is a 503, shown in the dialog
  (`data_api/tests/test_archive.py`). Consumer: the scheduler, which queues the day's full nightly
  sequence behind any running job and drops a duplicate queued day; bad JSON, an unknown command,
  a bad date, today or a future day are ledgered `archive.control_rejected` and ignored. The 202
  therefore means "received", not "accepted": the run is confirmed only when `archive:status`
  shows it.

### 1.14 WebSocket subscribe limits (Story 29.4)

Not a data type: the measured subscribe limits each client paces under, now that a plan can
change at runtime. Every Python-side wire call of the Bybit and Hyperliquid clients waits on
`capture.application.wire_channels.WireChannels`, a monotonic pacer at the venue's constant, and
holds at most one reference per (channel, id); a failed call runs its inverse (`undo`) to restore
the Rust client's own bookkeeping (Bybit's topic reference count, Hyperliquid's asset-context
set), both updated before the failing send, so a retry sends the channel again. Measured from the dev box (Bybit 2026-09-28
23:59Z, Hyperliquid 2026-09-29 00:02 to 00:05Z) with `PYTHONPATH=. python scripts/measure_ws_limits.py --venue bybit|hyperliquid`, an
independent aiohttp client (no code shared with the Rust clients), symbols and coins taken live
from each venue's REST listing. Refresh these numbers from a workstation, never from the VPS: the
Hyperliquid ramp holds all 1000 channels of its IP while it runs, starving the collector there.

| Venue | Test | Result |
|---|---|---|
| Bybit `linear` (886 symbols) | one subscribe request carrying N `publicTrade` topics, N = 10, 11, 20, 50 | all accepted (`success: true`) |
| Bybit `spot` (530 symbols) | same | N = 10 accepted; N = 11, 20, 50 refused, `ret_msg` "args size >10" |
| Bybit `linear` / `spot` | one burst of 200 single-topic subscribe requests back to back on one connection (sent in 6.6 / 6.7 ms), then 200 unsubscribes (6.3 ms each) | the burst accepted in total: 200/200 acked `success` both ways, all within 0.42 / 0.39 s (unsubscribe 0.38 / 0.43 s); no close; a ping afterwards answered |
| Hyperliquid (178 coins) | one burst of 100 `trades`/`l2Book` subscribe frames back to back on one connection (4 ms) | accepted in total: 100/100 `subscriptionResponse`, no `error`, no close |
| Hyperliquid | a 20/s ramp (under the documented 2000 sent messages/min per IP) through `trades`, `l2Book`, `activeAssetCtx`, `bbo` and three `candle` intervals per coin | 1000 acked; every subscription after the 1000th answered `error` "Cannot subscribe to more than 1000 channels." (15 of 1015 sent); socket kept open |

What these show, and what they do not: the Bybit rows are the total of one 200-request burst,
not a sustained rate, and the Hyperliquid ramp was one run at 20/s up to the 1000-channel limit.
Neither measured a per-second ceiling. The pacing constants are chosen margins under those
observations and under Hyperliquid's documented 2000 sent messages per minute, not measured
ceilings:

- **`BYBIT_WS_FRAMES_PER_SECOND` = 20** (`capture/venues/bybit/client.py`): Bybit documents no
  public-stream request rate; 20/s keeps a plan change at a tenth of one clean burst's size per
  second. The Rust client sends one topic per request, so the spot 10-args limit never applies.
- **`HYPERLIQUID_WS_FRAMES_PER_SECOND` = 10** (`capture/venues/hyperliquid/client.py`): 600/min,
  under a third of the documented 2000/min. Pacing is per Python call, so the one
  `activeAssetCtx` frame that mark, index, funding and open interest share is counted four times,
  the safe direction.
- **`HYPERLIQUID_MAX_WS_CHANNELS` = 1000** (same file): the documented and observed per-IP
  subscription limit. The venue refuses the excess only asynchronously (capture would show the id
  applied), so `HyperliquidClient.subscribe` counts the subscriptions it holds -- one per
  `trades`, `l2Book` and trades-only-twin channel, plus one `activeAssetCtx` per id -- and raises
  before sending anything for an id that would pass it: the id stays `pending` with one
  `collector.subscribe_failed` per retry. A venue limit, not a plan cap (the plan stays
  uncapped). Known limit: only this process's channels are counted, so another Hyperliquid socket
  from the same IP (`live-paper`) shrinks the real budget unseen; upgrade path: a per-IP budget
  shared across processes.

Known limit (apply latency): every call is paced and holds capture's subscription lock, so an add
costs about 0.7 s per Hyperliquid coin (7 calls at 10/s with the trades-only twin) and about
0.15 s per Bybit linear id (3 calls at 20/s), delaying other commands and resyncs by that much
per id in a large add; upgrade path: batched subscribes in the Rust clients.

Known limit: the Rust clients' reconnect replay of held subscriptions is not paced by us (it is
bounded by the plan's size); upgrade path: a pacing hook in the Rust client, outside `platform/`
(FORK-01). Raw measurement output is not committed; re-run the script to refresh these numbers.


### 1.15 Reference recordings (`platform/verification/`, Story 31.1)

Not catalog data: the raw wire record of an **independent** client, the source of truth every
Epic 31 comparator checks the catalog against (DATA-02's "second independent client with zero
shared code path"). `python3 -m verification.recorder --venue BYBIT|HYPERLIQUID` (compose
services `reference_recorder_bybit` / `reference_recorder_hyperliquid` of the verify stack,
`docker-compose.verify.yml`, `make verify-up`) opens its own aiohttp WebSockets and REST polls and
never reaches `nautilus_pyo3`, `nautilus_trader` or any capture, candles, ranking or views code,
directly or transitively: its only platform imports are `kernel.venue_http` (URLs; standard library
only since Story 31.1 moved the pyo3-derived dYdX URLs to `kernel.dydx_http`), `kernel.venues` (id
parsing) and `observability` (`platform/tests/test_boundaries.py` checks the transitive import
closure statically and `sys.modules` after an import). It records the venue's collected set: the collector's own `config.toml` (`BYBIT_COLLECTOR_CONFIG` /
`HYPERLIQUID_COLLECTOR_CONFIG`; its directory is mounted read-only, so a host-side replacement of
the file is seen, where the collector's single-file mount needs a restart), `instruments` deduped
in order minus `exclude`, re-read every 30 s (a change reconnects every endpoint with the new set,
tagged `plan_changed`; an unreadable file, or one with no `instruments` key -- what a read landing
mid-save sees, the plan store rewriting the file in place -- keeps the last good set, ledgered; at
start it refuses start, ledgered `verification.recorder.plan`).

Known limits of the reference itself (each also a `Known limit:` in the code):
- *Plan skew.* The recorder parses only `environment`, `instruments` and `exclude`, more loosely
  than the collector's loader (which also refuses unknown keys), and the two re-read the file on
  independent 30 s phases: after an edit the recorded and collected sets can differ for up to
  ~60 s, and indefinitely for a file only the collector refuses. The `plan_changed` connection
  lines and `collector:status` date both sides' changes.
- *Common mode.* Wire symbols/coins come from `kernel.venues` and URLs from `kernel.venue_http`,
  shared with the collectors, so a bug there misdirects both sides alike; the REST responses name
  the symbol actually served, which catches it only partly.
- *Per-endpoint staleness.* The stale-feed watchdog watches all data channels of a socket
  together, so one topic that silently stops while its siblings flow is not caught by it (a
  *refused* subscribe is: Bybit `success: false` and Hyperliquid's `error` channel are ledgered at
  `verification.recorder.venue_error`); it shows up as a gap to the comparators.
- *Receive-time skew.* `recv_ns` is when the recorder's one event loop takes the message from
  aiohttp, so a stall of that loop (the repair of a crash-truncated hour file at its first write,
  the rotation's accounting and prune, compressing a large REST body) stamps every frame queued
  behind it with the stall's end: normally microseconds, after a crash up to hundreds of
  milliseconds once. Comparators treat `recv_ns` as an upper bound, never exact arrival time.
- *Invalid UTF-8.* aiohttp fails a connection on a text frame that is not UTF-8 (close code 1007)
  before the recorder sees the bytes; the loss is a `close` line with reason `error` and a
  `verification.recorder.connection` ledger entry, not a filed frame.

**Files.** `<VERIFY_DATA_DIR>/raw/<venue>/<channel>/<YYYY-MM-DDTHH>.jsonl.zst` (`platform/data/
verification/` in the verify stack; `<venue>` lower case, the hour the UTC hour of the line's own
timestamp): zstd-compressed JSON lines, written with `pyarrow`'s zstd codec, one frame per writer
session per file. Read them with `verification.infrastructure.raw_store.iter_records(path)` (or
`RawFileReader`), which walks the zstd frames itself: a plain `zstd -d` stops at an unfinished
frame.

**Line kinds** (one JSON object per line; `raw` holds the frame or body *verbatim* as a JSON
string, so a malformed frame survives byte for byte; `raw_b64` instead for a binary frame or a
body that is not UTF-8):

```
{"kind":"frame","recv_ns":1790676385230129394,"endpoint":"linear","raw":"{\"topic\":\"orderbook.50.BTCUSDT\",...}"}
{"kind":"rest","sent_ns":...,"recv_ns":...,"endpoint":"linear","request":"/v5/market/orderbook?category=linear&symbol=BTCUSDT&limit=50","status":200,"raw":"..."}
{"kind":"connection","event":"open","ts_ns":...,"endpoint":"linear","reason":"startup","url":"wss://stream.bybit.com/v5/public/linear","subscriptions":["orderbook.50.BTCUSDT",...]}
{"kind":"connection","event":"close","ts_ns":...,"endpoint":"linear","reason":"server_closed","detail":"close code 1000"}
{"kind":"connection","event":"error","ts_ns":...,"endpoint":"linear","reason":"connect_failed","error":"ClientConnectorError(...)"}
```

- `recv_ns` is the local `time.time_ns()` taken the moment aiohttp hands the message over,
  before any parsing (see *Receive-time skew* above); a REST line's `sent_ns` is taken before the request, its `recv_ns` once the
  body is read. A failed REST poll is still a line: with `status` (non-2xx) or `"status": null`
  and `error` (`"error": "cancelled"` for a poll in flight when the recorder stops or the plan
  drops it), and a response judged a failure carries `refusal` (`HTTP 500`, `body is not JSON`,
  `body is JSON null` -- Hyperliquid answers some bad requests with 200 and `null` --,
  `retCode 10001`, `result.list is empty` -- Bybit's 200 for a symbol it does not list).
- `connection` lines: `open` (reasons `startup`, `reconnect`, `plan_changed`,
  `forced_reconnect`), `close` (reasons `server_closed`, `error`, `stale_feed`,
  `plan_changed`, `shutdown`, `forced_reconnect`, `cancelled` -- a session still stuck after the
  10 s stop grace --, with `detail`) and `error` (`connect_failed`,
  `transport_error`, with `error`). Each is written to the venue's `connection` channel **and** to
  every data channel of its endpoint, so any one channel file shows its own gaps (a
  `forced_reconnect` requested while no connection is up -- mid-connect or in backoff -- ends
  that one backoff (a venue still down is then retried with backoff again, never in a tight
  loop) and tags the next `open`; one that loses the race to a real close (`stale_feed`,
  `server_closed`) keeps the normal backoff and tags the next `open`; neither is dropped; `detail` of a server close is the close frame's own
  code and reason): frames between
  a `close` and the next `open` were never received, a recorder gap, never a collector gap.

**Channels** (`verification/domain/subscriptions.py`):

| Venue | Endpoint (socket) | Subscribed | Channels |
|---|---|---|---|
| Bybit | `linear` (`wss://stream.bybit.com/v5/public/linear`) | `orderbook.50.S`, `publicTrade.S`, `tickers.S` per symbol, subscribe args chunked to 10 | `linear.orderbook.50`, `linear.publicTrade`, `linear.tickers`, `linear.control` (subscribe and ping replies) |
| Bybit | `spot` (`.../v5/public/spot`) | `orderbook.50.S`, `publicTrade.S` (no ticker, as capture) | `spot.orderbook.50`, `spot.publicTrade`, `spot.control` |
| Hyperliquid | `ws` (`wss://api.hyperliquid.xyz/ws`) | `l2Book`, `trades`, `activeAssetCtx` per coin | `l2Book`, `trades`, `activeAssetCtx`, `control` (`subscriptionResponse`, `pong`, `error`) |
| both | | | `connection`; `unparsed` (not JSON, ledgered); `unknown` (JSON no table names, ledgered) |

REST polls (URLs from `kernel.venue_http`; each poll runs on its own fixed schedule in its own
task, so a slow one delays no other): Bybit `instruments-info?category=C&symbol=S`,
`open-interest?category=linear&symbol=S&intervalTime=5min&limit=1` and
`tickers?category=linear&symbol=S` (both linear only) and `recent-trade?category=C&symbol=S&limit=L`
every 30 s, `orderbook?category=C&symbol=S&limit=50` every 60 s, per instrument, into
`<category>.rest.<name>`. **The collector's open interest comes from `tickers`**, not from
`open-interest`: `capture/venues/bybit/open_interest.py` polls `GET /v5/market/tickers?category=linear`
(every linear symbol in one response) and reads each row's `openInterest`; the recorder polls the
same endpoint per symbol (the same row; the category-wide list would add ~300 MB/day), and the
`open-interest` history endpoint is the venue's second, independent view. `recent-trade`'s `L` is
the venue maximum per category, linear 1000 and spot 60: a sample of the latest trades for
spot-checking ids and prices, never a completeness oracle (more than `L` trades can print between
two polls; the WS `publicTrade` stream is the complete record); Hyperliquid `POST info`
`{"type":"metaAndAssetCtxs"}` every 30 s and `{"type":"l2Book","coin":C}` every 60 s per coin,
into `rest.metaAndAssetCtxs` / `rest.l2Book`. A Bybit 200 with `retCode` other than 0, or with an
empty `result.list`, counts as a failed poll (ledgered); the line is written either way.

**Keepalive and reconnect.** Bybit `{"op":"ping"}` every 20 s, Hyperliquid `{"method":"ping"}`
every 30 s. 30 s without a *data* frame on every data channel of an endpoint (a ping reply does
not count) forces a reconnect (`stale_feed`). Reconnects back off 1 s doubling to 60 s (it doubles
only after a pause actually waited), reset once a connection has stayed up 60 s.

**Flush and crash semantics.** Every open stream is flushed once a second, so a process crash
loses at most about a second of lines. The reader recovers every flushed line of an unfinished
last frame and reports the file as truncated (`iter_records` raises `TruncatedTail` after the
complete lines unless `allow_truncated=True`, which a reader of the hour still being written
passes). On reopen after a crash the writer first rewrites the file with only its complete lines
(temp file + `os.replace`, ledgered `verification.recorder.truncated_tail`), then appends a new
frame. At start the writer also repairs each channel's two newest files (the only ones a crash
can have left open: the hour then current and a late stream's), so an hour that ended while the
recorder was down is not left truncated. A tail of zero bytes after the last whole frame (a power
loss that saved the file's size but not its data) is repaired the same way. A file whose bytes
cannot be decoded is renamed aside as `<file>.corrupt-<ns>` (ledgered
`verification.recorder.corrupt_file`) and a fresh file started, so one damaged file never blocks
its channel; a file that merely cannot be opened now (`EACCES`, `EMFILE`, `EIO`) is left as it is
and the line ledgered as lost (`write`). After a failed open or write, that (channel, hour) is
retried at the next once-a-second flush, not per line, and the lines lost meanwhile are counted
in one `write` entry. A line stamped in an hour already rotated away (a late REST response) goes to one
late stream per (channel, hour), closed at the next once-a-second flush. A clean stop (SIGTERM)
closes every connection (`shutdown` lines) and finishes every frame; compose gives the recorders a
45 s `stop_grace_period` (Docker's default 10 s is shorter than a stop can take).
Known limit: lines are flushed, not `fsync`ed, so a host power loss can lose more than a second.

**Rotation and retention.** At start and at each UTC hour the old hour's streams are closed, the
venue's bytes per UTC day are logged, and files of days before the last `VERIFY_RETAIN_DAYS`
(default 7, today included, 1 to 36500) are deleted -- repair leftovers (`*.repair.tmp`) and
set-aside `*.corrupt-*` files included, which the byte counts include too. Pruning at start means a
recorder that keeps restarting still prunes. Measured footprint: `docs/VERIFICATION_REPORT.md`.

**Failures** go to the durable ledger (`data/errors/reference_recorder_<venue>.jsonl`) at the
`verification.recorder.*` sites of `verification/application/sites.py`: `connect`,
`connection`, `stale_feed`, `unparsed`, `unknown_frame`, `venue_error`, `rest`, `plan`,
`truncated_tail`, `corrupt_file`, `write`, `accounting`, `prune`, `crash`.

**Consumers.** The recordings are read by Epic 31's comparators. The first is
`python3 -m verification.conservation` (Story 31.2, §1.16). It reads only four things: these
verbatim raw records, the catalog's raw Parquet (through pyarrow), capture's coverage record
(§1.16) and the archive-gap markers. It never reads through the code it checks. The second,
`python3 -m verification.trades` (Story 31.4, §1.17), reads the same four.

### 1.16 Capture coverage record (`<catalog>/../coverage/<venue>.jsonl`, Story 31.2)

Not catalog data: capture's durable account of every second it wrote no snapshot row for, and
every trade it dropped, backfilled or could not recover. It is written so that
`verification.conservation` can prove that every missing second and trade is *explained*
(DATA-07). Format and rules: `capture/domain/coverage.py` (pure encoders, `SecondCoverage`),
`capture/infrastructure/coverage_file.py` (the file) and `CaptureService._note_verdicts` /
`_write_coverage` (`capture/application/capture_service.py`).

**Where.** `coverage_path(catalog_path, venue)` = the catalog root's *parent* / `coverage` /
`<venue lower>.jsonl`, next to the catalog rather than inside it, so no catalog reader or
archive step ever sees it. One file per venue, appended only by that venue's one collector (the
capture lock guarantees one): `bybit.jsonl`, `hyperliquid.jsonl`, `dydx.jsonl`. In the
containers `CATALOG_PATH=/app/catalog`, so the file lives in `/app/coverage/`. That path is
bind-mounted from `./data/coverage` (host `platform/data/coverage/`) on `collector`,
`bybit_collector` and `hyperliquid_collector`. `make up`, `redeploy-all` and `up-dydx` create the
directory as the invoking user, because the collectors run as uid 1000. The verify stack wipes
and recreates it with the rest (`VERIFY_DATA_DIRS`).

**Line kinds** (one JSON object per line, compact separators, exactly these keys):

```
{"kind":"seconds","instrument_id":"BTCUSDT-LINEAR.BYBIT","reason":"stale","first_s":1759150000,"last_s":1759150004,"count":5}
{"kind":"trades_dropped","instrument_id":"…","reason":"stale","first_ns":…,"last_ns":…,"count":3}
{"kind":"trades_backfilled","instrument_id":"…","count":2,"trade_ids":["…","…"]}
{"kind":"trades_unrecoverable","instrument_id":"…","reason":"depth","from_ns":…,"to_ns":…}
```

- `seconds`: seconds `first_s..last_s` (inclusive, epoch seconds; `count = last_s - first_s +
  1`) of one instrument, all without a row, for one `reason`. A second is `ts_event // 1 s`,
  the same number the archive's snapshot rows carry. In venue mode that is the exchange second
  being closed; in arrival mode (dYdX) it is the tick's `now // 1 s`.
- `trades_dropped`: `count` trades of one instrument dropped in one flush cycle, with their
  `ts_event` inside `first_ns..last_ns`. The only reason is `stale` (§1.1).
- `trades_backfilled`: the ids a REST backfill archived (§1.1), one line per instrument per
  backfill. The line carries no time. A Bybit linear backfill of 1000 UUID ids is ~39 KB.
- `trades_unrecoverable`: a `ts_event` window `from_ns..to_ns` that a backfill could not check.
  `depth`: the venue's history ended short of the baseline. `fetch_failed`: the fetch raised, or
  there was no instrument definition or no `VenueTradeHistory`, or shutdown interrupted or
  abandoned the backfill before it fetched the instrument (then the window ends at shutdown).
  The window runs from the instrument's last archived trade to the fetch time, or to where the
  venue's history began.

**Seconds reasons.** At every sample tick each plan id gets exactly one verdict: a row, a
rejection reason, or `not_collected`.

| Reason | Produced by |
|---|---|
| `no_book`, `empty_top`, `crossed`, `stale`, `unencodable` | the write gate's rejection (`capture.domain.verdicts` `NoBook`/`EmptyTop`/`Crossed`/`Stale`/`Unencodable`, via `SecondSampler.sample`) |
| `not_collected` | a plan id that is not subscribed on the wire (planned, not applied) |
| `catch_up_cap` | venue mode: the overdue seconds older than the 30 s catch-up cap after a stall (`_skipped_seconds`), for every plan id; ledgered once per stall at `collector.skipped_seconds` |
| `missed_tick` | arrival mode: floor seconds between two sample ticks that no tick sampled, for every plan id; ledgered at `collector.skipped_seconds` |
| `write_failed` | a row the gate accepted (noted as a row at its tick) whose catalog write then failed; re-noted by the same flush that ledgers the batch LOST at `collector.flush_write`, so the append that follows carries it |
| `restart` | at an id's first note in this process (or since it re-entered the plan), the seconds from its last archived row + 1 -- or from the last second this process noted for it + 1, when later (a re-added id's rows may still be in the flush buffer) -- to the second before (`ArchiveWriter.last_snapshot_second`, which reads only the newest snapshot files); ledgered at `collector.restart_gap` naming each id and span. A failed read is ledgered at the same site and no run is noted |

A written row is noted too, but never becomes a line: it only ends the open run. Consecutive
seconds of one instrument with the same reason extend one run. Any other verdict closes the run.
At each flush every run, open ones included, is written and forgotten, so a sustained condition
costs one line per instrument per flush interval. `collector.second_rejected` summarises the
five gate reasons once per flush, per instrument and reason (`not_collected` is not counted
there).

**When written.** Once per flush (`flush_interval_seconds`, 60 s on every committed config).
The report cycle runs first, so this cycle's stale-trade lines go out with this flush. The lines
are appended after that flush's catalog writes, so a second's run and its missing row land in
the same flush. They are also written on the final flush of `run()` (a clean stop, and also a
loop crash, because the final flush sits in `run()`'s `finally`). Order within one append:
lines left over from a failed write first, then seconds runs, then trade lines.

**Durability and failure.** The append is `open("a")`, write, `flush`, `os.fsync`, done off the
event loop: the pattern of `gap_markers.record_gap`. A failed append (any exception) is truncated
back to the file's size before it, ledgered at `collector.coverage_write`, and its lines are kept
for the next flush. At most the newest 10 000 kept lines are retained (`_COVERAGE_PENDING_MAX`).
Beyond that the oldest are dropped, and the count is ledgered at the same site: those seconds and
trades will then show as unexplained. The final flush has no next one, so its failed append is
ledgered as lines LOST. The append is shielded from the flush task's cancellation: an append a
cancelled flush left running is settled by the next (at shutdown, the final) flush before it
takes new lines. A torn tail (an unfinished last line a killed process, or a failed truncate,
left) is cut back to the last newline before the process's first append and again before the
first append after a failed one; the cut bytes are ledgered at the same site. Nothing is ever
skipped silently.

Known limits:
- *Growth, no rotation or pruning.* Measured line sizes: 126–135 B per `seconds` line, 153 B
  per `trades_dropped` line, 146 B per `trades_unrecoverable` line and ~39 B per id on a
  `trades_backfilled` line (Bybit UUIDs). A healthy day writes almost nothing, since rows are not
  lines. One instrument under a sustained condition (never subscribed, a stale book all day)
  writes one line per flush: 1440 lines, ~190 KB/day. Worst case, a verdict that flips every
  second, is 86 400 lines, ~11 MB/day per instrument. Stale drops add at most 1440 lines
  (~220 KB) per instrument-day. Each Bybit linear backfill can add ~39 KB. The file is never
  rotated or pruned, and the conservation tool re-reads the whole file on every run (its
  `trades_backfilled` ids, which carry no time, are all kept in memory). Upgrade path: one file
  per UTC day (`coverage/<venue>/<YYYY-MM-DD>.jsonl`), pruned with the verification retention,
  and a time on `trades_backfilled`.
- *A killed process.* SIGKILL, OOM or power loss loses the in-memory lines of the current flush
  interval (≤ 60 s). The next process's `restart` run covers those seconds, so they are
  explained as `restart`, not with the reason the dead process saw. Stale drops noted in that
  interval are lost, and their trades show as unexplained. A kill that lands after a flush's
  catalog writes but before its coverage append is worse: the rows are on disk, so the next
  `restart` run starts after them, and the seconds of that interval that had a reason show as
  unexplained (the day fails; nothing passes wrongly). Upgrade path: a durable "noted through"
  watermark per id that the next process starts its `restart` run from.
- *First data and plan changes.* `restart` needs an archived row. An instrument never archived
  before (the very first day, or a plan id added at runtime) has no run for the seconds before
  its first verdict. An id is noted only while it is in the plan. So a day with a plan change,
  or the collector's first day, cannot reconcile clean.

**Reconciliation: `python3 -m verification.conservation`** (`verification/conservation.py`, the
root; `verification/application/conservation.py`; `verification/domain/conservation.py`;
`verification/infrastructure/catalog_reader.py`). For one closed UTC day of one venue
(`--venue BYBIT|HYPERLIQUID`; there is no dYdX reference), and for every instrument in the
venue's *current* plan (the collector's `config.toml`, the same file the recorder reads), it
reconciles the three read-only sources. It reads only the verbatim raw records (§1.15), the
catalog's raw Parquet through pyarrow (`trade_tick`: `trade_id`, `ts_event`;
`custom_dydx_second_snapshot`: `ts_event`), `<catalog>/_archive_gaps/<iid>.jsonl` and this
coverage file. It imports nothing of `capture`, `kernel.second_snapshot`, `kernel.catalog_files`
or `nautilus_trader`, and parses the two durable files from the formats documented here
(registered in `tests/test_boundaries.py`'s `VERIFICATION_ROOTS` and runtime probe). A malformed
coverage or marker line is refused (file:line), never skipped.

- **Trades** (partitioned by the trade's own venue-time hour on both sides; hour H reads the raw
  files of H-1..H+1, because the raw store files lines by receive hour). The columns are:
  - `seen`: reference ids with venue time in the day, from WS `publicTrade`/`trades` frames and
    Bybit `recent-trade` polls (status 200, no `refusal`).
  - `rest_only`: ids seen only by REST.
  - `archived`: seen and in the archive.
  - `backfilled`: archived and on a `trades_backfilled` line.
  - `ledgered_unrecoverable`: not archived, but covered by a `trades_dropped` range, a
    `trades_unrecoverable` window, or an archive-gap marker (a `ts_init` span, widened 60 s below).
    A `trades_dropped` window, and a marker with `count` > 0, explains at most its recorded
    `count` missing trades; the excess stays unexplained. Missing trades are taken in time
    order, each spending the covering window with room that ends first. Known limit: `trades_unrecoverable`
    windows and count-0 (`quarantined`) markers carry no count and stay uncapped. Known limit:
    a `write_failed` marker of a REST-backfilled batch spans the batch's `ts_init` (the flush
    time the backfill restamped), while its trades' venue time can be minutes to hours earlier,
    so those lost trades show as unexplained (the day fails loudly on a loss capture ledgered).
    Upgrade path: markers that also carry the batch's `ts_event` span.
  - `unexplained`: the rest of the not-archived ids.
  - `archived_not_seen`, `archived_twice` (ids stored more than once in the day).
- **Seconds**: `expected` (86 400), `rows` (seconds with at least one row), explained per reason,
  `unexplained`, `duplicate_rows`, `row_and_reason` (a row *and* a run), `multiple_reasons`
  (two runs cover it; legitimate across a restart, and the earliest-starting run explains it).
- **Verdict**: an instrument passes only with `unexplained` (trades and seconds),
  `archived_twice`, `duplicate_rows` and `row_and_reason` all 0, and the day passes only with the
  coverage file present and no raw reference hour of the day missing (a missing hour would make
  the trade side vacuous). `archived_not_seen` and `multiple_reasons` are reported but do not
  fail it; the neighbour hours outside the day may be missing or truncated
  (`truncated_neighbour_files`). A day is refused ("day not closed") until 2 h after it ends (`DAY_SETTLE_NS`): its
  last seconds' rows and runs reach disk at the next flush, a backfill of a late reconnect
  settles after that, and the neighbour hour file closes an hour after midnight.
- **Output**: a text table by default. `--json` prints `passed`, `venue`, `day`,
  `coverage_file`, `coverage_present`, `missing_raw_files` and `instruments`. Each instrument has
  `instrument_id`, `passed`, `trades` {`seen`, `rest_only`, `archived`, `backfilled`,
  `ledgered_unrecoverable`, `unexplained`, `archived_not_seen`, `archived_twice`,
  `examples_unexplained`} and `seconds` {`expected`, `rows`, `explained_by_reason`,
  `unexplained`, `duplicate_rows`, `row_and_reason`, `multiple_reasons`,
  `examples_unexplained`}. Up to 5 examples are listed per instrument.
- **Exit codes**: 0 = every instrument passes, 1 = any fails, 2 = usage error (argparse). A
  refusal exits with status 1 and its message, ledgered at `verification.conservation.refused`.
  Refusals are: no raw root or catalog, an unreadable plan, a malformed coverage/marker/trade
  line, or a truncated raw file of an hour that has ended.
- **Repro** (host, from `platform/`, on the verify stack's data):
  `VERIFY_DATA_DIR=data/verification CATALOG_PATH=data/catalog python3 -m
  verification.conservation --venue BYBIT --day YYYY-MM-DD [--json]` (or `--raw-dir`/`--catalog`;
  `BYBIT_COLLECTOR_CONFIG`/`HYPERLIQUID_COLLECTOR_CONFIG` override the plan file).
- Known limits (each a `Known limit:` in `verification/application/conservation.py`). Memory is
  one instrument-day of archived ids in Arrow plus one hour of Python id sets, and every raw file
  is decoded up to three times per instrument that shares it. A trade first received two or more
  hours after its venue time (only a quiet-market `recent-trade` poll can reach that far) is not
  counted as seen. The plan is today's, not the one in force on the day.

### 1.17 Trades proven id by id and second by second (`verification.trades`, Story 31.4)

Not stored data: the check that every archived trade and every snapshot second's eight trade
columns equal what the venue published. `python3 -m verification.trades --venue BYBIT|HYPERLIQUID
--day D --stage live|rebuilt [--json] [--raw-dir DIR] [--catalog DIR]`
(`verification/trades.py`, the root; `verification/application/trades.py`;
`verification/domain/trade_check.py`; the readers in `verification/infrastructure/catalog_reader.py`).
It reads what conservation reads (§1.16) plus every stored `trade_tick` column and the snapshot's
trade columns, and imports nothing of `capture`, `candles`, `kernel.fold`, `kernel.second_snapshot`,
`nautilus_pyo3` or `nautilus_trader` (`tests/test_boundaries.py`, a registered root). It reuses
conservation's raw reader, coverage parser, explanations, channel table and closed-day rule, so the
two tools never disagree about what was seen.

**Inputs, decoded exactly.**
- Reference: the recorder's WS `publicTrade`/`trades` frames and Bybit `recent-trade` polls (§1.15),
  hour H read from the raw files of H-1..H+1. Price and size are the wire's decimal strings as
  `Decimal` (anything but ASCII digits with an optional fraction is refused as a malformed line; so
  is a missing side). Side: Bybit `Buy`/`Sell`, Hyperliquid `B`/`A` -> BUYER (1) / SELLER (2); any
  other token -> NO_AGGRESSOR (0), counted with the token (see §1.1). Every copy of an id (a replay,
  the poll) is merged: the first WS frame's copy is kept, and a copy that disagrees on price, size,
  side or time is a `reference_conflict`.
- Archive: `trade_tick` Parquet read raw with pyarrow. `price`/`size` are `fixed_size_binary[16]`,
  little-endian signed 128-bit integers at 10^16 (`FIXED_PRECISION` 16, restated), decoded as
  `Decimal(raw).scaleb(-16)` in a context whose `Inexact` trap makes any rounding an error; each
  file's `price_precision`/`size_precision` come from its schema metadata (a file without them is
  refused). `aggressor_side` is the enum value (0/1/2).
- Snapshot rows: the eight stored integers (`open/high/low/close_price`, `buy/sell_volume`,
  `buy/sell_count`) at the row's own `price_precision`/`size_precision` (§1.7).

**Ids** (per instrument; both sides partitioned by their own venue-time hour):
`seen`, `matched`; `missing_explained` / `missing_unexplained` (a reference id not archived,
explained by conservation's rules: a `trades_dropped` range, a `trades_unrecoverable` window or an
archive-gap marker, §1.16); `extra_explained` / `extra_unexplained` (an archived id the reference
did not see: explained only when it is on a `trades_backfilled` line **and** its `ts_event` lies in a
recorder connection gap of its trade channel); `duplicated` (ids stored more than once);
`mismatch_price` / `mismatch_size` (numeric `Decimal` equality), `mismatch_side`,
`mismatch_ts_event` (must equal the venue's ms x 10^6 exactly; Hyperliquid's ms truth is D-62's);
`reference_conflict`; `off_precision` (an archived raw that is not a whole unit at its file's
precision); `implausible_latency`. `backfilled` and `wire_no_aggressor` are evidence only. There is
no tolerance anywhere.

**Recorder connection gap.** Built from the trade channel's own lines (which carry every
`connection` line of their endpoint, §1.15), read from the start of hour H-1 of the window's first
hour to the end of the hour after its last: `close`/`error` to the next `open`; an `open` with no
gap open before it (a crash's `startup` open, or a reconnect whose `close` lies before the read)
starts the gap at the channel's previous line, or at the read's start when it is the first line
read (the gap began before the read); a gap still open at the end runs to the end of the read. Each gap is widened by `RECORDER_GAP_MARGIN_NS` = 5 s
on both sides (the recorder's subscribe after its `open`, and venue-clock skew).

**Latency.** `ts_init - recv_ns` of every matched id seen on WS, not backfilled and whose `ts_event`
lies outside the instrument's recorder gaps, `recv_ns` from the first WS frame carrying it, as a histogram of whole milliseconds (floor): min, nearest-rank p50
and p99, max. `|delta| > 60 s` is `implausible_latency` and fails: both processes stamp from the same
host clock, and 60 s is the catalog read-span margin (`kernel.clocks.READ_SPAN_MARGIN_NS`, restated).
A trade inside a recorder gap is no latency sample: the recorder was not connected when it
happened, so its first WS copy (if any) is a replay. Hyperliquid's `trades` subscribe answers with
the recent trades (the soak's startup frame held trades 55 s older than its `open`), and after a
recorder restart that copy's `recv_ns` is the reconnect, not the arrival (audit D-95).

**The reference fold** (its own, written from this rule, not `kernel.fold`): second = venue ms //
1000; order = `(ts_ns, recv_ns of the id's first WS frame, index in that frame)`, and a REST-only
trade takes its poll's `recv_ns` and its chronological position in the list (Bybit lists newest
first); open = first, close = last, high/low = max/min; buy = BUYER, sell = every other side (the
NO_AGGRESSOR convention, §1.1); units = `Decimal.scaleb(precision)` at the row's precisions, and a
value that is not a whole unit is `off_grid`. An empty second is OHLC None and zeros. `arc` is the
same fold over the second's archived trades (each id once; a seen id in the reference's order), so no
production code is involved on either side. Known limit (REST-only tie order, a `Known limit:` at
`fold_second`): a REST-only trade folds after every WS trade of its millisecond, as production's
rebuild places a backfilled trade (arrival order), but the venue's own order (Bybit's `seq`) may
differ; such a tie can make open/close differ, a loud false `rebuild_mismatch`, never a false pass.
Upgrade path: order Bybit ties by `seq` on both sides.

**Seconds.** A second is judged when it has a row, reference trades or archived trades (a second
holding only an archive-only id is judged too). A row that differs from ref is judged against arc
whenever arc is trusted: arc == ref, or every id discrepancy of the second is explained (an
explained missing id or an explained extra id). The archive is the rebuild's input, so an explained
second passes only when its row is arc's fold.

| Class | Condition | Fails |
|---|---|---|
| `exact` | row == ref | no |
| `explained_loss` | row != ref, arc != ref, every id discrepancy explained, row == arc | no |
| `live_provisional` | `--stage live`, row != ref, arc trusted, and not `explained_loss` (row != arc) | no |
| `rebuild_mismatch` | `--stage rebuilt`, row != ref, arc trusted, not `explained_loss` (row != arc), second not rebuild-exempt | yes |
| `live_kept` | as `rebuild_mismatch`, but the row's `ts_event` lies in an archive-gap marker's own `ts_init` span, or before the floor second of the instrument's first archived trade: the rebuild keeps live values there (§6) | no |
| `archive_differs` | row != ref, arc != ref, and an id discrepancy of the second is unexplained (or there is none) | yes |
| `missing_row` | ref or the archive has trades, no row, no coverage `seconds` run | yes |
| `missing_row_explained` | ref or the archive has trades, no row, a coverage `seconds` run covers it | no |
| `duplicate_row` | two rows in one second | yes |
| `off_grid` | the reference or archive fold cannot be held at the row's precisions | yes |

**Stage.** `archive.rebuild_seconds` rewrites rows in place and leaves no marker (`state.json` is a
scheduler cursor, not a verdict), so the stage is an explicit flag, never inferred (audit D-94). The
live window runs from the day's 2 h settle (`DAY_SETTLE_NS`) to the scheduler's `nightly_at`
(03:07 UTC by default); a `live` report labels its verdict provisional and never claims the rebuilt
guarantee; a wrong `rebuilt` can only false-fail. Story 31.11's nightly `verify_day` runs after
`compare_klines` with `--stage rebuilt`.

**Verdict and exit.** An instrument passes with every failing id count (`missing_unexplained`,
`extra_unexplained`, `duplicated`, the four `mismatch_*`, `reference_conflict`, `off_precision`,
`implausible_latency`) and every failing second class at 0; the day passes when every instrument
does, the coverage record exists and no raw reference hour of the day is missing. Exit 0 pass, 1
fail, 2 usage. Refusals exit 1 with their message, ledgered at `verification.trades.refused`: an
unknown `--stage`, a day not closed, no raw root or catalog, an unreadable plan, a malformed line,
a truncated raw file of an hour of the day, a trade file without precision metadata or `ts_event`
column, a snapshot row without its precisions, a value the exact decode cannot hold (an
`ArithmeticError` such as `decimal.Inexact` from an over-long wire decimal). Any other exception is
a crash: ledgered at the same site (detail `crashed: ...`), then re-raised. Failing id and second
examples are deterministic, never set order: hour by hour, and within an hour by kind
(`off_precision`, then field mismatches and conflicts, then missing, then extra ids), each kind in
venue-time order. `--json` gives
`passed`, `provisional`, `venue`, `day`, `stage`, `hours`, `coverage_file`, `coverage_present`,
`missing_raw_files`, `truncated_neighbour_files` and per instrument `ids` (every count, the
`no_aggressor_tokens`, up to 5 failing `examples`), `seconds` (`classes`, all ten, and up to 5
failing `examples`) and `latency_ms` (`count`, `min_ms`, `p50_ms`, `p99_ms`, `max_ms`).

**Repro** (host, from `platform/`, on the verify stack's data): `VERIFY_DATA_DIR=data/verification
CATALOG_PATH=data/catalog python3 -m verification.trades --venue BYBIT --day YYYY-MM-DD --stage
rebuilt [--json]`. Results: `docs/VERIFICATION_REPORT.md`.

Known limits (each a `Known limit:` in `verification/application/trades.py` or the reader). Memory
is one instrument-window of trades and rows in Arrow plus one hour of Python objects. The plan is
today's. A trade whose archived `ts_event` falls in another UTC hour than its venue time is one
missing and one extra id (both failing), not a `mismatch_ts_event`. A trade capture archived live
while only the recorder was disconnected, or before a recorder that started mid-day, is
`extra_unexplained` (a loud false fail; upgrade path: a second recorder connection per endpoint).
`first_ts_event` reads one footer per trade file of the instrument.

### 1.18 The stored book proven against an independently rebuilt book (`verification.book`, Story 31.5)

Not stored data: the check that every second's stored top-20 book (§1.7) is the venue's book at
that second. `python3 -m verification.book --venue BYBIT|HYPERLIQUID --day D [--json] [--raw-dir
DIR] [--catalog DIR]` (`verification/book.py`, the root; `verification/application/book.py`;
`verification/domain/reference_book.py`, pure; the row reader
`verification/infrastructure/snapshot_book.py`). It imports nothing of `capture`,
`kernel.second_snapshot`, `nautilus_pyo3` or `nautilus_trader` (`tests/test_boundaries.py`, a
registered root; `snapshot_book` is the one `verification` module in `_GAP_LAYOUT_READERS`: the
oracle must decode independently). It reuses conservation's raw reader, closed-day rule, plan,
wire index, coverage record (`collect_coverage`) and missing-hour rule, and the trades tool's
Parquet pruning (`catalog_reader.read_window`).

**Inputs, decoded exactly.**
- Reference book: the recorder's WS book frames (§1.15), Bybit `<category>.orderbook.50`, Hyperliquid
  `l2Book`, in arrival (file) order. Levels are the wire's decimal strings as `Decimal` (anything but
  ASCII digits with an optional fraction is a malformed line); size 0 deletes. Bybit is keyed by
  topic and `data.s` (they must agree); a frame without `ts`, `data.u`, `data.seq` or a `type` of
  `snapshot`/`delta`, or an `l2Book` without `time`/`levels`, is refused (the measured shape the
  replay relies on). Venue time: Bybit's frame `ts`, Hyperliquid's `data.time` (ms x 10^6), exactly
  what the adapters stamp as `ts_event` (`crates/adapters/bybit/src/websocket/parse.rs:238`).
- Stored rows: `custom_dydx_second_snapshot` read raw with pyarrow: `price_precision`/`size_precision`
  per row, `bid_prices`/`ask_prices` decoded from `[best, gap, ...]` (bids subtract each gap, asks add
  it; a gap that is not strictly positive is refused), sizes in units; every value a Python `int`. A
  file without `price_precision` is the float layout and is refused by name, with a pointer to
  `python3 -m archive.tools.migrate_snapshot_ints`. The integer layout is exact (Epic 30.2), so the
  float-noise class of the earlier float layout is retired, not measured.
- REST polls: the recorder's `<category>.rest.orderbook` (`limit=50`) and `rest.l2Book` polls,
  attributed by the requested symbol/coin (a response naming another is refused).

**The replay.** Bybit: a `snapshot` re-baselines (clear, then add); a `delta` applies only if
`u == last_u + 1`, else `u_breaks` += 1 and the book is unavailable until the next snapshot (deltas
meanwhile count `awaiting_snapshot`); a delta with empty `b` and `a` counts `zero_level_messages`
and still advances `u`. Hyperliquid: every `l2Book` replaces the book; a `time` below the previous
one counts `time_regress` (counted for Bybit's `ts` too). A connection line (`open`, `close`,
`error`, the same lines `recorder_gaps` reads) makes the book unavailable until the next snapshot
(Bybit) or until the message **after** the next one (Hyperliquid: the first `l2Book` after a
subscribe is that connection's own reply, off the broadcast cadence -- the soak's recorder
reconnect at 15:58:34Z got one at 513.642 s between broadcast pushes at 511.408 and 516.930 s that
capture, still connected, never received -- so it re-baselines the reference but is not judged;
`subscribe_replies`). A raw hour with no file inside the replay (the look-back or the day) breaks
the book exactly as a connection line does (Bybit waits for a snapshot, Hyperliquid as after a
reconnect), so no second of an unrecorded hour is ever closed against the book from before it.
**Replay start:** Bybit looks back over the raw hours before the day (while
their files exist) for the latest hour whose last replay event is the topic's `snapshot` or a
connection line, and replays forward from that hour; Hyperliquid looks back one hour. A connection
line is replayed, never skipped, so a Hyperliquid reconnect just before midnight still marks the
day's first `l2Book` as the recorder's own subscribe reply. Without either, the seconds before the
day's first baseline are `reference_unavailable`. The `replay` counters count only messages whose venue
time lies in the day (never the look-back or the hour after it).

**Close rule (DATA-01).** `ref(S)` = the top 20 per side after every message with venue time
`< (S+1)` s, taken when the first message beyond S arrives; `ref-(S)` = the same without that last
included message (the book keeps the message's undo -- the previous size of each price it touched,
or the replaced sides of a baseline -- never a copy); `ref+(S)` = `ref(S)` plus the first message
excluded. Units = `Decimal.scaleb(precision)` at the row's own precisions, `Inexact` trapped. When
the stream ends (no later message proves the second closed) the remaining seconds are unavailable.

**REST agreement (reported first, per instrument).** A Bybit poll is placed by `result.seq` among
the reference messages (`data.seq`); REST `u` is a different counter from WS `u` (the soak: ~29M
against ~192M), never used. A Hyperliquid poll is placed by `time`. Each good poll compares the top
20 per side exactly:

| Class | Condition | Fails |
|---|---|---|
| `agree_key` | a message has the poll's key and the books are equal | no |
| `disagree_key` | a message has the poll's key and the books differ | yes |
| `agree_bracket` | no key match; equal to the state just before or just after the poll's place | no |
| `between_pushes` | no key match; equal to neither (the venue's engine moved inside one push) | no |
| `unaligned` | the reference is unavailable at the poll's place (before the first baseline, after a break, after the last message) | no |
| `failed` | status not 200, a recorder `refusal`, or Bybit `retCode` not 0 | no |
| `persistent_disagreement` | a level contradicted (REST size equal to neither bracket state; or a reference level inside REST's price span that REST lacks) by two consecutive `between_pushes` polls, with the same REST value, and no reference message touched that price in between | yes |

Why `between_pushes` exists: over 2026-09-29 13:00-16:00Z (180 polls per Bybit instrument; the
smoke's window 12:59:19-16:00Z holds 181, the extra one `unaligned` before the first snapshot) 58 polls
matched a WS `seq` exactly and none disagreed; 460 equalled a bracketing state and 202 caught an
intermediate engine state (levels change several times inside one 20 ms push). A lost reference
message instead persists across polls, which `persistent_disagreement` catches without any
tolerance. On Hyperliquid every push replaces the book, so every price counts as touched between
two polls and `persistent_disagreement` practically cannot fire: its validation rests on exact
`time` key matches (14 of 181 polls in the smoke, all equal). The reference is `invalid` with any
`disagree_key` or `persistent_disagreement`, and `unvalidated` when the day holds no reference book
message at all ("no reference data") or no poll agreed (`agree_key` + `agree_bracket` = 0) while
polls were judged or rows exist; either fails the instrument whatever the rows say.

**Second classes** (one per UTC second of the day, per plan instrument):

| Class | Condition | Fails |
|---|---|---|
| `exact` | row == `ref(S)` | no |
| `boundary_late` | row == `ref-(S)`, and the omitted message's recorder `recv_ns` >= the row's `ts_init` - `LATE_ARRIVAL_MARGIN_NS` (500 ms: it bounds only the difference between the two connections' receipts of one message, measured |capture `ts_init` - recorder `recv_ns`| <= 394 ms over ~800k trades, Story 31.4; a message capture got more than Bybit's 0.5 s hold-back before the close it must have applied) | no |
| `boundary_unexplained` | row == `ref-(S)`, the omitted message arrived earlier | yes |
| `boundary_early` | row == `ref+(S)`: it holds a message from after its second (a DATA-01 violation) | yes |
| `content_differs` | any other difference; per side the first differing level index and its kind: `missing` (the row lacks the reference's level), `extra` (the row holds one the reference lacks), `size`, `price` | yes |
| `off_grid` | `ref(S)` is not a whole number of units at the row's precisions | yes |
| `duplicate_row` | more than one row in the second | yes |
| `missing_row` | reference available, no row, no coverage `seconds` run covers it | yes |
| `missing_row_explained` | as `missing_row`, but a coverage `seconds` run covers it | no |
| `reference_unavailable` | a row exists, the reference is unavailable | no |
| not judged | no row and no reference | -- |

**Verdict and exit.** An instrument passes with every failing REST and second count at 0, a
`validated` reference, and -- when rows exist -- at least one second verified (`exact`, a boundary
class, `content_differs` or `off_grid`; otherwise "nothing verified"). The day passes when every
instrument does, the coverage record exists and no raw book hour (WS or REST) of the day is missing.
Exit 0 pass, 1 fail, 2 usage. Refusals exit 1 with their message, ledgered at
`verification.book.refused`: a day not closed, no raw root or catalog, an unreadable plan, a
malformed line, a truncated raw file of an hour of the day, a float-layout snapshot file, a
non-positive stored gap, a row without its precisions or with unpaired sizes, a value the exact
decode cannot hold. Any other exception is a crash, ledgered at the same site and re-raised.
`--json` gives `passed`, `venue`, `day`, `layout`, `coverage_file`, `coverage_present`,
`missing_raw_files`, `truncated_neighbour_files` and per instrument `reference`, `rest` (all seven
classes), `rest_examples`, `replay` (`messages`, `baselines`, `u_breaks`, `zero_level_messages`,
`time_regress`, `awaiting_snapshot`, `subscribe_replies`), `rows`, `verified_seconds`, `seconds`
(all ten classes), `levels` (`"<side> <kind>"` counts of `content_differs`) and up to 5 failing
`examples` `[second, class, [details]]`.

**Repro** (host, from `platform/`, on the verify stack's data): `VERIFY_DATA_DIR=data/verification
CATALOG_PATH=data/catalog python3 -m verification.book --venue BYBIT --day YYYY-MM-DD [--json]`.
Measured cost: ~95 s of one core for Bybit's four instruments over 3 h of soak (~1.6M frames, each
instrument decoding only its own topic's frames), so ~13 min per full Bybit day; Hyperliquid ~4 s.
Results: `docs/VERIFICATION_REPORT.md`.

Known limits (each a `Known limit:` in the code). The Bybit replay starts at the recorder
connection's latest snapshot, so its cost grows with the connection's age, up to
`VERIFY_RETAIN_DAYS` (upgrade path: an end-of-day reference checkpoint, continuity-checked by `u`).
Memory is one instrument-day of the book columns in Arrow (~60 MB) plus one hour of rows and one
book. The plan is today's. A connection line makes the not-yet-closed seconds before it
`reference_unavailable` (fewer verified seconds, never a false verdict). More generally a `u`
break, a recorder gap or an unrecorded hour shrinks the verified set without failing the day: the
report's `reference_unavailable` and `verified_seconds` are the trace, and Story 31.11's verdict must
read them, not only `passed`. A receipt difference above 500 ms reads as `boundary_unexplained` (a
loud false fail). A capture-side
Hyperliquid resubscribe gives capture a subscribe reply the recorder never sees, and the rows up
to the next broadcast push are `content_differs` (audit D-101, OPEN).

### 1.19 Mark, index, funding, open interest and instrument definitions proven (`verification.derivs`, Story 31.6)

Not stored data: the check that every stored mark/index price, funding rate and open-interest row
(§1.4-1.6, §1.8) is the venue's, that every venue update the collector should store was stored,
and that every stored instrument definition (§1.10) is what the venue declares.
`python3 -m verification.derivs --venue BYBIT|HYPERLIQUID --day D [--json] [--raw-dir DIR]
[--catalog DIR]` (`verification/derivs.py`, the root; `verification/application/derivs.py`;
`verification/domain/derivs_check.py` and `verification/domain/instrument_check.py`, pure; the
reader `verification/infrastructure/derivs_reader.py`). It imports nothing of `capture`,
`kernel.open_interest`, `kernel.catalog_files`, `nautilus_pyo3` or `nautilus_trader`
(`tests/test_boundaries.py`, a registered root). It reuses conservation's raw reader, closed-day
rule, plan, coverage record and missing-hour rule, the trades tool's `recorder_gaps` and Parquet
pruning (`catalog_reader.read_window`) and its exact fixed-point decode (`trade_check.fixed_raw` /
`decode_fixed` / `whole_at`).

**Inputs, decoded exactly.**
- Reference (the recorder's verbatim frames, §1.15; the day's hours plus the hour before, for the
  state at 00:00, and the hour after). Bybit: `linear.tickers` frames of the instrument's topic; a
  field present in a frame (`markPrice`, `indexPrice`, `fundingRate`, `nextFundingTime`,
  `fundingIntervalHour`, `openInterest`) is an update keyed by the frame's `ts` ms x 10^6 -- what
  the adapter stamps as `ts_event`. A field's **state** at t is its last update keyed at or before
  t, unknown when that update's frame was *received* before the channel's last reset at or before
  t (a connection line, or an hour without a file); receipt, not key, is compared with the reset
  because both are local clocks (the soak's startup snapshot carried `ts` 12:59:20.283 and
  arrived at 12:59:21.26, after its `open` line at 12:59:21.07). Hyperliquid: `activeAssetCtx`
  frames of the coin, keyed by `recv_ns` (the wire has no time); an update of a field is a frame
  whose wire string differs from the previous frame's. Wire values are ASCII decimal text (a
  leading `-` for funding), never exponents. A ticker frame without `ts`/`data.symbol` or naming
  another symbol than its topic, or a context without `data.coin`/`data.ctx`, is refused.
- REST: Bybit `linear.rest.tickers` polls (`result.list[0].symbol` must be the requested one),
  placed by the response's `time`; Hyperliquid `rest.metaAndAssetCtxs`, the context at the
  universe index whose `name` is the coin. Definitions: Bybit `<category>.rest.instruments-info`,
  Hyperliquid's `meta.universe` entry.
- Stored rows (`derivs_reader`, raw pyarrow): mark/index `value` (`binary(16)` at 10^16) with each
  file's `price_precision` label; funding `rate` (JSON-quoted decimal text, exact scientific
  notation accepted, §1.6), `interval`, `next_funding_ns`; `open_interest` (decimal text);
  definition numerics (decimal strings). Every value a `Decimal` or an int, never a float.

**Expected ratio** (printed beside each type's counts):

| Venue and stream | Stored rows |
|---|---|
| Bybit mark, index | one per frame carrying the field |
| Bybit funding | one per frame whose `fundingRate` or `nextFundingTime` string differs from the last seen (the adapter's `funding_cache`); a frame changing only `nextFundingTime` without a rate is `next_time_only`, not written (Known limit, D-104) |
| Hyperliquid, every type | one per change of the wire string |
| Bybit open interest | one per `open_interest_poll_seconds` (the venue config's; 300 when absent, the collector's default) |

**`ts_event` rules** (a break is `ts_rule`): Bybit mark, index, funding: the venue frame `ts`, whole
milliseconds; Bybit open interest: `ts_event == ts_init`, the collector's clock after the poll
response (§1.8); Hyperliquid, every type: `ts_event == ts_init`, the adapter's receive clock.

**Stored-row classes** (per type; failing ones fail the instrument):

| Class | Condition | Fails |
|---|---|---|
| `exact` | Bybit: an unconsumed reference update of the row's own key with an equal value (a multiset per key). Hyperliquid: the earliest unconsumed update of equal value received within `HL_MATCH_BOUND_NS` of the row's `ts_init` | no |
| `agree_state` | Bybit: no same-key update, equal to the state at its key (a stored null component -- a funding field the frame did not carry -- agrees with any state). Hyperliquid: equal to any frame received within the bound. Bybit open interest (always this class when it agrees): equal to the state at some venue time in `[ts_event - OI_POLL_WINDOW_NS, ts_event]`. Covers the collector's own reconnect snapshot. A known component that differs is `unmatched` | no |
| `value_mismatch` | Bybit: a same-key update of another value (example: key, stored, reference) | yes |
| `unmatched` | nothing agrees | yes |
| `reference_unavailable` | nothing recorded to compare with: the row's time lies in a recorder gap (connection gaps and unrecorded hours, each widened by 5 s) and no recorded frame of its key (Bybit), equal frame within the bound (Hyperliquid) or agreeing state in the window (open interest) exists; or the state is unknown; or (Bybit funding) the rate agrees but a component the row stores -- a restart snapshot row's `interval`/`next_funding_ns` -- has no known state since the last reset. A recorded frame is always compared first, gap or not | no |
| `off_grid` | the fixed raw is not whole at its file's `price_precision` | yes |
| `ts_rule` | the type's `ts_event` rule is broken | yes |
| `duplicate` | a second row of the type with the same `(ts_event, value)` | yes |

**Reference-update classes** (every update keyed in the day; Bybit open interest uses poll
coverage instead):

| Class | Condition | Fails |
|---|---|---|
| `stored` | a row consumed it (`exact`) | no |
| `unchanged` | Bybit funding: an equal repeat of both strings | no |
| `next_time_only` | see the expected ratio | no |
| `reference_unavailable` | the update lies in a recorder gap: after a recorder reconnect its first frame is that connection's own snapshot, which capture's connection never received | no |
| `not_stored_explained` | its second lies in a coverage `seconds` run whose reason is in `FEED_LOSS_REASONS` = {`restart`, `stale`, `no_book`, `not_collected`} -- the reasons meaning capture's feed was absent (no process, a dead socket, no subscription yet, not subscribed); the others judge a second of a live feed | no |
| `not_stored` | otherwise | yes |

**Bybit open-interest poll coverage:** the day expects `86400 / period` polls; a spacing between
consecutive rows over 3/2 of the period (from 00:00 to the first row and from the last row to
24:00 included) is a `poll_gap`. It is explained (not failing) only when, with every `restart`
coverage run's span subtracted from it, no uncovered stretch of it still exceeds 3/2 of the period.

**Funding values:** `rate` as an exact `Decimal`; Bybit `interval` = `fundingIntervalHour` x 60 and
`next_funding_ns` = `nextFundingTime` x 10^6 when the frame carries them, else null; Hyperliquid
`interval` 60 and `next_funding_ns` null. The whole triple is the compared value.

**Precision labels:** every mark/index file with rows in the day must carry `price_precision`
metadata (else refused by name); more than one label per instrument-type-day fails; a file whose
label differs from the `price_precision` of any definition in force over the file's first to last
row (`ts_init`) fails (`label_vs_definition`). Funding and open interest have none ("none: stored as decimal text").

**REST validation (reported first).** Bybit, per type's field against the WS state at the
response's `time`: `agree_key` (equal), `agree_bracket` (equal to the update just before or just
after -- a neighbour in a recorder gap, or separated from the poll's time by a reset, by the same
receipt-vs-reset rule as the state, is not used), `between_pushes` (neither), `unaligned` (state unknown, or in a recorder gap), `failed`.
Hyperliquid: `agree` (equal to a frame received in `[sent_ns - 1 s, recv_ns + 1 s]`),
`between_pushes`, `unaligned` (in a recorder gap), `failed`. A type's reference is `unvalidated`
-- failing -- when polls were judged and none agreed, or rows exist and no poll was judged.
Why `between_pushes` is not failing: Hyperliquid's REST serves open-interest states the WS never
pushes (the smoke: 8 of 482 SOL polls held a value no frame of the day held; open interest moves
on every fill).

**Hyperliquid across midnight.** A row (capture's receipt) and its update (the recorder's) carry
different clocks, so an update received at 00:00:00.020 can have its row at 23:59:59.980. The
rows are read `HL_MATCH_BOUND_NS` beyond the day on both sides; the rows outside the day only
consume their updates and are never counted or classified.

**Venue oddities are counted, never refused.** A REST poll with a status other than 200, a
recorder `refusal`/`error`, a Bybit `retCode` other than 0, an empty `result.list` or an item of
another symbol, a Hyperliquid answer without the coin (or with contexts not matching the
universe), a field that is not decimal text, or a definition entry without its documented fields
is a `failed` poll (for that type, or for the definitions). A malformed `activeAssetCtx` frame of
another coin is skipped before the strict parse. An empty-string ticker field (Bybit's
`fundingRate: ""` on a dated future) is no value: the adapter's parse fails on it and writes
nothing, while its `funding_cache` still takes the string, which the emulation follows. Only the
recorder's own lines are refused (a missing `recv_ns`, an unknown line kind, a frame of the coin
without `data.coin`/`data.ctx`), and so is a WS frame *of the checked instrument* whose watched
field is present but not decimal text (a `null` `markPx`, say): that frame is the reference
itself, so the day cannot be judged without it -- loud, never skipped. A Bybit id of a category
the recorder does not record (inverse) is refused too. Dated Bybit futures' definitions are read from `crypto_future/`.

**Instrument definitions** (`instrument_check`, from the venue docs; §1.10 for the mapping): every
good venue poll of the day is judged against the stored definition in force (the latest row with
`ts_init` at or before its receipt): `agree`, `differs` (fails; field, stored, venue),
`before_first_definition`, `failed`. No stored definition at all fails (`no_definition`), as does a
day with no poll judged. A change between consecutive venue polls is listed as a `venue_change`
with the stored definitions' `ts_init`s, which show whether capture wrote it (only at a start).
Hyperliquid `lot_size` is shown `not_venue_declared (stored 1: Nautilus default)` and not compared
(D-106). Increments compare as `Decimal` values, precisions as ints.

**Spot (Bybit).** Every `-SPOT.BYBIT` id directory under `mark_price_update`, `index_price_update`,
`funding_rate_update` and `custom_open_interest` (plan ids always listed, "0 rows"): rows with
`ts_event` in the day are `fabricated` and fail the day.

**Bounds** (time alignment only, never a value tolerance): `HL_MATCH_BOUND_NS` = 1 s (stored
`ts_init - recv_ns` measured p1 -57 ms to max 437 ms; push cadence median 1.02 s, min 196 ms; the follow-up smoke found one row at +1,048 ms, a push later than the recorder's, which reads a false `not_stored` -- audit D-112, OPEN);
`OI_POLL_WINDOW_NS` = 2 s (the measured maximum needed was 623 ms over 96 rows; median WS
open-interest update interval 8.7 s). Recorder gaps and unrecorded hours are widened 5 s each way
(`RECORDER_GAP_MARGIN_NS`): the store files a line by receipt, so a frame keyed in an hour's last
instants can sit in the next hour's file (the smoke: a BTCUSDT mark `ts` 16:59:59.984Z filed in the
unread 17:00Z hour).

**Verdict and exit.** An instrument passes with every type passing (no failing row or update
count, one label equal to the definition's, no unexplained poll gap, a validated reference) and its
definitions passing. The day passes when every instrument does, no spot row is fabricated, the
coverage record exists and no raw hour of `linear.tickers`, `linear.rest.tickers`,
`<category>.rest.instruments-info` (Bybit) or `activeAssetCtx`, `rest.metaAndAssetCtxs`
(Hyperliquid) is missing. Exit 0 pass, 1 fail, 2 usage. Refusals exit 1 with their message,
ledgered at `verification.derivs.refused`: the book tool's (a day not closed, no raw root or
catalog, an unreadable plan, a malformed line, a truncated raw file of an hour of the day), a
mark/index file without its label, a stored value that is not decimal text, a null value or clock,
an unreadable `open_interest_poll_seconds`. Any other exception is a crash, ledgered at the same
site and re-raised. `--json` gives `passed`, `venue`, `day`, `bounds_ns`,
`open_interest_poll_seconds`, `coverage_file`, `coverage_present`, `missing_raw_files`,
`truncated_neighbour_files`, per instrument `types` (each `kind`, `passed`, `reference`, `ratio`,
`ts_rule`, `rest`, `rows`, `row_classes`, `updates`, `coverage`, `labels`, `label_vs_definition`,
`examples`) and `definitions` (`passed`, `polls`, `differs`, `venue_changes`,
`definitions_ts_init`, `not_declared`, `no_definition`), and `spot` (`rows`, `fabricated`).

**Repro** (host, from `platform/`, on the verify stack's data): `VERIFY_DATA_DIR=data/verification
CATALOG_PATH=data/catalog python3 -m verification.derivs --venue BYBIT --day YYYY-MM-DD [--json]`.
Measured cost (smoke, 12:59-17:00Z of raw): 6.6 s for Bybit's four instruments, 1.5 s for
Hyperliquid; 12:59-18:00Z: 8.1 s and 2.1 s. Results: `docs/VERIFICATION_REPORT.md`.

Known limits (each a `Known limit:` in the code): memory is one instrument-type-day of rows and the
instrument's reference as Python objects (a busy full Bybit index day is ~180k rows, ~55 MB; upgrade path:
hour windows carrying the state); the plan is today's; a Hyperliquid receipt difference above 1 s
or a Bybit poll slower than 2 s reads as a loud false fail.

### 1.20 Catalog integrity and backtest-read parity (`verification.catalog`, Story 31.7)

Not stored data: the check that the catalog is internally consistent over one closed UTC day and
that every reader -- a bounded `catalog.query`, a `BacktestNode`, the archive's consolidation,
the candle store -- sees exactly the stored rows. Readers never dedupe, so a duplicate or an
overlapping file reaches every consumer; D-24 showed a second schema silently dropping columns;
consolidation used to be checked by row count only; the candle store's bars had never been
compared with the rows a strategy reads.
`python3 -m verification.catalog --venue BYBIT|HYPERLIQUID --day D [--json] [--catalog DIR]
[--raw-dir DIR] [--candles DIR] [--scratch-dir DIR]` (`verification/catalog.py`, the root;
`verification/application/catalog.py`; `verification/domain/catalog_check.py`, pure;
`verification/infrastructure/catalog_scan.py`, the oracle's reads; `verification/subject/`, the
code under test). Exit 0 when every failing count is 0, 1 otherwise or on a refusal, 2 on usage.

**Oracle and subject (DATA-02).** The tool must open every file through `ParquetDataCatalog`, run
the archive's own consolidation and read the day through a `BacktestNode` -- the readers and
writers it judges -- so it drives them from one package, `verification.subject`
(`nautilus_reads.py`, `backtest_probe.py`, `consolidation.py`), the only non-test verification
code that may import `nautilus_trader`, `kernel.second_snapshot`, `kernel.open_interest` and the
archive's consolidation. It may import `verification.domain` (both sides hash rows with the one
digest), is imported only by `verification.catalog`, and reaches no `capture`, `candles`,
`ranking`, `views`, `research` or `bots` (`tests/test_boundaries.py`: the DATA-02 walk and the
allowlist walk stop at it; a runtime probe proves the oracle modules load no `nautilus_trader`).
The oracle reads raw pyarrow and stdlib `sqlite3`, parses file names itself (never
`kernel.clocks`) and judges candles with the reference fold (`reference_signals.fold_candles`,
`RefBook.from_stored`) and `signal_compare.at_places`.

**Scope.** The plan's instruments (the venue `config.toml`, as in every tool) and every
`<catalog>/data/<type>/` directory holding a leaf for one. A leaf's **day files** are those whose
name span (`YYYY-MM-DDTHH-MM-SS-<9 digits>Z_<same>.parquet`, UTC ns, inclusive `ts_init`) meets
`[D - M, D + 1 + M)`, `M = READ_MARGIN_NS = 60 s` -- the soak measured `ts_init - ts_event` at
1.000-1.006 s for Bybit snapshots, 3.000-3.005 s for Hyperliquid's and at most 9.8 s for trades --
**or** any leaf file whose Parquet row-group statistics of `ts_init` or `ts_event` (the columns
themselves where a row group has none) meet that window: a lying name is caught as `name_span`,
and a row with `ts_event` in D arriving after `D + 1 + M` as `beyond_margin` (a row further apart
than `M` fails), so the margin can never hide a row. Known limit (cost and scope): once the nightly
has consolidated D - 1 and D + 1, each is one file whose span meets the margin, so it is a day
file of D too -- structure-checked, opened and rehearsed with D (roughly doubling the run), and a
defect in it fails D as well, reported under its own file name; upgrade path: judge only a
neighbour file's rows inside the window and attribute its own classes to its own day.

**Row digest.** Per row, `blake2b(digest_size=16)` over `repr` of each `(column, value)` pair
sorted by name, values as pyarrow `to_pylist()` gives them (a dictionary column yields its string,
a `fixed_size_binary[16]` its bytes); a reader's multiset is `(count, sum of the row digests mod
2**128)`, order-independent, over every column of the stored file. The subject re-encodes what a
reader returns with `ArrowSerializer.serialize_batch` (the serializer `write_data` uses) and
projects it to the stored column names; a stored column the encoding lacks is a `read_mismatch`.

**1. Structure** (every class fails the day when non-zero):

| Class | Counts |
|---|---|
| `bad_name` | a leaf file whose name does not parse (or names an impossible date, or ends before it starts) |
| `overlap` | pairs of day files of a leaf whose inclusive name spans intersect, and pairs of a day file and any other file of the leaf whose name span meets it |
| `name_span` | a file whose name is not `[min, max]` of its `ts_init` (a name lying outside its rows hides them from name-pruned readers) |
| `unsorted` | a file whose `ts_init` decreases somewhere |
| `empty`, `null_ts` | a file without rows; a file with a row whose `ts_init` or `ts_event` is null (such a row is never read as a time: it is out of `beyond_margin` and out of the digest's day filter when its `ts_init` is null) |
| `open_failed` | `ParquetDataCatalog.query(cls, identifiers=[iid], files=[f], start, end)`, over each UTC hour holding one of the file's `ts_init`s, clipped to their `[min, max]` (never its name, so neither a corrupt name nor one stray stamp far from the rest becomes thousands of queries), raises. `files=` always takes Nautilus's PyArrow path, which has no decoder for the Rust-only types; a file of a type the catalog reads through its Rust backend (`MarkPriceUpdate`) is then opened by the same bounded query without `files=` -- what a backtest runs; the window is the file's own span, so only its rows while `overlap` is 0 (audit D-114). `IndexPriceUpdate` decodes on neither path in the pinned Nautilus (§1.5): every index file is `open_failed` (audit D-113, OPEN) |
| `open_count` | the decoded total differs from the file's rows (a file with no non-null `ts_init` is not opened: `empty`/`null_ts` judge it) |
| `unknown_type` | a type directory with no Nautilus class (its files are not opened, so never also `open_failed`): the class map is built from Nautilus's data and instrument classes and the two custom types through Nautilus's own `class_to_filename`, never typed by hand |
| `schemas` > 1 | per type, the signatures of its day files: the ordered (name, Arrow type) list plus the sorted metadata **keys** (the values are per-instrument precision labels, judged by §1.19). Each class is listed with its file count and one path |
| `duplicate_ts_event` | snapshots: rows beyond the first sharing `(iid, ts_event)` among rows with `ts_event` in D |

**2. Consolidation rehearsal.** The day files are hard-linked (a copy where a link is refused)
into a `TemporaryDirectory` under `--scratch-dir` (default `<VERIFY_DATA_DIR>/scratch`), keeping
the `data/<type>/<iid>/` layout; digest per type (`linked`); the archive's own
`run_closed_hours(writer, scratch, now_ns = D + 1 - 1 ns)` (the intraday merge of the small types'
closed hours; `intraday`); then `run(writer, scratch, None, None, True, now_ns = the clock)` (the
nightly consolidation of every closed day, every type; `nightly`), the writer from
`maintenance(scratch)` clocked at each stage's `now_ns`. A type's digest is recomputed only when
its files' (name, inode, size, mtime) changed. Verdict per type: `identical` (all three digests
equal and a file was rewritten), `not_exercised` (no file changed: printed, not failing),
`different` (fails); the archive's `leaves_failed` and `days_refused` fail too. Known limit:
intraday merges are rehearsed only while unmerged hours remain (the live `archive` service merges
the small types hour by hour), and a day the nightly already consolidated reads `not_exercised`
for every type -- Story 31.11 runs the tool before the nightly consolidates D (audit D-116).

**3. Backtest-read parity** (per plan instrument, `trade_tick` and the snapshot): three legs of the
rows with `ts_init` in D, by count and digest -- `stored` (the oracle's raw read of the day files),
`query` (24 hourly `catalog.query(cls, identifiers=[iid], start=h, end=h + 1 h - 1)`) and
`received` (what a `RecordingActor` receives from a `BacktestNode`: `BacktestDataConfig` for
`TradeTick` and `"kernel.second_snapshot:DydxSecondSnapshot"` with `client_id` = the venue,
`start_time`/`end_time` bounding `ts_init` over `[D - M, D + 1 + M)`, a `BacktestVenueConfig`
as the archive's backtest test, logging bypassed, `dispose_on_completion=False`, the actor loaded
by `ImportableActorConfig` string path and read back from the engine's trader). Any pair that
differs is a `read_mismatch` naming the pair. Known limit (audit D-117): the pinned Nautilus cannot stream a
Python custom type through `chunk_size` (the research runner's same limit), so the day is read
one-shot per window -- one node per window (the margin before D, each hour, the margin after),
one run config per instrument, each node disposed before the next. A snapshot with `ts_event` in D
keeps its trade columns for the fold even when its `ts_init` falls after midnight (23:59:59.5
sampled at 00:00:02.5 is in D's fold, not in D's digest).

**4. Candle parity.** The store `<--candles>/candles_<venue lowercased>.db` (`--candles`, else
`CANDLES_DIR`, else `<catalog>/../candles`), opened `mode=ro`: every row of the instrument with
`t` in D, per stored width `STORE_BAR_SECONDS = (60, 300, 900, 3600, 14400, 86400)` (the widths of
`candles/domain/fold.py`, §2.5); another width is `unknown_width` (fails). The reference is
`fold_candles` over the received rows with `ts_event` in D (each `RefBook` built from the trade
columns with empty book lists: the fold reads only trades). Per bucket, o/h/l/c are
`at_places(stored, reference, price places)` and v `at_places(..., size places)`, the places being
the largest precision among the bucket's rows; `seconds_observed` must be int-equal:

| Class | Fails |
|---|---|
| `exact` | no |
| `float_noise` | no: reported apart as a DEVIATION -- the store folds decoded floats (`candles/domain/fold.py` Known limit, audit D-115) |
| `both_undefined` | no: nothing traded on either side |
| `different`, `undefined_mismatch`, `missing` (reference only), `extra` (store only) | yes |

Known limit: 1m and 5m bars are pruned after `RETAIN_DAYS` (30 and 90 days), so a day older than
that reads `missing` at those widths.

**Refusals** (ledgered at `verification.catalog.refused`, exit 1 with the message): a day not
closed (`DAY_SETTLE_NS` after midnight), a missing catalog, candles directory or store file, an
unreadable plan, a plan instrument without a stored definition, a day file that vanished,
appeared or changed during the run ("catalog changed during the check (maintenance ran?)" -- every
day file -- selected as above, each with its (name, inode, size, mtime) -- is fingerprinted first,
after the rehearsal (the scratch holds hard links) and at the end, so a same-name rewrite counts
too; a live collector's new file of a later day is no day file and never refuses the run, nor
does one caught mid-write: `write_data` writes in place, so a file whose name misses the window and
whose footer cannot be read is left to its own day's check, which lists it by name), an
uncreatable scratch directory, a scratch directory inside the catalog root. The candle store's bars of D are read once, at the start. Any
other exception is a crash, ledgered at the same site and re-raised. The tool is read-only outside
its scratch directory.

`--json` gives `passed`, `failing`, `venue`, `day`, `catalog`, `candle_store`, `read_margin_ns`,
`instruments`, `structure` (per type `failing`, `known`, `schemas`, `leaves` with `counts` and
`examples`, `duplicate_ts_event`), `rehearsal` (`failing`, `leaves_failed`, `days_refused`, per
type `verdict` and `stages` with files, rows and digest), `parity` (per instrument and type the
three `legs`, `read_mismatch`, `mismatches`, `beyond_margin`), `candles` (per instrument `rows`,
`unknown_width`, per width `counts` and `examples`) and `float_noise`.

**Repro** (host, from `platform/`, on the verify stack's data): `VERIFY_DATA_DIR=data/verification
CATALOG_PATH=data/catalog CANDLES_DIR=data/candles python3 -m verification.catalog --venue BYBIT
--day YYYY-MM-DD [--json]`. Measured cost (smoke, the soak's 12:59-19:30Z, see
`docs/VERIFICATION_REPORT.md`): Bybit's four instruments (2,227,486 trades, 93,721 snapshots) 257 s
and 1.56 GB peak RSS, Hyperliquid 38 s and 0.71 GB. Known limit (runtime, memory): every row is
digested in Python per `repr` (about 7.5 us a trade row and 60 us a snapshot row per pass, six
passes at most); peak RSS is up to one hour of every plan instrument's objects in the backtest plus
the engines, and the archive's own merge of the busiest leaf-day (the code under test); upgrade path: an Arrow-compute hash over
each column.

### 1.21 Candles and klines on every timeframe (`verification.candles`, Story 31.8)

Not stored data: the check that every candle the chart can show -- the six stored widths and the
four folded at read time -- equals the market over one closed UTC day. Story 31.7 (§1.20) compared
the stored widths with a fold of the rows a backtest receives; nothing compared them with the
venue's own trades, nothing checked the read-time widths (10m, 30m, 45m, 1W: they exist only as
`data_api` responses), and nothing told an untraded bucket from missing data or checked
`seconds_observed`/`partial` against the coverage record (§1.16).
`python3 -m verification.candles --venue BYBIT|HYPERLIQUID --day D [--json] [--catalog DIR]
[--raw-dir DIR] [--candles DIR] [--data-api URL] [--no-served]` (`verification/candles.py`, the
root; `verification/application/candles.py`, the ports and orchestration;
`verification/domain/candle_check.py`, pure; `verification/infrastructure/served_candles.py`, the
HTTP pager). Exit 0 when every failing count is 0, 1 otherwise or on a refusal, 2 on usage.

**Sources, all read-only (DATA-02: the tool imports no `candles`, `views`, `data_api`, `capture`,
`kernel.fold`, `kernel.second_snapshot`, `nautilus_trader` or `verification.subject`):**
- the catalog's snapshot rows with `ts_event` in D, read raw with pyarrow (`read_day`, the trade
  columns and precisions in integer units);
- the candle store `candles_<venue lowercased>.db` (`--candles`, else `CANDLES_DIR`, else
  `<catalog>/../candles`), opened `mode=ro`;
- the served bars: `GET <data-api>/api/candles/{iid}?before_ns&limit&bar_seconds` over stdlib
  `urllib` (`--data-api`, else `VERIFY_DATA_API_URL`, else `http://127.0.0.1:29100`, the verify
  stack's loopback data_api, SEC-01) -- the read-time widths never exist at rest, so the response
  the chart receives is the only production artifact to check (`tests/test_boundaries.py`'s
  `NON_VENUE_HTTP_CLIENTS`: "the local data_api HTTP API");
- the reference recorder's raw trades (§1.15, `VERIFY_DATA_DIR`, every copy of an id merged:
  `channel_trades`, `merge_reference`), read one venue hour at a time (files H-1..H+1);
- the coverage record `<catalog>/../coverage/<venue>.jsonl` and the archive-gap markers.

**Per plan instrument, per width, per bucket of D.** The day grid is `86400 / w` buckets for each
of the nine widths dividing a day (`DAY_BAR_SECONDS`): the six stored `(60, 300, 900, 3600, 14400,
86400)` and the three read-time folds `(600, 1800, 2700)`. The fourth read-time width, 1W
(`READ_TIME_BAR_SECONDS = (600, 1800, 2700, 604800)`), spans seven days and is judged separately
(below). All restated from §2.5 and the route, never imported. Every bucket starts at the reference
`bucket_start` (day divisors at UTC midnight, 1W on Monday).

| Class | Meaning | Fails |
|---|---|---|
| `traded` / `untraded` / `no_data` | the bucket has a row with a trade / rows but no trade / no row | no (reported) |
| seconds `unexplained` | a second of D with no row and no coverage `seconds` run naming it (the others are counted per reason: `stale`, `no_book`, `catch_up_cap`, ...) | yes |
| catalog fold (stored widths) | 31.7's `judge_width` over the stored bars read raw: `exact`, `float_noise`, `both_undefined` / `different`, `undefined_mismatch`, `missing`, `extra`; `seconds_observed` int-equal | as §1.20 |
| served `exact` / `float_noise` | o/h/l/c/v of the served bar `at_places` the bucket's price/size places vs the catalog fold | no (`float_noise` reported apart, D-115) |
| `served_differs` | a served value outside its places | yes |
| `served_missing` / `served_extra` | a traded bucket not served / a served bucket that did not trade | yes |
| `partial_ok` / `partial_mismatch` | the served `partial` equals `seconds_observed < 0.9 x width` over the bucket's rows (`PARTIAL_OBSERVED_FRACTION`, judged as the exact fraction 9/10) / absent or not | `partial_mismatch` |
| reference `exact` / `both_undefined` | the masked reference fold equals the catalog fold | no |
| `ref_recorder_gap` | every differing second lies in a recorder gap of one of the instrument's WS trade channels (the reference itself was blind) | no |
| `ref_explained` | every differing second lies in a coverage trade window (`trades_dropped`, `trades_unrecoverable`) or an archive-gap marker span (live values kept) | no |
| `ref_different` | a differing second none of those explains (a second the reference cannot fold at the row's precision -- `off_grid` -- always differs) | yes |
| `unknown_width` | a stored row of a width the store does not keep | yes |

The **reference** is the fold (`fold_second`, at that second's row precisions) of the reference
trades whose venue time lies in each second **that has a row**; a second without a row can never
hold its trades in any bar, so its reference trades are counted per coverage reason as
`trades_unobserved` (informational: the bar understates them by design, and the second itself
must be explained above). The reference is compared with the catalog fold; the stored and served
bars are each proven equal to that same fold, so by transitivity each equals the masked reference
exactly when the catalog fold does.

Known limit (per-second cause, not per trade): a differing second inside a coverage trade window
or an archive-gap marker is `ref_explained` whatever the difference -- a window's `cap` (the count
of trades its writer recorded) is not spent here, and an *extra* catalog trade in the window reads
the same as a missing one. Both are proven per trade id by `verification.trades` (31.4), which
spends each capped window's budget (`Explanations`) and fails an archive-only id, so this tool
relies on that verdict for the same day. Upgrade path: carry the per-second trade-count difference
into the cause and spend the window's `cap` here too, and class a catalog-only surplus
`ref_different` (`verification/domain/candle_check.py` `Causes`) `[amended 2026-09-30: Story 31.8
follow-up review]`.

**Served paging.** Per day-grid width, pages of `limit = min(SERVED_PAGE_LIMIT, buckets in D)` (the
route's cap of 500, restated), the first from `before_ns` = the next midnight (D's `end_ns`, D+1
00:00 UTC), each next one from the oldest bar's `t`, back until a page reaches D's start or
`has_more` is false (an empty page with `has_more` true is refused); items with `o` null (gap rows) are ignored; a page that is not the documented shape, is not
strictly ascending before its cursor, or is non-200 is refused.

**1W.** The Monday-anchored bucket holding D is judged only once its week is closed (`week end +
DAY_SETTLE_NS <= now`), else `week_open` (not failing). Its reference is the catalog fold of the
week's seven days, read and folded one day at a time (MEM-01). The served bar is fetched twice: an
aligned page (`before_ns` = the week's end) and a chart-like page (`before_ns` = the week's end + 1
day, when that is not after now; else `not_fetched`), which must either leave the week out
(`omitted`) or serve it whole -- a bar folded from part of the week is `served_differs` (audit
D-118). The chart-like page is a **regression probe** for D-118, not a second proof: the fixed
route rounds that cursor up to the next week's end and reads one whole week, so when the next week
has data it serves that week only and the judged week is `omitted` (accepted, not failing); it
serves the judged week, whole, only when the next week is empty and the gap jump lands on it. The
pre-fix route read `[cursor - 7 days, cursor]` and folded the judged week from its last six days,
which the probe fails (`served_differs`). It has not yet run against the old image (the soak's only
week is open); both outcomes are pinned by `verification/tests/test_candles.py`
(`test_a_chart_page_that_serves_only_the_next_week_is_omitted_and_passes`,
`test_a_truncated_week_planted_beside_the_next_week_is_served_differs`). Known limit: the week's bar is proven against the reference only transitively, through
each day's 1d reference verdict; upgrade path: a `--week` mode folding the week's masked reference.

**Refusals** (ledgered at `verification.candles.refused`, exit 1 with the message): a day not
closed (`DAY_SETTLE_NS` = 2 h after midnight), a missing catalog, candles directory, store file,
raw directory or coverage record, an unreadable plan, a malformed line, a truncated raw file of the
day, the data_api unreachable or answering non-200 or with a malformed page, a file vanishing
mid-run. Any other exception is a crash, ledgered at the same site and re-raised. A missing raw
reference file of the day is counted failing (`missing_raw_files`), as in §1.16. With
`--no-served` the served bars are not checked: the report says `served: not checked` and the
verdict is `PROVISIONAL`, never `PASS` (exit 0 when nothing else failed, so it stays scriptable);
Story 31.11 runs it with the served checks on.

`--json` gives `verdict` (`PASS`/`FAIL`/`PROVISIONAL`), `passed`, `provisional`, `failing`, `venue`,
`day`, `served`, `data_api`, `candle_store`, `coverage_file`, `missing_raw_files`,
`truncated_neighbour_files` and per instrument `rows`, `seconds` (`expected`, `rows`,
`explained_by_reason`, `unexplained`, examples), `trades_unobserved`, `unknown_width`, per width
`buckets`, `catalog`, `served`, `reference`, `examples`, and `week` (`status`, `kind`, `served`).

**Repro** (host, from `platform/`, on the verify stack's data, read-only):
`CATALOG_PATH=data/catalog VERIFY_DATA_DIR=data/verification CANDLES_DIR=data/candles
BYBIT_COLLECTOR_CONFIG=capture/venues/bybit/config.toml
HYPERLIQUID_COLLECTOR_CONFIG=capture/venues/hyperliquid/config.toml python3 -m verification.candles
--venue BYBIT --day YYYY-MM-DD [--json] [--data-api http://127.0.0.1:<port>]`. Measured cost
(smoke, 2026-09-29, the soak's 12:59:19-20:42Z, see `docs/VERIFICATION_REPORT.md`): Bybit's four
instruments 300 s and 519 MiB peak RSS, Hyperliquid 5 s and 194 MiB, served checks included. Known
limit (runtime): every width folds both books over the whole day, and every raw trade file is
decoded up to three times (its own hour and each neighbour's) per instrument of its channel, which
is where Bybit's time goes; upgrade path: one decode per raw file shared by every instrument of its
channel, as conservation's. Known limit (plan): the instruments are the venue config's current
plan, as in the other tools. Known limit (a live data_api): a bar the nightly rebuilds while the
tool runs is judged as served at that moment (a loud difference, never a silent pass).

---

### 1.22 Live, backtest and display parity, incl. the bot's own signals (`verification.bot_parity`, Story 31.9)

Not stored data: three proofs that the value a screen shows, the value a bot acts on and the value
a backtest computes are the same, or differ only by a named, measured mechanism.
`docs/VERIFICATION_REPORT.md` (row "Live, backtest and display parity") holds the numbers.

**1. The signal log (`bots/strategies/signal_log.py`, opt-in).** `DummyStrategyConfig.signal_log_path`
(None by default: nothing written, trading unchanged); the host sets it to
`<BOT_SIGNAL_LOG_DIR>/<bot_id>.jsonl` for paper `dummy` bots only -- the real-money exec bot writes
none (`BotConfig` and its TOML are unchanged).
One JSON line per decision cycle (every 1 s timer event and every bar), floats in `json`'s
shortest repr (the exact double fed; NaN written `NaN`, never dropped):

| Field | Meaning |
|---|---|
| `kind` | `start` (one per `on_start`), `book`, `book_skipped` or `bar` |
| `bot_id`, `instrument_id` | the bot (its `order_id_tag`) and its instrument |
| `ts_ns` | `start`: the clock at start, truncated to whole microseconds (below); otherwise the event's `ts_event` (the timer event's, the bar's) |
| `start` only | `trade_size`, `bar_spec`, `trend_lookback`, `trend_buy_threshold`, `trend_sell_threshold`, `ofi_levels`, `ofi_window`, `obi_levels`, `ofi_confirm_threshold` |
| `book_ts_ns` | `book`: the book's `ts_last` (optional on `book_skipped`) |
| `bids` / `asks` | `book`: the top `max(ofi_levels, obi_levels)` levels exactly as the indicators were fed, `[price, size]` floats (`Price.as_double()`, so `83062.59999999999` for 83062.6) |
| `reason` | `book_skipped`: `no_book` or `one_sided` (a skipped second is visible, DATA-07) |
| `bar` | `bar`: `{ts_event, close}` |
| `microprice`, `ofi`, `obi`, `mlofi`, `trend` | the indicator values, null while not initialized |
| `signal` | `long`/`short`/`none`/`not_ready`: `signal_log.signal_of(trend, mlofi, thresholds)`, the one statement of the entry rule (`_wanted_side` decides from it) |
| `action` | the side (`BUY`/`SELL`) of the order submitted this cycle, or null (reset at the top of every timer event and bar, so a skipped cycle never carries an earlier cycle's side) |

A restart appends a new `start`: a file holds one run segment per start, and every reader takes
the latest. Known limit (start): the live clock's timers fire on whole microseconds (a start of
`...519479442` ns fires at `...520519479000`), so the strategy truncates its start to whole
microseconds and starts its 1 s timer at exactly that `ts_ns`; the few hundred ns lost are before
the first cycle and change nothing it computes. Known limit (size): one file per bot growing for
the run, about 1 KB per cycle (~86 MB per bot-day); upgrade path hourly rotation.

**2. The verify fleet and its replay.** `bots/config.verify.toml`: one paper `dummy` bot per verify
instrument (Bybit `BTCUSDT-LINEAR`, `ETHUSDT-LINEAR`, `BTCUSDT-SPOT`, `ETHUSDT-SPOT`; Hyperliquid
`SOL-USD-PERP`), mainnet data, Sandbox execution only. `docker-compose.verify.yml`'s `live-paper`
mounts it over `/app/bots/config.toml`, sets `BOT_SIGNAL_LOG_DIR=/app/verify_data/bot_signals/live`
and mounts only `./data/verification/bot_signals/live` there (the rest of `data/verification` is the
reference recorders', DATA-02; `make verify-up` creates the directory first); it is in the
`Makefile`'s `VERIFY_SERVICES`.

`python3 -m bots.signal_replay --config F --catalog P --live-log DIR --out DIR [--start ISO]
[--end ISO] [--bot BOT_ID ...]` runs one `BacktestNode` per dummy bot of F (the strategy by string
path, the bot's thresholds, sizing and exits, `signal_log_path=<out>/<bot_id>.jsonl`):
- **Clock and window.** `start` is the live log's latest `start` record's `ts_ns` exactly, so the
  replay's 1 s timer fires on the same `start + k s` nanoseconds and cycles pair by equal `ts_ns`,
  never by nearest match; `end` is the segment's last record (`--start` is snapped up onto that
  grid and never before the live start, `--end` taken as given). The live `start` record's
  instrument, thresholds and sizing must equal F's (else refused). The live log is read like the
  comparator reads it: an unterminated last line is held back, a malformed complete line refused.
- **Its own output is checked.** The run must append a fresh segment (a `start` after the file's
  size before the run, so an older segment never passes for it) that starts at the window start and
  whose last record reaches the window end within 1 s.
- **Data.** The catalog's instrument definition in force at the window start (the latest with
  `ts_init <= start`; refused when none) and `TradeTick`s (the `LAST-INTERNAL` trend bars
  aggregate from the same trades), plus, per stored snapshot row, one `OrderBookDeltas` (`CLEAR`
  and every stored level, `F_SNAPSHOT`, the last `F_LAST`) and one `QuoteTick` (the row's top), both
  from `kernel/snapshot_book.py` (the one conversion; research's per-row quote derivation,
  `research.application.quotes`, builds through its `top_quote`, SSOT-01) and both stamped
  `ts_event = ts_init =` the row's `ts_init`: the moment the row could first be known, the clock
  backtests replay on (§1.7). Prices and sizes come from the stored integers
  (`Price.from_raw`), never a float. The derived data goes to a throwaway catalog; the source is
  read one hour at a time (MEM-01), never written.
- **Venue.** Configured as the fleet's Sandbox client (the venue's account type and starting
  balances, NETTING, `L1_MBP`, leverage 1), but it does not fill as the live Sandbox does. Known
  limit: the backtest's `L1_MBP` matching engine skips a trade older than its book's last update,
  and the derived quotes/deltas are stamped at the row's `ts_init` (at or after `S + 1 + hold_back`
  s) while trades keep the venue's `ts_event`, so most replay trades are skipped for fill
  simulation and a market order fills at the derived quote; fills, positions and so `action` differ
  by construction (hence informational). Upgrade path: replay the trades on the book's clock, or
  derive the book from the raw archive at venue time.
- **Cold start.** No warm-up, as live; but the replay holds no book until the first stored row
  after `start` (about 1 + `hold_back` s), so its first cycles may be `book_skipped`, explained
  below (`cold_start`).

Exit 0 when every selected bot replayed, 1 on any refusal, 2 on usage. Each bot runs on its own: a
refusal (a missing or malformed log, a mismatched config, no definition or rows, an engine error, a
run that wrote no fresh aligned segment reaching the end) is ledgered at
`bots.signal_replay.refused` (its own `job_service` ledger, `bots.signal_replay` unless `ERROR_LEDGER_SERVICE` names a parent) and the next
bot still runs. Known limits (chunking,
the engine log bypassed) are in the module docstring.

**3. The comparator.** `python3 -m verification.bot_parity --venue BYBIT|HYPERLIQUID --live-dir D
--replay-dir D [--catalog P] [--json]` (`verification/bot_parity.py`, the root;
`verification/application/bot_parity.py`; `verification/domain/bot_parity.py`, pure;
`verification/infrastructure/signal_logs.py`). Oracle side (DATA-02): stdlib plus verification's
own raw readers; it imports no `bots`, `kernel.indicators` or `nautilus_trader` (the runtime probe
in `tests/test_boundaries.py`). The logs are read as JSON text (an unterminated last line is a
write in flight, left for the next run); the stored rows raw, decoded by the oracle's own book
decoder; the coverage record `<catalog>/../coverage/<venue>.jsonl` for gap reasons.

Pairing: the latest segment of each side, their `start` records equal but for `ts_ns`, the
replay's on the live grid (`live start + k s`, k >= 0; a `--start` override pairs from it, and the
live cycles before it stay visible as memory the replay never had, `carried_state`); `book` and
`book_skipped` share the timer's group, `bar` its own; equal `ts_ns` over `(replay start, min(last
live, last replay)]`. When one side's last cycle falls over one timer interval (1 s) short of the
other's, the longer side's cycles past it are counted `truncated`, which fails: a cut-short side
never passes as a shorter window. **Equality is exact** (a null
equals a null, a NaN a NaN, nothing rounded). The one grid rule: a logged level equals a stored
level iff each float, at its exact binary value rounded half-even to the row's precision, is the
stored integer (`grid_units`: `as_double()` is a few ulp off its grid value, never half a step).

Per signal (`microprice`, `ofi`, `obi`, `mlofi`, `trend`): paired count, exact-equal share, max
absolute difference; each non-equal paired signal gets exactly one class, first match wins:

| Class | Meaning | Fails |
|---|---|---|
| `gap` | a second in `[T-3 s, T)` has no stored row and the coverage record names why (`gap_reasons`) | no |
| `book_timing` | the fed levels differ, and the live top-N equals a stored row's top-N at a second in `[T-5 s, T+1 s]` | no |
| `book_source` | the fed levels differ and the live top-N equals no stored row in that window | no |
| `quote_cadence` | `microprice`/`ofi` only: the live side updates on every quote, the replay once per stored row -- and only when the replay's microprice equals its active row's top's (below); `ofi` is fed the same quotes, so it rests on that evidence | no |
| `bar_source` | `trend` only: the bars' `{ts_event, close}` up to T differ | no |
| `carried_state` | inputs equal this cycle but different within the indicator's memory (`mlofi`: `ofi_window` + 1 cycles; `trend`: every bar of the segment, an online SGD model, deliberately not `trend_lookback`) | no |
| `unexplained` | none of the above, or a missing second without a coverage reason | **yes** |

`trend` is fed by bars alone, so only `bar_source`/`carried_state`/`unexplained` apply to it. A
cycle's own class is `unexplained` if any signal or its decision is, else its earliest class. A
`signal` disagreement is attributed to its diverging input's class (`trend`, `mlofi`), `unexplained`
when both are equal; `action` disagreements are informational (fills differ by construction). A
cycle on one side only is `live_only`/`replay_only`: a replay book the live side skipped at the same
T carries the live skip's own reason (`book_skipped:<reason>`); a live book the replay skipped is
explained only by the replay's `cold_start` (`no_book` before the first stored row with `ts_init` at
or after the replay start) or a coverage-explained `gap`; anything else, a replay skip mid-window
included, is `unexplained`.

**The replay's own input (`replay_input`, fails).** Every replay timer cycle is checked against the
catalog, whatever the live side logged: its fed levels must equal the stored row active at T by
`ts_init` (the latest with `start <= ts_init <= T`; `grid_units`' exact rule), its microprice that
row's top's (`reference_microprice`, `reference_signals.microprice`'s formula over the row's
integers, within `signal_compare.REL_TOL`), and a skip means no row was active. Reasons: `levels`,
`microprice`, `no_row`, `skipped_with_row`. This is what fails a broken snapshot conversion even
when both logs agree (it found D-135).

Exit 0 iff nothing fails (`unexplained`, `replay_input`, `truncated` all 0); 1 otherwise or on a
refusal (ledgered `verification.bot_parity.refused`: a missing directory, catalog or coverage
record, a bot without its replay, a bot logging more levels a side than the stored rows of its
window hold, a log whose venue cannot be told, a catalog not flushed past a bot's window -- no
stored row in the minute after it --, a malformed record of this venue's logs, `start` records
differing but for `ts_ns` or a replay start off the live grid); 2 on usage. Each live log's venue
is read leniently from its `start` record first, so another venue's malformed log never refuses
this venue's run.

**What the classes do not see (audit D-133, D-134).** A class names *where* two sides differ, not
which side is right. On Bybit the live side's inputs are themselves wrong: the pinned adapter
builds every Bybit quote from the first entries of each depth-50 book message (D-133) and on spot
also replays the depth-1 quote stream into the book (D-134), and both surface here only as
`quote_cadence` / `book_source`. Measured against the reference recorder (§1.15) outside this tool;
the upgrade path is a live-vs-venue input check (`quote_source`) in this tool, D-133. So on Bybit
a run with nothing else failing is a classification, **not a parity proof**, while D-133/D-134 are
OPEN: the replay's input is checked against the catalog, the live side's against nothing.

**4. The display chain, traced (`verification/tests/test_ssot_trace.py`, AC1).** One stored second
followed along six hops: catalog row -> `snapshots:raw` -> `RankingBoard` -> `rankings:live` ->
`metrics.db` -> the `data_api` responses (`/api/rankings`, `/api/metrics/history`,
`/api/snapshots`). Fixture variant (always runs, in `make test`): the committed real rows (Bybit
BTCUSDT linear, Hyperliquid SOL, 300 each) published as `snapshots:raw` JSON through a real
`RankingEngine` (in-memory ports, a real tmp `metrics.db`), served by the real routes (TestClient)
and a tmp `write_data` catalog. Per field and per hop: the stored integers equal the payload
integers; stateless fields equal the reference (`verification.domain.reference_signals`, 31.3's
tolerances); windowed and stateful fields equal it over the rows so far; `metrics.db` equals the
board's state at write time; the API equals the store and the bus exactly. Every key a hop emits is
compared or names a row of the test's `ACCOUNTED` table, so a new field fails until traced:

| Accounted row | Hop | Field | Why it differs |
|---|---|---|---|
| `metrics_price_is_trade_close` | board -> metrics.db | `price` | the last trade close (§3.3), not the live mid `rankings:live` calls `price` |
| `metrics_ts_is_wall_clock` | board -> metrics.db | `ts` | the engine's wall clock when the slow loop read the board (D-130), not a row's `ts_event` |
| `rankings_live_has_no_ts_event` | board -> rankings:live | `updated_at` | the engine's wall clock at build; no entry names the `ts_event` it reflects |
| `spread_rounded_to_price_precision` | board -> rankings:live, metrics.db | `spread` | rounded to the row's price precision (31.3); judged `at_places(p)` |
| `float_decode` | snapshots:raw -> board | every derived value | `units / 10^p` decoded to the nearest double; `FLOAT_NOISE` reported apart |
| `snapshots_mid_is_kernel_mid_price` | catalog -> /api/snapshots | `mid` | `kernel.indicators.mid_price` (inline until Story 31.9, D-131): compared with the kernel function (bit-equal) and the reference, every row |
| `api_rankings_renames_ranks` | rankings:live -> /api/rankings | `ranks` | served as `items`, every entry byte-for-byte |
| `metrics_ofi_is_ofi_5` | board -> metrics.db | `ofi` | the rank entry's `ofi_5` under its historical name |
| `metrics_rank_is_real` | board -> metrics.db | `rank` | a REAL column: 1 reads back 1.0 |
| `metrics_api_row_has_no_instrument_id` | metrics.db -> /api/metrics/history | `instrument_id` | the path names it |
| `undefined_z_published_as_zero` | board -> rankings:live | `ofi_10_z` | an undefined z-score is published 0.0 (§2.2, D-89): `pinned_zero` |
| `obi_carried_on_zero_total` | board -> rankings:live | `obi_3/5/10` | a zero-total OBI keeps the last value (§2.3, D-89): `carried` |
| `snapshots_t_is_milliseconds` | catalog -> /api/snapshots | `t` | `ts_event // 10^6`, truncated |
| `volume24h_from_the_volume_poll` | volume source -> rankings:live, metrics.db | `volume24h` | the venue's 24 h USD volume poll |
| `slow_fields_from_the_last_slow_loop` | metrics.db -> rankings:live | `pct_*`, `volatility` | copied from the last slow-loop row |
| `in_flight_publish` (live only) | snapshots:raw -> rankings:live | stateless fields | a message published while the engine handles the previous batch reflects that batch: matched to the newest or the one before, counted apart |
| `slow_loop_reads_its_clock_first` (live only) | board -> metrics.db | `ofi`/`microprice`/`spread` | the deployed image stamps `ts` before its awaits (fixed in code, D-130, undeployed): matched at/before `ts` or within 2 s after |

Live variant (`VERIFY_STACK=1`, a module-level `skipif`, else skipped): subscribes to the verify
Redis (`127.0.0.1:26379`) `snapshots:raw` and `rankings:live`; checks each `rankings:live` row's
stateless fields against the last batch before it; finds the traced rows in `CATALOG_PATH` after
the flush (bounded, <= 150 s); checks `/api/rankings` against the latest bus message and the newest
`metrics.db` row against the last `rankings:live` at or before its `ts`.

**5. `OFIStrategy`'s backtest z-score (`verification/tests/test_ofi_parity.py`, AC3).** A recording
subclass of `OFIStrategy`, run by a real `BacktestNode` over a `write_data` catalog, appends
`(ts_event, ts_init, value, initialized)` after every `on_data`. Against
`research.application.microstructure.ofi_readings`: **exactly equal** on every row with a reading;
a row without one (NaN: the first row, the baseline after a gap over `OFI_GAP_NS`, a one-sided row)
is the pinned class `carried` (the strategy keeps the previous value, 0.0 before the first). Against
the independent reference `rolling_ofi_z(..., usd=True, ...)`: within `signal_compare.REL_TOL`; an
undefined z-score (under 2 readings, a flat window) published 0.0 is `pinned_zero`. Row order by
`ts_init` must equal order by `ts_event`, else the test fails. Fixture variant: both fixtures at the
defaults (10 levels, window 20, z-window 300) and z-window 20. Soak variant: `VERIFY_SOAK_CATALOG`
+ `VERIFY_SOAK_DAY`, every stored instrument's day at the defaults, the catalog only read.

**Repro** (host, from `platform/`, on the verify stack's data): stop the fleet after at least an
hour (`docker stop verify-live-paper`), wait ~3 minutes for the collectors' 60 s flush, then
`python3 -m bots.signal_replay --config bots/config.verify.toml --catalog data/catalog --live-log
data/verification/bot_signals/live --out data/verification/bot_signals/replay` and
`python3 -m verification.bot_parity --venue BYBIT|HYPERLIQUID --catalog data/catalog --live-dir
data/verification/bot_signals/live --replay-dir data/verification/bot_signals/replay [--json]`;
`VERIFY_STACK=1 CATALOG_PATH=data/catalog python3 -m pytest -o addopts="" --rootdir=.
verification/tests/test_ssot_trace.py`; `VERIFY_SOAK_CATALOG=data/catalog VERIFY_SOAK_DAY=YYYY-MM-DD
python3 -m pytest -o addopts="" --rootdir=. verification/tests/test_ofi_parity.py`.
Measured cost (2026-09-30, 66.5 min of 5 bots, `docs/VERIFICATION_REPORT.md`): the replay 16.5 s
wall and 718 MiB peak RSS for all five (about 3 s per bot-hour); the comparator 5.9 s / 230 MiB
(Bybit, 4 bots) and 2.5 s / 211 MiB (Hyperliquid), 7.9 s / 220 MiB and 2.7 s / 212 MiB with the
replay-input check; result after the review patches: 1 `replay_input` `levels` per bot (D-135),
nothing else failing, the Bybit classes not a parity proof while D-133/D-134 are OPEN; the SSOT live variant ~125 s; the OFI soak
variant 126 s / 1.16 GB over the 2026-09-29 soak day.

### 1.23 `capture:hotpath` / `capture:hotpath:<venue>` (capture's hot-path figures, Story 28.1)

Not market data: each collector's own cost figures, so a `_second_loop` stall (audit D-10) or an
ingest-queue backlog (D-07) can be told apart from host contention (D-136)
`[amended 2026-09-30: Story 28.1 -- new channel and key]`.

- **Producer:** `CaptureService._report_hotpath` (`capture/application/capture_service.py`), right
  after each **periodic** flush (`_flush_loop`, every `flush_interval_seconds`, 60 s by default, at
  :02 past the boundary), also when that flush raised, and once more after the final flush of a
  stop or a crash (its partial window). The figures are kept by
  `capture.application.hotpath_metrics.HotPathWindow` and restart from zero after every report.
  The same figures go to the collector's log as one INFO line,
  `hotpath: window_s=60.001 queue_depth_max=37 messages_processed=500 wakes=60 lag_max_ms=3200.0
  lag_p99_ms=3200.0 writes=3 write_data_ms=4.5 write_data_max_ms=12.0` (Dozzle), with or without
  Redis.
- **Carrier:** `RedisLiveStream.publish_hotpath` (`capture/infrastructure/redis_stream.py`) sends
  one pipeline (no MULTI) on the collector's existing Redis client: `PUBLISH capture:hotpath <json>`
  and `SET capture:hotpath:<venue> <json>`, `<venue>` the lower-case `kernel.venues` token
  (`bybit`, `hyperliquid`, `dydx`). The key holds the latest record (no TTL: a stale `ts` shows a
  collector that stopped reporting). A failed publish is ledgered `collector.hotpath_publish` and
  the next flush publishes normally.
- **Payload** (one `json.dumps` object, keys in this order):
  - `venue` — the `kernel.venues` token (`BYBIT`);
  - `ts` — wall-clock ns at publish (the same name `collector:status` uses);
  - `window_s` — seconds (3 decimals, monotonic clock) since the previous report: the span every
    count below covers (the first window starts at service construction);
  - `queue_depth_max` — the largest `_ingest_queue.qsize()` seen by `_process_data` in the window
    or at the report itself (messages still queued behind the one being processed; 0 when idle).
    A sample, not a continuous peak: a backlog that builds and drains between two processed
    messages is not seen, but one that is still there at the report always is;
  - `messages_processed` — messages handed to `_process_data` in the window (every type, planned or
    not, including one that then failed and was ledgered `collector.process`);
  - `wakes` — sample-loop wakes counted: one per `_second_loop` wake (arrival mode) or per
    `_venue_second_loop` wake that closed at least one second (venue mode; an early wake with
    nothing due counts nothing);
  - `lag_max_ms`, `lag_p99_ms` — milliseconds (3 decimals) between each counted wake and the wall
    time the loop slept toward (`_next_sample_at` in arrival mode, `_next_close_at` in venue mode),
    clamped at 0; max and nearest-rank 99th percentile (`ceil(0.99 n)`-th smallest), `null` with no
    wake in the window. At the default 60 s flush a window holds ~60 wakes, and the nearest-rank
    p99 of up to 99 samples is their maximum, so the two are equal there; they part only from 100
    wakes on (a longer `flush_interval_seconds`). Not the `_second_loop tick arrived ... late`
    canary (that measures tick to tick against the interval, WARNING above 2 s; unchanged).
    Known limit: both ends are wall-clock, so an NTP step reads as lag (forward) or clamps to 0
    (backward); and in arrival mode the target is recomputed after the previous tick's own work,
    so a stall inside the sample tick itself shows as `missed_tick` coverage (§1.16) and the canary,
    not as lag (venue mode counts it). Upgrade path: a monotonic deadline and a fixed arrival
    schedule (`CaptureService._note_wake`);
  - `writes` — successful `ArchiveWriter.write` calls in the window (one per `(type, instrument)`
    batch; a flush writes several);
  - `write_data_ms`, `write_data_max_ms` — milliseconds (3 decimals) of the window's last and of
    its slowest successful `ArchiveWriter.write` call (`ParquetDataCatalog.write_data` of one
    batch), timed with `perf_counter_ns` inside the worker thread; `null` when no batch was
    written. A failed write never counts (it is `collector.flush_write`).
- **Consumers:** none yet -- read it with `redis-cli GET capture:hotpath:bybit` or
  `redis-cli SUBSCRIBE capture:hotpath` (`docs/DEPLOY_CHECKLIST.md` §7). No HTTP endpoint serves it.
- **Deviation, recorded:** the epic asked for these figures "on the existing per-flush Redis
  channel the dashboard reads"; no such channel exists. Capture publishes only `snapshots:raw`
  (every second); `collector:status` (§1.12) is collection control's frozen, replay-tested
  contract on a 30 s/1800 s cadence, and `/api/errors` reads ledger files. A dedicated channel
  plus a latest-value key on capture's own Redis client gives the same visibility without an
  endpoint and without coupling to another context's contract.

---

## 2. Computed signals / ML features (`platform/kernel/`, `platform/views/`, `platform/ranking/`)

Everything here is computed **on read** from the raw types in §1 — nothing in this
section is stored back to Parquet. Since Story 24.2 every value a UI shows is computed in the
`views/` read-model context (§2.4, §2.6, §2.7, §2.10 moved there from `ml_signals/`; the old
module paths' re-exports were deleted in Story 24.4); `data_api` only formats and transports it
(`bot_tui` shows no market value at all since Story 25.1a: rankings are web-only). Per SSOT-01/02 (`platform/CLAUDE.md`), stateless
single-snapshot formulas live as plain functions in `kernel/indicators.py` (the shared kernel,
Story 23.2; the `ml_signals.indicators` re-export was deleted in Story 24.2); stateful/rolling
indicators are classes, and for anything shown in a live UI, exactly one process
(`ranking_engine`) is allowed to own the running instance (§3).

### 2.1 Stateless, single-snapshot functions (`kernel/indicators.py`)

All take one `DydxSecondSnapshot`-shaped dict (§1.7) and return a value with no memory
of prior calls:

| Function | Formula | Raw fields used |
|---|---|---|
| `microprice(snapshot)` | `(bid_prices[0]*ask_sizes[0] + ask_prices[0]*bid_sizes[0]) / (bid_sizes[0]+ask_sizes[0])`; None on an empty side or when both top sizes are 0 (undefined, never the mid) | `bid_prices[0]`, `bid_sizes[0]`, `ask_prices[0]`, `ask_sizes[0]` |
| `spread(snapshot)` | `ask_prices[0] - bid_prices[0]`, as written (negative when crossed), rounded to the row's `price_precision` (Story 31.3: the float difference of two decoded prices cancels digits -- a one-tick 0.000001 spread at 8.578755 came out 1.0000000010279564e-06 -- so the double nearest the exact difference is returned) | `bid_prices[0]`, `ask_prices[0]` |
| `mid_price(snapshot)` | `(bid_prices[0] + ask_prices[0]) / 2` | `bid_prices[0]`, `ask_prices[0]` |
| `volume_delta(snapshot)` | `buy_volume - sell_volume` | `buy_volume`, `sell_volume` |
| `trade_aggregates(snapshots)` | sums `buy_volume`/`sell_volume`/`buy_count`/`sell_count` across a list — feeds CVD and `avg_trade_size` downstream | all four trade fields |

Every value is a float of the exact decimal it stands for: `mid` is exact at p+1 places, `spread`
at p, `volume_delta`/CVD at s (p, s: the row's precisions), and each is within float noise of that
decimal (§2.13 counts the noise; `spread` is the nearest double exactly). An empty side makes
`mid`/`spread`/`microprice` None; nothing substitutes for them (no mid for a missing microprice in
the chart, §2.7).

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

**The rule, stated in full (Story 31.3; the reference is `verification/domain/reference_signals.py`'s
`ofi_step`/`rolling_ofi`/`rolling_ofi_z`).** Source: Cont, Kukanov and Stoikov (2014), "The Price
Impact of Order Book Events" (level 0), summed over the top levels as in Xu, Gould and Howison
(2019), "Multi-Level Order-Flow Imbalance in a Limit Order Book".

- **Levels.** Level i of the top `levels` counts only when it exists in the current and the previous
  bid *and* ask lists: `n = min(levels, |bid|, |ask|, |prev_bid|, |prev_ask|)`. A thinner side never
  biases the sum one way; an empty side makes the contribution 0.
- **Terms.** Bid: a higher price adds the new size, an equal price adds `size - prev_size`, a lower
  price subtracts `prev_size` (a withdrawal). Ask: a lower price adds the new size, equal adds the
  change, a higher one subtracts `prev_size`. `contribution = sum(bid_term - ask_term)`.
- **USD notional.** A price-up (improved) or unchanged term is valued at the *current* level price; a
  withdrawal at the *previous* price, where that size actually rested.
- **Rolling value.** The sum of the last `window` contributions. The first row, and the first row
  after a gap, is a baseline: it adds no contribution and only becomes the previous book; the
  value it reads is the unchanged window (None until a first contribution exists). The window
  keeps contributions from before a gap.
- **Gap rule, one threshold everywhere.** `kernel.indicators.OFI_GAP_NS` = 3 s: when consecutive fed
  rows' `ts_event`s differ by strictly more, the caller clears the tracker's previous book
  (`clear_prev_state`) before the update. Every OFI replay applies it: `ranking` (live), the chart's
  per-bar replay (`views.chart_series.replay_bucket_samples`), `OFIStrategy`, `SnapshotStrategy` and
  `research.application.microstructure.ofi_readings`. Before Story 31.3 research used 5 s, the chart
  replay and `SnapshotStrategy` none. `DummyStrategy` (the paper bot, `bots/strategies/dummy.py`)
  and its gap handling are decided with the operator in Story 31.9, not here.
- **One-sided rows differ by reader (not unified).** The ranking's OFI trackers, `OFIStrategy`,
  `SnapshotStrategy` and `ofi_readings` skip a row with an empty side: it is not fed, so the next
  two-sided row diffs against the last two-sided one. The chart's per-bar replay feeds it: its
  contribution is 0 (the level rule above) and it becomes the previous book, so the next two-sided
  row contributes 0 as well. The reference (`rolling_ofi`) follows the rule as written, the chart's
  way. Known limit; upgrade path: one shared feed policy in `kernel.indicators`.
- **Z-score.** `(x - mean) / std` of each reading against the last `zscore_window` readings, one
  reading per contribution (a baseline adds none and keeps the last z-score), population standard
  deviation (ddof=0). **Known limit:** undefined (fewer than 2 readings, or all equal) is published
  as 0.0 by `RollingZScore`, not None; pinned by
  `verification/tests/test_reference_signals.py::test_the_z_scored_ofi_matches_the_reference_with_zero_when_undefined_pinned`.
  Upgrade path: publish None and let each reader show a gap.

### 2.3 Order Book Imbalance (OBI)

`MultiLevelOBI` (`indicators.py`): `sum(bid_sizes[:levels]) / (sum(bid_sizes[:levels]) + sum(ask_sizes[:levels]))`.
1.0 = all depth on the bid side, 0.5 = balanced, 0.0 = all ask. `ranking_engine` runs
three instances per instrument at levels 3/5/10, fed only two-sided books. A reader that feeds a
one-sided book -- the chart's per-bar replay (`views.chart_series.replay_bucket_samples`), which
skips nothing -- gets the formula as written: 0.0 or 1.0.

**Known limit (zero total):** when the top `levels` sizes total 0 the value is undefined, but
`MultiLevelOBI` keeps its previous value (`.initialized` stays as it was), so a stateful reader --
the ranking's `obi_N`, the chart's per-bar OBI -- publishes the last defined OBI for that second.
Pinned by `verification/tests/test_reference_signals.py::test_multilevel_obi_keeps_its_previous_value_on_a_zero_total_known_limit`;
upgrade path: publish None there.

### 2.4 Footprint / order-book flow (`views/chart_series.py`'s `build_footprint`, was `ml_signals/footprint.py`)

`build_footprint` buckets **resting order-book size changes** (not executed trades —
dYdX L2 deltas have no order IDs, so a shrinking level can't be told apart from a
cancel vs. a fill; see the module's own caveat, `footprint.py`) into
per-candle, per-price-band cells (`bands_per_candle`, default 4). Each cell tracks
gross `bid_added`/`bid_removed`/`ask_added`/`ask_removed` size (not just net, so a
churning level is visible). Input: `OrderBookDelta`s + `Candle`s (§2.5). Used by the
web dashboard's footprint chart only — no ranking/live-tick consumer.

### 2.5 Candles (the `candles/` context, Story 24.1)

Every bar the platform shows comes from **one** seconds → bars fold,
`candles.domain.fold.fold_arrays`: open of the first traded second in the bucket, high/low across
all of them, close of the last, volume summed, and `seconds_observed` counting every second a row
existed for (traded or not). It is the only such aggregation in `platform/`; the only other fold
anywhere is trades → second, `kernel.fold.fold_trades`. The collector never subscribes to `Bar`s
(§1.3), so this is where minute-and-wider candles come from.

Three readers, all over that one fold, so they cannot disagree:

- **The stored closed bar** — `candles.application.queries.window(db, iid, bar_seconds,
  before_ms, limit)`: up to `limit` traded buckets with `t < before_ms`, oldest first, read from
  `candles_<venue>.db` (§5). Each dict is
  `{t (ms), o, h, l, c, v, seconds_observed, partial, source: "candle_store"}`; `partial` is
  `seconds_observed < 0.9 * bar_seconds` (D-15), meaning the collector only saw part of the
  bucket and its high/low/volume are understated. Only buckets that traded are returned
  (`o IS NOT NULL`). `latest`, `oldest_t` and `watermarks` are the other reads.
- **The forming bar** — `candles.application.forming.forming_bar(rows, bar_seconds)`: the newest
  traded bucket of the live 1 s rows it is given, as `{t (ms), o, h, l, c, v}`, or `None` when
  nothing traded in them. `views/live_candles.py`'s `LiveCandleBus` calls it per `snapshots:raw` tick over the
  in-progress bucket's buffer; that dict *is* the `/ws/live` `bar` payload. `bar_seconds` need
  not be one the store keeps (the chart offers 10 m, 30 m, 1 w). The same dict is handed to every
  attached `BarObserver` watching the pair -- the alert engine (§2.11) -- so an alert is evaluated
  on the candle the chart draws, whether or not a chart is open.
- **The archive-side read** — `candles.application.queries.candle_dicts_for_window(iid, start_ns,
  end_ns, bar_seconds, snapshot_rows_fn)`: the same fold over raw 1 s rows read from Parquet, for
  history older than the store's first bucket, and the only source of the read-time widths (10m,
  30m, 45m, 1W). Each dict carries `source: "raw_1s"` and, since Story 31.8, `partial` by the same
  `is_partial(seconds_observed, bar_seconds)` rule as a stored bar (before, a read-time bar carried
  no flag at all, so an understated bucket looked whole: audit D-119); the `/ws/live` forming-bar
  payload is unchanged. `views.chart_series`'s Parquet page (`_parquet_page`) queries
  **bucket-aligned** windows: every window -- the first and each gap jump of
  `views.catalog_reads.fetch_page` -- ends on a bucket boundary (`before_ns` rounded up, by the
  bucket rule below; a gap jump to the last row + 1 ns, so a file whose last row opens a bucket
  keeps it) and spans a whole number of buckets (the span `limit x width x 3`, capped at
  `MAX_QUERY_SPAN_SECONDS` = 7 days, rounded down to whole buckets and never below one), so no
  served bar is folded from only its later seconds. No read reaches `before_ns`: the bucket holding
  the cursor is folded from its seconds before `before_ns` only (no look-ahead for a historical
  cursor) and is marked `partial` when that is under 90 % of its span; on the chart's first page
  the cursor is now, so that bucket is the forming one. The page's filter `t < before_ms` is
  unchanged. Before, a 1W page always folded the week
  its 7-day window started in from only the days it reached, with no marker (audit D-118). Known
  limit: a 1W page from Parquet holds at most one week per request (the cap is one week), so the
  chart pages a 1W history back one bar at a time; upgrade path: compose the read-time widths from
  stored bars. `verification.candles` (§1.21) proves every served bar of a day against the catalog
  fold, the venue's trades and the coverage record `[amended 2026-09-30: Story 31.8]`.

**The bucket rule (Story 31.3).** Every bucket in `platform/` -- the fold, the forming bar
(`views.live_candles`), the chart's per-bar replay and footprint, the picker's custom-indicator
buckets -- is `candles.domain.fold.bucket_start_ms(ts_ms, bar_seconds)`: a width that divides a day
starts at UTC midnight (epoch-aligned); **1W (604800 s) starts on Monday 00:00 UTC**, like the
venues' weekly klines and the frontend's week (the epoch, 1970-01-01, was a Thursday, which is
where 1W buckets started before). Known limit: any other width that does not divide a day would be
epoch-aligned; none is offered (`TIMEFRAMES` holds day divisors and 1W only). The indicator panes
(`/api/indicator-series`, `/api/coin/{id}/indicator-values`) accept `bar_seconds` up to 604800, so a
1W pane is computed on 1W buckets -- before, both routes clamped it silently to 86400. Every raw
read stays capped at 7 days (`chart_series.MAX_QUERY_SPAN_SECONDS`). **Known limit:** so a 1W
OFI/OBI pane page holds at most two buckets (the older one computed from only the days inside the
read -- the OFI/OBI replay, `indicator_series_page`, still queries unaligned windows: audit D-122,
OPEN), and the picker's custom indicators (CVD, cancel pressure, delta OFI) carry a value only on
the last 7 1D bars or the last 1W bar of a page (§2.7); upgrade path: stored per-bar aggregates,
paged like the candle store, instead of raw-row replays. Known limit (research):
`ReturnSeries.resample` keys buckets by `ts // period` (its invariant: every stamp a multiple of the
period), so a 604800 s resample there is epoch-(Thursday-)anchored; upgrade path: an anchored grid
in `ReturnSeries`.

`candles.domain.candle.is_valid_candle` is the shape guard every served candle passes
(`l <= min(o,c) <= max(o,c) <= h`, `v >= 0`, all finite); a violator is a bug upstream, failed
loudly as a 500 and counted (`candles.invalid_candle`), never clamped (DATA-07).

### 2.6 Book features (`views/chart_series.py`, was `ml_signals/book_features.py`)

A second, independent set of L2-derived features, computed by replaying raw
`OrderBookDelta`s (not `DydxSecondSnapshot`) — used by the chart page (§2.7), not by
`ranking_engine`.

| Feature | Formula | Notes |
|---|---|---|
| `depth_profile` | top-N bid/ask prices+sizes from a live `OrderBook` | levels 1-10 default |
| `book_imbalance` | per-level `bid/(bid+ask)`, plus an aggregate across all levels | same imbalance formula as OBI, computed from a live book object instead of stored list fields |
| `liquidity_distance` | price distance from best to where cumulative depth reaches `pct_threshold` (default 80%) of one side's total | small = dense support/resistance nearby; large = a liquidity vacuum |
| `CancellationTracker` / `cancel_pressure` | `(deleted_size - added_size) / (deleted_size + added_size)` at the best bid/ask, over a rolling event window (default 200) | +1 = all cancellations, -1 = all additions; only tracks ADD/DELETE at the *current* best price, UPDATE is ambiguous-direction and skipped |

### 2.7 Chart series (`views/chart_series.py`, was `ml_signals/chart_data.py` and the `data_api` routes)

The chart page's pages are `views.chart_series` read models (Story 24.2): `candle_page` (the
candle store, then the archive's seconds -> bars fold), `snapshot_series_page`/`price_series_rows`
(Lines mode: bid, ask, mid, `kernel.indicators.microprice` -- null when undefined, never the mid
(Story 31.3) -- and the CVD-weighted price
`mid + ((buy_volume - sell_volume) / (buy_volume + sell_volume)) * (ask - bid) / 2`, the mid when
nothing traded),
`indicator_series_page` (per-bar OFI/OBI replay, microprice, spread: `replay_bucket_samples`,
the last value per bucket, the §2.2 gap rule and §2.5 bucket rule, OBI's zero-total Known limit of
§2.3) and `indicator_values_page` (whose custom indicators replay raw seconds/deltas over a window
capped at `MAX_QUERY_SPAN_SECONDS`, 7 days, back from the page's end: an older bar of a long 1W/1D
page carries None for them, MEM-01)
(the picker's indicators over the chart's own candles, via `views.indicator_picker`). Every
archived second is priced as written -- a crossed second (`bid >= ask`) included: today's gate
never writes one (`SecondSampler` rejects it as `Crossed`, Story 26.1), so one in the archive predates that
gate or is a capture bug to fix at the gate or with `repair_catalog`, never a reader filter (AD-3).
A second with an empty side cannot be drawn at all and is never written by the gate, so reading one
fails the request (500) and counts `views.snapshot_without_top`. Gap rows are the only rendering rule:
`SNAPSHOT_GAP_THRESHOLD_MS` (2500 ms) in Lines mode, one bar in the bar-spaced panes
(`with_gap_markers`).

`compute_chart_series` (the legacy `/catalog/chart-series` endpoint's backend) reads the window's
archived 1s snapshots (`views.catalog_reads.query_second_snapshots`, `DydxSecondSnapshot` rows)
and emits one point per second for `microprice` (`kernel.indicators.microprice`), `spread`
(`kernel.indicators.spread`: best ask - best bid as written, rounded at the row's precision, §2.1), `imbalance` (aggregate top-10-level book imbalance),
`mid_imbalance` (mean of levels 2-3), and `bid_depth`/`ask_depth` (top-10-level size sums).
It does not replay `OrderBookDelta`s or `TradeTick`s. An empty-top second raises
`EmptyTopOfBook` like the pages above. Entirely a read-time computation — nothing here is
persisted or fed into ranking.

### 2.8 Price stats — moved to the ranking context (§3)

`[amended 2026-09-26: Story 25.2 -- `ml_signals/metrics_computer.py` (`compute_all`, dead since
Story 13.2 replaced its recurring Parquet re-scan with the in-memory price series) was deleted, and
`catalog_stats`' price math moved to `ranking/`: the formula is
`ranking/domain/metrics.py`'s `price_stats_from_series`, the catalog read
`ranking/infrastructure/catalog_prices.py`. See §3.2/§3.4.]`

### 2.9 `research/rank_history.py` / `research/watchlist.py`

`[amended 2026-09-25: Story 24.4 -- `watchlist.py` moved from `ml_signals/` to the research
context; `ml_signals.watchlist` is a deprecated re-export until Story 25.2.]` `[amended 2026-09-26:
Story 25.2 -- `rank_history.py` moved from `ml_signals/` to the research context too, and
`ml_signals` was deleted.]`

Thin, dependency-light HTTP fetch helpers, not computations: `fetch_rank_history`
(`research/rank_history.py`) and `fetch_watchlist` (`research/watchlist.py`) pull already-computed data from the running `data_api`'s HTTP
API (`/api/metrics/nearest/{iid}`, `/api/rankings`) for scripts/notebooks that don't want to
import the full dependency set (fastapi, redis).
`research.application.ranking_history.HttpRankingHistory` (Story 27.1) is the notebook-facing
`RankingHistory` port over `/api/metrics/history/{iid}?days=N`: the same rows, as a DataFrame,
values untouched `[amended 2026-09-28: Story 27.1]`.

### 2.10 `views/ranking_columns.py` — shared column definitions (was `ml_signals/ranking_columns.py`)

**Actively wired up**, not a work-in-progress stub: it defines
`RANKING_COLS`, the ordered list of `(store_key, label, format_fn)` tuples that is the web
rankings table's single column source (`platform/CLAUDE.md` SSOT-04). `frontend/`'s
`RankingsPage.tsx` renders a hand-declared TS mirror of it, held to the same `(key, label)`
sequence by `data_api/tests/test_ranking_columns_mirror.py`. `[amended 2026-09-26: Story 25.1a
-- its old renderer, `bot_tui`'s Coins pane, was deleted (rankings are web-only), and with it the
`color_fn` member and `POSITIVE_COLOR`/`NEGATIVE_COLOR`, which only that pane read.]` Every
`store_key` in this list
(`ofi_10_z`, `obi_10`, `obi_5`, `obi_3`, `cvd`, `spread`, `microprice_lean`,
`volume_delta`, `price`, `pct_1h`, `pct_24h`, `volatility`, `volatility_score`,
`volume24h`) is a field name coming straight off a `rankings:live` rank entry —
i.e. every column this file defines maps 1:1 to a field `ranking_engine` publishes
(§3). It is display metadata (labels, `f"{v:+.2f}"`-style formatting), not a new
computation. The same module also holds the Technicals tab's per-coin
values (`technicals_values`, Story 24.2): each column's latest value through the chart's own
indicator dispatch over the chart's own candles -- no indicator or ranking math of its own.

**Units and names (Story 31.3).** Values are published and shown in their raw units -- nothing
normalises them, server- or client-side: `cvd` and `volume_delta` are base-token units (buy minus
sell size), `spread` and `microprice_lean` price units (quote per base token), `price` is the mid. The
three volatilities carry labels that name their window and input: `volatility` is **"Vol 24h σ
(trade closes)"**, `volatility_score` **"Vol 1h σ (mids)"**; `volatility_fast` (the coin page only,
last 300 mids) is the third (§3.2). The Docs page used to say CVD/spread were converted by
`usdFromTokens`/`bpsFromPriceUnits` helpers that never existed;
`data_api/tests/test_frontend_named_helpers.py` now fails any helper the frontend names as code
without defining it.

The page's pinned identity columns are not part of `RANKING_COLS` and not in the mirror: Rank
(the row's position in the message), Symbol (the rank entry's `symbol`), Exchange (`venue`, with
`market` as a dim tag: `BYBIT · spot`) and Instrument (`instrument_id`), in that order, on both
tabs (Story 29.1). Symbol and Exchange are sortable -- a header click cycles ascending,
descending, then back to message order; an Exchange sort groups a venue's markets (`perp`
before `spot` ascending) before the rank tie-break; ties break by rank, a row missing the field
sorts last, and every row keeps its message rank -- and filterable with `=` (`Symbol`, `Exchange (venue)`).
The page never derives a symbol from the id itself. `[amended 2026-09-28: Story 29.1]`

### 2.11 Price alerts (the `alerting/` context, Story 24.3, was `data_api/alerts.py`)

**Store:** `alerts.toml` (`ALERTS_PATH`, default `platform/data_api/alerts.toml`; compose sets
`/app/data_api/alerts.toml`, bind-mounted from `platform/data_api/alerts.toml`). Owner: the
`alerting` context -- `alerting/infrastructure/toml_store.py`'s `AlertStore` is its only reader and
writer (full rewrite on every change; a corrupt file raises at load rather than starting empty),
constructed once per process by `data_api/alert_wiring.py`. Key set, frozen (AD-D12): one
`[[alerts]]` table per alert with `id`, `instrument_id`, `level`, `frequency`
(`once_per_bar_close` | `once_per_bar` | `only_once`, `alerting.domain.policy.FiringPolicy`),
`bar_seconds`, `template`, `webhook_url` (may be empty), `created_ns`, optional `expires_at_ns`,
`triggered`, optional `last_fired_ns` (TOML has no null: an absent optional key is `None`).

**Evaluation:** on the forming bar's close `c` (§2.5) for the alert's own `(instrument_id,
bar_seconds)`, at the producing second's `ts_event`, by `AlertEngine.on_bar` -- never on a raw
snapshot or a bar folded anywhere else. A second with no trade republishes an unchanged close (or
no bar at all at a bucket's start), so it can never fire. Run state (the previous price) is
in-memory: the first bar after a restart cannot fire.

**Delivery:** a fire is recorded in the store (a failed persist is ledgered at
`alerting.store.persist`, the fire still happens), sent on the alert's channels through
`observability.notify` (its webhook, and Telegram when `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are
set; a failed send is ledgered at `observability.notify.<transport>`), and toasted on `/ws/live` as
`{"channel": "alerts", "alert": {"id", "message"}}` (frozen).

### 2.12 Research reads (the six notebooks of `research/notebooks/`, Epic 27)

`[amended 2026-09-28: Story 27.9]` What each notebook reads from the stores, and every value it
shows that is derived on read, with the one function that derives it (SIGNAL-01: nothing below is
stored; every market-data read is bounded by the notebook's `START`/`END`, NB-04). Index,
purpose and run times: `research/README.md`. No notebook reads `metrics.db`: ranking history
reaches research only over HTTP (§2.9).

**`01_catalog_inspection`**

- *Stored, read:* instrument definitions (all fields, `price_precision` among them); the
  `DydxSecondSnapshot` files' `ts_init` spans (`kernel.catalog_files.data_file_ranges`);
  `DydxSecondSnapshot` rows (`ts_event`, `ts_init`, `bid_prices`/`bid_sizes`/`ask_prices`/
  `ask_sizes`, `buy_volume`/`sell_volume`, `buy_count`/`sell_count`,
  `open_price`/`high_price`/`low_price`/`close_price`) through `CatalogFrames.seconds`;
  `TradeTick` (`price`, `size`, `aggressor_side`, `trade_id`, `ts_event`); `MarkPriceUpdate`
  `ts_event`; the Parquet `price_precision` metadata of the `TradeTick`, `MarkPriceUpdate` and
  `IndexPriceUpdate` files (`kernel.catalog_files.price_precision_labels`); the candle store's
  `verified_days.status` (`candles.application.queries.verified_status`); the error ledger's
  `data/errors/*.jsonl` lines (`ts_ns`, `site`, `suppressed`, `process_start`; §1.11) through
  `observability.error_ledger`.
- *Derived on read:* mid, spread and microprice (`kernel.indicators.mid_price`/`spread`/
  `microprice`, in `CatalogFrames.seconds`); `obi_1`/`obi_5`/`obi_10`/`obi_20`
  (`kernel.indicators.MultiLevelOBI`); the gap classes (outage / book gap / quiet market, the
  archive's `find_gaps` through `research.application.inspection.gap_report`); the trades re-fold
  per day (`kernel.fold.fold_trades`, in `inspection.fold_agreement`); the sanity counts (crossed,
  spread; `inspection.snapshot_sanity`); the receive lag `ts_init - ts_event`
  (`inspection.receive_lag_ms`).

**`02_microstructure`**

- *Stored, read:* `DydxSecondSnapshot` (as `01`); the instrument's `price_increment`;
  `MarkPriceUpdate` (`value`, `ts_event`, `ts_init`); `IndexPriceUpdate` (`value`, timestamps)
  through `kernel.catalog_files.query_index_prices` (`CatalogFrames.mark_index`);
  `FundingRateUpdate` (`rate`, `interval`, `next_funding_ns`, timestamps); `OpenInterest`
  (`open_interest`, timestamps).
- *Derived on read:* mid, spread, microprice and OBI (as `01`); `volume_delta`
  (`kernel.indicators.volume_delta`); spread in ticks and bps
  (`research.application.microstructure.spread_frame`); OFI (`kernel.indicators.MultiLevelOFI`,
  USD notional) and its z-scores (`kernel.indicators.RollingZScore`); depth by level and by
  distance from mid (`kernel.indicators.snapshot_depth`/`cumulative_depth`/`depth_within_bps`);
  the microprice edge and its hit rate per bin (`microstructure.microprice_edge`,
  `research.domain.microstructure.hit_rate_by_bin`); trade flow and CVD
  (`microstructure.trade_flow`); the impact fit (`research.domain.microstructure.price_impact`);
  the basis (`microstructure.basis_frame`); returns (`research.domain.returns.ReturnSeries.
  from_prices`/`resample`); autocorrelation, volatility signature and realised volatility
  (`research.domain.microstructure.autocorrelation`/`volatility_signature`/
  `realised_volatility`).

**`03_correlation`**

- *Stored, read:* the candle store's `candles` rows (`t`, `o`, `h`, `l`, `c`, `v`,
  `seconds_observed`; `c` and `partial` used) at 60/300/3600/86400 s through
  `candles.application.queries` (`window`, `bucket_starts`, `oldest_t`, `newest_t`);
  `DydxSecondSnapshot` level-0 prices (the mid), `buy_volume`/`sell_volume`, `ts_event`;
  `FundingRateUpdate` (`rate`, `interval`, `ts_event`); `OpenInterest` (`open_interest`,
  `ts_event`); the instrument ids.
- *Derived on read:* bar returns (`ReturnSeries.from_prices`); the correlation matrices, clusters,
  merge order and rolling correlation (`research.domain.correlation.correlation_matrix`/`cluster`/
  `merge_order`/`rolling_correlation`/`correlation_of`); the cross-venue basis in bps and the
  lead-lag with its peak (`correlation.basis_bps`/`lead_lag`/`peak_lag`); funding per hour
  (`research.application.aligned.funding_per_hour`); OI change (`aligned.oi_changes`); each
  venue's volume share (`aligned.venue_volume_share`).

**`04_backtest_evaluation`**

- *Stored, read* (through `NodeRunner`, `data="seconds"`): instrument definitions;
  `DydxSecondSnapshot` level 0, turned into `QuoteTick`s by `kernel.catalog_files.query_top_of_book`
  and `research.application.quotes.derived_quotes`; the full snapshots streamed by
  `BacktestDataConfig` (`OFIStrategy` reads `bid_prices`/`bid_sizes`/`ask_prices`/`ask_sizes`,
  `buy_volume`/`sell_volume`, `ts_event`); `TradeTick` only for a `data="trades"` or
  `"bars:..."` run.
- *Derived on read:* the equity (`research.application.backtest_runner.equity_from_account`); the
  metrics table (`MetricReport.from_ledger` → `kernel.performance_metrics.all_metrics`); the
  underwater series and drawdowns (`EquityCurve.underwater`/`drawdowns`); the rolling Sharpe
  (`ReturnSeries.from_equity(...).rolling_sharpe` → `performance_metrics.return_stats`); PnL by
  hour and weekday and the holding times (`TradeLedger.by_hour_of_day`/`by_weekday`/
  `holding_times_s`); the sweep grid and top runs (`research.application.evaluation.metric_grid`/
  `top_runs`); the walk-forward and its joined equity
  (`research.application.walk_forward.walk_forward`/`concat_equity`).

**`05_monte_carlo`**

- *Stored, read:* as `04`.
- *Derived on read:* the trade-order and block bootstraps, risk of ruin, the Sharpe confidence
  interval and the deflated Sharpe (`research.domain.monte_carlo.bootstrap_trades`/
  `block_bootstrap_returns`/`risk_of_ruin`/`sharpe_confidence_interval`/`deflated_sharpe`, the
  last through `research.application.robustness.deflated_check`); the metrics (`MetricReport`).

**`06_candlestick_scanner`**

- *Stored, read:* the candle store only (`t`, `o`, `h`, `l`, `c`, `seconds_observed` → `partial`,
  `bucket_starts`) through `CatalogFrames.bars`/`bar_coverage`; no catalog row.
- *Derived on read:* the EMA (`nautilus_trader.indicators.ExponentialMovingAverage`, in
  `research.application.patterns.ema_values`); the patterns
  (`kernel.candle_patterns.CandlePatternSet`); forward returns and the hit rate
  (`research.domain.events.forward_returns`/`hit_rate`).

**Known limits pinned by Story 31.3** (each held by a test in
`verification/tests/test_reference_series.py` or the named one, against the reference of §2.13):

- `microstructure.microprice_edge` reads a predictor within 4 ulps of the mid (`_ROUNDING_ULPS`) as
  0 (flat): with equal top sizes the two kernel floats round apart, but a genuine lean that small
  (2.5e-12 on a 13 437 mid) reads flat too
  (`test_microprice_edge_reads_a_lean_within_four_ulps_of_the_mid_as_flat_known_limit`). Upgrade
  path: judge the lean on the exact decimals.
- `aligned.oi_changes` makes an open interest of 0 or less NaN, so a real drop *to* 0 (a -100 %
  change) is undefined too, not only the change *from* 0
  (`test_oi_changes_read_a_move_to_zero_as_undefined_known_limit`,
  `research/tests/test_aligned.py::test_oi_changes_are_relative_and_undefined_from_zero`).
- `correlation._pearson` treats a side as constant when its std is at most `1e-12 * max(1, |mean|)`;
  the reference treats only an *exactly* constant side as undefined. A near-constant but
  unequal series (std under that bound) is NaN in production and defined in the reference; the
  comparisons use exact constants only.
- `kernel.indicators.depth_within_bps` counts a level within `1e-9` relative of an edge as on it
  (`_BPS_EDGE_SLACK`), so a float distance a few ulps past an exact edge does not flap. The
  reference is exact (no slack); a disagreement explained only by levels inside that slack is
  counted as its own class, EDGE_SLACK (§2.13), anything else DIFFERENT
  (`verification/tests/test_reference_signals.py::test_a_level_exactly_on_an_edge_differs_only_by_the_pinned_edge_slack`).
  NaN beyond the deepest stored level is the rule (the snapshot does not say whether the book
  ended there).

### 2.13 Reference signals (Story 31.3)

`verification/domain/reference_signals.py` re-implements every derived value above from this
dictionary's text only, in exact `Decimal` (floats only for the z-score, Pearson and lead-lag),
importing the standard library only (`tests/test_boundaries.py::test_the_reference_signals_import_the_standard_library_only`),
and decodes the stored rows itself (§1.7), so the two decoders are compared too.
`verification/tests/test_reference_signals.py` and `test_reference_series.py` compare every
production function with it on seeded generators (`verification/tests/signal_cases.py`: 0-50
levels, empty sides, zero tops, crossed books, precisions 0..8, gaps under/at/over 3 s, duplicate
seconds, week boundaries, NaN gaps), on 300 real rows per soak instrument
(`verification/tests/fixtures/snapshots/`, cut by `python3 -m verification.tools.cut_snapshot_fixtures`)
and on hand-computed golden cases. Each comparator has a planted defect it must catch (OBI with bid
and ask swapped, OFI one level short, a fold taking the first close, a ddof-swapped reference, a pct
taking the latest point as its base, a picker fed opens).

Verdict classes (`verification/domain/signal_compare.py`):

| Class | Meaning | Pass? |
|---|---|---|
| `EXACT` | the production float is the double nearest the reference value | yes |
| `FLOAT_NOISE` | a value exact at known places quantizes (half-even) to the reference there, but is not its nearest double (`85891.90000000001`) -- reported, never absorbed | yes |
| `WITHIN_TOL` | a division/statistical output within the relative tolerance, not the nearest double | yes |
| `BOTH_UNDEFINED` | both None/NaN (an empty side, a zero total, too few points) | yes |
| `EDGE_SLACK` | a depth sum that differs from the exact reference only through levels within production's 1e-9 relative edge slack (§2.12's pinned Known limit) | yes, counted |
| `WITHIN_ULPS` | the microprice lean (`microprice - mid`, a cancelling difference) off the reference by more than 1e-9 of itself but within 8 ulps of the mid (`signal_compare.LEAN_ULPS`) | yes, counted |
| `DIFFERENT` | anything else | **no** |
| `UNDEFINED_MISMATCH` | one side defined, the other not | **no** |

Tolerances, never widened to make a case pass:

| Values | Rule |
|---|---|
| `mid` | exact at p+1 places |
| `spread`, OHLC | exact at p places |
| CVD, `volume_delta`, depth sums, count OFI, candle volume, `trade_flow` CVD | exact at s places |
| bucket starts, `seconds_observed`, lags, decoded integers | equal |
| microprice, OBI, `avg_trade_size`, pct changes, basis, `funding_per_hour`, USD-notional OFI, z-score, the three stdevs, returns, Pearson, rolling Pearson, lead-lag, spread in ticks/bps, the picker's SMA/EMA | relative 1e-9, absolute 1e-12 |
| `microprice_lean` | as above, else within 8 ulps of the mid (WITHIN_ULPS) |
| an infinite production value | always DIFFERENT; a float reference is read as its shortest repr |

Why these bounds (`verification/domain/signal_compare.py` states the same budget): each input is
the float64 decode of an exact stored decimal (0.5 ulp, 1.1e-16 relative), and a sum or mean over
at most 3600 terms adds under ~1e-12 relative, so a ratio of products and sums (microprice, OBI,
USD OFI, z-score) lands within ~1e-12: 1e-9 is a 1000x margin. A *difference of two decoded prices*
over a price (a return, a pct change, a stdev of returns) is the case the relative bound does not
cover: its absolute error is ~2 decode errors (2.2e-16) whatever the move, so relative to a small
move it grows as price/move -- a one-unit move on the soak's largest stored price, BTCUSDT linear at
8.4e6 units (p=2), is a 1.2e-7 return carrying up to ~1.9e-9 relative, past 1e-9, but only
~2.2e-16 absolute (~2.2e-14 as a pct). The absolute floor 1e-12 covers that with a 100x margin and
a reference of exactly 0. A basis in bps of two nearly equal prices would exceed the floor
(2.2e-12) and is judged relative only. The microprice lean has its own class (WITHIN_ULPS) because
both operands are of the price's magnitude. The one place a decode error broke these bounds -- a
spread divided into ticks or bps (`research.application.microstructure.spread_frame`, 1.03e-9 on a
one-tick spread at 8.578755) -- was fixed at its source (`kernel.indicators.spread` rounds, §2.1),
not tolerated. Run with `pytest -s` to print every signal's counts; the numbers are
in `docs/VERIFICATION_REPORT.md`.

---

## 3. Ranking engine (`platform/ranking/`, the `ranking_engine` service)

`[amended 2026-09-26: Story 25.2 -- moved from `ranking_engine/engine.py`'s module globals to the
`ranking/` bounded context: `RankingBoard` (`ranking/domain/board.py`) holds the state,
`RankingEngine` (`ranking/application/engine.py`) runs the loops, `python3 -m ranking` wires them.
Every payload field, store column and ledger site below keeps its name. `ranking/tests/test_replay.py`
proves the `rankings:live` bytes and `metrics.db` rows identical to the pre-move engine for a
recorded 200 s burst over an empty catalog (live ingest, three venues' volumes, one slow-loop cycle,
a mode switch); it does not exercise the catalog backfill or `age_out`. Deliberate changes,
both in §3.3: `age_out` and the dYdX volume parse.]`

The ranking context is the **sole computer and publisher** of the live coin
ranking (architecture decision AD-9) — `data_api` and the web UI are pure readers
of its output, never independent computers of the same indicators (`platform/CLAUDE.md`
SSOT-02). This exists specifically to prevent two processes independently running
the same rolling-window indicator class and silently diverging via differing startup
time / window contents / float accumulation order.

### 3.1 Inputs

- **`snapshots:raw`** — the Redis pub/sub stream of `DydxSecondSnapshot` dicts
  published live by the collector (§1.7), decoded only by `DydxSecondSnapshot.from_dict`
  (an entry it rejects is skipped and counted at `ranking_engine.snapshot_entry`). This is the
  engine's only per-tick market data input; it never reads Parquet directly for live-tick fields.
- **USD 24 h volume, per venue** (Story 22.10) — polled independently every 60s,
  all sources concurrently (`RankingEngine.volume_cycle`), one `VolumeSource` adapter per
  source in `ranking/infrastructure/`: dYdX's indexer `/v4/perpetualMarkets` `volume24H`
  (`volume_dydx.py`), Bybit's `/v5/market/tickers?category=linear|spot` `turnover24h`
  (`volume_bybit.py`; spot kept only for USDT/USDC quotes) and Hyperliquid's
  `POST /info {"type":"metaAndAssetCtxs"}` `dayNtlVlm` (`volume_hyperliquid.py`), every request
  built with `kernel.venue_http`. `DYDX_NETWORK`, `BYBIT_ENVIRONMENT` and
  `HYPERLIQUID_ENVIRONMENT` pick mainnet/testnet. Each source's last good result is
  kept on a failed poll and expires after 3 missed polls; failures, expiries and fresh
  instruments with no volume are counted at `ranking_engine.volume24h`. A source's fetch is
  bounded at 45 s as a whole (`RankingConfig.volume_fetch_timeout_s`), on top of the kernel's
  per-socket-operation timeout.
- **Parquet catalog** — read **once per instrument** (a lazy backfill of the in-memory 25 h
  price series of trade closes, `ranking/infrastructure/catalog_prices.py` over
  `kernel.catalog_files`; a window with no trade is an empty series -- Story 31.3 deleted the
  mark-price fallback), never re-read; `price`/`pct_1h`/`pct_24h`/
  `volatility` come from that series every minute.
- **`ranking:control`** — a Redis control channel that switches the active ranking
  mode between `"volume"` (default) and `"volatility"`. Global and last-write-wins.
  - Publisher: `data_api`'s `PUT /api/rankings/mode` (body `{"mode": "volume" | "volatility"}`,
    anything else a 422), driven by the web rankings page's Volume/Volatility control. It
    publishes `json.dumps({"mode": mode})` -- exactly `{"mode": "volatility"}` /
    `{"mode": "volume"}`, byte-identical to the retired TUI `m` key
    (`data_api/tests/test_rankings_mode.py`). A publish no subscriber received (the engine is
    down) or a Redis error is a 503, shown on the page.
  - Consumer: `RankingEngine.switch_mode` (`ranking/application/engine.py`), which reads only `mode`
    and logs-and-ignores an unknown one. The page shows the new mode only once
    `rankings:live` carries it (publish-and-wait, never optimistic).
  `[amended 2026-09-26: Story 25.1a -- the publisher moved from `bot_tui/ranking_state.py`
  (deleted) to `data_api`]`

### 3.2 What's computed, per instrument, on every `snapshots:raw` batch

`RankingBoard.ingest` (`ranking/domain/board.py`) feeds every incoming snapshot into the
instrument's `InstrumentMetrics` -- long-lived per-instrument indicator instances:

- **`VolatilityTracker`** (`ranking/domain/volatility.py`) → `volatility_score`, labelled **"Vol 1h
  σ (mids)"**: the sample standard deviation (ddof=1, `statistics.stdev`) of consecutive mid-price
  percentage returns over an age-based (not fixed-length) window -- every mid with
  `ts_event >= latest - 3600 s`, both ends kept -- None under two returns. Age-based eviction specifically because a
  fixed-length deque would silently shrink its effective time span if the snapshot
  rate varies.
- **`MultiLevelOFI(levels=10, window=50, zscore_window=3600)`** → `ofi_10_z`
- **`MultiLevelOFI(levels=n, window=300)` for n in (3, 5, 10)** → `ofi_3`, `ofi_5`,
  `ofi_10` (raw, unscored)
- **`MultiLevelOBI(levels=n)` for n in (3, 5, 10)** → `obi_3`, `obi_5`, `obi_10`
- A 300-entry rolling window of decoded snapshot dicts (`DydxSecondSnapshot.as_floats()`) per instrument (`InstrumentMetrics.rolling`) —
  feeds `trade_aggregates` (§2.1) for CVD/`avg_trade_size`, and `volatility_fast`: the sample
  standard deviation (ddof=1) of the pct returns of the last 300 mids. Only a two-sided book with a
  positive mid enters the window or any tracker.
- **The three volatilities**, named by what they measure (Story 31.3): `volatility` = 24 h of trade
  closes, ddof=0 (§3.3); `volatility_score` = 1 h of mids, ddof=1; `volatility_fast` = the last 300
  mids, ddof=1.
- The reconnect-gap guard is the one rule of §2.2 (`kernel.indicators.OFI_GAP_NS` = 3 s, strict `>`
  on `ts_event`), so a stale pre-gap price never gets diffed against a fresh one.

### 3.3 The published `rankings:live` message

`RankingBoard.current_ranks()` builds one row per instrument that has had a
snapshot within the last 30 seconds (`STALE_NS`, reusing OBS-01's
"pipeline failure, not quiet market" threshold verbatim; stamped on arrival). An instrument
silent for longer is listed in `stale_instrument_ids` for an hour, then aged out (its state
dropped, `RankingBoard.age_out`, Story 25.2). Each row combines:

- Identity fields, first in the entry and in this order: `instrument_id`; `venue`
  (`kernel.venues.venue_of`: `BYBIT`); `symbol`, the base coin (`kernel.venues.base_symbol`:
  `BTCUSDT-LINEAR.BYBIT` -> `BTC`, `km:US500-USD-PERP.HYPERLIQUID` -> `km:US500`; a Bybit head
  with an unlisted quote such as `ETHBTC` is kept whole, never guessed); `venue_kind`
  (`cex`/`dex`); `market` (`perp`/`spot`). `rank` comes last (below). All are derived from the id
  on every publish (SIGNAL-01): `metrics.db` stores none of them. `symbol` was added in Story
  29.1 as an added field only -- every earlier field keeps its bytes and order
  (`ranking/tests/test_replay.py` strips it and re-hashes against the 25.2 recording); a message
  from an older producer has no `symbol`, and the web page shows `—` for it.
  `[amended 2026-09-28: Story 29.1]`

- Live-tick fields from §3.2's indicators (`InstrumentMetrics.fast_metrics`):
  `ofi_10_z`, `ofi_3/5/10`, `obi_3/5/10`, `microprice`, `microprice_lean`
  (`microprice - mid`), `spread`, `cvd` (`buy_vol - sell_vol` from the rolling
  window; None while the window holds no snapshot, never 0), `volume_delta` (`buy_volume -
  sell_volume` over the last 60 snapshots; None likewise), `buy_count`/`sell_count` (the empty
  sums, 0), `avg_trade_size` (None at zero trades), `volatility_fast`, `price` (the live mid, or
  None with no two-sided book yet -- Story 31.3 deleted the fallback to the slow loop's trade
  close, a different quantity).
- Slow fields folded in from the last slow-loop pass (at most 3 minutes old, else null): `pct_1h`,
  `pct_24h`, `pct_1w`, `pct_1m`, `volatility` (the formula `ranking/domain/metrics.py`'s
  `price_stats_from_series`, the only one in `platform/`), all over trade closes:
  - `pct_1h`/`pct_24h` = `(latest - base) / base * 100`, `base` the first close at or after
    `latest_ts - H`; None when the series does not reach back to that cutoff, and (Story 31.3)
    when `base` lies more than `PCT_MAX_SHORTFALL_NS` = 300 s past it -- a gap at the cutoff would
    otherwise shorten the horizon silently. **Known limit:** a quiet market with no trade within
    300 s after the cutoff (an illiquid spot pair) reads None although the price at the cutoff is
    known; upgrade path: an as-of base, the last close at or before the cutoff, bounded.
  - `pct_1w`/`pct_1m` = `pct_change_from(price, base)`, `base` the newest `metrics.db` price at or
    before `now - 7/30 days` and within 1 h of it (`price_near_days_ago`); None otherwise or at a
    base of 0.
  - `volatility`, labelled **"Vol 24h σ (trade closes)"**: population standard deviation (ddof=0)
    of consecutive close pct returns over the closes within 24 h of the latest (the series is
    kept 25 h as a backfill margin; Story 31.3 cut the stdev to the 24 h its label names); None
    under two returns.
- `volume24h` and `volatility_score` — always both present regardless of active mode.
  `volume24h` is the venue's own USD 24 h volume (Story 22.10) and is `null` when that
  venue has no current volume for the instrument; such a row is left out of volume mode
  entirely (never ranked at 0) and appears only in volatility mode. Since Story 25.2 this
  includes a dYdX market whose `volume24H` field is absent, null, empty or non-finite: it is
  left out and counted at `ranking_engine.volume24h`, no longer read as 0.
- **`rank`** — 1-indexed position after sorting all rows by the active mode's score
  descending: `volatility_score` if mode is `"volatility"`, else `volume24h`
  (`board.py`). This is the actual ranking: **default mode ranks
  instruments purely by 24-hour USD volume**; switching mode re-sorts the identical
  row set by the cross-sectional volatility stdev instead. No other field in the row
  affects sort order — OFI/OBI/CVD/etc. are informational columns on the ranked row,
  not ranking inputs themselves. **Known limit:** volatility mode sorts a row whose
  `volatility_score` is None (under two returns) as 0, i.e. last, while publishing None; pinned by
  `ranking/tests/test_board.py::test_volatility_mode_sorts_an_unscored_row_as_zero_known_limit`.
  Upgrade path: sort None rows after every scored row explicitly.

Published to Redis channel `rankings:live` whenever a representative subset of fields
changes (`RankingsPublisher._ranks_key`: instrument_id, rank, `ofi_10_z`, `spread`,
`cvd`, `microprice`, `price`) or at least every `RANKING_HEARTBEAT_SECONDS` (default
5s) regardless, so a quiet market still gets a heartbeat.

### 3.4 Persistence (`ranking/infrastructure/metrics_store.py`)

Every `db_write_interval_seconds` (60s), `RankingEngine.slow_loop_once` merges the current rank +
`volume24h` into that pass's snapshots and writes them to a SQLite table through
`SqliteMetricsStore`, the store's only writer; other processes read it through
`ranking.application.queries` (`history`/`nearest`, read-only connections), columns: `price`, `pct_1h`, `pct_24h`, `pct_1w`, `pct_1m`,
`volatility`, `ofi`, `microprice`, `spread`, `rank`, `volume24h` — a 31-day rolling
history used by the dashboard's per-coin history page. Its `price` column is the slow loop's
latest trade close (the `pct_1w`/`pct_1m` base), not the rank entry's live mid. `nearest(ts)`
(`/api/metrics/nearest/{symbol}`) returns the row closest to `ts` only within
`NEAREST_TOLERANCE_S` = 120 s (two write intervals); a farther row is another time, so the answer is
None -- as for no rows -- never a stale row (Story 31.3). Note this stored `ofi` column
is the live raw `ofi_5` (`InstrumentMetrics.book_metrics`, the same tracker the rank entry
reads -- SSOT-02), not the z-scored `ofi_10_z` the live ranking table leads with.

### 3.5 What the ranking is used for

`data_api`'s `GET /api/rankings` (a verbatim passthrough, `data_api/routes/rankings.py`)
and its `/ws/live` relay feed the web rankings page, which renders `rankings:live` directly, row
order and column values unchanged (the web page is the only renderer since Story 25.1a deleted
`bot_tui`'s Coins pane) — no independent computation on the read side. No code path in
`platform/bots/` (the bots context, the actual trading bots) imports `ranking` or reads
`rankings:live` — bots are configured independently, not auto-selected from the live
ranking. The ranking's current, only
confirmed consumer is the human-facing web dashboard's coin-picker UI, not an automated
trading decision.

### 3.6 The published `markets:live` message (Story 29.5)

`[amended 2026-09-29: Story 29.5 -- new channel]` Not a ranking and not market data: each venue's
list of market **names**, for `bot_tui`'s Collector-pane market browser (`/`), so the operator
can find and add a coin without looking its id up elsewhere.

- **Publisher:** `RankingEngine.publish_markets` (`ranking/application/engine.py`), at the end of
  every volume cycle (`volume_poll_seconds`, 60 s), after `refresh_volumes`. The list is the
  union of that venue's volume sources (§3.1) still fresh by `refresh_volumes`'s own rule (a
  source's last good poll younger than `volume_max_age_ns`, 3 missed polls):
  `RankingBoard.venue_markets`. Bybit's linear and spot sources go in one `BYBIT` message. A venue
  with no fresh source publishes nothing (DATA-01) -- never an expired list. Because the lists come
  from the volume sources, they carry what those sources keep: Bybit spot only for USDT/USDC
  quotes, and never a market whose volume its source could not parse (ledgered at
  `ranking_engine.volume24h`, left out). `ranking/__main__.py` wires the channel
  as a second `RedisLivePublisher` on the engine's client.
- **Shape:** one `json.dumps` message per venue per cycle, keys in this order:
  `{"venue": "BYBIT", "ts": <the cycle's now_ns>, "markets": [{"instrument_id":
  "BTCUSDT-LINEAR.BYBIT", "symbol": "BTC"}, ...]}`, `markets` sorted by id. `symbol` is
  `kernel.venues.base_symbol`, derived on publish and stored nowhere (SIGNAL-01); `venue` is the
  `kernel.venues` code. Names only: no volume, price or other metric (operator decision
  2026-09-26), so `bot_tui` reading it re-grows no ranking view (`bot_tui/tests/
  test_no_rankings_feed.py`).
- **Failures:** each venue's publish is its own try: a failure is ledgered at
  `ranking_engine.markets` and the other venues' messages still go out; it never stops the volume
  cycle. An id `base_symbol` cannot name (no `.VENUE` suffix) is ledgered at the same site and left
  out.
- **Reader and freshness:** `bot_tui/markets_state.py` keeps each venue's newest message, validated
  whole (a `markets` that is not a list, an entry without a string `instrument_id` and `symbol`,
  or an id of another venue rejects the message with a WARNING, and the last good list is kept).
  By arrival time, a venue's rows read stale (`~ `) after `MARKETS_STALE_SECONDS` = 180 s (three
  missed polls) and the venue leaves the browser after `MARKETS_EXPIRE_SECONDS` = 900 s -- never
  earlier. Redis pub/sub keeps no history, so a TUI started between two cycles waits up to 60 s
  for the first list ("waiting for markets:live…").

---

## 4. Data lineage: raw field → signal → ranking

| Raw field (§1) | Computed signal (§2) | In `rankings:live` (§3) | Notes |
|---|---|---|---|
| `DydxSecondSnapshot.bid/ask_prices[0]`, `bid/ask_sizes[0]` | `microprice()`, `spread()`, `mid_price()` | `microprice`, `microprice_lean`, `spread`, `price` | pure functions, `indicators.py` |
| `DydxSecondSnapshot.bid/ask_prices[:N]`, `bid/ask_sizes[:N]` | `MultiLevelOFI`, `MultiLevelOBI` | `ofi_10_z`, `ofi_3/5/10`, `obi_3/5/10` | the `ranking_engine` service (`ranking/`) is the only live runner of these classes |
| `DydxSecondSnapshot.buy_volume`/`sell_volume`/`buy_count`/`sell_count` | `trade_aggregates()`, `volume_delta()` | `cvd`, `volume_delta`, `avg_trade_size`, `buy_count`, `sell_count` | |
| `DydxSecondSnapshot` mid-price sequence | `VolatilityTracker` (3600s cross-sectional) | `volatility_score` | **this is the sort key when mode = `"volatility"`** |
| `DydxSecondSnapshot` mid-price sequence (300-tick window) | `statistics.stdev` fast volatility | `volatility_fast` | separate from `volatility_score` and catalog `volatility` — 3 distinct volatility numbers by design |
| `DydxSecondSnapshot.close_price` (25h lookback; `TradeTick` pre-cutover) | `ranking.domain.metrics.price_stats_from_series()` → `pct_change_1h/24h`, catalog `volatility` | `pct_1h`, `pct_24h`, `volatility` | over the in-memory `PriceSeriesStore` (fed live, backfilled once per instrument from the catalog), refreshed every 60s by the ranking slow loop. `pct_1w`/`pct_1m` come from `metrics_store`'s persisted prices (`price_near_days_ago`), `None` until 7/30 days of history exist |
| dYdX indexer `volume24H`, Bybit v5 tickers `turnover24h` (linear; spot USDT/USDC-quoted only), Hyperliquid `metaAndAssetCtxs` `dayNtlVlm` (independent polls in `ranking/infrastructure/volume_*`) | — (used as-is, USD) | `volume24h` | **this is the sort key when mode = `"volume"` (default)**; an instrument with no volume is absent from that mode and counted at `ranking_engine.volume24h` |
| `OrderBookDeltas` | `book_features.py`, `chart_data.py`, `footprint.py` | *not present* | chart-page-only; never reaches `ranking_engine` |
| `MarkPriceUpdate` / `IndexPriceUpdate` | — | *not present* | research notebook frames only (`CatalogFrames.mark_index`, Story 27.1); `ranking` no longer backfills prices from marks (Story 31.3) |
| `FundingRateUpdate` | — | *not present* | research notebook frames only (`CatalogFrames.funding`, Story 27.1) |
| `InstrumentStatus` | — | *not present* | stored, no downstream reader found |
| `OpenInterest` (stored) | — | *not present* | research notebook frames only (`CatalogFrames.open_interest`, Story 27.1) — only the *parallel* `volume24H`-based liquidity classification (not this field) affects anything live |

**Bottom line:** the live ranking table's actual sort key is either raw 24h USD
volume or a 1-hour cross-sectional volatility stdev — both computed from data outside
or adjacent to the book-level signal machinery. Every OFI/OBI/CVD/microprice column
visible on the ranking table is informational, derived from `DydxSecondSnapshot`
alone, and does not itself move an instrument's rank. Of the raw types collected today,
`InstrumentStatus` has no downstream consumer anywhere in `views/`, `ranking/` or `research/`,
and `FundingRateUpdate` and the `open_interest` field of `OpenInterest` are read only by
research's notebook frames (`research.application.frames`), never by a live signal or ranking
`[amended 2026-09-28: Story 27.1]`.

---

## 5. Retention: how long is each type kept, and why the catalog keeps growing

Every catalog file that leaves the archive is chosen by one policy,
`archive.domain.retention.RetentionPolicy`, and deleted by one executor,
`archive.application.prune` through `CatalogFiles.delete` (Story 25.1; before it the dYdX
collector ran a second pruner of its own, `_prune_loop`, under no lock). It runs as
`python -m archive.prune_catalog` (the nightly's last step, and `make prune`), under the catalog
maintenance lock, and never deletes a file whose span reaches the current UTC day. Four rules;
each deletion is logged with its rule and reason. None of them bounds the data that actually
accumulates day to day for collected instruments, which is why the catalog only ever grows.

**1. Dropped instruments (`dropped_instrument`, dYdX only, `--dydx-plan`).** Every DYDX leaf whose
instrument the collection plan (`data/dydx_config.toml`'s `instruments`, mounted at
`/app/dydx_collector/config.toml` and read through
`collection_control`'s `TomlPlanStore`, over the one venue loader) does not collect loses every data type except `trade_tick`
once its files end more than `non_config_retain_hours` ago -- **currently `4` hours**. This is why
`crypto_perpetual`/`instrument_status`/etc. exist for ~140 markets on disk even though only the
configured ones are subscribed: the markets channel is global, so mark/index price, funding rate
and instrument status/definitions get written for every market dYdX lists, and only the
configured ones are exempt. Known limit: it runs nightly on closed UTC days only, so the effective
floor is "the day closes, plus the next nightly" (about 24 h worst case), where the old 15-minute
loop pruned intra-day; and a dropped instrument is now any DYDX leaf not in the plan (a delisted
market's leftovers age out too), not only a market the indexer still lists. An empty plan file is
refused, never read as "collect nothing". Upgrade path: a capture-exclusive retention pass over
closed files run more often than nightly.

**2. Per-instrument raw `OrderBookDeltas` retention (`delta_retention`, dYdX only).** `retain_hours`
on an `InstrumentEntry`, only meaningful if that same entry also sets
`store_order_book_deltas = true`: its `order_book_deltas` files ending more than that many hours
ago are deleted; `None` = unlimited. **None of the 29 instruments in the current `config.toml`
set `store_order_book_deltas = true`**, so no raw deltas are being written at all right now
(`order_book_deltas/` is an empty directory) and this rule currently has nothing to do.

**3. Plain age retention (`age`, `--types T --days N`, `make prune`)** -- `make prune` targets
`order_book_deltas` at a flat **14-day** global cutoff, regardless of pinned status. Since
nothing is stored there today (see above), running it currently frees nothing. `trade_tick` is
refused in `--types`, and rule 1 skips it too: trades have their own policy (4).

**4. Trade retention, gated on reconciliation (`trade`)** (`archive.prune_catalog --candles-dir ...
--trade-retention-days 7`, run by `make nightly`): a `data/trade_tick/<iid>/` file is
deleted only when every UTC day its name spans is older than 7 days **and** that
instrument-day's `verified_days` row (`candles_<venue>.db`, written by `archive.compare_klines`,
read through the `VerifiedDays` port) is `pass` -- the `ArchiveDay` is `verified`, the only status
`released` accepts. Otherwise it is kept and listed with its reason (`unverified` / `failed`).
Each deleted trade file is first recorded as a `pruned` archive-gap marker.

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

**Bottom line:** every instrument you've configured is collected, and collected instruments
are exempt from the dropped-instrument rule. So for all 29 configured coins,
mark/index price, funding rate, open interest, instrument status, and the 1-second book
snapshots (which now also carry trade OHLC) still accumulate forever with no built-in
cap. Raw `TradeTick` is archived again since story 22.13 (§1.1), but bounded: a proven day
is released after 7 days (mechanism 4). The other unlimited types above still need
`non_config_retain_hours`-style bounding if you want them capped too.

---

## 6. Nightly maintenance: rebuild, reconcile, release (story 22.13, the `archive/` context since Story 25.1)

`make nightly VENUE=<DYDX|BYBIT|HYPERLIQUID> [DAY=YYYY-MM-DD]` (default: yesterday, UTC)
runs the `archive.nightly` saga, each step its own process (`python -m archive.<step>`), stopping
at the first failure:

1. `rebuild_seconds --apply --result-file <saga temp>` -- rewrites the day's snapshot trade
   columns from the raw archive on `ts_event` (late trades move to their exchange second; the
   arrival row is cleared). Rows before the instrument's first archived trade keep live values
   (`not covered`), and so do rows inside an archive-gap marker (`<catalog>/_archive_gaps/<iid>.jsonl`, format
   `kernel.archive_markers`:
   a trade write that failed while its snapshots landed, a quarantined or a pruned trade file) --
   the archive is known to miss trades their live values hold; those are counted `in gap`. A marker
   line that does not parse, lacks a key, or holds a non-integer or inverted span makes the rebuild
   refuse that instrument every night, naming the file and line, until the line is fixed by hand:
   guessing a span could overwrite exactly the rows the marker protects. Trades with no covered row
   are counted (`orphan trades`). The day's files are every snapshot file whose `ts_init` span
   overlaps `[D, D end + MAX_TS_INIT_SKEW_NS]` -- a row of D can be sampled after midnight (venue
   time closes second S at `S + 1 + hold_back_seconds`), so it may sit in a file that starts in
   D+1 -- and rows are chosen by `ts_event` in D. Every write keeps the rows of the current UTC day
   identical (`RewriteMode.KEEP_OPEN_DAY_ROWS`, verified against the original before the rename),
   and all of an instrument-day's changed files are staged and verified before the first rename.
   An instrument-day with two rows in one second, mixed schemas, a change to a row of the current
   UTC day (`rebuild.open_day`) or a temp failing its read-back (`rebuild.verify`) is refused and
   left untouched (exit 2, the chain continues); a rename failing part-way is `rebuild.error`
   naming how many files were replaced (the rerun completes the day). The result file
   (`{"venue", "day", "rebuilt", "refused"}`) is the run's rebuild proof -- `rebuilt` names only
   instruments with snapshot rows that day, one with none is in neither list; the saga reads it
   and a missing or unparsable one fails the step
   (`nightly.rebuild_seconds`). `--apply` on the current UTC day is always refused
   (`rebuild.open_day`, exit 1).
2. `consolidate_catalog --apply --venue --days 2` -- one file per recent closed day and data type.
3. `python -m candles.rebuild --day --venue --workers 1` -- refolds the day into
   `candles_<venue>.db` (the nightly step is still named `build_candles`).
4. `compare_klines --rebuilt-by <run id> --rebuilt <iid> ... [--not-rebuilt <iid> ...]` -- every
   traded minute
   against the venue's own 1 m klines (`archive.infrastructure.klines_<venue>`), exact integer
   units (no tolerance), parsed from the venue's decimal strings. Bybit's klines are seeded with
   the previous close, so our Bybit bars are put in that definition first (wire-verified; see
   `archive.application.reconcile_day`). Each mismatch is a `reconcile.kline_mismatch`; the verdict
   goes to `verified_days` through the `ArchiveDay` transitions (rebuilt -> verified/mismatched,
   stored `pass`/`fail`). Only instruments named by `--rebuilt` and not by `--not-rebuilt` are
   compared (an allowlist); any other instrument on the day is `reconcile.not_rebuilt` and gets no
   verdict; without `--rebuilt-by` nothing is compared or written (`reconcile.not_rebuilt`,
   exit 1). A per-instrument error (no definition, fetch error, no venue history for the day,
   unrepresentable value) is a `reconcile.error` with no verdict. Exit 2 = findings of any kind
   (the chain continues), 1 = run-level failure (stops). `--kline-source catalog` never writes
   `verified_days`.
5. `prune_catalog --apply [--dydx-plan]` -- the retention rules above (§5).

One summary line per venue (per-step outcome and seconds, peak child RSS, the run id). The
first-run measurements still owed are in `docs/DEPLOY_CHECKLIST.md`.

**Each step ledgers durably, in its own file (Story 31.8).** Every step entrypoint's `main()` --
`archive.rebuild_seconds`, `archive.consolidate_catalog`, `candles.rebuild`,
`archive.compare_klines`, `archive.prune_catalog`, and `archive.nightly` itself for a manual
`make nightly` -- calls `error_ledger.start(service=job_service(<step>, "archive", args.venue))`,
writing `<ERROR_LEDGER_DIR>/<parent>.<step>_<venue>.jsonl` for one venue's run, else
`<parent>.<step>.jsonl` (`<parent>` = the inherited `ERROR_LEDGER_SERVICE`, a `.` in it clamped
to `_` as `start()` clamps it, else `archive`): e.g. `archive.rebuild_seconds_bybit.jsonl`,
`archive.candles_rebuild_bybit.jsonl`, `archive.compare_klines_hyperliquid.jsonl`,
`archive.nightly_bybit.jsonl`, and the scheduler's catalog-wide `archive.consolidate_catalog.jsonl`.
The venue is part of the name because the scheduler runs every venue's saga back to back: in one
shared `archive.compare_klines.jsonl`, the second venue's `process_start` would hide the first
venue's mismatches from `/api/errors`' `since_start` (the latest run's errors only). The one-shot
verification tools ledger the same way, `<parent>.verify_<tool>_<venue>.jsonl` (`<parent>` else
`verification`). Before, the child processes never started the durable ledger, so every
`reconcile.kline_mismatch` reached stdout (`docker logs verify-archive`) only: the verify stack's
`archive.jsonl` held 3 lines against 4,901 mismatches of 2026-09-29 (audit D-120). A separate
file per step keeps a child's `process_start` from resetting the scheduler's since-restart window
(`service_summary`). `archive.crosscheck_errors` treats a dotted service as a one-shot job
(`error_ledger.is_job_service`): its `process_start` lines print as `runs`, not `restarts`, and never
count toward `--fail-on process_start` (every night's steps would otherwise fail the window). A
`--catalog` that does not exist is refused by `candles.rebuild` too (`archive.catalog_missing`,
exit 1), as by every archive tool -- it used to list "no instruments" and exit 0 when run
standalone (audit D-123) `[amended 2026-09-30: Story 31.8]`.

**Scheduled by the `archive` service, not cron (Story 25.1b).** `python3 -m archive.scheduler`
(compose service `archive`) runs this saga for every venue, then `consolidate_catalog`, then the
rclone backup when `backup_enabled = true` (off by default until off-site storage exists, Story
26.1b), each step its own subprocess, every night at `nightly_at` (`archive/config.toml`),
catching up missed days, and merges the current day's closed hours of the small types every
`intraday_consolidate_hours`. Its status and "run now" command are §1.13's channels. `make nightly`
stays a manual tool; the host crontab line is retired (`docs/DEPLOY_CHECKLIST.md` §1). Its cursor
is `platform/data/archive/state.json` (mounted at `/app/archive_state`), written atomically
through `CatalogFiles.write_json_atomic`:
- `last_run_day` — the last day whose scheduled run completed (drives `next_run`, so a standing
  failure is retried the next night, never in a tight loop);
- per venue, `last_success_day` — the last day of an unbroken run of days with no FAILED saga
  (drives which days the next run covers: a failed day is run again, up to `catch_up_max_days`);
- `last_run` / `last_intraday` — the last finished runs, republished on start.

It is a scheduling cursor, **not a data verdict**: reconcile and prune never read it, and
`verified_days` stays the only per-day status. An unreadable or corrupt file is ledgered
`archive.state_unreadable` and treated as no state: every slot already past counts as handled,
so the first run is the next slot and targets that slot's yesterday only
`[amended 2026-09-26: Story 25.1b -- the scheduler replaces the host cron line]`.

**Who writes what, offline (Story 25.1).** Every in-place Parquet rewrite by an archive tool --
the rebuild, the consolidation's merge, `tools.migrate_open_interest`,
`tools.migrate_snapshot_ints` (Story 30.2; it replaced `tools.normalize_snapshot_schema`),
`tools.recompress` -- is
`archive.infrastructure.catalog_files.CatalogFiles`: it writes `<file>.archive.tmp` with the
compact write settings below (Story 30.1; zstd before it), reads it back, and renames it into
place only when its full schema (Arrow metadata included), its row count and every value in
order match `[amended 2026-09-29: Story 30.1]`; a crash leaves only the temp file, which
the next run of any archive tool deletes (with the pre-25.1 `*.rebuild.tmp`,
`*.consolidate.tmp`, `*.parquet.tmp`). The only offline `ParquetDataCatalog.write_data()` callers
are `archive.backfill_bars` (venue bars, §1.3) and `archive.repair_catalog` (cleared snapshot
rows, through `delete_data_range` + `write_data`). No archive tool changes a row of the current
UTC day: a whole-file rewrite (the migration tools), merge or delete of a file whose `ts_init` span
reaches it is refused (`OpenDayWriteError`; ledgered `<tool>.open_day` and skipped), and the
rebuild's row-preserving rewrite may touch such a file only with every row whose `ts_event` lies in
the open day identical in value and order: capture is that day's one writer, and it writes each
file once and never reopens it. A report-only run of any tool takes no lock. Each collector holds a shared `flock` on
`<catalog>/.capture-<VENUE>.lock` for its whole run (pid and start time inside, informational;
never unlinked -- a killed process's flock is released by the kernel), and
`repair_catalog --apply` refuses that venue while it is held (`repair.capture_running`, exit 1);
a collector starting while a tool holds it exclusively waits and ledgers
`collector.capture_lock_wait` once. New ledger sites in Story 25.1: `reconcile.not_rebuilt`,
`repair.capture_running`, `repair.open_day`, `rebuild.open_day` on `--apply` (the site existed),
`consolidate.open_day`, `prune.open_day`, `migrate_open_interest.open_day`,
`normalize_snapshot_schema.open_day` (the tool was deleted in Story 30.2), `collector.capture_lock_wait`, `prune.bad_plan` (a plan
file that is unreadable, empty, malformed, or holds a window that is not finite hours >= 0: exit 1,
nothing pruned), `prune.error` (one file's stat/delete failed: skipped, the run goes on; with
`prune.open_day` or a `marker_failed` keep, the run exits 2),
`repair.error` (an instrument id with no venue, or a venue `kernel.venues` does not know: refused,
never repaired without its capture lock), `archive.catalog_missing` (any archive tool given a
catalog directory that does not exist: exit 1, nothing done), `migrate_open_interest.error` and
`normalize_snapshot_schema.error` (one file failed -- unreadable, refused, a failed read-back or
an I/O error: left as it was, the run goes on, exit 2; the latter tool deleted in Story 30.2)
and `nightly.dydx_plan_missing` (a DYDX saga without `--dydx-plan`: the chain runs, plan retention
is not applied, the outcome is findings). A trade file whose `pruned` marker could not be written is
kept (`kept <iid> <day>: marker_failed`; the failure itself is `archive_gaps.write`), an unknown
`verified_days` status keeps its day's files (`unknown_status:<s>`), and a dYdX instrument with any
file reaching the current UTC day is never treated as dropped (a torn read of the plan file, which
control rewrites in place, must not delete a collected coin's history). Every rewrite fsyncs the
temp file before its rename and the directory after it, and before any source or file removal.

### Write settings (Story 30.1)

Every file archive writes itself -- nightly and intraday consolidation merges, the nightly
snapshot rebuild, the migration tools, `archive.tools.recompress` -- takes its Parquet write
options from one function, `archive.infrastructure.compact_parquet.compact_write_options`, the only place they
are chosen (`platform/CLAUDE.md` DATA-05); `CatalogFiles` is the only caller that writes with them.
Capture's live minute files keep the encoding of Nautilus's own `write_data` (FORK-01;
`kernel.parquet_compat` only makes it zstd), as do the files of the two offline `write_data`
callers above (`archive.backfill_bars`, `archive.repair_catalog`), so a file gets these settings
when archive merges or rewrites it.

| Setting | Value | Why |
|---|---|---|
| compression | zstd, level `COMPACT_ZSTD_LEVEL` = 16 | the most compact level inside the CPU budget (below) |
| integer timestamp columns (`ts_*`, `*_ns`: `ts_event`, `ts_init`, funding's `next_funding_ns`) | `DELTA_BINARY_PACKED`, dictionary off | consecutive nanosecond stamps differ by about a second: the deltas pack into a few bits, a dictionary of unique 64-bit values only adds a page |
| every other leaf column | dictionary on, named by its Parquet leaf path (`bid_prices.list.element`, a struct child as `<name>.<child>`) | pyarrow silently leaves a nested column PLAIN when only its top-level name is given; an unknown nesting (map, union) raises instead. Booleans are bit-packed by Parquet whatever is asked |
| row groups | one per instrument-day, capped at `_MAX_ROW_GROUP_ROWS` = 1,048,576 rows | a bigger day splits, each group with its own statistics |
| statistics | on | `ts_event`/`ts_init` filter pushdown still prunes row groups |

**Measured** (epic 30, one consolidated Bybit BTC day, zstd level 19), bytes per row from the
pre-story default zstd write to these settings: second snapshot 96.8 -> 59.2 (-39 %), mark price
22.3 -> 10.7 (-52 %), index price 21.4 -> 9.4 (-56 %), funding rate 22.4 -> 12.3 (-45 %), trade tick
19.9 -> 14.1 (-29 %). **Rejected by measurement, not to be reintroduced:** `BYTE_STREAM_SPLIT` on
the float book columns (+43 %), float32 book columns (no gain after zstd, and lossy), dropping
`ts_event` (on Bybit/Hyperliquid it is the exchange second, 1.0-5.2 s from `ts_init`, and cannot be
recovered).

**Level and CPU budget (Story 30.1, synthetic instrument-day: 86,340 snapshot rows, 172,264 trade
ticks, 86,400 mark prices, each first written as 1,440 minute files through `write_data`).** Pure
write time of the consolidated day with these options, default zstd level vs 19: trade ticks
0.025 s vs 0.513 s (20.7x), mark prices 0.004 s vs 0.059 s (14.5x), snapshot 0.164 s vs 1.218 s
(7.4x). Over the epic's 10x budget, so the level is the smallest one within 2 % of level 19's size
for every type: 16 (snapshot 8,914,680 B vs 8,793,072 B at 19, +1.4 %, where 15 is +3.1 %; trade
ticks -0.7 %; mark prices +0.5 %), at 0.848 s / 0.267 s / 0.033 s. The consolidate step on that
day (`python -m archive.consolidate_catalog --apply`, 4,321 minute files -> 5, separate process
per run, two runs each): before this story 4.1-4.2 s wall, peak RSS 479-482 MB, 15.6 MB out;
with it 5.2-5.3 s wall, peak RSS 547-570 MB, 10.3 MB out. Merged-file bytes per row there, before
-> after: snapshot 131.3 -> 103.3 (-21 %), trade ticks 20.9 -> 9.5 (-55 %), mark prices 15.9 -> 2.5
(-84 %) -- synthetic values (random book sizes, a regular price walk), so the epic's real-day
numbers above are the reference for the saving; the time and memory costs are what this measured.

**Verification is value-level.** Before the rename, the temp file is read back and compared with
the table it was written from: full schema and metadata, row count, and every value in order
(`ChunkedArray.equals`, one column of one row group at a time so the check never holds a second
copy of the day -- a whole-table read-back measured +140 MB peak RSS). A mismatch is
`RewriteVerifyError` ("values changed"): the temp is removed and the original kept. The check is
what makes a new encoding safe to adopt: a lossy option fails it before anything is replaced. A
NaN would fail it too (NaN is unequal to itself); no catalog writer stores one (Known limit in
`catalog_files._read_back_mismatch`).

**Files written before these settings: `python -m archive.tools.recompress`.** Report-only by
default (no lock, nothing written: per data type the files in scope, their bytes and the bytes
they would take, measured by an in-memory write, `catalog_files.encoded_size`); `--apply` holds
the maintenance flock and rewrites each closed-day, not-yet-compact file through
`CatalogFiles.rewrite`, one file in memory at a time; `--venue V`, `--type T` (repeatable, a
directory under `data/`) narrow it. A file reaching the current UTC day and an already-compact
file (its `ts_event` chunk is `DELTA_BINARY_PACKED`, which `write_data` never produces) are
skipped and counted, so a second run is a no-op. Each ends with one line per type and a total:
files, MB before -> after, % saved, wall seconds, peak RSS. A file failing on its own, or a leaf
that cannot be listed or cleaned of its temps, is `recompress.error` (left as it was, the run goes
on, exit 2); a file gone between the listing and its read (a lock-free report racing a
consolidation's merge) is counted as vanished, not a failure; a missing catalog or a held lock is
exit 1. `is_compact` checks the timestamp encoding only, so a later change of the level or the
dictionary set would not be picked up by a rerun (Known limit in `compact_parquet.is_compact`).
`bar` leaves are never merged nor recompressed (their file intervals are `backfill_bars`'
coverage record) and keep Nautilus's encoding. **Known limit:** consolidation merges only a closed day holding more than one file and
never a midnight-crossing file, so a closed day already one file, or a file crossing midnight,
keeps Nautilus's encoding until the next recompress run; upgrade path: the `archive` service runs
recompress over closed days after its nightly consolidation. The VPS run and its before/after
totals are a deferred operator action (`docs/DEPLOY_CHECKLIST.md`), recorded in
`docs/DATA_INTEGRITY_AUDIT.md`.

### Exact snapshot integers: the migration and the rebuild (Story 30.2)

`python -m archive.tools.migrate_snapshot_ints --catalog <root> [--apply] [--venue V]` rewrites the
closed-day float-layout `DydxSecondSnapshot` files in the integer layout (§1.7). Report-only
without `--apply` (no lock, nothing written; the whole conversion and its checks run in memory
and the "after" bytes are projected); with it, under the maintenance flock, through
`CatalogFiles.rewrite` with the compact write settings above. Per file, one at a time (MEM-01;
about 0.3 GB above the interpreter and ~10 s for a full 86,400-row, 20-level day), converted and
checked 8,192 rows at a time:

- each row's precisions are those of the catalog's own instrument definition with the greatest
  `ts_init` not after the row's `ts_event` (`ParquetDataCatalog.instruments`, every stored
  definition); a row older than all of them refuses the file (`migrate_snapshot_ints.error`);
- every float is snapped to its nearest unit `rint(v · 10^p)` only when it lies within float
  noise of that unit's exact value: at most **1024 ULP** of the value (capture's `as_double()` is
  within 2 ULP; a pre-22.13 float-summed volume within one ULP per trade) and never more than
  **0.001 unit** (the bar `archive.domain.reconciliation.float_units` uses). A value carrying a
  real digit finer than the precision (`100.00001` at p=1) is far outside that, as is a
  non-finite value or one too large for a double to resolve 0.001 unit (about 2.2e12 units):
  the file is refused and ledgered once as `migrate_snapshot_ints.off_grid` naming the file and
  the value (never rounded);
- book prices are gap-encoded by the kernel (`encode_book_prices`), a pre-OHLC file gets null
  OHLC (what `normalize_snapshot_schema`, deleted, used to do for D-24);
- the new rows are decoded back through `DydxSecondSnapshot.from_dict` and every book, OHLC and
  volume value must lie within the snap bar of the original float, every other column identical,
  before `CatalogFiles.rewrite` (which verifies its own read-back) replaces the file.

Integer files are counted and skipped (a rerun is a no-op), a file whose span reaches the current
UTC day is counted and skipped (a later run takes it). The report prints per venue: files, rows,
snapped values (floats that were not already `unit_float` of their units: the removed noise),
bytes before -> after. Exit 0 done, 1 missing catalog or held lock, 2 any refused/failed file.

The rebuild (`rebuild_seconds`) folds each second to exact `SecondTradeFields`, encodes it at that
row's own `price_precision`/`size_precision` and compares and writes integers -- no float, no
tolerance. It refuses an instrument-day with a float-layout file (`rebuild.legacy_layout`: migrate,
then rebuild the day again) or a trade finer than its row's precision (`rebuild.off_grid`).

New ledger sites in Story 30.2: `collector.unencodable` (capture: a second not stored because its
row cannot be encoded exactly; one line per instrument per minute), `migrate_snapshot_ints.off_grid`,
`migrate_snapshot_ints.error`, `rebuild.legacy_layout`, `rebuild.off_grid`.
