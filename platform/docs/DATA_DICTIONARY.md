# Data Dictionary: Collection → Signals → Ranking

What the dYdX collector stores, what `views`/`ranking` compute from it, and
how a value traces from raw feed to the ranking table. All file:line references are
against the `bmad` branch as of 2026-09-05.

Every "error ledger" site named below (`collector.late_trade`, `collector.trade_backfill`,
`collector.candle_store`, ...) is recorded through `observability.error_ledger.record`
(Story 23.1; formerly `ml_signals.error_ledger`, whose shim Story 24.1 deleted). The sites, their
names and what they count are unchanged `[re-cited 2026-09-21: Story 23.1]`, with one addition:
`archive_gaps.inverted_span` counts a gap marker whose `from_ns > to_ns` — a backward wall-clock
step between a lost trade's arrival and the flush. Since Story 26.1 every capture site is a constant in `collector_core/sites.py` and `Collector._ledger` is capture's only `record` call; it added `collector.empty_top` (a book with no best bid or ask: seconds skipped, one WARNING and one ledger line per instrument per minute) `[amended 2026-09-26: Story 26.1]`. The marker is written as the ordered span and
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
  `archive.rebuild_seconds` re-derives closed days from this archive (§6).
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
`[last archived ts_event - 5 s, now]` over stdlib REST (each venue's `<venue>_collector/trade_history.py`, a `VenueTradeHistory` `[amended 2026-09-26: Story 26.1]`).
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

### 1.5 `IndexPriceUpdate` (native Nautilus type)

- Same channel/path/precision-fix as mark price (`client.py`). Oracle index
  price, distinct from the venue's own mark price.

### 1.6 `FundingRateUpdate` (native Nautilus type)

- **Source:** markets channel, plain pyo3-object path (`client.py`), forwarded
  via `FundingRateUpdate.from_pyo3` with no transformation.
- **Fields:** `instrument_id`, funding rate value, `ts_event`, `ts_init`.
- **Downstream use:** **none found.** Stored to the catalog but no file in
  `views/`, `ranking/` or `research/` reads `FundingRateUpdate` — dead data as of this
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
    book columns and timestamps are never touched. `candles.domain.fold.fold_arrays`
    combines these across multiple seconds for coarser candles (§2.5);
    `ranking/infrastructure/catalog_prices.py` (the ranking price backfill) reads `close_price` as its primary
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
  (`ranking`'s `InstrumentMetrics.last_seen_ns`), so that lag never reads as a stale feed. `hold_back_seconds`
  is set per venue from `python -m archive.tools.measure_lag` (the per-kind distribution of
  `ts_init - ts_event`); it only makes fewer trades late, the rebuild is what makes a second
  correct (audit D-50).
- **Built by:** `collector_core/collector.py`'s `Collector._second_loop` (the gate itself is `collector_core/domain/sampler.py`'s `SecondSampler` `[amended 2026-09-26: Story 26.1]`), every
  `snapshot_interval_seconds` (config default 1.0s, overrideable in `config.toml`), on a
  drift-free wall-clock schedule at mid-interval (`_next_sample_at`): exactly one row per
  floor second, which the rebuild's trade-to-row mapping relies on. Venue mode
  (`_venue_second_loop`) instead closes each exchange second at wall
  `S + 1 + hold_back_seconds`; after a stall it closes every overdue second (at most 60) in
  order, each from the book as of its own end.
- **Guards before emission:** skips a missing book and an empty top of book (no best bid or
  ask; rate-limited WARNING + `collector.empty_top` since Story 26.1), skips crossed books (the
  venue's `CrossedBookPolicy` -- on dYdX the active uncross first, then a forced resync after a
  persistent cross; on Bybit a forced resync past `crossed_resync_seconds`) `[amended 2026-09-26: Story 26.1]`, and skips
  stale books with no `OrderBookDeltas` for `config.stale_book_seconds` (5s; in venue mode
  measured from the last applied delta's `ts_event` to the end of the second, the feed-dead
  test staying on arrival) — both are
  `platform/CLAUDE.md` DATA-01 "flag the gap, never fabricate" implementations.
- **Scope:** pinned + liquid instruments only. Illiquid instruments get no snapshots.
- **Dual delivery:** written to the catalog via the normal buffer/flush path
  (`Collector._sample_tick` appends it to the flush buffer, `collector.py`) **and** published
  live to Redis channel `snapshots:raw` (`publish_snapshot_batch`,
  `collector_core/infrastructure/redis_stream.py`) `[amended 2026-09-26: Story 26.1]` —
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
  notes below describe `dydx_collector/open_interest.py`, which keeps the poll
  (`classify_liquidity` moved to `collection_control.domain.liquidity` in Story 25.4).

- **Why custom/separate:** open interest is parsed Rust-side but never forwarded to
  Python on either the REST or WS markets-channel path (`open_interest.py`
  docstring, citing `crates/adapters/dydx/src/python/{http,websocket}.rs`) — the one
  field this collector has to re-fetch itself.
- **Fields:** `instrument_id`, `open_interest` (`Decimal`, stored as a string in Arrow
  to avoid float round-tripping), `ts_event`, `ts_init`.
- **Source:** plain stdlib `urllib` poll of dYdX's public indexer
  `/v4/perpetualMarkets` REST endpoint (`fetch_markets_json`, `open_interest.py`),
  every `open_interest_poll_seconds` (config default 300s, `collector_core/config.py`'s
  `DydxConfig`).
- **Also drives liquidity tiering:** `classify_liquidity` (`collection_control/domain/
  liquidity.py`, Story 25.4) reads the *same* markets JSON response but keys off `volume24H`
  (USD), not `openInterest` (base-token units) — `platform/CLAUDE.md` OBS-03 explicitly calls out
  the token-vs-USD confusion as a past production bug. It labels each collected instrument
  liquid/illiquid on `collector:status` and alone admits a `pin_top_liquid` pin (§1.12),
  independent of storing `OpenInterest` itself.
- **Downstream use of the stored `open_interest` field:** **none found** in
  `views/`/`ranking/`/`research/` — only the *volume*-based liquidity classification
  (a separate, parallel computation in the same module) is used live. The OI Parquet
  record itself is written and retained but not read back by any of the code
  inspected. Likely intended for future backtest/research use, not currently wired
  into any live signal or ranking.

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

Not market data: the dYdX collection plan's live control surface, read and written by `bot_tui`'s
Collector pane (Story 6.1). Published language, frozen (AD-D12); the bytes are replay-tested
(`collection_control/tests/test_status_replay.py` against a pre-move recording,
`bot_tui/tests/test_collector_status_replay.py` through the TUI's reader).

- **`collector:status`** — published by `StatusPublisher` in the dYdX collector process at start,
  every `liquidity_check_seconds`, right after every control action, and within 30 s of a row's
  `pending` state changing (capture's retry applied it). One `json.dumps` message
  per planned instrument, in plan order, keys in this order:
  - `id` — the instrument id;
  - `liquid` — `true` when its USD `volume24H` is at or above the plan's `liquidity_min_oi_usd`
    (the last classification; `false` until the first one);
  - `last_trade_ts` — capture's last book update for it (wall-clock ns; `0` = never);
  - `trade_backfill` — trades recovered over REST after reconnects since start (§1.1);
  - `pending` — present, and `true`, only when the instrument is planned but capture has **not**
    applied it (its subscribe failed on the wire and is being retried, or the venue does not list
    it). Absent on an applied instrument, so every pre-25.4 row shape is unchanged.
  Then one `{"unpinned_ids": [...]}` (every `exclude` id, sorted), and on `stop`/`unpin` a
  `{"id": ..., "removed": true}` tombstone.
- **`collector:control`** — `{action, id}` published by `bot_tui`: `start` (plan `add`), `unpin`
  (stop and exclude), `stop` (plan `remove`), `pin_top_liquid` (fill the free slots under the
  30-instrument cap with the top USD-volume liquid ids, never an excluded one). A refused command
  or an unknown action logs a WARNING and changes nothing. A valid one is saved to
  `data/dydx_config.toml` (validated through the one loader first), then applied through
  `Collector.apply`, then published.

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
    kept apart so a 4-hourly merge never hides the nightly's `last_run`.

  `kind` is one of `nightly`, `catch_up`, `run_now`, `intraday`; `day` is the run's (first) UTC
  day and `days` every day it covers (a catch-up runs several, oldest first); `started`/`finished`
  are ISO-8601 UTC. Each `steps` entry is `{venue, name, exit, duration_s}`: `venue` is `null` for
  the venue-less steps (consolidate, backup), `exit` the step subprocess's exit code (0 clean,
  2 findings, anything else failed -- the archive tools' convention), `duration_s` its wall
  seconds. Readers require an object carrying `next_run` and `last_run` (neither may be
  omitted; `last_run` may be `null`), treat `next_intraday`/`running`/`last_intraday` as optional
  and ignore keys they do not know, so a key can be added without breaking them.
  Consumers:
  - `data_api`'s `GET /api/archive/status` (through `views.archive_status_bus.ArchiveStatusBus`,
    one subscriber per process): the latest valid message, 503 until one has arrived. A message
    failing the shape check keeps the previous one and is ledgered `views.archive_status`;
  - the web UI's maintenance status in the top bar (`frontend/src/components/ArchiveStatus.tsx`,
    polled every 30 s): last run day and outcome -- `ok` when every step exited 0, `findings` when
    the only non-zero exits are 2, else `FAILED` with the non-zero steps named -- its finish time,
    the next run, the running job, and a failed intraday merge;
  - `bot_tui`'s Collector pane, its last line (`collector_pane.format_archive_line`), the same
    verdicts; `~` marks it stale after 120 s without a message.
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
  history older than the store's first bucket. Each dict carries `source: "raw_1s"`.

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
(Lines mode: bid, ask, mid, `kernel.indicators.microprice`, and the CVD-weighted price
`mid + ((buy_volume - sell_volume) / (buy_volume + sell_volume)) * (ask - bid) / 2`),
`indicator_series_page` (per-bar OFI/OBI replay, microprice, spread) and `indicator_values_page`
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
(best ask - best bid, as written), `imbalance` (aggregate top-10-level book imbalance),
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
  price series, `ranking/infrastructure/catalog_prices.py` over `kernel.catalog_files`, falling
  back to mark prices when the window holds no trade), never re-read; `price`/`pct_1h`/`pct_24h`/
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

- **`VolatilityTracker`** (`ranking/domain/volatility.py`) — a *fourth*, deliberately separate
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
- A 300-entry rolling window of snapshot dicts (`DydxSecondSnapshot.to_dict`) per instrument (`InstrumentMetrics.rolling`) —
  feeds `trade_aggregates` (§2.1) for CVD/`avg_trade_size`, and a fast 300-tick
  `statistics.stdev` of mid-price returns (`volatility_fast`) — a *fifth*, separate
  volatility number, distinct from both `VolatilityTracker`'s and the catalog-derived
  one, by explicit design (`board.py`).
- A reconnect-gap guard (`OFI_GAP_NS = 3s`) clears OFI trackers' previous-tick state
  after a gap, so a stale pre-gap price never gets diffed against a fresh one.

### 3.3 The published `rankings:live` message

`RankingBoard.current_ranks()` builds one row per instrument that has had a
snapshot within the last 30 seconds (`STALE_NS`, reusing OBS-01's
"pipeline failure, not quiet market" threshold verbatim; stamped on arrival). An instrument
silent for longer is listed in `stale_instrument_ids` for an hour, then aged out (its state
dropped, `RankingBoard.age_out`, Story 25.2). Each row combines:

- Live-tick fields from §3.2's indicators (`InstrumentMetrics.fast_metrics`):
  `ofi_10_z`, `ofi_3/5/10`, `obi_3/5/10`, `microprice`, `microprice_lean`
  (`microprice - mid`), `spread`, `cvd` (`buy_vol - sell_vol` from the rolling
  window), `volume_delta` (`buy_volume - sell_volume` over the last 60 snapshots),
  `buy_count`/`sell_count`, `avg_trade_size`, `volatility_fast`, `price` (mid).
- Slow fields folded in from the last slow-loop pass (at most 3 minutes old, else null): `pct_1h`,
  `pct_24h`, `pct_1w`, `pct_1m`, `volatility` (the formula `ranking/domain/metrics.py`'s
  `price_stats_from_series`, the only one in `platform/`).
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
  not ranking inputs themselves.

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
history used by the dashboard's per-coin history page. Note this stored `ofi` column
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
`OpenInterest`) have no confirmed downstream consumer anywhere in `views/`, `ranking/`
or `research/`.

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
instrument the collection plan (`dydx_collector/config.toml`'s `instruments`, read through
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

**Scheduled by the `archive` service, not cron (Story 25.1b).** `python3 -m archive.scheduler`
(compose service `archive`) runs this saga for every venue, then `consolidate_catalog`, then the
rclone backup, each step its own subprocess, every night at `nightly_at` (`archive/config.toml`),
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
`tools.normalize_snapshot_schema` -- is `archive.infrastructure.catalog_files.CatalogFiles`: it
writes `<file>.archive.tmp` (zstd), reads it back, and renames it into place only when its full
schema (Arrow metadata included) and row count match; a crash leaves only the temp file, which
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
`normalize_snapshot_schema.open_day`, `collector.capture_lock_wait`, `prune.bad_plan` (a plan
file that is unreadable, empty, malformed, or holds a window that is not finite hours >= 0: exit 1,
nothing pruned), `prune.error` (one file's stat/delete failed: skipped, the run goes on; with
`prune.open_day` or a `marker_failed` keep, the run exits 2),
`repair.error` (an instrument id with no venue, or a venue `kernel.venues` does not know: refused,
never repaired without its capture lock), `archive.catalog_missing` (any archive tool given a
catalog directory that does not exist: exit 1, nothing done), `migrate_open_interest.error` and
`normalize_snapshot_schema.error` (one file failed -- unreadable, refused, a failed read-back or
an I/O error: left as it was, the run goes on, exit 2)
and `nightly.dydx_plan_missing` (a DYDX saga without `--dydx-plan`: the chain runs, plan retention
is not applied, the outcome is findings). A trade file whose `pruned` marker could not be written is
kept (`kept <iid> <day>: marker_failed`; the failure itself is `archive_gaps.write`), an unknown
`verified_days` status keeps its day's files (`unknown_status:<s>`), and a dYdX instrument with any
file reaching the current UTC day is never treated as dropped (a torn read of the plan file, which
control rewrites in place, must not delete a collected coin's history). Every rewrite fsyncs the
temp file before its rename and the directory after it, and before any source or file removal.
