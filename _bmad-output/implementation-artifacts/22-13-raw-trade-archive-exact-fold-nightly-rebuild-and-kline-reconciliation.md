# Story 22.13: Raw trade archive, exact fold, nightly rebuild and kline reconciliation

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the platform operator,
I want every venue's raw trades archived with both clocks, one exact fold shared by the live loop and the rebuild, and every closed day rebuilt from trades and reconciled against the venue's klines,
so that a bar is either proven equal to the exchange's or loudly flagged, and nothing the live fold gets wrong is unrecoverable.

## Acceptance Criteria

1. **Raw trades archived, both clocks kept.** `collector_core/collector.py` `_process_data` appends each `TradeTick` that passes the stale-age filter and `trade_id` dedup to `self._buffer[(TradeTick, iid)]` *in addition to* folding it. Every flush writes `data/trade_tick/<instrument>/` through `ParquetDataCatalog.write_data()` for all three venues. `ts_event` (venue) and `ts_init` (arrival) are stored untouched. `BacktestDataConfig(data_cls=TradeTick, instrument_ids=[...])` loads them with no conversion (NAUT-03). Footprint measured on one day per venue and recorded in the audit (expected ~1 MB/venue/day on dYdX: ETH ~2.9k, BTC ~1.4k, median coin ~150 trades/day from 2026-09-20 snapshot counters).
2. **One exact fold, three callers.** `collector_core/fold.py` exposes a pure `fold_trades(trades: Sequence[TradeTick]) -> SecondTradeFields` (open/high/low/close as `Price`, buy/sell volume as `Quantity`, buy/sell counts). It accumulates `Quantity.raw` integers, compares `Price.raw` integers, and never touches a float; the caller converts to the snapshot's `float64` columns exactly once (`Quantity.as_double()` on the final sum). The live `_process_data` accumulators are replaced by a per-second `list[TradeTick]` folded once in `_sample_tick` via `fold_trades`; `rebuild_seconds` (AC 3) and any strategy feature code call the same function. Equivalence test: for 10k real `TradeTick`s the new fold equals the old float fold within one unit of the last place of the *float* result, and equals a `Decimal` sum exactly. `DydxSecondSnapshot`'s Arrow schema is unchanged (D-24, no new column).
3. **Nightly rebuild of closed days from trades.** `collector_core/rebuild_seconds.py --catalog ... --day YYYY-MM-DD [--instrument ...] [--apply]` (report-only by default): for each instrument, loads day `D`'s `trade_tick` rows and `custom_dydx_second_snapshot` rows, buckets trades by venue `ts_event` into half-open `[S, S+1)` (22.12 semantics -- this story ships them for trades), recomputes every snapshot's trade fields with `fold_trades`, leaves book columns, `ts_event` and `ts_init` untouched, and rewrites the files temp-then-rename exactly like `repair_catalog` (row count checked, one schema per day or refused). A second whose trades are all absent from the archive keeps its live values and is counted. Output per instrument: seconds rebuilt, seconds changed, seconds without trades. Idempotent (second run changes 0). Today is refused unless `--include-open-day` with the collector stopped. Then `build_candles --day D` refolds `D` into `candles_<venue>.db`.
4. **Kline reconciliation, loud.** `collector_core/compare_klines.py --venue DYDX|BYBIT|HYPERLIQUID --day D [--instrument ...] [--db candles_<venue>.db]`: fetches the venue's 1 m klines for `D` (Bybit pyo3 `BybitHttpClient.request_bars`; Hyperliquid `POST /info {"type":"candleSnapshot"}` via stdlib `urllib`; dYdX indexer `GET /v4/candles/perpetualMarkets/{ticker}?resolution=1MIN&fromISO=...&toISO=...`), or reads 22.9's backfilled `-EXTERNAL` bars if present, and compares against `candle_store.window(iid, 60, D)` bar for bar: volume as integers at the instrument's size precision (exact), OHLC as `Price.raw` (exact). It upserts `verified_days(instrument_id TEXT, day TEXT, status TEXT, checked_at INTEGER, mismatches INTEGER, PRIMARY KEY(instrument_id, day))` in `candles_<venue>.db`, records each mismatch in `error_ledger` (`reconcile.kline_mismatch`, `"{iid} {minute} vol {ours}/{theirs} ohlc {ours}/{theirs}"`), and prints the per-venue pass rate. No tolerance parameter exists (DATA-02: a mismatch is root-caused). Minutes the venue reports and we have no bar for are mismatches, not skipped.
5. **Retention: keep until proven, then release.** `collector_core/prune_catalog.py` gains `--trade-retention-days N` (default 7): a `trade_tick` day is deleted only when it is older than `N` days **and** every instrument's `verified_days` row for that day is `pass`; otherwise it is kept and listed in the report with the reason (`unverified` / `failed`). `DATA_DICTIONARY.md` gets a `Known limit:` paragraph: trades exist to correct aggregates; the window is 7 days; upgrade path is raising `N` (or disabling the prune) if tick-level features are ever wanted in backtests.
6. **One nightly job.** `make nightly VENUE=<venue> DAY=<yesterday>` runs, in order and stopping at the first failure: `rebuild_seconds --apply` -> `consolidate_catalog --apply` -> `build_candles --day` -> `compare_klines` -> `prune_catalog --apply`. One summary line per venue in the log; every failure in `error_ledger` (`nightly.<step>`). The VPS cron line is in `DEPLOY_CHECKLIST.md`. First full run timed and recorded in the audit (MEM-01: stays under the collector's headroom; the job runs as a separate process, not inside the collector).

## Tasks / Subtasks

- [ ] Task 1 — archive trades (AC: #1)
  - [ ] `collector_core/collector.py` `_process_data`: after the dedup check, `self._buffer[(TradeTick, iid)].append(data)`. Nothing else changes in the flush path (`_flush_once` already writes every buffered type).
  - [ ] Confirm the Cython `TradeTick` reaching `_on_data` writes through `write_data` (the other Cython types already do); if a venue client hands pyo3 ticks, convert with `TradeTick.from_pyo3` at the client, not in the core.
  - [ ] `collector_core/tests/test_collector.py`: a flushed `TradeTick` is readable via `ParquetDataCatalog.trade_ticks(instrument_ids=[iid])` with `ts_event`/`ts_init` equal to the input (TEST-01, real catalog).
  - [ ] `consolidate_catalog`: add `trade_tick` to the consolidated types (it walks `data/<type>/`; verify it needs no change, test it).
  - [ ] `docker-compose.yml`: nothing (same catalog volume). `DATA_DICTIONARY.md`: `trade_tick` entry.
- [ ] Task 2 — exact fold (AC: #2)
  - [ ] `collector_core/fold.py`: `SecondTradeFields` (frozen dataclass), `fold_trades`. Integer accumulation on `size.raw`; `Quantity.from_raw(total, precision)` for the result; OHLC by `ts_event` then arrival order for ties.
  - [ ] Live loop: replace `_second_open_price/_high/_low/_close/_buy_volume/_sell_volume/_buy_count/_sell_count` with `self._second_trades: dict[str, list[TradeTick]]`, folded in `_sample_tick`; keep the `None` OHLC contract for a second without trades. `ohlc_outside_book` unchanged (it reads the snapshot).
  - [ ] `ml_signals/candle_store._fold` is seconds->bars and stays; document in its docstring that trades->seconds lives in `collector_core/fold.py`.
  - [ ] Tests: `collector_core/tests/test_fold.py` (empty, one trade, buy/sell split, tie order, precision at 8 dp with sizes like `0.1 + 0.2` where float would err), plus the equivalence test in AC 2.
- [ ] Task 3 — rebuild (AC: #3)
  - [ ] `collector_core/rebuild_seconds.py`, reusing `repair_catalog`'s file rewrite helpers (extract them into `collector_core/catalog_files.py` if they are private today) and `build_candles`'s day/instrument listing.
  - [ ] Bucketing: `second = ts_event // 1_000_000_000`; trades whose second has no snapshot row are counted as `orphan_trades` (a second the collector never sampled) and reported, not silently dropped -- this is the D-14-style restart gap made visible.
  - [ ] Tests (`test_rebuild_seconds.py`, real catalog in tmp): a day with trades arriving 2 s late is rebuilt into the right second; second run changes 0; book columns byte-identical before/after; today refused.
- [ ] Task 4 — reconciliation (AC: #4)
  - [ ] `collector_core/compare_klines.py`; venue fetchers behind one `fetch_klines(venue, iid, day) -> list[Kline]` with `Kline(t_ms, o, h, l, c, v)` in raw ints at the instrument's precisions (instrument from the catalog).
  - [ ] `candle_store.py`: `verified_days` table + `mark_verified`/`verified_status` helpers; `prune` unchanged.
  - [ ] Tests: synthetic klines vs a store built from synthetic trades -> `pass`; one altered kline -> one ledger entry, `fail`, exact message; a missing bar -> mismatch.
  - [ ] Run for one day per venue on real data; record pass rate and every mismatch's cause in the audit (expect dYdX boundary cases to disappear after Task 3; anything left is 22.14's gap or a real finding).
- [ ] Task 5 — retention (AC: #5)
  - [ ] `prune_catalog.py`: `trade_tick` policy gated on `verified_days`; report lists kept days with reason.
  - [ ] Test: unverified day kept; failed day kept; passed day older than N deleted; passed day younger than N kept.
- [ ] Task 6 — nightly job (AC: #6)
  - [ ] `Makefile` `nightly` target; `DEPLOY_CHECKLIST.md` cron line; audit entry with the first VPS run's wall time, RSS and file counts.
- [ ] Task 7 — docs
  - [ ] `troll/CLAUDE.md` DATA-01: one sentence on the two clocks (`ts_event` = exchange, `ts_init` = arrival; aggregates bucket on `ts_event`, backtests replay on `ts_init`). DATA-05: the rebuild is the documented way to fill a live-fold error. `DATA_INTEGRITY_AUDIT.md`: close D-46 (float fold), D-45 (no trade archive), D-44 and D-31 for trades; state what stays open (book on dYdX, gaps -> 22.14).

## Dev Notes

### Why archive trades when the snapshot already has OHLC/volume

The snapshot is derived, not raw: every `TradeTick` is folded live and discarded, so a fold bug, a late trade, a reconnect gap or a subscribe-time replay that slips past the 10 s filter (D-44) changes history with no way back. Raw trades cost ~1 MB/venue/day on dYdX and make every aggregate rebuildable. This is the same reasoning that put the candle store *under* the 1 s snapshots (D-35): raw is truth, derived is rebuilt.

### Why the fold must be integer

Volume equality against the venue's kline is the acceptance test, and the venue sums integers. `sum(size.as_double())` over a day is not exact. `Quantity.raw` is exact; the snapshot's `float64` column then holds one conversion of an exact number, which round-trips for any realistic total. Do not change the Arrow schema (D-24 refuses mixed-schema days).

### Live is provisional, archive is final

The live loop stays arrival-timed and instant. The nightly rebuild is where exchange time, late trades and backfilled trades are applied. `verified_days` is the finality marker; nothing in the snapshot schema says "provisional". This removes the need for a hold-back in the live loop (22.12 keeps it as an optional knob only).

### What "100 %" can and cannot mean

Exact on every minute whose trades our archive holds completely. Minutes with a gap (reconnect, Hyperliquid's lack of trade history) fail reconciliation until 22.14 closes the source. Reconciliation must never be made to pass by tolerance.

### Project Structure Notes

- New: `troll/collector_core/{fold,rebuild_seconds,compare_klines}.py`, tests for each, `catalog_files.py` if extracted.
- Modified: `troll/collector_core/{collector,prune_catalog,consolidate_catalog}.py`, `troll/ml_signals/candle_store.py` (`verified_days`), `troll/Makefile`, `troll/docs/{DATA_DICTIONARY,DATA_INTEGRITY_AUDIT,DEPLOY_CHECKLIST}.md`, `troll/CLAUDE.md`.
- Unchanged: `DydxSecondSnapshot` schema, `data_api`, `frontend`, `ranking_engine`.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.13] — ACs and the 2026-09-20 candle-accuracy decision.
- [Source: troll/collector_core/collector.py `_process_data` (:455-483, float accumulators), `_sample_tick` (:612), `_flush_once` (:533)] — what changes.
- [Source: troll/ml_signals/candle_store.py `_fold` (:90), `apply_batch`, `rebuild`] — seconds->bars fold and watermark; `verified_days` sits beside `built_through`.
- [Source: troll/collector_core/repair_catalog.py, build_candles.py, consolidate_catalog.py, prune_catalog.py] — file rewrite, day listing, consolidation and prune precedents.
- [Source: troll/docs/DATA_INTEGRITY_AUDIT.md D-31..D-34, D-44, D-45..D-49] — what this closes and what stays open.
- [Source: troll/CLAUDE.md DATA-01, DATA-02, DATA-05, DATA-07, NAUT-02/03, MEM-01, TEST-01/03] — rules applied.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
