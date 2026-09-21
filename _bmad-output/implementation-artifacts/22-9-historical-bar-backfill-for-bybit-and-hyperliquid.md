# Story 22.9 (optional): Historical bar backfill for Bybit and Hyperliquid

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a strategy developer,
I want 1-minute `Bar` history backfilled into the catalog from Bybit klines (pyo3 `request_bars`) and Hyperliquid `candleSnapshot` (last 5000 candles),
so that backtests on these venues can run over more history than the collector has been alive for.

## Acceptance Criteria

1. **Catalog-native, idempotent.** Bars are written via `ParquetDataCatalog.write_data()` (NAUT-02); re-runs are idempotent (skip ranges already present); a `BacktestDataConfig` over the backfilled range loads them without conversion (NAUT-03).

## Tasks / Subtasks

- [x] Task 1 — `collector_core/backfill_bars.py` CLI (AC: #1)
  - [x] Args, mirroring `collector_core/build_candles.py`: `--catalog` (required), `--instrument` (repeatable, full Nautilus id), `--start`/`--end` (`YYYY-MM-DD` UTC), `--bar-spec` (default `1-MINUTE-LAST`), `--apply` (default report-only: prints the windows it *would* fetch and which are already covered).
  - [x] Venue dispatch by `venue_of(instrument_id)`: `BYBIT` → pyo3 `BybitHttpClient().request_bars(product_type=bybit_product_type_from_symbol(symbol), bar_type=BarType.from_str(f"{iid}-{spec}-EXTERNAL"), start=..., end=..., limit=1000, timestamp_on_close=True)` (`nautilus_pyo3.pyi:7345`); `HYPERLIQUID` → `HyperliquidHttpClient(environment=MAINNET).request_bars(bar_type, start, end, limit)` (`nautilus_pyo3.pyi:9430`, backed by `candleSnapshot`, ≤ 5000 most recent candles — the script must report when the requested start is older than what the venue returns, never silently return less). Any other venue → error (dYdX candles come from its own 1s archive).
  - [x] Pagination: fixed windows of `limit` bars (1000 × 1 min = 16 h 40 m for Bybit) walked from `start` to `end`; a pure `_windows(start_ns, end_ns, bar_ns, limit) -> list[tuple[int, int]]` helper. Pace requests to stay far under Bybit's 600 req / 5 s and Hyperliquid's 1200 weight/min (a `asyncio.sleep(0.2)` between calls is plenty; document it).
  - [x] Conversion: pyo3 bars → Cython `Bar` via `Bar.from_pyo3_list` (verify the helper name in `nautilus_trader/model/data.pyx`; it exists for the other data types the collectors already convert) → `catalog.write_data(bars)`. Instruments must already be in the catalog (the collectors write them on every start); assert and tell the operator to run the collector once otherwise.
  - [x] Idempotency: before fetching a window, check the catalog's existing bar files for that `BarType` (`catalog.get_intervals`/the `data_file_ranges` pattern `normalize_snapshot_schema.py`'s docstring refers to — use the catalog's own interval API, don't parse file names by hand) and skip windows fully covered; partially covered windows are fetched and the overlap deduped by `ts_event` before writing, so the resulting files never hold duplicate timestamps (a duplicate bar would double volume in any aggregation — DATA-05/DATA-06 spirit).
- [x] Task 2 — tests (TEST-01: catalog integration path + window arithmetic)
  - [x] `collector_core/tests/test_backfill_bars.py`: `_windows` cases (exact multiple, remainder, start == end, end < start rejected); write a small list of real `Bar`s for a `.BYBIT` `BarType` into a tmp catalog, re-run the "skip covered" logic and assert zero windows to fetch; write an overlapping batch and assert no duplicate `ts_event` after the dedup; `BacktestDataConfig(catalog_path=..., data_cls=Bar, bar_types=[...], start_time=..., end_time=...)` resolves those bars (construct the config and load through `BacktestNode`'s data loading the way `ml_signals/tests/test_snapshot_backtest_node.py` does, or the smallest catalog query that proves the same file layout — no mocks).
  - [x] Network calls are not unit-tested (they are the venue's REST); a `--apply` smoke run against mainnet for one day of `BTCUSDT-LINEAR.BYBIT` and the last 5000 candles of `BTC-USD-PERP.HYPERLIQUID` goes in Completion Notes with row counts.
- [x] Task 3 — docs
  - [x] `troll/docs/DATA_DICTIONARY.md`: a short "Backfilled `Bar`s" entry (bar type string, `EXTERNAL` aggregation source, venue coverage limits, idempotent re-run); `troll/README.md` one usage line.

## Dev Notes

### Why this is optional and small

Research §B6: `.BYBIT`/`.HYPERLIQUID` ids already load through `BacktestDataConfig` on `DydxSecondSnapshot`; this story only extends the *depth* of history for those venues using the venues' own klines. It does not touch the collectors, the candle store or `ml_signals`: backfilled bars land in Parquet only and never appear in `candles_<venue>.db` or on the chart (say so in the data dictionary). They double as a stored reference for 22.13's `compare_klines`. One CLI module, two pyo3 calls, one pure window helper.

### `EXTERNAL` bars are the venue's candles, not ours

Bar type string ends in `-EXTERNAL` (venue-aggregated), unlike `live_paper`'s `INTERNAL` bars aggregated by Nautilus from ticks. Backtests that mix venue klines with our 1 s snapshots must know they are different sources; say so in the data dictionary. Bybit's `timestamp_on_close=True` matches Nautilus's convention (`BybitDataClientConfig.bars_timestamp_on_close` default).

### Precision

pyo3 bars carry `Price`/`Quantity` at the instrument's precision already; conversion via `from_pyo3` is exact. Never rebuild bars from floats (AD-5).

### Project Structure Notes

- New: `troll/collector_core/backfill_bars.py`, `troll/collector_core/tests/test_backfill_bars.py`.
- Modified: `troll/docs/DATA_DICTIONARY.md`, `troll/README.md`.
- Unchanged: collectors, `ml_signals`, compose/dockerfile (the core dir is already in the image, so `python3 -m collector_core.backfill_bars` works inside the container like the other operator scripts).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.9] — AC.
- [Source: research 2026-09-20 §B6] — scope and venue limits (Bybit years of 1m klines; Hyperliquid last 5000 candles).
- [Source: nautilus_trader/core/nautilus_pyo3.pyi:7345 (`BybitHttpClient.request_bars`), :9352-9445 (`HyperliquidHttpClient`, `request_bars`)].
- [Source: troll/collector_core/build_candles.py] — CLI/idempotent-backfill precedent; [Source: troll/dydx_collector/normalize_snapshot_schema.py docstring] — file-range/report-first conventions.
- [Source: troll/ml_signals/backtest_dydx.py:48-57; troll/ml_signals/tests/test_snapshot_backtest_node.py] — `BacktestDataConfig`/`BacktestNode` usage to prove loadability.
- [Source: troll/CLAUDE.md NAUT-02, NAUT-03, NAUT-01/AD-5, MEM-01, DATA-05, TEST-01] — rules applied.

## Dev Agent Record

### Agent Model Used

Claude Opus 5 (`claude-opus-5`): bmad-dev-auto attempt 1 (run `20260920-211553-ca94`, timed out after review pass 2's rework), finished interactively from `refs/attempt-preserve-dirty/20260920-211553-ca94-75c63e99-1`.

### Debug Log References

Full spec, both review passes and their bad_spec loopbacks: `spec-22-9-historical-bar-backfill-for-bybit-and-hyperliquid.md`.

### Completion Notes List

- Attempt 1 ran two independent review passes (Blind Hunter + Edge Case Hunter), each ending in a bad_spec loopback, and re-derived the code after pass 2. It then hit the 90-min dev-session timeout before closing out. The loop preserved the dirty tree. Attempt 2 was stopped about 10 minutes in, having only begun a from-scratch rewrite, and was discarded. Attempt 1's snapshot was restored instead, with every pass-2 finding already addressed in the code: one `write_data()` per contiguous run, a retention short-circuit on a fully empty window only, the per-venue epoch-grid-safe spec table, a pre-write precision check against the catalog instrument, per-instrument isolation with a `finally` summary, delisted-instrument skip, and an injected `_Fetcher` for offline write-path tests.
- Verified on the host: `collector_core/tests/test_backfill_bars.py` 45 passed; `collector_core/tests` 98 passed; `ruff format --check`, `ruff check` and `mypy` clean on both new files.
- **Live `--apply` smoke run (mainnet, 2026-09-21, scratch catalog seeded with the venues' own pyo3 instrument definitions):**
  - `BTCUSDT-LINEAR.BYBIT`, `--start 2026-09-19 --end 2026-09-19`: 2 windows, **1440 bars** in 2 files, 0 discarded, 0 missing. Re-run: **0 windows planned, 0 REST calls** (AC #3 on real data).
  - `BTC-USD-PERP.HYPERLIQUID`, `--start 2026-09-16 --end 2026-09-21`: 8 windows planned, **5143 bars** in 6 files. The venue served nothing before 2026-09-17T16:22Z (a window 579/1000 short at the front, then a fully empty one, then 1 older window short-circuited). All three are logged as WARNINGs, and the summary reports 1421 missing — the ~5000-candle retention made visible, not silent.
  - **Closes the spec's deferred wire question for one bar on each venue:** the stored bar with close `2026-09-19T12:00Z` equals, field-for-field, the raw REST kline opening at `11:59Z` (Bybit `/v5/market/kline`: 81281.4/81281.5/81281.1/81281.1, vol 4.993; Hyperliquid `candleSnapshot`: 81344.0/81352.0/81343.0/81352.0, vol 1.58541). So the open-time range params and both close-stamp conventions are correct. Across all stored bars: 0 duplicate `ts_event`, every stamp on the minute grid.
- Observed, accepted by design (D-53, detection-only): every Hyperliquid re-run re-probes the unreachable pre-retention range with one REST call and repeats the WARNING, because that range stays an uncovered catalog interval.
- Still unverified on the wire: whether either venue emits klines continuously across zero-volume minutes. A thin instrument would surface that as interior-hole WARNINGs, never as silent loss.

### File List

- `troll/collector_core/backfill_bars.py` (new)
- `troll/collector_core/tests/test_backfill_bars.py` (new)
- `troll/docs/DATA_DICTIONARY.md`, `troll/docs/DATA_INTEGRITY_AUDIT.md`, `troll/README.md` (modified)
- `_bmad-output/implementation-artifacts/spec-22-9-historical-bar-backfill-for-bybit-and-hyperliquid.md` (new)
