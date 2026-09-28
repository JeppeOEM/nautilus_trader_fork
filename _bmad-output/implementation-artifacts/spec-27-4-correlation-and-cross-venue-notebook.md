---
title: 'Story 27.4: Correlation and cross-venue notebook'
type: 'feature'
created: '2026-09-28'
status: 'done'
baseline_revision: 'db51eb363b8ee00a34f8009205ffe30c9b0f2346'
final_revision: 'ca12d54a366d5552a465770b17d51689da7214bb'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/27-4-correlation-and-cross-venue-notebook.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-27-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** A researcher cannot see how the collected instruments co-move (return correlation per horizon, clusters, funding and OI co-movement), or how one asset behaves across dYdX, Bybit and Hyperliquid (basis, which venue leads, funding differential, volume share). Without that they cannot pick uncorrelated instruments for a portfolio or find a leading venue.

**Approach:** Ship `research/notebooks/03_correlation.py` + `.ipynb` on the 27.2 harness. The numbers come from three places. `research/domain/correlation.py` gains `rolling_correlation`, `merge_order`, `peak_lag` and `basis_bps`. `kernel.venues` gains same-asset matching. A new `research/application/aligned.py` service builds aligned returns from candle-store bars (bars ≥ 60 s) or from the 1 s mid grid, plus the cross-venue frames. The `MarketFrames` port gains `same_symbol` and `bar_coverage`.

## Boundaries & Constraints

**Always:**
- Bar returns come from `MarketFrames.bars` (the candle store's fold). There are no pandas resamples of prices, and a bar return is `ReturnSeries.from_prices(close, t, bar_seconds)`. The window is read span by span over `bar_coverage`, the maximal runs of stored buckets inside `[start, end)`. A bucket that was never observed, or that lies outside the store, is therefore a gap (NaN return), never an exception and never a fill. All four horizons (60, 300, 3600, 86400) are stored sizes (`BAR_SECONDS`).
- Correlation is pairwise-complete (`correlation_matrix`, `_pearson`), and nothing is forward-filled (DATA-01). The 1 s mid grid is `inspection.second_grid`/`mid_series`, so a missing, shared or crossed second is NaN.
- Same-asset matching lives only in `kernel.venues`. Research never splits an id. The quote-equivalence table (USD/USDC/USDT) and the Bybit quote-suffix table are documented constants there. An id the tables cannot read gives `None` and never matches: no guess.
- Funding is compared per hour, as `rate * 60 / interval`. It is NaN where `interval` is None or not positive, and an interval is never assumed. The notebook prints each instrument's distinct intervals.
- A venue in `VENUE_KINDS` with no matching id, or with no snapshot row in the window, prints `"<asset> <VENUE>: not collected in this window"`. It never raises and never draws an empty plot.
- Every new domain/application function's docstring names its invariant. Every new `.py` has the LGPL header, full type hints, ruff at 100 and one import per line. Cells hold only calls, loops and plotly.
- Docs: amend the DDD spine's research row/tree (`aligned.py`, `03_correlation`) and its kernel `venues.py` note, each with `[amended 2026-09-28: Story 27.4]`. Also update the `platform/ARCHITECTURE.md` notebooks paragraph and the `research/README.md` index row.

**Block If:**
- The notebook cannot run under `warnings.simplefilter("error")` without filtering a warning that our code cannot avoid.
- The fixture's wiggle change breaks a 27.2/27.3 assertion whose intent needs the old periodic series.

**Never:**
- No new dependency (no scipy). Clustering stays numpy-only single linkage.
- No research import of `views`, `ranking` or `data_api`, and no new `RESEARCH_*_SERVICES` entry.
- No edit under `nautilus_trader/` or `crates/`, and never write `sprint-status.yaml`.
- Nothing is run inside `BacktestRunner` in the notebook. §7 constructs a `RunSpec` and prints it, and runs nothing.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Same asset | `BTC-USD-PERP.DYDX`, `BTCUSDT-LINEAR.BYBIT`, `BTCPERP-LINEAR.BYBIT`, `BTC-USD-PERP.HYPERLIQUID` | same `AssetKey("BTC","USD","perp")` | — |
| Not matched | `BTCUSDT-SPOT.BYBIT` vs the perp; `BTCUSDT-25SEP26-LINEAR.BYBIT`; `ETHBTC-SPOT.BYBIT`; `BTCUSD1-SPOT.BYBIT`; `garbage` | different kind / `None` / `None` / `None` / `None` | never raises |
| Store edge | window past the store, or a missing bucket | spans cover only the stored runs; returns across them NaN | no exception |
| Rolling corr | window w, a NaN inside | pairwise-complete over the window; NaN before the window fills or with < `min_pairs` finite pairs | `window < 2` → ValueError |
| Merge order | 3 ids, rho(a,b)=0.9, rho(b,c)=0.5 | `[(a),(b),0.1]`, then `[(a,b),(c),0.5]` | NaN pairs never merge (the list stops) |
| Peak lag | all-NaN lead-lag | `None` → "no overlapping 1 s returns" sentence | ties → smallest abs lag |
| Basis | mids 100.01 vs 100 | 1.0 bps; NaN where either NaN | non-positive finite mid → ValueError |
| Funding | interval 60 vs 480 | per-hour rates, differential per hour bucket | None interval → NaN |
| Volume share | a venue with no seconds observed in a bucket | share NaN for every venue in that bucket | — |

</intent-contract>

## Code Map

- `platform/kernel/venues.py` -- the only id parser; add `AssetKey`, `asset_key`, `same_asset`, `USD_QUOTES`. Tests: `platform/kernel/tests/test_venues.py`.
- `platform/research/domain/correlation.py` -- `_pearson`, `align`, `correlation_matrix`, `lead_lag`, `cluster` (single linkage, `_closest_pair`); extend it here.
- `platform/research/domain/returns.py` -- `ReturnSeries.from_prices`, which handles gaps.
- `platform/research/application/ports.py` -- the `MarketFrames` Protocol; `RunSpec` (single venue, `.venue`).
- `platform/research/application/frames.py` -- `CatalogFrames.bars` (raises on coverage), candles query imports, and `self._catalog.instruments()` for `same_symbol`.
- `platform/research/application/inspection.py:517-561` -- `second_grid`, `mid_series`, `plot_axis`.
- `platform/research/application/microstructure.py:442-475,553` -- `basis_frame`, `_grid_ns`, `second_returns` and `coverage_line`, the precedents to reuse.
- `platform/research/notebooks/02_microstructure.py`, `_params.py` -- the cell pattern.
- `platform/research/tests/fixture_catalog.py` -- `_mid` (a periodic wiggle with period 23: it would tie lead-lag peaks at 2/−21/25), `FundingRateUpdate` without `interval`, `FixtureDefects`, `FixturePaths`.
- `platform/research/tests/test_notebooks.py` -- the harness `_run`, which runs every numbered notebook.
- DDD spine `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md:157,223,439`; `platform/ARCHITECTURE.md:336-347`; `platform/research/README.md:14-15`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/kernel/venues.py` + `kernel/tests/test_venues.py` -- `AssetKey(base, quote, kind)` NamedTuple. `asset_key(iid)`: dYdX/Hyperliquid take exactly `BASE-QUOTE-PERP`. Bybit takes exactly `<head>-LINEAR|SPOT`, whose head ends with one of the `_BYBIT_QUOTE_SUFFIXES` (`USDT`, `USDC`; `PERP` → USDC, Bybit's USDC perpetual), with a non-empty base. A quote in `USD_QUOTES` maps to class `"USD"`. Anything else is None. `same_asset(a, b)` holds when both keys are non-None and equal. Table-driven tests follow the matrix.
- [x] `platform/research/domain/correlation.py` + `research/tests/test_correlation.py` -- `rolling_correlation(a, b, window, min_pairs=None)` over `sliding_window_view`, `_pearson` per window, `min_pairs` defaulting to `max(2, window // 2)`. `MergeStep(left, right, distance)` and `merge_order(matrix)` share single linkage with `cluster`, so both use one `_closest_pair`. `peak_lag(pairs) -> tuple[int, float] | None`. `basis_bps(a, b)` = `(a / b - 1) * 1e4`. `correlation_of(ids, matrix)`, a pairwise-complete matrix over any value columns that `correlation_matrix` delegates to, is used for funding levels. Closed-form tests cover each matrix row.
- [x] `platform/research/application/ports.py` + `frames.py` + `research/tests/test_frames.py` -- `MarketFrames.same_symbol(instrument_id) -> list[str]`: the catalog's defined ids with the same `asset_key`, including itself, sorted by venue then id; `[]` when `asset_key` is None. `MarketFrames.bar_coverage(instrument_id, bar_seconds, *, start, end) -> list[tuple[int, int]]`: the ns spans of maximal runs of stored buckets whose whole bar lies in `[start, end)`, `FileNotFoundError` without a store (as `bars()`); it uses only `open_store`/`bucket_starts`.
- [x] `platform/research/application/aligned.py` + `research/tests/test_aligned.py` -- `HORIZONS_S`, `bar_returns`, `second_frame` (the 1 s grid of `mid`, `buy_volume`, `sell_volume`), `aligned_returns(frames, ids, bar_seconds, start_ns, end_ns)`, `by_venue`, `clustered_order(matrix, threshold)`, `bucket_last`, `funding_per_hour`, `funding_matrix`, `oi_change_matrix` (relative OI change via `ReturnSeries.from_prices`), `rolling_vs_anchor`, `cross_venue(frames, instrument_id, start_ns, end_ns, max_lag, bucket_s) -> CrossVenue` (groups, per-pair basis/lead-lag/peak sentence/funding differential, volume share, and the missing-venue lines), `lead_sentence`, `venue_volume_share(grids, bucket_s)`.
- [x] `platform/research/tests/fixture_catalog.py` -- replace the wiggle with a fixed seeded `random.Random(27)` table in [−11, 11]. `LEAD_INSTRUMENT = BTCUSDT-LINEAR.BYBIT` reads it 2 s ahead (`LEAD_SECONDS = 2`). Set funding `interval` to 60 (dYdX, Hyperliquid) and 480 (Bybit). `FixtureDefects` gains `lead_instrument`/`lead_seconds`, and `FixturePaths` gains `same_asset: dict[str, tuple[str, ...]]`. Update the module docstring.
- [x] `platform/research/notebooks/03_correlation.py` + `.ipynb` -- §1 Parameters (`ANCHOR`, `HORIZONS`, `CLUSTER_DISTANCE`, `ROLLING_WINDOW_S`, `MAX_LAG_S`, `FUNDING_BUCKET_S`, `OI_BUCKET_S`, `VOLUME_BUCKET_S`); §2 aligned returns per horizon; §3 heatmaps (`RdBu`, `zmid=0`, clustered order) plus the per-venue matrices; §4 rolling correlation vs `ANCHOR`; §5 clusters and the merge list per venue; §6 funding and OI-change correlation; §7 "feed a cluster into a backtest" (`RunSpec` over `BacktestRunner.run`, Story 1.3's watchlist idea); §8 cross-venue. The markdown covers the two-clocks caveat and the store's stored sizes, and that daily returns need a multi-day window.
- [x] `platform/research/tests/test_notebook_correlation.py` -- the notebook namespace over the fixture: each venue's 1 m matrix is 2×2, and the BTC cross-venue group covers all three venues. The service asserts `lead_lag(bybit, dydx)` peaks at +2 on the fixture, with the sentence "BYBIT leads DYDX by 2 s". The dYdX outage makes those returns NaN, and a missing venue gives the line and does not raise.
- [x] Docs -- the spine research row/tree/kernel note, `platform/ARCHITECTURE.md` and `research/README.md`.

**Acceptance Criteria:**
- Given the session fixture, when `03_correlation.py` runs through the harness (`simplefilter("error")`, headless), then it finishes in under 60 s and its `.ipynb` passes the pairing test.
- Given the notebook source, when read, then no cell holds a formula, a resample, or a split of an id.
- Given the full platform suite, when run, then there are no failures beyond the baseline, and `test_boundaries.py` passes with unchanged allowlists.

## Spec Change Log

### 2026-09-28 — review patch (no loopback)
- **Trigger:** a missing candle store made `bar_coverage` return `[]`, so a wrong `CANDLES_DIR` read as an outage over the whole window (DATA-07).
- **Amended:** the `bar_coverage` task. It now raises `FileNotFoundError`, as `bars()` does.
- **Known-bad state avoided:** every horizon all-NaN with no error.
- **KEEP:** span-by-span reads, and holes as NaN.

## Review Triage Log

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 0, medium 3, low 5)
- defer: 0
- reject: 14
- addressed_findings:
  - `[medium]` `[patch]` `bar_returns` used a `partial` bar's close as if the bar were complete. That covers an outage inside the bucket and the still-forming newest bucket. A partial bar's close is now NaN, so both returns touching it are gaps. A fixture test was added, and the grid-count test now expects 6 returns for dYdX BTC.
  - `[medium]` `[patch]` `bar_coverage` returned `[]` without a candle store, which read as a whole-window outage. It now raises `FileNotFoundError`, as `bars()` does. Tests are updated in `test_frames` and `test_aligned`.
  - `[medium]` `[patch]` A lead-lag peak could be stated from a handful of overlapping seconds, where rho is ±1 by construction. `MIN_LEAD_LAG_PAIRS = 30` now gates it, and below that the sentence says "no lead stated". Test added.
  - `[low]` `[patch]` `oi_changes({})` raised while `funding_matrix` returned an empty result; it now returns an empty `AlignedReturns`. `cross_venue` rejects `max_lag < 0` up front. The notebook's bucket-parameter doc now names which section each bucket drives. "Bybit every 480" is reworded as per-symbol. A test asserting the NaN at the hole was weak and is tightened.

### 2026-09-28 — Review pass (follow-up review of the done spec)
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 2, low 4)
- defer: 0
- reject: 8
- addressed_findings:
  - `[medium]` `[patch]` `MIN_LEAD_LAG_PAIRS` gated only the lag-0 overlap, so a far lag with 2–3 pairs (rho ±1 by construction) could win the peak and be stated as a lead. `lead_lag` gains `min_pairs` (each lag needs its own finite pairs, else NaN) plus `lagged_pairs`; `_returns_lead_lag` passes `MIN_LEAD_LAG_PAIRS`. Tests added in the domain and at the service level.
  - `[medium]` `[patch]` A peak with rho ≤ 0, or inside sampling noise, was stated as a lead. The lead is now stated only above `NOISE_Z / sqrt(pairs at the peak)` (never ≤ 0); `VenuePair.noise_band` carries the band, and the sentence says "within the noise band, no lead stated". The approximate band (autocorrelated 1 s returns) is a `Known limit:`, with a block bootstrap as the upgrade path. The notebook markdown and `.ipynb` are re-synced.
  - `[low]` `[patch]` The no-peak sentence blamed "under 30 overlapping returns" even when the cause was a constant mid. It is reworded as "no lag holds a finite rho over 30+ overlapping 1 s returns".
  - `[low]` `[patch]` `cross_venue` now rejects `bucket_s <= 0` with `ValueError`; before, it raised a bare `ZeroDivisionError` or returned an empty grid.
  - `[low]` `[patch]` The `MarketFrames.bar_coverage` port docstring still promised `[]` without a store. It now states `FileNotFoundError`, as the implementation and the Spec Change Log do.
  - `[low]` `[patch]` `cross_venue` gets a `Known limit:` for its per-leg window read: peak memory grows with window length; the upgrade path is a day-by-day read.

## Design Notes

- **`bar_coverage` on the port:** `bars()` raises on a window past the store or across an unobserved bucket, which is correct for a single read. A correlation over a real day, though, must survive a collector outage as a gap. Reading each stored run separately keeps `bars()`'s guarantee (no run crosses a hole) and makes the hole a NaN return.
- **`same_symbol` returns `list[str]`, not `InstrumentId`:** every port method takes `str` ids. Candidates come from `ParquetDataCatalog.instruments()` (a Nautilus built-in), and "collected" is decided by the window read.
- **`venue_volume_share` takes the 1 s grids:** these are the grids the cross-venue section already read. That keeps the port reads-only and avoids a second read of the book lists (MEM-01).
- **Heatmaps cover the whole `INSTRUMENTS` set, clusters are per venue:** `RunSpec` runs one venue per run (a Known limit), so a cluster fed to a backtest must be single-venue.
- **Known limits (in docstrings):** the O(n·w) rolling loop (upgrade path: masked cumulative sums). Multiplier-named bases (`1000PEPEUSDT` vs `kPEPE`) never match (upgrade path: a base-alias table in `kernel.venues`). Stablecoin quotes are equivalent for matching only, so the basis carries any USDT/USDC spread.
- **Prior attempt.** Run `20260928-070755-9b5a` was stopped mid-dev on this exact plan; the work is pinned on branch `27-4-prior-attempt` (parent = this spec's baseline `db51eb363b`). The implementer starts from that tree (`git read-tree -m -u HEAD 27-4-prior-attempt`, then drop its copy of this spec), and re-verifies every task and AC against it rather than trusting its checkboxes.

## Verification

**Commands:**
- `cd platform && ../.venv/bin/python -m pytest -o addopts="" --rootdir=. research/tests kernel/tests tests/test_boundaries.py -q` -- expected: all pass
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests research/tests alerting/tests views/tests candles/tests capture/tests capture/venues/dydx/tests capture/venues/bybit/tests capture/venues/hyperliquid/tests collection_control/tests ranking/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q` -- expected: only the baseline Redis-dependent `data_api` failures plus the known flaky ranking test
- ruff check/format and mypy on the new and changed files -- expected: clean beyond the pre-existing errors


## Auto Run Result

**Summary:** Story 27.4 adds `research/notebooks/03_correlation` (`.py` + `.ipynb`). Its correlation matrices per horizon come from the candle store's bars, read span by span over `bar_coverage`, or from the 1 s mid grid. It also shows clustered heatmaps, rolling correlation against an anchor, single-linkage clusters with the merge order, funding and OI-change correlation, a `RunSpec` built from a cluster (not run), and a cross-venue view per asset: basis, lead-lag, funding differential and volume share. Same-asset matching is in `kernel.venues`. The follow-up review pass made lead-lag statements stricter: each lag needs its own pair floor, and a lead is stated only above the noise band.

**Files changed:**
- `platform/kernel/venues.py` / `kernel/tests/test_venues.py`: `AssetKey`, `asset_key`, `same_asset`, `USD_QUOTES`, the Bybit quote-suffix table.
- `platform/research/domain/correlation.py` / `tests/test_correlation.py`: `rolling_correlation`, `MergeStep`/`merge_order`, `peak_lag`, `basis_bps`, `correlation_of`, `lead_lag(min_pairs=)`, `lagged_pairs`.
- `platform/research/application/ports.py` / `frames.py` / `tests/test_frames.py`: `MarketFrames.same_symbol` and `bar_coverage`.
- `platform/research/application/aligned.py` / `tests/test_aligned.py`: aligned returns, funding/OI matrices, rolling vs anchor, cross-venue frames, the lead sentence with its noise band.
- `platform/research/tests/fixture_catalog.py`: a seeded random mid table, a planted 2 s Bybit lead, funding intervals, `same_asset`.
- `platform/research/notebooks/03_correlation.py` / `.ipynb`, `tests/test_notebook_correlation.py`: the notebook and its namespace test.
- Docs: DDD spine, `platform/ARCHITECTURE.md`, `research/README.md`.

**Review findings (this pass):** 6 patches applied (2 medium, 4 low), 0 deferred, 8 rejected. The rejected findings:
- "Not collected" lines for spot on dYdX/Hyperliquid: the spec mandates one line per `VENUE_KINDS` venue.
- Bybit's funding interval may be None in production: it is handled as NaN, and each instrument's intervals are printed.
- The funding test is scale-invariant: `funding_per_hour` has a direct test.
- `same_symbol` re-reads the instrument definitions each call: bounded, research-only.
- The 1-day rolling window shows nothing on a short window: that is the parameter's meaning.
- The Bybit suffix length: None is by design.
- Illiquid instruments lose returns next to untraded buckets: that is the DATA-01 no-fill invariant.
- A tz-naive index: it cannot arise from `second_grid`.

**Verification:**
- `../.venv/bin/python -m pytest research/tests kernel/tests tests/test_boundaries.py`: 556 passed.
- The full platform suite: 2354 passed, 1 skipped, 10 failed. The failures are the known baseline: 9 Redis-dependent `data_api` tests plus the flaky `ranking/tests/test_metrics_store.py` test.
- `ruff check` and `ruff format` are clean on every changed file.
- mypy reports 7 errors, all present at the baseline (`ports.py:50`, `test_correlation.py:72/76`, `test_frames.py:139`).
- `jupytext --sync` re-paired the notebook, and the pairing test passes.

**Residual risks:**
- The noise band assumes independent returns. 1 s returns are autocorrelated, so the band is optimistic (documented as a `Known limit:`).
- Bybit funding rows in production may carry no interval, which leaves Bybit's per-hour funding NaN. This is visible in the printed intervals and is not hidden.
