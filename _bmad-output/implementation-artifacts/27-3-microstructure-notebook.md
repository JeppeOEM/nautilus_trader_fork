# Story 27.3: Microstructure notebook

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> Research epic (Epic 27). Depends on Stories 27.1 and 27.2. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 27.3".

## Story

As a strategy researcher,
I want to inspect the microstructure of any collected instrument over any bounded window,
so that I can see spread, depth, imbalance, order flow, trade flow, seasonality and the return-autocorrelation and volatility structure before I design a signal.

## Acceptance Criteria

1. **Given** `MarketFrames.seconds` and `kernel.indicators`
**When** `02_microstructure` runs
**Then** it shows, for each instrument in `INSTRUMENTS` over `START`–`END`: the spread in ticks and basis points over time with its distribution by hour of day (UTC); the depth profile (cumulative size by level and by distance from mid) for both sides, from `views.book_features.depth_profile` over the snapshot's 20 levels; order-book imbalance at N = 1, 5, 10, 20 (`MultiLevelOBI`) and OFI (`MultiLevelOFI` replayed over consecutive seconds) with their z-scores at the window the OFI strategy uses; microprice-minus-mid as a predictor of the next-second mid change (a binned scatter with the hit rate per bin); trade flow (`buy_volume`, `sell_volume`, counts, CVD) and a Kyle-lambda style price-impact regression of mid change on signed volume by bucket size; the funding rate, mark-minus-index basis and open interest overlaid on price; return autocorrelation at 1 s, 10 s, 1 m, 5 m and 1 h lags (`ReturnSeries.resample`), the volatility signature plot (realised variance per sampling interval) and rolling realised volatility; every indicator is the `kernel.indicators` class, never a formula in a cell

2. **Given** the fixture catalog's deliberate defects
**When** the smoke test executes the notebook
**Then** the crossed second and the gap are visible as `None` gaps in the series (never interpolated, DATA-01), and no cell computes over an unbounded window

3. **Given** a reader who has not seen the platform's signal architecture
**When** they open the notebook
**Then** each section's markdown states what is read from the archive, what is derived on read and by which `kernel.indicators` class (SIGNAL-01), and the closing section lists the observations the notebook is designed to surface (spread regime changes, depth asymmetry, seasonality, autocorrelation sign at each horizon) with a pointer to the strategy that uses each (`research/strategies/ofi_strategy.py` for OFI)

## Tasks / Subtasks

- [ ] Task 1 — domain additions needed by the notebook (AC: #1)
  - [ ] `research/domain/microstructure.py` (numpy only): `autocorrelation(ReturnSeries, lags)`, `volatility_signature(prices, ts_ns, intervals_s)` (realised variance per sampling interval, from `ReturnSeries.resample`), `realised_volatility(ReturnSeries, window)`, `price_impact(signed_volume, mid_change, buckets)` (OLS slope per volume bucket; numpy `lstsq`, no statsmodels), `hit_rate_by_bin(predictor, outcome, bins)`. Each docstring: invariant + formula source. Tests: autocorrelation of an AR(1) series with known phi, signature plot of i.i.d. returns flat within tolerance, impact slope of a synthetic linear relation equal to the slope.
  - [ ] `research/application/frames.py`: `seconds()` gains `obi_levels: tuple[int, ...]` and `ofi_window: int` options so OBI/OFI columns come from `kernel.indicators` (`MultiLevelOBI.update_raw(bid_sizes, ask_sizes)`, `MultiLevelOFI` replayed in `ts_event` order) exactly as `views.custom_indicators._ofi_replay` does; z-scores via the same rolling window the OFI strategy uses (`OFIStrategyConfig.ofi_zscore_window`, default 300; import the config to read the default, never re-type the number).
- [ ] Task 2 — `02_microstructure` (AC: #1, #3)
  - [ ] Sections: (1) Parameters; (2) Spread: ticks and bps series, hour-of-day box plot; (3) Depth: `views.book_features.depth_profile` per snapshot sampled every minute, cumulative size by level and by bps-from-mid, both sides; (4) Imbalance and OFI: OBI at 1/5/10/20, OFI and its z-score, with the strategy's threshold lines; (5) Microprice: `microprice - mid` binned vs next-second mid change, hit rate per bin; (6) Trade flow: buy/sell volume, counts, CVD; price impact regression per bucket; (7) Funding, basis (`mark - index`), open interest on a secondary axis; (8) Returns: autocorrelation at 1 s/10 s/1 m/5 m/1 h, volatility signature plot, rolling realised vol; (9) Observations checklist with strategy pointers.
  - [ ] Every derived column is named after the kernel function that made it; the markdown of each section names the stored fields it reads and the class that derives the rest (SIGNAL-01).
- [ ] Task 3 — defects and smoke test (AC: #2)
  - [ ] The fixture's crossed second and 90 s gap render as `None` (plotly `connectgaps=False`); a test in `research/tests/test_notebook_microstructure.py` runs the notebook's frame-building cells (importable helpers, not the whole notebook) against the fixture and asserts the gap indices are NaN and the crossed second is NaN in `mid`, `spread`, `microprice`.

## Dev Notes

- **Reuse map:** `ml_signals/book_features.py` (`top_of_book_series`, `depth_profile`, `book_imbalance`, `liquidity_distance` — `views` after 24.2), `ml_signals/indicators.py` (`Microprice`, `MultiLevelOBI`, `MultiLevelOFI`, `mid_price`, `spread`, `microprice`, `volume_delta`, `trade_aggregates` — `kernel` after 23.2), `ml_signals/custom_indicators.py` (`_ofi_replay` shows the exact replay order and window handling), `ml_signals/strategies/ofi_strategy.py` (thresholds and windows to display).
- **Depth from snapshots:** `depth_profile` takes an `OrderBook`; for a `DydxSecondSnapshot` build the 20-level arrays directly (a small `views` helper if one does not exist after 24.2 — add it there, not in the notebook, and not in `research`).
- **Crossed/gap seconds:** a crossed second (`best_bid >= best_ask`) in the archive can only be a fixture defect (the gate never writes one); the notebook shows it as a gap and says so in prose. Never clamp or drop silently (DATA-01/DATA-07).
- **Horizons:** 1 s series come from `seconds()`; 1 m/5 m/1 h come from `ReturnSeries.resample` of the 1 s series within the window (compounding), not from the candle store, so the autocorrelation and signature plots are internally consistent. Say so in the markdown.
- **MEM-01:** a day of 1 s snapshots for one instrument is ~86 400 rows × 80 floats; the notebook's default window is one day and the markdown warns before widening it.
- **Project rules:** DATA-01, DATA-07, MEM-01, SIGNAL-01, SSOT-02 (no rolling metric that ranking already computes: pct/volatility for the screener come from `RankingHistory`, and the notebook says which is which), TEST-01/03/04, READ-03, NB rules as drafted for 27.9 (no logic in cells).
- **Working directory:** `platform/`; `python3 -m pytest -o addopts="" --rootdir=. research/tests -q`.

### Project Structure Notes

- `research/notebooks/02_microstructure.py` + `.ipynb`; `research/domain/microstructure.py`; `research/tests/test_microstructure.py`, `test_notebook_microstructure.py`.

### References

- Epic text: "Story 27.3"; FR73
- Code: `ml_signals/book_features.py`, `ml_signals/indicators.py`, `ml_signals/custom_indicators.py:289-360`, `ml_signals/strategies/ofi_strategy.py:51-95`
- Rules: `platform/CLAUDE.md` "Signal Architecture: 1s-Based, Not Event-Driven"

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
