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
| D-14 | Restart loses the minute in progress; first minute of a mid-minute start is not emitted | Idempotent `backfill_minute_rollup` (DATA-05) | DOCUMENTED |
| D-15 | Rollup `partial_start` does not cover gap-created partial minutes | Epic-17 review ledger. With restarts every ~20 min this is hit often, so wide candles can be understated at gaps | MITIGATED (Story 21.1/21.2): rollup-sourced candles carry `partial: true` when observed < 90% of their span (`ml_signals.candles.PARTIAL_OBSERVED_FRACTION`). Not a fix: the missing seconds stay missing; the flag is on the wire only, the frontend does not draw it differently (ANSI 16-colour palette) |
| D-16 | A rollup inherits any bad raw row | `repair_catalog` regenerates affected minutes | FIXED once D-03 is run |
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
5. Watch logs for `Dropped subscribe-time trade history`, `Dropped duplicate trades`, and
   `IMPOSSIBLE trade OHLC` (must stay absent).
