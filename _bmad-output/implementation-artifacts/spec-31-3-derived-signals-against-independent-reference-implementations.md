---
title: 'Story 31.3: Derived signals against independent reference implementations'
type: 'feature'
created: '2026-09-29'
status: 'done'
baseline_revision: '5e324bbb8e'
final_revision: '7782bc45e0'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-31-context.md'
  - '{project-root}/platform/docs/DATA_DICTIONARY.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Every derived value that bots, backtests, rankings and charts use is tested only on hand-computed toy inputs, or by consistency checks that run the same code on both sides. Nothing proves that a signal means what `docs/DATA_DICTIONARY.md` §2–§3 says it means.

Several derived values also silently substitute one quantity for another:
- micro→mid in the chart lines.
- The ranking `price` falling back to a trade close.
- Mark prices backfilled into a trade-close series.
- `pct_1h`/`pct_24h` shortened at a gap.
- `metrics_nearest` with no distance bound.
- OFI state carried across time gaps.
- 1W buckets starting on Thursday, and 1W indicator panes computed on 1D bars.
- `cvd = 0.0` on an empty buffer.
- Three volatilities with confusable names.
- A units claim for a normalisation that does not exist.

**Approach:**
1. Write `verification/domain/reference_signals.py` from the dictionary text only, in stdlib `Decimal` (floats only where the definition itself is statistical).
2. Compare every production function with it on seeded adversarial generators, real soak fixtures and golden cases. Each comparison has a written tolerance, and a planted-defect test must fail it.
3. Decide every substitution: make it loud, or keep it as a tested, documented `Known limit:`.

## Boundaries & Constraints

**Always:**
- **The reference imports stdlib only** (`decimal`, `math`, `statistics`, `bisect`, `dataclasses`, `typing`). It does not import `kernel.indicators` or any other production module. Its module docstring cites the dictionary section each function implements, and the literature source for OFI (Cont, Kukanov and Stoikov 2014; for multi-level, Xu, Gould and Howison 2019).
- **Reference inputs are stored integer units plus a precision**, decoded to `Decimal` by the reference's own gap-encoding decoder, written from DATA_DICTIONARY §1.7. The production side decodes through `DydxSecondSnapshot`. The two decoders are therefore compared too.
- **Tolerances:**
  - A value that is exact at a known number of places is compared by quantizing the production float to those places (half-even):
    - `mid`: p+1 places
    - `spread`: p places
    - CVD, `volume_delta`, depth sums and count-OFI: s places
    - OHLC: p places; candle volume: s places

    If the float differs from its quantized value, that case is counted as **float noise**, its own reported class. Anything that is not equal after quantizing is a **difference**, and a difference always fails.
  - Division and statistical outputs (microprice, OBI, `avg_trade_size`, pct, basis, `funding_per_hour`, USD-notional OFI, z-score, stdevs, Pearson, lead-lag) use relative 1e-9 with absolute 1e-12. That bound is justified in a comment beside it: it covers float64 summation-order error over at most 3600 terms, which is under about 1e-12 relative, with a 1000× margin.
  - A tolerance is never widened to make a comparison pass.
- **Comparator location and boundaries:** comparator tests may import production code. Register each test module that crosses contexts in `test_boundaries.py`'s `COMPOSITION_ROOTS`, and fix the contradictory comment near `VERIFICATION_ALLOWED_MODULES`.
- **Tests:** seeded `random.Random(seed)` only. Tests are plain `-> None` functions using real Nautilus types.
- **Code rules:** the LGPL header, type hints, functions of about 30 lines or fewer, cognitive complexity of 10 or less, and a `Known limit:` with an upgrade path for every deliberate simplification.

**Block If:**
- The soak catalog (`platform/data/catalog`) holds no `custom_dydx_second_snapshot` rows for Bybit linear, Bybit spot or Hyperliquid. Real fixtures cannot be cut then, and must not be fabricated.

**Never:**
- Never modify `nautilus_trader/` or `crates/`, `sprint-status.yaml`, or a venue `config.toml`. Never add a dependency.
- Never set `awaiting-operator` or `operator_actions`. OPS-01: deploy steps go to `DEPLOY_CHECKLIST.md`.
- Do not re-implement the upstream Nautilus TA indicators. Only the picker's candle → indicator feed is checked (SMA, EMA).
- Do not decide `DummyStrategy` gap handling. It is recorded as deferred to Story 31.9.
- Do not write the trade, book, derivatives or candle-versus-kline tools. Those are Stories 31.4 to 31.8.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Empty side | bids `[]` | micro, mid and spread are None on both sides; the OFI contribution is 0; depth is NaN | none |
| Zero top sizes | `bs = as = 0` | reference micro is None. Production micro is None, and `price_series_rows` emits `micro: null` (no mid substitute) | none |
| Crossed | bid ≥ ask | every formula is computed as written (a negative spread); no clamp | none |
| OFI gap | Δts_event > `OFI_GAP_NS` (3 s) | previous-level state cleared before the diff, everywhere (ranking, `OFIStrategy`, `SnapshotStrategy`, `_replay_bucket_samples`, research microstructure) | none |
| pct shortfall | first point at or after the cutoff is more than `PCT_MAX_SHORTFALL_NS` (300 s) past the cutoff | `pct_*` is None | none |
| Nearest miss | the nearest metrics row is more than `NEAREST_TOLERANCE_S` (120 s) away | None, as for no rows (404) | none |
| 1W bucket | `ts` on a Wednesday | bucket starts the preceding Monday 00:00 UTC | none |
| 1W panes | `bar_seconds=604800` on the indicator routes | computed on 1W buckets (not clamped to 1D) | none |
| Empty rolling buffer | no snapshot held | `cvd` is None | none |
| Planted defect | OBI with bid and ask swapped; a level dropped from OFI; a close taken from the wrong row | comparison reports differences > 0, and the test asserts that | none |

</intent-contract>

## Code Map

- `kernel/indicators.py` -- `microprice`/`spread`/`mid_price`/`volume_delta`/`trade_aggregates`, `MultiLevelOFI` (position-aligned, `n = min` of the four lengths, `clear_prev_state`), `MultiLevelOBI` (keeps its previous value on a zero total), `RollingZScore` (ddof=0, 0.0 when undefined), `snapshot_depth`/`cumulative_depth`/`depth_within_bps` (1e-9 edge slack, NaN beyond depth). Inputs are `DydxSecondSnapshot.as_floats()` dicts.
- `kernel/second_snapshot.py` -- the gap-encoded integer rows (`to_dict`/`from_dict`), the constructor with absolute units, and `kernel/tests/snapshot_factory.make_snapshot`.
- `candles/domain/fold.py:147` -- `fold_arrays` (epoch bucket). `candles/domain/candle.py:34` holds `TIMEFRAMES`.
- `candles/application/forming.py` and `views/live_candles.py:320,405` -- live bucket math.
- `views/chart_series.py`:
  - `:652` micro→mid.
  - `:897-940` `_replay_bucket_samples` (no gap reset, `ts // bar_ns` bucket).
  - `price_series_rows`, `compute_chart_series`.
- `views/indicator_picker.py:554,646,713` -- bucket math. `CumulativeVolumeDelta` is at `:524`, and `replay_native` at `:347`.
- `views/ranking_columns.py:62-90` -- the false `usdFromTokens`/`bpsFromPriceUnits` comment and the volatility labels. The TS mirror is `frontend/src/pages/RankingsPage.tsx`; the docs claims are `frontend/src/pages/docs/data.ts:89,102,166`; the pin is `data_api/tests/test_ranking_columns_mirror.py`.
- `data_api/routes/indicators.py:60,247` and `indicator_series.py:44,72` -- the silent clamp to 86400.
- `ranking/domain/board.py`:
  - `OFI_GAP_NS` at `:55-75`.
  - `fast_metrics` (`cvd` 0.0) at `:117-153`.
  - `_fast_volatility` at `:176`.
  - The price→slow-close fallback at `:482`.
- `ranking/domain/metrics.py:26-63` -- `price_stats_from_series` (searchsorted base, `np.std` ddof=0) and `pct_change_from`.
- `ranking/domain/volatility.py` -- `VolatilityTracker.score` (sample stdev of pct returns of mids).
- `ranking/infrastructure/catalog_prices.py:47-66` -- the mark-price fallback.
- `ranking/infrastructure/metrics_store.py:82-90,178-226` -- `_nearest_row` (unbounded) and `price_near_days_ago`.
- `ranking/tests/test_replay.py` + `fixtures/replay_burst.json` -- byte pins of `rankings:live` and `metrics.db`.
- `research/strategies/ofi_strategy.py:49,135` (5 s gap), `research/strategies/snapshot_strategy.py:95` (no gap), `research/application/microstructure.py:323` (5 s).
- `research/domain/{returns,correlation}.py`, `research/application/aligned.py:217` (`funding_per_hour`), and `research/application/microstructure.py` (`spread_frame`, `trade_flow`).
- `tests/test_boundaries.py` -- `COMPOSITION_ROOTS` (`:154-190`), the verification allowlist and its comment (`:1966-1993`), and `VERIFICATION_ROOTS`.
- `verification/infrastructure/catalog_reader.py` -- pyarrow reads for the fixture cutter.
- Soak data: `platform/data/catalog/data/custom_dydx_second_snapshot/{BTCUSDT,ETHUSDT}-{LINEAR,SPOT}.BYBIT`, `SOL-USD-PERP.HYPERLIQUID`.

## Tasks & Acceptance

**Execution:**
- [x] `verification/domain/reference_signals.py` -- the reference, in stdlib `Decimal`:
  - **Book decoding:** `RefBook` (Decimal levels, decoded from gap-encoded units plus precision).
  - **Top-of-book:** `mid`, `spread`, `microprice`.
  - **Imbalance:**
    - `obi(n)`.
    - `ofi_step(prev, cur, n, usd)`: CKS terms, level i used only when present in all four lists, the withdrawal notional at the previous price.
    - `rolling_ofi(books, n, window, usd, gap_ns)`: resets on a gap, rolling sum.
    - `zscore(history, window)`: floats, ddof=0, None when undefined.
  - **Flow:** `cvd`, `volume_delta`, `avg_trade_size`, `depth_within_bps` (NaN beyond depth).
  - **Candles:** `bucket_start(ts, width)` (Monday anchor for 604800) and `fold_candles` (Decimal OHLCV over traded seconds, `seconds_observed`).
  - **Price stats:**
    - `pct_change`, using the bounded base rule, plus the 1w/1m as-of rule (at or before, within tolerance).
    - `vol_score_1h` (ddof=1, mid pct returns, age window, ends kept).
    - `vol_catalog_24h` (ddof=0, trade-close pct returns).
    - `vol_fast` (ddof=1, last 300 mids).
  - **Research functions:**
    - `simple_returns`, adjacent buckets only.
    - `resample`, compounding; a bucket is None when incomplete.
    - `pearson`, pairwise-complete; None below 2 pairs or when a side is exactly constant.
    - `rolling_pearson`.
    - `lead_lag`: positive lag means a leads b.
    - `basis_bps`: `(a/b - 1) * 1e4`.
    - `funding_per_hour`: `rate * 60 / interval_min`.
- [x] `verification/domain/signal_compare.py` -- `Agreement` (EXACT / FLOAT_NOISE / WITHIN_TOL / DIFFERENT, plus BOTH_UNDEFINED / UNDEFINED_MISMATCH), `at_places(prod, ref, places)`, `relative(prod, ref, rel, abs_)`, and a `Tally` that counts each class per signal.
- [x] `verification/tests/signal_cases.py` -- seeded generators:
  - 1–50 levels, empty sides, zero totals, one-sided and crossed books.
  - Mixed precisions p, s in 0..8.
  - Time gaps, duplicate seconds, and NaN in float series.
  - Builders for both the production snapshot (`DydxSecondSnapshot`) and the reference `RefBook` from the same integers.
- [x] `verification/tools/cut_snapshot_fixtures.py` (a root, registered in `VERIFICATION_ROOTS`):
  - Reads the soak's snapshot Parquet with pyarrow and writes `verification/tests/fixtures/snapshots/<iid>.jsonl.gz`: 300 consecutive stored rows as JSON of the stored integers, plus a README line with the source window and revision.
  - Run it once for the five soak instruments and commit the output.
- [x] `verification/tests/test_reference_signals.py` (+ `test_reference_series.py` if it grows past about 800 lines) -- the comparisons, each on generators, on real fixtures and on golden cases:
  - **Kernel:**
    - microprice, mid, spread, OBI 1..20 (with `MultiLevelOBI`'s keep-previous rule pinned as a Known limit).
    - `MultiLevelOFI` count and USD at 3/5/10 with window 50 and 300, and z-score at 3600 (0.0-when-undefined pinned).
    - `volume_delta`, `trade_aggregates`→CVD/`avg_trade_size`, `depth_within_bps`.
  - **Views:** `price_series_rows` (mid, micro, CVD-weighted price) and `_replay_bucket_samples` OFI/OBI per bar.
  - **Candles:** `fold_arrays` at every `BAR_SECONDS` and every `TIMEFRAMES` width.
  - **Ranking:**
    - `InstrumentMetrics.fast_metrics` (cvd, `volume_delta`, `avg_trade_size`, `volatility_fast`).
    - `VolatilityTracker.score`, `price_stats_from_series`.
    - `pct_change_from` + `price_near_days_ago` (tmp store).
  - **Research:**
    - `ReturnSeries.from_prices`/`resample`.
    - `correlation_of`/`rolling_correlation`/`lead_lag`/`basis_bps`.
    - `funding_per_hour`, `spread_frame`, `trade_flow`.
  - **Picker:** `replay_native` SMA/EMA feed.
  - **Planted defects**, each asserting DIFFERENT > 0:
    - OBI with bid and ask swapped.
    - OFI using `n-1` levels.
    - A fold taking the first close instead of the last.
    - A reference with ddof swapped.
    - pct using the latest point as its base.
- [x] `candles/domain/fold.py` -- `bucket_start_ms(ts_ms, bar_seconds)`. A width that divides a day stays epoch-aligned; 604800 is anchored at Monday 00:00 UTC. Route every bucket computation through it: `fold_arrays`, `live_candles` (both sites), `chart_series._replay_bucket_samples`, and the three `indicator_picker` sites. `Known limit:` comment for other non-day-dividing widths.
- [x] `data_api/routes/indicators.py`, `indicator_series.py` -- raise `_MAX_BAR_SECONDS` to 604800 (the candles route's bound; the 7-day `MAX_QUERY_SPAN_SECONDS` keeps reads bounded). Add a test that a 1W request yields buckets 604800 apart, Monday-anchored.
- [x] `views/chart_series.py` -- micro is None when microprice is undefined (no mid substitute). Reset `_replay_bucket_samples` OFI with `clear_prev_state` on a gap greater than `kernel.indicators.OFI_GAP_NS`.
- [x] `kernel/indicators.py` -- `OFI_GAP_NS = 3_000_000_000`, the one gap threshold. The rule is strict `>` on the `ts_event` difference. `ranking/domain/board.py`, `research/strategies/ofi_strategy.py`, `research/strategies/snapshot_strategy.py` and `research/application/microstructure.py` import it; the local 3 s and 5 s constants are deleted.
- [x] `ranking/domain/board.py` -- `cvd` None on an empty buffer. Delete the price→slow-close fallback: `price` is the mid or None.
- [x] `ranking/domain/metrics.py` -- `PCT_MAX_SHORTFALL_NS = 300 s`: None when the base point lies more than that past the cutoff.
- [x] `ranking/infrastructure/catalog_prices.py` -- delete the mark-price fallback. A window with no trade is an empty series (stats None). Update the docstring and `test_catalog_prices.py`.
- [x] `ranking/infrastructure/metrics_store.py` -- `_nearest_row` bounded by `NEAREST_TOLERANCE_S = 120` (two write intervals). A test covers inside and outside the bound.
- [x] `ranking/tests/test_replay.py` -- if the pinned bytes change, re-record only after proving each changed field is one this story changed deliberately. Record the reason in the test docstring.
- [x] `views/ranking_columns.py`, `frontend/src/pages/RankingsPage.tsx`, `frontend/src/pages/docs/data.ts`:
  - Labels become "Vol 24h σ (trade closes)" for `volatility` and "Vol 1h σ (mids)" for `volatility_score`.
  - The docs describe `volatility_fast` as the third metric.
  - Remove the false normalisation claims and state the raw units: base-token units for cvd and `volume_delta`, price units for spread and `microprice_lean`.
  - Add a test (`data_api/tests/test_ranking_columns_mirror.py` or a sibling) that fails if `frontend/src` names a helper it does not define. The mirror test stays green.
- [x] `tests/test_boundaries.py` -- `COMPOSITION_ROOTS` entries for the comparator test modules. Register `verification.tools.cut_snapshot_fixtures`. Correct the comment.
- [x] Docs:
  - `DATA_DICTIONARY.md` §2.1–§2.7, §3.2–§3.4:
    - the OFI level rule and its source, and the unified gap threshold;
    - the zero-total, empty-side and z-score behaviours, as Known limits;
    - the three named volatilities; the pct bound; the nearest tolerance;
    - the 1W Monday anchor and 1W panes; no micro→mid; no mark backfill; `cvd` None;
    - a new §2.13 "Reference signals" giving the tolerance table;
    - `DummyStrategy` deferred to 31.9;
    - the extra substitutions found (`microprice_edge` flat→0, `oi_changes` OI≤0→NaN, the volatility-mode None→0 sort) as pinned Known limits.
  - `DATA_INTEGRITY_AUDIT.md` rows D-77 onward, one per decision, with the evidence.
  - A `VERIFICATION_REPORT.md` row: derived signals, VERIFIED/DEVIATION, float-noise counts, the fixture window.
  - A `DEPLOY_CHECKLIST.md` deferred entry to redeploy `ranking_engine` and `data_api` and rebuild the frontend.

**Acceptance Criteria:**
- Given `make test`, when `verification/tests/test_reference_signals.py` runs, then every listed production function agrees with its reference on generators, fixtures and golden cases, with 0 DIFFERENT, and float noise is reported as its own count.
- Given each planted defect, when compared, then the tally shows DIFFERENT > 0 and the test asserts it.
- Given `tests/test_boundaries.py`, when run, then the reference module imports stdlib only, and the comparator tests are declared composition roots.
- Given each preamble substitution, when the story ships, then it is either fixed with a test or documented as a `Known limit:` in code and in DATA_DICTIONARY with a test pinning it, and it has an audit row.

## Design Notes

**The OFI level rule, stated for the dictionary.** Level i of the top n contributes only when i exists in the current and previous bid and ask lists: `n_eff = min(n, |bid|, |ask|, |prev_bid|, |prev_ask|)`. That avoids a one-sided bias when one side is thinner. With USD notional, a price-up or unchanged term is `size × current price`, and a withdrawal is `prev_size × prev price`, where the size actually rested.

**Why production changes (all pre-prod, "nothing frozen"):**
- 3 s wins over 5 s because it is the live, published and documented value.
- The Monday anchor matches venue weekly klines, which 31.8 needs, and the frontend's week.
- Removing the 1W clamp is safe because every indicator read is already capped at 7 days.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. verification/tests kernel/tests candles/tests views/tests ranking/tests research/tests data_api/tests tests/test_boundaries.py -q` -- expected: all pass.
- `cd platform/frontend && npx vitest run` -- expected: all pass.
- `cd platform && ruff format --check verification kernel candles views ranking research data_api tests && ruff check verification kernel candles views ranking research data_api tests` -- expected: clean on changed files.
- `cd platform && mypy verification` -- expected: no new errors.

## Spec Change Log

## Review Triage Log

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 15: (high 1, medium 5, low 9)
- defer: 1: (high 0, medium 1, low 0)
- reject: 5: (medium 1, low 4)
- addressed_findings:
  - `[high]` `[patch]` The replay window for the custom indicators in `indicator_values_page` had no span cap (years of raw rows at 1W, ~1.4 years already at 1D), and the route's comment said it was bounded. It is now capped at `MAX_QUERY_SPAN_SECONDS`, and older bars read None. Test added; audit D-90.
  - `[medium]` `[patch]` The board comparator skipped microprice, `microprice_lean`, spread, counts, one-sided rows, and the `cvd`/`price` None on an empty buffer. All are covered now. The lean's float cancellation (168 cases, all within 8 ulps of the mid) is counted as its own class, WITHIN_ULPS, with 0 differences. The no-substitute fixture test now reaches the deleted fallback path.
  - `[medium]` `[patch]` `compute_chart_series` still emitted the raw float spread, which D-88 had fixed elsewhere. It now uses `kernel.indicators.spread`. Test added.
  - `[medium]` `[patch]` The reference depth copied production's 1e-9 edge slack. The reference is now exact, and the comparator reports a slack-only disagreement as its own class, EDGE_SLACK.
  - `[medium]` `[patch]` D-89 claimed in-code Known limits that were not there. `Known limit:` comments, each with an upgrade path, are now at the OBI zero total, the z-score 0.0, `_ROUNDING_ULPS`, `_ZERO_VARIANCE_RTOL`, `oi_changes` OI≤0, the Thursday anchor of `ReturnSeries.resample`, the pct quiet-market rule, and the depth slack.
  - `[medium]` `[patch]` The tolerance justification contradicted the §2.13 magnitudes. Both texts are reconciled. An infinite production value is now DIFFERENT, and a float reference is read via `repr`.
  - `[low]` `[patch]` The golden checks now assert EXACT/WITHIN_TOL, not merely "not DIFFERENT".
  - `[low]` `[patch]` Input guards in the reference: `_ints` refuses non-ints; a window < 1, lead-lag bounds, and a resample period that is not a multiple all raise; mid ≤ 0 gives NaN.
  - `[low]` `[patch]` The 1W-base test now puts rows on and ±1 ns around both tolerance edges.
  - `[low]` `[patch]` The fixture tests assert rows strictly 1 s apart.
  - `[low]` `[patch]` The fixture README labels the "cutter checkout revision", flags a dirty tree, and points to VERIFICATION_REPORT for the soak's collector revision.
  - `[low]` `[patch]` The nearest-row bounds are clamped to the int64 range.
  - `[low]` `[patch]` Differing bucket sets are recorded as DIFFERENT, not raised as a KeyError.
  - `[low]` `[patch]` `SnapshotStrategy` handles one-sided rows as `OFIStrategy` does. Test added.
  - `[low]` `[patch]` The frontend-helper guard also scans the `views/ranking_columns.py` comments.

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 0, medium 1, low 8)
- defer: 1: (high 0, medium 1, low 0)
- reject: 23: (high 0, medium 2, low 21)
- addressed_findings:
  - `[medium]` `[patch]` D-90's 7-day cap on the custom-indicator replay leaves a value only on the last 7 1D bars or the last 1W bar, and a 1W OFI/OBI pane holds at most two buckets. Neither was written as a `Known limit:`. Both now are, with an upgrade path (stored per-bar aggregates), in `views/chart_series.py`, both indicator routes and DATA_DICTIONARY §2.5.
  - `[low]` `[patch]` `_nearest_row` clamped only `ts ± tolerance`, not `ts` itself, so a `ts_ns` past int64 (e.g. 2**64) still overflowed the SQLite bind (a 500). It now clamps `ts` first. The test covers ±2**64.
  - `[low]` `[patch]` DATA_DICTIONARY §2.2/§2.3 now say plainly that one-sided rows are skipped by the ranking, the strategies and `ofi_readings` but fed by the chart replay (a Known limit with an upgrade path). The §2.3 wording is no longer self-contradictory.
  - `[low]` `[patch]` The audit rows are in order (D-89 before D-90). DEPLOY_CHECKLIST and VERIFICATION_REPORT now cite D-90.
  - `[low]` `[patch]` The VERIFICATION_REPORT row states the real fixtures' scope: gap-free two-sided rows, with the edge cases covered by generators and golden cases only.
  - `[low]` `[patch]` Stale `kernel/indicators.py:N` refs in `frontend/src/pages/docs/data.ts` now point at the current lines.
  - `[low]` `[patch]` The fixture cutter refuses `--rows < 1`.
  - `[low]` `[patch]` The fixture cutter runs git in its own checkout, not the caller's cwd.
  - `[low]` `[patch]` The duplicated `_pinned_zero`/`_carried` test helpers are now one copy each, `pinned_zero`/`carried` in `verification/tests/signal_cases.py`.

## Auto Run Result

Status: done

**Summary (follow-up review pass):** A fresh Blind Hunter + Edge Case Hunter review of the whole 31.3 diff (baseline 5e324bbb8e) found no intent gap and no spec defect. It produced 9 patches, all local: documentation of limits, one input clamp, tooling guards and test hygiene. It also produced 1 deferral and 23 rejections. The story's substance is unchanged: an independent stdlib-`Decimal` reference for every derived signal, compared with 0 DIFFERENT on generators, real fixtures and golden cases, and every preamble substitution fixed or pinned as a Known limit.

**Files changed in this pass:**
- `platform/views/chart_series.py`, `platform/data_api/routes/{indicators,indicator_series}.py`: `Known limit:` comments for the 7-day replay cap at 1D/1W.
- `platform/ranking/infrastructure/metrics_store.py` + `ranking/tests/test_metrics_store.py`: clamp `ts` before binding; ±2**64 test.
- `platform/verification/tools/cut_snapshot_fixtures.py`: the `--rows` guard, git runs in its own checkout.
- `platform/verification/tests/{signal_cases,test_reference_signals,test_reference_series}.py`: shared `pinned_zero`/`carried`.
- `platform/docs/DATA_DICTIONARY.md` (§2.2 one-sided readers, §2.3 wording, §2.5 1W/1D limits), `DATA_INTEGRITY_AUDIT.md` (row order), `DEPLOY_CHECKLIST.md` + `VERIFICATION_REPORT.md` (D-90, fixture scope).
- `platform/frontend/src/pages/docs/data.ts`: current line refs.

**Review:**
- **Patches:** 9 (1 medium, 8 low).
- **Deferred:** 1, the pre-existing `test_backtest_runner` constructor errors.
- **Rejected:** 23, among them:
  - The reference's "independence" and the ABS_TOL floor: both are as the spec sets them.
  - The dual meaning of `price`: already deferred.
  - A z-score underflow that no input produces.
  - Cutter anchor, README and provenance items rejected in pass 1.
  - Defensive guards in the reference for rows the encoder never writes, which would fail loudly.
  - Test-clock and threshold-cadence nits.

**Follow-up review recommended:** false. This pass's changes are localized, low-consequence documentation, tooling and test-hygiene fixes plus one input clamp. No behaviour of a published signal changed.

**Verification:**
- The spec's pytest command: 1825 passed. There are 9 failures (they need Redis on 127.0.0.1:6379) and 5 errors (`test_backtest_runner` constructor, deferred); both were already there at baseline.
- After the clamp fix: `ranking/tests/test_metrics_store.py` + `verification/tests` 236 passed; boundaries, frontend-helper, mirror and chart-series tests 112 passed.
- vitest: 353 passed.
- ruff format is clean; ruff check is clean on the changed files. The 4 remaining errors in the touched directories are pre-existing, in untouched code. `mypy verification` is clean.
- `cut_snapshot_fixtures --rows 0` now exits with a usage error.

**Residual risks:**
- The real fixtures are 300 gap-free rows from a ~37-minute soak, so rarer classes may surface in 31.11's longer soak.
- The 1D/1W custom-indicator and 1W OFI/OBI panes are sparse by design until stored per-bar aggregates exist.
- The production changes take effect only after the deferred redeploy in DEPLOY_CHECKLIST.

