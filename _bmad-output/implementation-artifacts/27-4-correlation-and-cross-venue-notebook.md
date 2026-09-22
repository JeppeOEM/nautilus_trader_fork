# Story 27.4: Correlation and cross-venue notebook

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> Research epic (Epic 27). Depends on Stories 27.1 and 27.2. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 27.4".

## Story

As a strategy researcher,
I want return correlation, lead-lag and clustering across the collected universe and across venues for the same symbol,
so that I can pick uncorrelated instruments for a portfolio, find which venue leads on price, and see where the same asset trades at a basis across venues.

## Acceptance Criteria

1. **Given** `research.domain.correlation` and `MarketFrames`
**When** `03_correlation` runs
**Then** it builds, for `INSTRUMENTS` over `START`–`END`, aligned `ReturnSeries` at 1 m, 5 m, 1 h and 1 d (from `MarketFrames.bars`, so the seconds→bars fold is the candle store's), shows the correlation matrix at each horizon as a diverging heatmap (plotly, clustered order from `cluster()`), rolling 1-day correlation for every pair against a chosen anchor instrument, the clustering dendrogram as an ordered list with the merge distances, and the correlation of funding rates and of open-interest changes across the same instruments; missing bars align as gaps and the matrix is pairwise-complete (the domain function's contract), never forward-filled

2. **Given** the same symbol collected on more than one venue (BTC and ETH on dYdX, Bybit linear and Hyperliquid)
**When** the cross-venue section runs
**Then** it shows the mid-price basis between each venue pair in basis points over time, the lead-lag cross-correlation of 1 s returns for lags of ±1 s to ±30 s (`lead_lag`) with the peak lag and its sign stated in words ("Bybit leads dYdX by 2 s"), the funding-rate differential, and a trade-volume share per venue per hour; the symbol matching uses `kernel.venues` (`market_kind`, base/quote parsing), never string prefix guessing, and a venue absent from the fixture or the archive produces a stated "not collected in this window" line rather than an exception

3. **Given** the smoke test
**When** the notebook executes against the fixture catalog
**Then** the two instruments per venue produce a `2 × 2` matrix per venue and the BTC cross-venue section finds all three venues, and the notebook's markdown explains how to feed the clustered universe into a multi-instrument `BacktestRunner.run` (the Story 1.3 watchlist idea, restated on this epic's types)

## Tasks / Subtasks

- [ ] Task 1 — domain and application additions (AC: #1, #2)
  - [ ] `research/domain/correlation.py`: `rolling_correlation(a, b, window)` (numpy sliding window, NaN-aware), `merge_order(matrix) -> list[tuple[str, str, float]]` (the single-linkage merge sequence, for the dendrogram-as-list), `basis_bps(mid_a, mid_b)`; tests with closed-form cases.
  - [ ] `research/application/frames.py`: `MarketFrames.same_symbol(instrument_id) -> list[InstrumentId]` using `kernel.venues` (`market_kind`, base/quote parsing; e.g. `BTC-USD-PERP.DYDX` ↔ `BTCUSDT-LINEAR.BYBIT` ↔ `BTC-USD-PERP.HYPERLIQUID`; quote-currency equivalence USD/USDC/USDT is a documented table in `kernel.venues`, not in research); `MarketFrames.venue_volume_share(ids, start, end, bucket_s)`.
  - [ ] `research/application/aligned.py`: `aligned_returns(frames, ids, bar_seconds, start, end) -> AlignedReturns` from `MarketFrames.bars` (candle store fold) for `bar_seconds >= 60`, and from `seconds()` for the 1 s lead-lag section.
- [ ] Task 2 — `03_correlation` (AC: #1, #3)
  - [ ] Sections: (1) Parameters (adds `ANCHOR` instrument, `HORIZONS`); (2) Aligned returns per horizon; (3) Correlation heatmaps (diverging scale centred on 0, clustered order); (4) Rolling correlation vs `ANCHOR`; (5) Clusters and merge distances; (6) Funding-rate and OI-change correlation; (7) "Feed a cluster into a backtest" prose with a `RunSpec` example over `BacktestRunner.run` (multi-instrument, the Story 1.3 watchlist idea).
- [ ] Task 3 — cross-venue section (AC: #2)
  - [ ] (8) Same-symbol discovery per `INSTRUMENTS` entry via `same_symbol`; per venue pair: basis in bps (mid vs mid on the 1 s grid, NaN where either is absent), lead-lag ±30 s with the peak lag sentence, funding differential, hourly volume share; a missing venue prints "not collected in this window" (no exception, no empty plot).
- [ ] Task 4 — smoke test expectations (AC: #3)
  - [ ] Extend `research/tests/test_notebooks.py` fixture with the venue-pair mapping; `test_notebook_correlation.py` asserts the `2 × 2` per venue matrix and the three-venue BTC set on the fixture, and that `lead_lag` on the fixture's deliberately shifted Bybit series (add a 2 s shift to the fixture, documented in `FixturePaths.defects`) peaks at +2.

## Dev Notes

- **Bars come from the candle store**, never a pandas `resample` in a cell: the platform has exactly two folds (`fold_trades`, `fold_arrays`, spine MR6). If the store lacks a horizon (e.g. 1 d is not a stored bar size), build it with `ReturnSeries.resample` from the smallest stored bar and say so in the markdown.
- **Pairwise-complete correlation:** with gaps in one instrument the matrix uses, per pair, only rows where both are finite. This is the domain function's documented contract; the notebook never forward-fills (DATA-01).
- **Symbol matching lives in `kernel.venues`** (the only `InstrumentId` parser, MR5). If it lacks a same-symbol helper, add it there with a test; research must not parse ids.
- **Clustering is numpy-only** (NFR12): single-linkage over `1 - rho`, `O(n^3)`, fine for the collected universe (≤ ~100 instruments). `Known limit:` in the docstring with the upgrade path (a proper linkage routine if the universe grows).
- **Cross-venue 1 s alignment:** `ts_event` is venue time (D-62 re-stamp on Hyperliquid); the lead-lag is computed on `ts_event`-keyed 1 s returns, and the markdown explains that a peak lag also carries each venue's own timestamping latency (the "two clocks" value object).
- **Project rules:** DATA-01, MEM-01 (default window one day; the 1 s section is per pair, not per universe), SSOT-02, TEST-01/03/04, READ-03, NFR12.
- **Working directory:** `platform/`; `python3 -m pytest -o addopts="" --rootdir=. research/tests -q`.

### Project Structure Notes

- `research/notebooks/03_correlation.py` + `.ipynb`; `research/domain/correlation.py` (extended); `research/application/aligned.py`; `kernel/venues.py` (same-symbol helper, with test in `kernel/tests`).

### References

- Epic text: "Story 27.4"; FR74
- Code: `common/venues.py` → `kernel/venues.py` (23.2), `ml_signals/candle_store.py` `window` → `candles.application.window` (24.1), `collector_core/second_snapshot.py`
- Spine: MR5 (kernel membership), MR6 (two folds), AD-D3

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
