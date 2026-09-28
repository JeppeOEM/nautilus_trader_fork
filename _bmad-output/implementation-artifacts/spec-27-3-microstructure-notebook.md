---
title: 'Story 27.3: Microstructure notebook'
type: 'feature'
created: '2026-09-28'
status: 'done'
final_revision: '43aa98922e0415bbf3da5b87cf26c54e32599eab'
baseline_revision: 'e0c6a4a54367569a90f1a635081c372e96665ad4'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/27-3-microstructure-notebook.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-27-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** A researcher cannot see the microstructure of a collected instrument (spread, depth, imbalance, OFI, microprice edge, trade flow and price impact, funding/basis/OI, return autocorrelation and volatility structure) over a bounded window before designing a signal. The only way today is ad-hoc cell formulas, which break SIGNAL-01/one-home-per-metric.

**Approach:** Ship `research/notebooks/02_microstructure.py` + `.ipynb` on the 27.2 harness. Every number in it comes from three places: `kernel.indicators` classes and functions (OBI, OFI, microprice, spread, mid, depth, and a new shared `RollingZScore`), a new numpy-only `research/domain/microstructure.py` (autocorrelation, volatility signature, realised volatility, price impact, hit rate by bin), and a new `research/application/microstructure.py` service that turns `MarketFrames` reads into the 1 s grid frames the cells plot.

## Boundaries & Constraints

**Always:**
- Every read goes through `CatalogFrames` with `start=`/`end=` from `Params` (MEM-01). Cells hold only calls, loops over `params.instruments` and plotly; they contain no arithmetic, groupby or rolling.
- Gaps stay gaps. Every plotted per-second series lives on the 1 s grid of `[START, END)`. A missing second is NaN, and so is a second with two rows. A crossed second (bid >= ask) is NaN in every book-derived column. Traces use `connectgaps=False`. Nothing is forward-filled or interpolated (DATA-01).
- The OFI replay mirrors `OFIStrategy.on_data` exactly. Parameters come from an `OFIStrategyConfig` constructed for the instrument (`ofi_levels`, `ofi_window`, `ofi_zscore_window`, `usd_notional=True`). It feeds every two-sided row, crossed ones included (as the strategy would), and calls `clear_prev_state()` on a gap larger than the strategy's gap constant, which becomes public `MAX_GAP_NS`. The z-score window is never re-typed.
- A z-score has one formula: the new `kernel.indicators.RollingZScore`. `MultiLevelOFI` delegates its z-score to it, so its behaviour is unchanged and existing tests still pass. OBI z-scores use it too.
- Depth has one home. `DepthProfile` moves to `kernel.indicators` (views imports it from there), and new kernel functions compute `snapshot_depth(snapshot_dict, levels)`, cumulative size by level, and cumulative size within bps of mid. `views/chart_series.py`'s inline snapshot→`DepthProfile` switches to `snapshot_depth`.
- Every domain/application function docstring names its invariant (and its formula source for the domain ones). Every new `.py` carries the LGPL header, full type hints, ruff at 100, one import per line.
- The DDD spine research row and tree gain the new modules, and the kernel list notes `RollingZScore`/`DepthProfile`, each with an `[amended 2026-09-28: Story 27.3]` note. `platform/ARCHITECTURE.md`'s notebooks paragraph names `02_microstructure`.

**Block If:**
- The notebook cannot run under `warnings.simplefilter("error")` without filtering a warning from `nautilus_trader`/pandas/plotly internals that our code cannot avoid.
- Delegating `MultiLevelOFI`'s z-score changes any existing kernel/research test result.

**Never:**
- No new dependency (no scipy/statsmodels; OLS via `numpy.linalg.lstsq`).
- No research import of `views`, `ranking` or `data_api` (tests included); no new `test_boundaries.py` allowlist entry for them.
- No edit under `nautilus_trader/` or `crates/`, and never write `sprint-status.yaml`.
- No change to the `MarketFrames` port or `CatalogFrames.seconds()` columns. OFI and OBI z-scores are application-service outputs.
- No bars from the candle store in this notebook. The 1 m/5 m/1 h horizons come from `ReturnSeries.resample` of the 1 s series, per the story.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Outage | fixture BTC-USD-PERP.DYDX seconds 200-290 missing | grid rows NaN in mid/spread/microprice/obi/ofi/flow; returns across it NaN | — |
| Crossed second | ETHUSDT-LINEAR.BYBIT second 300 bid > ask | NaN in mid, spread, microprice, obi_*, ofi at that grid second; trade columns kept | — |
| AR(1) | phi = 0.5, n = 20 000 | lag-1 autocorrelation ≈ 0.5 (±0.03) | lag ≥ n → NaN, not raise |
| i.i.d. returns | synthetic 1 s, σ const | signature (variance per second) flat within 10% across 1-300 s | interval not a multiple → ValueError |
| Impact | Δmid = 2·sv + noise | slope per bucket ≈ 2 | bucket with < 3 points or zero variance → NaN |
| Hit rate | predictor/outcome signs | per-bin count, flat count, hit rate over non-zero pairs | empty bin → count 0, NaN rate |
| Short window | 600 s fixture, 1 h horizon | ACF/signature row NaN with n = 0 | never raises |
| Basis | mark/index rows in one second | basis = last mark - last index in that second; NaN where either absent | — |
| Mark-less (Bybit spot) | no mark/index/funding/OI | a "not collected" line, empty traces | no exception |

</intent-contract>

## Code Map

- `platform/kernel/indicators.py` -- `MultiLevelOBI`, `MultiLevelOFI` (z-score block :369-378), stateless `microprice/spread/mid_price/volume_delta` (:421+); add `RollingZScore`, `DepthProfile`, `snapshot_depth`, `cumulative_depth`, `depth_within_bps`.
- `platform/views/chart_series.py:141-176,544` -- `DepthProfile` (move), OrderBook `depth_profile` (stays, returns kernel type), inline snapshot profile (use `snapshot_depth`); `views/tests/test_book_features.py` imports `DepthProfile` from views (update).
- `platform/research/strategies/ofi_strategy.py:44-48,51-95,130-146` -- `_MAX_GAP_NS` -> `MAX_GAP_NS`; config defaults; replay order to mirror.
- `platform/research/application/frames.py` -- `CatalogFrames.seconds/funding/open_interest/mark_index`, `OBI_LEVELS`.
- `platform/research/application/inspection.py:471-535` -- `_spread`, `mid_series` (grid logic to generalise into a shared `second_grid`), `plot_axis`, `instrument_definitions` (tick size).
- `platform/research/domain/returns.py` -- `ReturnSeries.from_prices/resample/annualisation_factor`.
- `platform/research/notebooks/01_catalog_inspection.py`, `_params.py` -- the cell pattern to follow.
- `platform/research/tests/test_notebooks.py`, `fixture_catalog.py`, `conftest.py` -- the harness, `FixtureDefects`, session fixture.
- `platform/tests/test_boundaries.py:805` -- `KERNEL_MODULES` (no new kernel module: additions go in `indicators.py`).
- DDD spine `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md` (research row, kernel list, tree); `platform/ARCHITECTURE.md`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/kernel/indicators.py` -- add `RollingZScore(Indicator)` (`window>=2`; `update_raw(x)`; value = (x - mean)/population std over the last `window` readings, 0.0 when std is 0 or fewer than 2 readings, i.e. MultiLevelOFI's current rule); make `MultiLevelOFI` use it; move `DepthProfile` here; add `snapshot_depth(snapshot, levels) -> DepthProfile | None`, `cumulative_depth(profile) -> (bid_cum, ask_cum)`, `depth_within_bps(profile, bps_edges) -> (bid, ask)` (cumulative size whose price lies within each distance of mid).
- [x] `platform/kernel/tests/test_indicators*.py` -- tests for `RollingZScore` (closed-form), depth functions, and MultiLevelOFI z-score parity.
- [x] `platform/views/chart_series.py` + `views/tests/test_book_features.py` -- import `DepthProfile` from kernel; use `snapshot_depth` for the inline profile.
- [x] `platform/research/strategies/ofi_strategy.py` -- rename `_MAX_GAP_NS` to public `MAX_GAP_NS`.
- [x] `platform/research/domain/microstructure.py` -- `autocorrelation(series, lags)` (pairwise-complete Pearson at each lag in points), `volatility_signature(series, intervals_s)` (mean r_q² / q per interval via `resample`, with n), `realised_volatility(series, window)` (trailing sqrt(mean r²)·annualisation_factor; NaN when the window holds a NaN or is not contiguous), `price_impact(signed_volume, mid_change, edges)` (per |sv| bucket OLS slope/intercept/n via `lstsq`), `hit_rate_by_bin(predictor, outcome, edges)`.
- [x] `platform/research/application/inspection.py` -- `second_grid(seconds, start_ns, end_ns, columns)` generalising `mid_series` (which delegates to it). It returns the 1 s grid with NaN at missing and duplicate seconds, and NaN in book-derived columns at crossed seconds.
- [x] `platform/research/application/microstructure.py` -- `tick_size`, `spread_frame` (ticks, bps), `by_hour_utc`, `depth_summary` (first valid snapshot per minute → mean cumulative size by level and by bps, total bid/ask depth series), `obi_zscores(grid, window)`, `ofi_replay(seconds, config)` (raw + z, gridded), `microprice_edge(grid)` (predictor, next-second Δmid), `trade_flow(grid)` (buy/sell volume, counts, CVD over present seconds), `impact_inputs(grid)` (signed volume vs mid[t+1]-mid[t-1]), `quantile_edges`, `basis_frame(mark_index, start_ns, end_ns)`, `second_returns(grid)`.
- [x] `platform/research/notebooks/02_microstructure.py` + `.ipynb` -- sections: Parameters; Spread; Depth; Imbalance and OFI (±`ofi_threshold` lines); Microprice edge; Trade flow and impact; Funding, basis, OI on price; Returns (ACF at 1 s/10 s/1 m/5 m/1 h, signature, rolling RV); Observations with strategy pointers. Each section's markdown names the stored fields, the derived values and the deriving class (SIGNAL-01), and states that the screener's volatility is ranking's (SSOT-02) while this one is a research measure.
- [x] `platform/research/tests/test_microstructure.py` (domain matrix cases), `test_notebook_microstructure.py` (service over the session fixture: outage and crossed NaNs, OFI replay equals a manual strategy-order replay, tick size), `test_inspection.py` (`second_grid`).
- [x] Docs -- DDD spine research row/tree and kernel note, `platform/ARCHITECTURE.md` notebooks line, `research/README.md` stub list.

**Acceptance Criteria:**
- Given the session fixture, when `02_microstructure.py` runs via the 27.2 harness (`simplefilter("error")`, headless), then it finishes in under 60 s and the pairing test holds for its `.ipynb`.
- Given the fixture defects, when the service builds the grid frames, then the outage seconds and the crossed second are NaN in every plotted book series, and no value is interpolated.
- Given the notebook source, when read, then no cell contains a formula (all numbers come from `kernel.indicators`, `research.domain.microstructure`, `research.application.microstructure`/`inspection`), and the closing section lists spread regime changes, depth asymmetry, seasonality and autocorrelation sign per horizon with `research/strategies/ofi_strategy.py` named for OFI.
- Given the full platform suite, when run, then there are no failures beyond the pre-change baseline, and `test_boundaries.py` passes unchanged in its allowlists.

## Spec Change Log

## Review Triage Log

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 16: (high 0, medium 4, low 12)
- defer: 0
- reject: 5
- addressed_findings:
  - `[medium]` `[patch]` The OFI z-score was drawn against the thresholds through the strategy's warm-up, and §9 claimed it counted crossings. Now `warmup_end_ns` is added and shaded on the plot, and §9 says a crossing after warm-up is only a candidate entry, which the strategy's filters decide.
  - `[medium]` `[patch]` `RollingZScore` read a flat window whose mean rounds off the value (300 × 0.1) as z = ±1. Flatness is now an exact max == min comparison, so `MultiLevelOFI` inherits the fix.
  - `[medium]` `[patch]` The CVD carried its level across an outage, counting the unknown trades as zero. It now restarts from 0 after each missing or shared second.
  - `[medium]` `[patch]` With equal top sizes, `microprice - mid` came out as float noise with a random sign, which pulled hit rates toward 0.5. A predictor within 4 ulps of the mid is now 0 (flat).
  - `[low]` `[patch]` The microprice-edge and impact plots put all instruments on one axis in raw price units; each instrument now gets its own subplot row. "Total book size" is renamed stored top-20 depth. `DEPTH_LEVELS` = `second_snapshot.BOOK_DEPTH`, no longer the OBI tuple. `assert`s in production code are replaced by raises. `depth_within_bps` gets a 1e-9 relative slack, so a level on an edge stops flapping. `RollingZScore` refuses a non-finite reading. The first OBI reading's z-score is NaN.
  - `[low]` `[patch]` Docs: `Known limit:` notes for tick size (current definition over the whole window) and the impact window (overlap, neighbouring impact). The depth-mean docstring explains per-level counts. The ACF docstring no longer cites Box–Jenkins for a per-pair Pearson. Test fixes: a misnamed spread test, the hard-coded 600 and a missing bucket assertion. One test per behavioural patch.

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 0, medium 2, low 7)
- defer: 1: (high 0, medium 1, low 0)
- reject: 20
- addressed_findings:
  - `[medium]` `[patch]` `observations` printed an autocorrelation sign (momentum / mean reversion) from any point estimate, even at the 1 h horizon's ~23 pairs a day. `_sign` now returns `+`/`-` only outside the white-noise band `±2/√pairs`, `~0` inside it and `n/a` under 10 pairs. §9's markdown says so, and a parametrised test covers it.
  - `[medium]` `[patch]` `ofi_readings` claimed its z-score equals the strategy's own. On the first row after a gap, though, `OFIStrategy.on_data` evaluates its stale pre-gap value while the replay shows NaN. The docstring and §4 now record this as a `Known limit:` of the strategy, and the strategy defect itself is deferred.
  - `[low]` `[patch]` `RollingZScore` divided by zero when unequal readings have an underflowing std (`[0.0, 1e-170]`). A zero std now reads as flat, and there is a test.
  - `[low]` `[patch]` `depth_summary.by_level` reported only the bid side's snapshot count for both sides' means. It now has `bid_snapshots` and `ask_snapshots`, like `by_bps`.
  - `[low]` `[patch]` `snapshot_depth(levels < 1)` returned an empty profile that `depth_within_bps` then indexed. It now raises `ValueError`, with a test.
  - `[low]` `[patch]` `depth_bid_over_ask` divided by a zero or NaN ask mean, a `RuntimeWarning` that is fatal under `simplefilter("error")`. `_ratio` now returns NaN, with a test.
  - `[low]` `[patch]` The warm-up shading ran past `END` when the window was shorter than `warmup_seconds`. The new service function `warmup_span` clamps it to the window, with a test.
  - `[low]` `[patch]` `DepthProfile`, now a kernel value object, was a mutable dataclass. It is now `frozen=True`, with a test.
  - `[low]` `[patch]` The DDD spine said `MultiLevelOFI`'s delegation left its "behaviour unchanged". It now names the two deliberate changes: the exact flat-window comparison and the raise on a non-finite reading.

## Design Notes

- **Depth in kernel, not views:** the AC names `views.book_features.depth_profile`. That module is now `views/chart_series.py`, and research may not import `views`, a boundary the epic keeps. The snapshot→depth derivation is shared book math, so it moves to the kernel (SSOT-01), next to `mid_price`/`spread`/`microprice`. Views keeps only the `OrderBook` adapter.
- **OFI/OBI z-scores in the service, not `seconds()`:** the story draft suggested new `seconds()` options. Keeping the port unchanged avoids a column set that depends on strategy config.
- **Impact window:** a snapshot's trades are `[S, S+1)` and its book is sampled inside second S (S + 0.5 s venue-timed; arrival-timed on dYdX). `mid[t+1] - mid[t-1]` always spans the whole trade bucket. `Known limit:` a 2 s window adds one second of unrelated drift; the upgrade path is a trade-timestamp-aligned mid from the raw `OrderBookDelta` archive where it is stored.
- **Horizons:** ACF and signature come from `resample` of the 1 s mid returns, so they are internally consistent. A resampled bucket containing a gap is NaN.

## Verification

**Commands:**
- `cd platform && ../.venv/bin/python -m pytest -o addopts="" --rootdir=. research/tests kernel/tests views/tests tests/test_boundaries.py -q` -- expected: all pass
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests research/tests alerting/tests views/tests candles/tests capture/tests capture/venues/dydx/tests capture/venues/bybit/tests capture/venues/hyperliquid/tests collection_control/tests ranking/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q` -- expected: only the baseline Redis-dependent `data_api` failures
- ruff check/format + mypy on new and changed files -- expected: clean (beyond the pre-existing `kernel/catalog_files.py:129`/`second_snapshot.py:152`)

## Auto Run Result

Status: done

**Summary:** This was a follow-up review of the shipped Story 27.3, whose notebook `02_microstructure` and service `research.application.microstructure` sit on the kernel's `RollingZScore` and depth functions. It found no spec or intent defect. Nine patches were applied: an autocorrelation sign is now reported only when it is significant, the OFI replay's divergence after a gap is documented, and the kernel and service each gained a few edge-case guards.

**Files changed (this pass):**
- `platform/kernel/indicators.py` -- `RollingZScore` treats a zero std as flat; `DepthProfile` is frozen; `snapshot_depth` refuses `levels < 1`.
- `platform/research/application/microstructure.py` -- per-side depth-by-level counts; a significance-gated `_sign`; a NaN-safe `_ratio` for depth asymmetry; `warmup_span`; a `Known limit:` for the OFI replay after a gap.
- `platform/research/notebooks/02_microstructure.py` + `.ipynb` (jupytext-synced) -- clamped warm-up shading; §4 and §9 wording.
- `platform/kernel/tests/test_indicators_zscore_depth.py`, `platform/research/tests/test_notebook_microstructure.py` -- one test per patch.
- The DDD spine kernel note now names `RollingZScore`'s two deliberate behaviour changes.
- `deferred-work.md` gained one new entry for the strategy's stale OFI evaluation after a gap.

**Review:** 9 patches were applied (2 medium, 7 low) and 1 item was deferred. 20 findings were rejected:
- Most were spec-mandated: shared-second NaN, `research.application` importing the strategy config, ts_event order via a sorted `seconds()`, and the ulp tolerance on float rounding, which is not a malfunction.
- Others were already documented `Known limit:`s: O(window) z-score cost and overlap in the impact window.
- The rest were speculative or cosmetic, such as the impact x-axis at the labelled bucket edge, the column-list duplication and numpy-int windows.

**Verification:**
- `.venv`, `research/tests kernel/tests views/tests/test_book_features.py tests/test_boundaries.py`: 480 passed. This includes the notebook run under `simplefilter("error")` and the pairing test.
- System python, full platform suite: 10 failed, 2256 passed, 1 skipped.
  - 9 are the baseline Redis-dependent `data_api` failures.
  - 1 is the known order-dependent `ranking/tests/test_metrics_store.py::test_price_near_days_ago_returns_price_at_or_before_target_per_instrument`.
- ruff check and format are clean on the touched files. mypy reports only the 8 pre-existing `kernel/indicators.py` errors.

**Follow-up review recommended:** false. The patches are few, localized and each has its own test. The only behaviour changes are a divide-by-zero guard and the notebook's significance-gated sign labels.

**Residual risks / Known limits:**
- `OFIStrategy` still evaluates a stale z-score after a gap; this is deferred.
- The existing limits are unchanged: OBI z-score cost, overlap in the impact window, tick size taken from the current definition, NaN in `depth_within_bps` past the stored depth, `seconds()` materialising the window, and the flaky full-suite ranking test.
