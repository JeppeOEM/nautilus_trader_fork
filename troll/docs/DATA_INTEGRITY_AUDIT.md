# Data Integrity Audit — wrong-data dangers, old and new

Audit date: 2026-09-19. Scope: everything between dYdX and a pixel on the chart — collector
ingestion, the Parquet catalog, minute rollups, `data_api`, the frontend. Governing rules:
`troll/CLAUDE.md` DATA-01…DATA-06. Every entry states the mechanism, the evidence, the
treatment, and what is left open. "Open" is never rounded up to "fixed" (DATA-02).

## 1. Analysis of the triggering incident (fake spike candles)

**Symptom.** Candles with an ~$850 high–low range and ~21 BTC volume in a single second,
recurring every 15–40 minutes, with identical high (81694) and low (80841).

**Mechanism (proven).** dYdX's `v4_trades` `subscribed` reply carries up to 1000
*historical* trades. Verified against the live indexer on 2026-09-19: 1000 trades, oldest
17 h old, 26.27 BTC total. The Rust adapter (`parse_trade_ticks`) emits each as an ordinary
`TradeTick`; `collector._process_data` folded all of them into the current second's
OHLC/volume. Every (re)subscribe therefore stamped hours of price range and volume onto one
second, and that row flowed into 1s snapshots → minute rollups → every candle timeframe.

**Why it recurred.** The collector was being OOM-killed (`docker events`: ~10 `oom`/exit-137
events between 09:23 and 13:32), and each restart re-subscribed. Each spike landed ~6 min
after a restart (10:34→10:40, 11:26→11:32, 12:31→12:37, 12:51→12:57, 13:07→13:13).

**Independent cross-check.** The raw dYdX websocket, read with a client sharing no code with
our pipeline (`websockets` → `wss://indexer.dydx.trade/v4/ws`), reproduced the 1000-trade
replay. Not inferred from code.

**Conclusion.** Root cause identified and fixed at ingest (§2, D-01, D-02). Historical rows
remain until `repair_catalog` is run (§2, D-03). One thing is *not* proven: the exact delay
of ~6 min between restart and spike row (the replay arrives at subscribe; the first row
carrying it is emitted later). It does not affect the fix (the filter acts on trade age, not
arrival time) but is left labelled open.

## 2. Register of dangers

Status: **FIXED** (code + test), **GUARDED** (canary/detection, cause outside our control),
**OPEN** (known, not yet treated), **DOCUMENTED** (accepted, with detect/repair path).

### Ingestion

| ID | Danger | Mechanism / evidence | Treatment | Status |
|----|--------|----------------------|-----------|--------|
| D-01 | Subscribe-time trade history counted as live | Above. Proven vs raw WS | `_STALE_TRADE_NS` (10 s) filter in `_process_data`; drops counted and logged each flush | FIXED (undeployed until `make redeploy-all`) |
| D-02 | Replayed trades within the age window double-counted (short reconnect) | Reconnect resubscribes → replay of trades already counted, still < 10 s old for a brief outage | Bounded per-instrument `trade_id` dedup (2000 ids); duplicates counted, logged at WARNING | FIXED (undeployed) |
| D-03 | Old spike rows already in the catalog and rollups | Rows written by pre-fix collector | `python -m dydx_collector.repair_catalog` (report-only by default; `--apply` clears trade fields on flagged seconds, deletes their rollup minute, regenerates it via the idempotent backfill). **Not yet run on production** | OPEN until run |
| D-04 | Any future bug that writes impossible prices | Class of failure D-01 belonged to | `integrity.ohlc_outside_book`: a second's trade high/low must lie inside that second's own top-20 book range (±0.1%). Live: logged at ERROR in `_second_loop`. Offline: the same function drives D-03's scan | GUARDED |
| D-05 | BONK-USD-PERP book unparseable | 505 `Failed to parse orderbook deltas for BONK-USD … Raw value … exceeds QUANTITY_RAW_MAX` in 30 min. Sizes overflow the Rust `Quantity` in this build. Every BONK book message is dropped, so BONK has no valid book: no snapshots (the second loop skips it), candles/rankings absent or stale, and constant log/IO churn (ws raw-debug log rotates every ~20 s). Cannot be fixed here: `crates/` is off limits (FORK-01) | Unsubscribe BONK / add it to `exclude` via `collector:control` (runtime config lives on the VPS, not the repo). Any other coin with huge size scaling will fail the same way | OPEN — needs the control action on the VPS |
| D-06 | Collector OOM-killed every 15–40 min → unflushed 60 s buffer lost, gaps (BTC: 1459 of 3600 expected snapshots in an hour) | `docker events`, exit 137; host 3.8 GB with 2.9 GB used, no swap, no `mem_limit`; orphan `dydx-dashboard` container (585 MB) still running | Order-book deltas of instruments outside `_delta_store` are no longer buffered for the 60 s flush interval (they were buffered then discarded). Swap file + removing the orphan dashboard are host actions | PARTLY FIXED — effect of the buffer fix unmeasured; host actions pending |
| D-07 | Unbounded `_ingest_queue` under sustained load (collector at ~108 % CPU) | Same shape as the documented `DataEngine` OOM | None yet. Needs a queue-depth metric first; do not add a drop policy blind (DATA-05) | OPEN |
| D-08 | Crossed dYdX book | Architectural (DATA-04) | Per-level message-id uncrossing already in place; resync only as fallback | GUARDED (pre-existing) |
| D-09 | Stale book stamped as live | DATA-01 | `_STALE_BOOK_NS`, accumulators discarded on a skipped second | GUARDED (pre-existing) |
| D-10 | Detection loop itself stalls silently | DATA-02 | `second_loop` wall-clock lag canary; four `second_loop_lag` incidents today (12:22, 12:26, 12:50, 13:27) are a symptom of D-06/D-07 | GUARDED; incidents are open evidence for D-06 |
| D-11 | Open interest dropped by the Rust bindings | Known | Polled separately over REST | GUARDED (pre-existing) |
| D-12 | Mark/index price precision labels vary per tick | Known incident | `_at_fixed_precision()` | FIXED (pre-existing) |
| D-13 | `Price(decimal, precision)` returns wrong values (NAUT-01) | Known bug | Only `scaleb()` + `from_raw()` | FIXED (pre-existing) |

### Storage / derived data

| ID | Danger | Treatment | Status |
|----|--------|-----------|--------|
| D-14 | Restart loses the minute in progress; first minute of a mid-minute start is not emitted | Idempotent `backfill_minute_rollup` (DATA-05) | SUPERSEDED (D-35): rollups retired; the store has no minute-close seam |
| D-15 | Rollup `partial_start` does not cover gap-created partial minutes | Epic-17 review ledger. With restarts every ~20 min this is hit often, so wide candles can be understated at gaps | MITIGATED (Story 21.1/21.2): rollup-sourced candles carry `partial: true` when observed < 90% of their span (`ml_signals.candles.PARTIAL_OBSERVED_FRACTION`). Not a fix: the missing seconds stay missing; the flag is on the wire only, the frontend does not draw it differently (ANSI 16-colour palette) |
| D-16 | A rollup inherits any bad raw row | `repair_catalog` regenerates affected minutes | SUPERSEDED (D-35): `repair_catalog --candles-db` rebuilds the affected days in the store |
| D-17 | Non-atomic `screener_columns.toml` write; technicals params unvalidated | Epic-17 ledger | OPEN (low; not market data) |

### Read path / presentation

| ID | Danger | Treatment | Status |
|----|--------|-----------|--------|
| D-18 | Live forming bar overwrote the history bar with a partial open/high/low/**volume** (bucket start unseen by `LiveCandleBus`) → jumping candles | Story 21.3: `LiveCandleBus.seed()` fills the current bucket from the catalog on subscribe (buckets ≤ 1 h; wider stay partial), the client-side merge is deleted, and live volume now drives the volume pane | FIXED in code + tests (undeployed) |
| D-19 | Chart shows only trade-seconds; thin coins show sparse candles | By design (no fabricated values, DATA-01); gaps drawn as whitespace | DOCUMENTED |
| D-20 | `has_more=False` on an empty page truncates scroll-back on sparse coins | An in-progress, uncommitted rewrite of `routes/candles.py` (file-range paging) is addressing this; not verified by me | OPEN pending that work |
| D-21 | Chart load latency (7–9 s at 1m, >45 s at 15m on the VPS) | **Local measurement (960 MB catalog, BTC, `scripts/bench_candles.py`): 1m 1.14 s → 0.40 s, 15m 1.78 s → 0.66 s, 1h 1.63 s → 0.64 s, 4h 5.9 s → 3.0 s.** Cause: 95% of a request was the catalog decoder deserialising 20-level books nobody reads; candles now read 7 columns straight from the Parquet files (`query_second_ohlc`). VPS not measured — OPEN until measured there. Remaining: 4h+ falls back to raw 1s before the first rollup; original contributors: | Contributors: host oversubscribed (D-06), a second full catalog scan per page for `has_more`, per-request `ParquetDataCatalog`. Paging rewrite in progress | OPEN — not measured on the VPS in this pass |
| D-22 | Live-candle path: out-of-order/duplicate snapshots, unbounded queues, unbounded subscriptions, non-numeric bar guard | Epic-15.5 review ledger | OPEN (low) |
| D-23 | `NaN`/`Infinity` metric reaches the History chart | Epic-17.2 ledger | OPEN (defensive; not observed) |

### Found during Story 21.5 (new)

| ID | Danger | Evidence / treatment | Status |
|----|--------|----------------------|--------|
| D-24 | **Root cause found; migration written (Story 21.6).** `second_snapshot` Parquet files come in two schemas: 11 columns (no `open/high/low/close_price`) up to 2026-09-10, 15 columns from 2026-09-16. `ParquetDataCatalog.query()` builds `pds.dataset(file_list)` (nautilus `persistence/catalog/parquet.py:2064`), which takes its schema **from the first file** — the oldest. Any query whose file set includes an old-schema file therefore silently drops the OHLC columns of every new-schema file in it: all trade seconds come back with `None` OHLC. Reproduced with plain pyarrow (`[old,new]` → no `open_price` column; `[new,old]` → has it) and by window bisection on BTC: windows centred on 2026-09-16 lose 0 rows at ≤ 7 d and 284 at 14 d and 30 d (the 14 d window is the first to reach a pre-09-10 file). All 29 instruments have both schemas; no other data type has mixed schemas. | Candles + legacy `/catalog/candles` now use `query_second_ohlc` (per-file read, immune). Other consumers read only book/volume columns present in both schemas, or read day-sized chunks that never span the 09-10→09-16 boundary (`repair_catalog`, `backfill_minute_rollup`), so they are unaffected today — but any *future* reader of OHLC over a range crossing that boundary via the catalog will silently lose it. Durable fix: `python -m dydx_collector.normalize_snapshot_schema --catalog … [--backup-dir … --apply]` rewrites the old files with the four OHLC columns as nulls (backup required, atomic replace, idempotent, refuses unknown columns); tested by reproducing the bug on a mixed-schema fixture. Local catalog: 22,143 files need it (report-only run; NOT applied). | FIX WRITTEN; OPEN until run on the production catalog after a backup |
| D-25 | Because D-24 hid some rows, the new candle read also shows some historical spike seconds (D-01) that the old path hid by accident. They are the same rows `repair_catalog` targets | Run `repair_catalog` (D-03) | OPEN until D-03 is run |

### Story 21.6 — no hidden errors (DATA-07)

| ID | Danger | Treatment | Status |
|----|--------|-----------|--------|
| D-26 | Sites that carried on past a failure with only a log line (collector enqueue/process/flush-write/rollup/OI-poll/status/control/quarantine, live-candle decode, ranking snapshot entry + price backfill, metrics snapshot, technicals per coin, catalog reads of unreadable data types) | All now `error_ledger.record(...)`: ERROR + traceback + counted, exposed at `GET /api/errors`, shown by the frontend `ErrorBar` | FIXED (undeployed) |
| D-27 | An unparseable venue `volume24H` silently became **0** (a coin reclassified illiquid / zero-volume) | Coin left unclassified/absent + ledger error; no fabricated 0 | FIXED |
| D-28 | An impossible candle was dropped (or would have been drawn) | `/api/candles` returns HTTP 500 + ledger error; the frontend shows the failed load and the bar | FIXED |
| D-29 | One coin's failed technicals read was returned as `{}` ("no data") and cached 90 s | Response carries `errors{iid}`; failures are never cached | FIXED |
| D-30 | Collector-side ledger counts live in the collector process only (its errors are in Dozzle at ERROR, not in `/api/errors`) | — | OPEN: needs the collector to publish its counts (e.g. in `collector:status`) |
| D-06 | OOM kills | Explicitly excluded from this story | OPEN |

### Found 2026-09-19 — chart "missing bars / missing volume" deep dive

Method: `/api/candles` on the VPS (tunnel) and on the local `frontend-dev` stack, compared
minute-by-minute with dYdX's indexer candles (independent source), plus a CDP read of the
React chart's own props to prove the frontend holds what the API served.

| ID | Danger | Mechanism / evidence | Treatment | Status |
|----|--------|----------------------|-----------|--------|
| D-31 | **A trade near a minute boundary lands in the next minute** — 1m+ candles are wrong at the boundary, not just 1s. | Trades are accumulated into the *receive* second (`_process_data` → `_second_loop` pops per tick); dYdX delivers each trade 1–3 s after its `createdAt`. VPS vs indexer, 2.5 h: BTC 19:59 ours v=0.0358 c=81351 / indexer v=0.0379 c=81353, and ours has a 20:00 candle (v=0.0021, c=81353) where the indexer has **0 trades**; SOL 18:49 (1 trade, 4 SOL) absent from ours, ours 18:50 = 49 vs 45; ETH 19:33/19:34 = 0.002/0.004 vs 0.003/0.003. Rate: ETH 3/138, BTC 2/36, SOL 3/33 minutes. The spec-21-x backlog called this "visible only at 1s" — disproved. | Attribute trades to their venue second: hold each second's book sample and emit the snapshot D s later (D ≈ 5, covers the measured lag) with the trades whose `ts_event` falls in that second; count late trades (> D) as a loud canary. Costs D s of live latency for every `snapshots:raw` consumer — operator decision | OPEN — fix designed, not built |
| D-32 | **Trades during a WS outage are lost, by design.** | Same root as D-31. During an outage the book goes stale, snapshots are skipped and accumulators discarded (D-09); the resubscribe reply *does* carry the outage's trades (with venue timestamps) but D-01's 10 s filter drops them because a receive-stamped second cannot hold them honestly. Local 2026-09-19 20:16:49–20:17:30: all 29 books stale 41 s, reconnect replay dropped (1000/coin); BTC 20:16 ours 0.0002 vs indexer 0.0013 (4 trades). VPS reconnect 20:06:34: ETH 20:05 ours 0.001 vs 0.002. | D-31's venue-second attribution lets replayed outage trades be placed in their true seconds (book for those seconds stays absent — the snapshot would carry trades only, or the loss stays named). Until then: each reconnect = a known, logged loss (`Dropped subscribe-time trade history`) | OPEN |
| D-33 | **VPS catalog reset**: `dydx_collector/catalog/data` on nifelheim was born 2026-09-19 17:53:17 (`stat %w`); every data type dir has that mtime; oldest ETH file 17:53:50. All VPS chart history before then is gone (`/api/candles` → `has_more:false` at 17:54). | No backup/tar/other copy on the host; no repo tool or Makefile target deletes the catalog; `.bash_history` (last flushed 17:00) shows only `make redeploy-all` + swap setup. Cause not established. Side effect: D-03 / D-24 / D-25 are moot on the VPS (no pre-fix rows, one schema) **if** the reset was deliberate. | Operator to confirm whether the wipe was intentional. If not: it is unrecoverable; treat as a DATA-05 incident and add a catalog backup (rsync/cron) before it matters again | OPEN — needs operator answer |
| D-34 | Volume histogram "missing bars" / 1m bars "that are only a line" | Not loss. dYdX volume is tiny now (indexer 24h: ETH 2654 trades = 1.8/min, BTC 1.1/min, SOL 0.5/min; 135 of 200 BTC minutes had 0 trades), so a 1m bucket with one trade is a genuine o=h=l=c bar (ETH dojis: ours 25 vs indexer 24 over the same minutes). lightweight-charts draws a bar shorter than 1 px as a 1 px tick on the baseline (`PaneRendererHistogram`), so 0.002 ETH next to a 120 ETH bar is invisible. CDP read of the chart's props: volume bars == real candles (148/148 VPS, 240/240 local); last candle == API's last. | None needed for correctness. If wanted: log-scale the volume pane, or a per-bar min height (custom primitive) | DOCUMENTED |
| D-05 | (update) BONK book still unparseable | VPS 24 h: 4433 `Failed to parse orderbook deltas for BONK-USD … exceeds QUANTITY_RAW_MAX`; local 3 h: 2315. BONK `/api/candles` on the VPS: empty. | Unchanged: exclude BONK (and any coin whose level sizes exceed ~3.4e13 units) | OPEN |

### Found 2026-09-20 — candle store (Parquet was the read path)

| ID | Danger | Treatment | Status |
|----|--------|-----------|--------|
| D-35 | **Minute rollups retired.** Every candle read re-opened thousands of tiny Parquet files (~0.5 ms/file regardless of rows; one file per coin per flush), and the rollup did not help: same file count, 60x fewer rows. | Finished 1m..1D bars now live in SQLite (`candles.db`, `ml_signals/candle_store.py`), fed from raw 1s by the collector at :02/:32 past each minute and rebuilt with `python -m dydx_collector.build_candles`. `minute_rollup.py`, `backfill_minute_rollup.py`, the rollup read paths and `/catalog/candles` are deleted. **Existing rollup files are now unused: delete `<catalog>/data/custom_dydx_minute_rollup/` by hand when convenient (nothing reads it; the code never runs this).** | FIXED (undeployed) |
| D-36 | **Parquet file count grows without bound**: one file per coin per data type per 60 s flush, about coins x types x 1,440 per day (locally 155k files in ~19 days; a VPS running 24/7 would reach inode limits in weeks, and backup/rsync/rclone/listing cost scales with file count). | `python -m dydx_collector.consolidate_catalog --catalog … --apply` (`make consolidate`, nightly cron): merges each closed UTC day into one file per (type, instrument) with plain pyarrow -- Nautilus's `consolidate_data_by_period` is NotImplemented for `MarkPriceUpdate`/`IndexPriceUpdate`/`FundingRateUpdate` and deletes sources before any check -- one schema per day or refused (D-24), row count verified before sources are deleted, interrupted runs self-heal, today untouched. **Local copy trial (BTC, 5 types, 97 closed days): 7.7 s, 9,007 -> 351 files, 61 -> 21 MB; `query_second_ohlc` rows on closed days identical before/after (168,771 = 168,771, every field; today's files untouched).** VPS: NOT measured (Story 22.11). Existing history must be consolidated once; then nightly. | FIX WRITTEN; OPEN until scheduled on the VPS |
| D-37 | The store is fed every 30 s and can be ahead of the archive by up to that after a crash; the forming bar is up to 30 s stale (the live WebSocket bar covers it). | Documented in DATA-05; `build_candles` for the affected day drops seconds the archive lacks | DOCUMENTED |
### Story 22.1 — collector core (Bybit/Hyperliquid)

`troll/collector_core/` now owns the ingest/flush/sample/write path for the Bybit and
Hyperliquid collectors, so dYdX's venue-neutral guards run for every venue (dYdX itself
moves onto the core in Story 22.2).

| ID | Danger | Mechanism / evidence | Treatment | Status |
|----|--------|----------------------|-----------|--------|
| D-38 | Bybit/Hyperliquid had none of dYdX's ingestion guards (19.3/19.4 left them out) | Each sibling had its own ~200-line copy of the loop with no stale-trade filter, no dedup, no OHLC canary, and a bare `logger.exception(...); continue` at every drop site (DATA-07) | `collector_core.Collector`: D-01 stale-age filter (`stale_trade_seconds`, 10 s) + D-02 bounded `trade_id` dedup (2000) with drops reported each flush; D-04 `ohlc_outside_book` ERROR canary; D-09 stale-book skip with accumulator discard; crossed book = skip + `error_ledger` `collector.crossed_book` once per episode, forced resync only after `crossed_resync_seconds` and only for a client with `resync_orderbook` (Bybit; Hyperliquid's full-snapshot book never resyncs); D-10 `_second_loop` lag canary; OBS-01 watchdog; every drop site is an `error_ledger` entry. Both venues now sample at 1 s | GUARDED |
| D-39 | Subscribe-time replay on the Bybit/Hyperliquid trade channels is unverified against the raw feed | First live run on the core (2026-09-20, 100 s, mainnet): Hyperliquid's `trades` subscribe reply **does** replay history — the age filter dropped 21 (BTC) / 19 (ETH) trades at subscribe (`Dropped subscribe-time trade history`), none afterwards; Bybit's `publicTrade` dropped nothing in the same run. Neither has been compared with the raw WS payload yet, so the replay size/age bounds (and whether the 10 s filter always covers Hyperliquid's) are unproven | Verify per venue against the raw feed (Story 22.5), then record the bounds here | OPEN — Story 22.5 |

## 3. Conclusions

1. The spike candles were an **ingestion bug of ours**, not market data: the venue sends
   history on subscribe and we counted it as live. Proven against the raw feed.
2. It was made frequent by a **second, independent problem**: the collector being
   OOM-killed every 15–40 minutes. That also causes the missing-data gaps. Fixing only the
   filter would leave the gaps.
3. **BONK is a third, separate loss**: its book is unparseable in this build, so it cannot be
   captured correctly at all until the adapter changes or it is excluded.
4. Nothing above is deployed until `make redeploy-all`. Until D-03 is run the catalog still
   contains the old spikes, and until the host actions in D-06 are done OOM kills can
   continue.
5. **Not verified**: the effect of the delta-buffer change on memory; the paging rewrite's
   correctness; chart latency after any fix. These need measurement on the VPS.

## 4. Runbook (in order)

1. `make redeploy-all` — ships D-01, D-02, D-04, D-06 (buffer), D-18.
2. On the VPS: add swap, remove the orphan `dydx-dashboard` container (D-06).
3. Unsubscribe BONK via `collector:control` (D-05).
4. `python -m dydx_collector.repair_catalog --catalog /app/catalog` — read the report;
   then `--apply` (D-03). Back up `catalog/` first: it rewrites parquet files.
5. After the first deploy of the candle store: `make build-candles` (populates `candles.db` from the raw 1s archive; until then charts and technicals fall back to the slow Parquet path). Re-run after `repair_catalog --apply` for the repaired days, or run `repair_catalog` with `--candles-db`.
6. Schedule `make consolidate` nightly (D-36) and run it once by hand for existing history; then delete `<catalog>/data/custom_dydx_minute_rollup/` (D-35, unused).
7. Watch logs for `Dropped subscribe-time trade history`, `Dropped duplicate trades`, and
   `IMPOSSIBLE trade OHLC` (must stay absent).
