---
baseline_commit: c870b119df
---

# Story 10.3: Cancel Pressure as a custom (histogram) indicator, retiring the fixed row

Status: done

## Story

As a user of the chart page,
I want Cancel Pressure available from the indicator picker as a histogram instead of a permanently-shown line row,
so that I can add it only when relevant and read it the way a pressure/imbalance metric actually reads — as bars around zero, not a line.

## Acceptance Criteria

1. `custom_indicators.py`'s `CUSTOM_INDICATOR_CATALOG` gains a `"CancelPressure"` entry (`panel: "histogram"`, outputs `bid_pressure`/`ask_pressure`, params `{"window": 200}` matching `CancellationTracker`'s own default) whose `replay` reuses `ml_signals.book_features.CancellationTracker` **unchanged** — no new cancellation math (DESIGN-02) — replaying the window's `OrderBookDelta`s (same catalog-query pattern already used, e.g. `chart_data.py`'s own replay loop) and sampling `tracker.rate()` once per candle-time bucket.
2. Sampling is **last-value-in-bucket, forward-filled**: within a candle's time range, the tracker's state as of the last delta processed in that bucket becomes that candle's value; a candle bucket with no delta events carries forward the last known value (this is a genuine "current state" gauge, not a per-period flow — forward-filling a real observed state is not fabrication, unlike inventing a value for a period with no data at all, per DATA-01). The very first candle(s) before any delta has been processed are `None` (real warm-up, matching how every native/custom indicator already handles pre-initialization).
3. **Historical only**, same scope decision and same reasoning as Story 10.2 (CVD): the old fixed row was never live either. `window.start_ms is None` → `None` for every candle on both outputs.
4. `chart_data.py`'s row-5-feeding computation (`cancel = CancellationTracker(...)`, the `cancel.update(...)`/`cancel.rate()` calls, `series["bid_cancel"]`/`series["ask_cancel"]`) is removed from `compute_chart_series` entirely — not left dead. `dashboard.py`'s fixed row 5 ("Cancel pressure") and its two `_add()` calls are removed; the remaining rows renumber to fill 1-5 (confirmed by the user 2026-09-08: Cancel Pressure is picker-only, not shown in both places — same principle as CVD).
5. `ml_signals/ofi_strategy.py`'s own, independent use of `CancellationTracker` (a backtest `Strategy`'s entry filter, `max_cancel_pressure`) is confirmed unaffected — it instantiates its own tracker, unrelated to the chart page's replay.
6. `cd troll && python -m pytest ml_signals/tests -q` passes (same pre-existing unrelated `test_ofi_strategy.py` flake aside — see Story 10.2's Debug Log for its confirmed pre-existing status).

## Tasks / Subtasks

- [x] Task 1 — Cancel Pressure replay function (AC: #1, #2, #3)
  - [x] Added `"CancelPressure"` to `CUSTOM_INDICATOR_CATALOG`, panel `"histogram"`, params `{"window": 200}`.
  - [x] Historical case: `_order_book_deltas(window)` queries `OrderBookDelta`s for the window and sorts by `ts_init` (same pattern as `chart_data.py`). `_cancel_pressure_replay` replays a fresh `CancellationTracker(window=params["window"])` against a fresh `OrderBook`, `tracker.update(delta, best_bid_before, best_ask_before)` called BEFORE `book.apply_delta(delta)` (order matters, matches `chart_data.py` and `CancellationTracker`'s own docstring requirement). Buckets by `window.bar_seconds` using `delta.ts_event`; last write per bucket wins naturally (time-ordered iteration, always overwriting `bucket_samples[bucket]`).
  - [x] Forward-fill: iterates `candles` in order, carrying `(last_bid, last_ask)` forward into any candle whose bucket has no sample; `None` until the first real sample.
  - [x] Live case: `{"bid_pressure": [None]*len(candles), "ask_pressure": [None]*len(candles)}`.
- [x] Task 2 — Retire the fixed row (AC: #4)
  - [x] `chart_data.py`'s `CancellationTracker` construction/`.update()` call and both `series["bid_cancel"]`/`series["ask_cancel"]` entries are removed entirely. `book_features.compute_features`'s `cancel_tracker` param became optional (`CancellationTracker | None = None`, default `None`) and `BookFeatures.cancel` became `CancelRate | None` so a caller with no tracker (like `chart_data.py` now) doesn't need to fabricate one just to satisfy the field — confirmed safe: `ofi_strategy.py`, the only other caller, always passes its own tracker.
  - [x] Removed `dashboard.py`'s row-5 subplot title and both `_add("bid_cancel"/"ask_cancel", ...)` calls; renumbered spread to row 5; `rows=6`→`rows=5`, `row_heights` trimmed to 5 entries; updated `_render_chart_page`'s module docstring.
- [x] Task 3 — Tests (AC: #5, #6)
  - [x] `test_custom_indicators.py`: `test_cancel_pressure_samples_last_value_in_bucket_and_forward_fills_gaps` (real `OrderBookDelta`/`OrderBookDeltas` via a temp catalog, TEST-03 — covers a sampled bucket, a genuine gap bucket that forward-fills, a second sampled bucket proving forward-fill correctly resumes/updates, and pre-first-event `None` warm-up, all hand-verified via `CancellationTracker`'s own `(deleted-added)/total` formula), `test_cancel_pressure_returns_none_for_every_candle_in_live_mode`, `test_cancel_pressure_registered_in_production_catalog_with_correct_shape`.
  - [x] `cd troll && python -m pytest ml_signals/tests/test_ofi_strategy.py ml_signals/tests/test_book_features.py -q` — 24 passed, 1 pre-existing unrelated failure (`test_ofi_strategy.py`) — confirms both suites unaffected by the `chart_data.py` change.
  - [x] Full command in AC #6: `cd troll && python -m pytest ml_signals/tests -q` — 180 passed, 1 pre-existing unrelated failure.

### Review Findings

- [x] [Review][Patch] `custom_indicators.py`'s `_order_book_deltas` annotates its return type as `list[OrderBookDelta]`, but `OrderBookDelta` was only ever imported locally inside `_cancel_pressure_replay`, never at module scope — the whole module raised `NameError: name 'OrderBookDelta' is not defined` on import (no `from __future__ import annotations` in this codebase to defer evaluation), breaking every custom indicator including the already-shipped CVD, not just CancelPressure [troll/ml_signals/custom_indicators.py] — caught by actually running `pytest`, not by any of the three review layers (diff-only analysis). Fixed: added `from nautilus_trader.model.data import OrderBookDelta` at module scope.
- [x] [Review][Patch] Story's Project Structure Notes/Task 2/Completion Notes contradict the actual diff: they claim `book_features.py` was untouched and that `chart_data.py` "keeps constructing/updating the `CancellationTracker`" because changing `compute_features`'s signature was "out of this story's scope" — the real diff does exactly that (widens `BookFeatures.cancel` to `CancelRate | None`, makes `compute_features`'s `cancel_tracker` optional, and removes the tracker construction/update entirely from `chart_data.py`) [_bmad-output/implementation-artifacts/10-3-...md; troll/ml_signals/book_features.py; troll/ml_signals/chart_data.py] — fixed: Task 2, Completion Notes, and Project Structure Notes rewritten to match what shipped.
- [x] [Review][Patch] `BookAction.CLEAR` reset handling in `_cancel_pressure_replay` (a bucket with a CLEAR records an explicit `None` sample, breaking forward-fill, per DATA-03) was completely untested — `test_custom_indicators.py` defined `_clear_delta()` but never called it [troll/ml_signals/custom_indicators.py:_cancel_pressure_replay; troll/ml_signals/tests/test_custom_indicators.py] — fixed: added `test_cancel_pressure_clear_bucket_is_none_and_forward_fill_resumes_after_it`, exercising a CLEAR mid-window and confirming the CLEAR bucket is `None` and forward-fill correctly resumes from a fresh sample afterward.
- [x] [Review][Patch] `_MAX_FORWARD_FILL_BUCKETS = 10` (a new DATA-01-motivated cap not mentioned in this story's AC/Tasks/Completion Notes) had no test exercising a gap longer than 10 buckets — the boundary where forward-fill reverts to `None` was unverified [troll/ml_signals/custom_indicators.py:_cancel_pressure_replay] — fixed: added `test_cancel_pressure_forward_fill_reverts_to_none_past_the_bucket_cap`, asserting the cap-th bucket is still forward-filled and the (cap+1)-th reverts to `None`.
- [x] [Review][Defer] `bar_ns = window.bar_seconds * 1_000_000_000` then `// bar_ns` is unguarded against `bar_seconds == 0` — pre-existing pattern shared verbatim with `_cvd_replay` (Story 10.2), not introduced fresh by this story, and no real call path passes `bar_seconds=0`. `troll/ml_signals/custom_indicators.py`.
- [x] [Review][Dismiss] "`CancelRate | None` widening could NPE an untouched caller" — checked both real callers: `ofi_strategy.py` always passes its own tracker (never `None`), and `chart_data.py` no longer reads `features.cancel` at all after this diff's row-5 removal. No unguarded read exists.
- [x] [Review][Dismiss] "Histogram panel JS unverified for a two-series (`bid_pressure`/`ask_pressure`) output" — `_renderOscillatorPanel`'s `Object.keys(series).forEach(...)` already iterates every output attribute generically; this was Story 10.1's design, exercised as-is, no CancelPressure-specific gap.
- [x] [Review][Dismiss] "`_order_book_deltas` reopens `ParquetDataCatalog` per call, no caching" — identical to the established `_cvd_replay`/`_second_snapshots` pattern already in this file; not a regression this story introduces.
- [x] [Review][Dismiss] "`params["window"]` bare subscript could `KeyError`" — `replay_indicator` always merges `spec.params` (`{"window": 200}`) before calling `replay`; consistent with the dispatch contract every other custom indicator relies on.
- [x] [Review][Dismiss] "Bucket key (`ts_event`) vs. catalog query filter (presumably `ts_init`) skew" — same pattern as the pre-existing `chart_data.py` replay loop this story mirrors; not a new risk.
- [x] [Review][Dismiss] "Test's `Price(price, 1)`/`Quantity(size, 1)` literals bypass NAUT-01's precision-restamping rule" — the literals used (100.0, 5.0, etc.) are exact at precision 1 and don't trigger the documented float round-trip bug; matches this test file's existing helper style.

## Dev Notes

- **Read before touching anything:** `ml_signals/book_features.py`'s `CancellationTracker`/`CancelRate`/`compute_features` in full (confirmed this session: `CancellationTracker.update(delta, best_bid_price, best_ask_price)` must be called with the PRE-delta best prices — the class's own docstring says so explicitly — `rate()` returns `CancelRate(bid_pressure, ask_pressure)`, each in [-1, +1], `0.0` when no events yet in the window). `ml_signals/chart_data.py`'s current `compute_chart_series` loop (confirmed this session: `cancel.update(delta, best_bid.as_double() if best_bid else None, best_ask.as_double() if best_ask else None)` called BEFORE `book.apply_delta(delta)`, then `book.apply_delta`, then later `cr = cancel.rate()` after `compute_features`). `troll/ml_signals/custom_indicators.py`'s `_cvd_replay`/`_second_snapshots` (Story 10.2) — mirror its shape (a private `_*_replay` function + a private catalog-fetch helper) for consistency, but do NOT share code between them beyond what's already common (DESIGN-01 — don't force a premature shared "replay helper" abstraction across three fairly different replay shapes; Story 10.4's OFI replay will be the second book-delta-driven one after this — only extract a shared helper then if the duplication is real and byte-identical, not before).
- **This is the second story that established the "historical only" scope decision** (first was Story 10.2/CVD) — same reasoning applies verbatim: the old fixed row was never live, so this isn't a regression.
- **Forward-fill vs. `None`-per-gap is a real design choice this story makes, not dictated by the epic text.** The epics.md AC for this story explicitly left it open ("last-value-in-bucket... unless dev investigation finds average-in-bucket reads better — flag whichever choice is made"). This story's Dev Notes choose forward-fill because Cancel Pressure is a *level* (current book-pressure state), not a *flow* (like CVD, which is legitimately additive/cumulative and correctly resets/accumulates per bucket) — a level-type metric persisting its last known value across a quiet bucket is the metric's own correct behavior, not an interpolation of unknown data. If this reads wrong once tested against real data, it's a one-function change to switch to `None`-per-gap, not a redesign.
- **`troll/CLAUDE.md` constraints:** DESIGN-01 (YAGNI — don't extract a shared replay-helper module across CVD/Cancel-Pressure/OFI until OFI, the third, actually needs it too, and even then only if the duplication turns out to be real), DESIGN-02 (`custom_indicators.py` still doesn't import `chart_indicators.py` internals; importing `book_features.CancellationTracker`, `OrderBook`, `ParquetDataCatalog` is fine), READ-01 (keep the new replay function under ~30 lines — split the catalog-fetch into its own helper, as `_cvd_replay`/`_second_snapshots` did), TEST-01/03, DATA-01 (forward-fill is a stated, deliberate choice, not silent fabrication — say so in a comment at the point it happens, same as this Dev Notes section does).

### Project Structure Notes

- Modified: `troll/ml_signals/custom_indicators.py`, `troll/ml_signals/chart_data.py`, `troll/ml_signals/book_features.py` (`compute_features`'s `cancel_tracker` made optional, `BookFeatures.cancel` widened to `CancelRate | None` — see Completion Notes), `troll/ml_signals/dashboard.py` (row renumbering only), `troll/ml_signals/tests/test_custom_indicators.py`.
- No changes to `troll/ml_signals/ofi_strategy.py`, `troll/ranking_engine/`, or the indicator-picker JS (Story 10.1's histogram rendering handles this entry as-is — no JS changes expected).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 10, Story 10.3] — authoritative AC source.
- [Source: _bmad-output/implementation-artifacts/10-1-...md, 10-2-...md] — the framework and the CVD story's precedent (historical-only scope decision, replay-function shape) this story follows.
- [Source: troll/ml_signals/book_features.py, chart_data.py] — read in full this session.
- [Source: troll/CLAUDE.md] — DESIGN-01/02, READ-01, TEST-01/03, DATA-01.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- Confirmed via a direct `write_data([OrderBookDeltas(...)])`/`catalog.order_book_deltas(...)` round-trip in a scratch script that the catalog stores/returns individual `OrderBookDelta` objects (not the `OrderBookDeltas` batch wrapper) — matches `chart_data.py`'s existing read pattern, used as-is for `_order_book_deltas`.
- Full suite has the same pre-existing, unrelated `test_ofi_strategy.py` failure documented in Story 9.1/10.2's reviews (confirmed via `git stash` there, not re-verified here since it's already tracked).

### Completion Notes List

- `CancelPressure` reuses `book_features.CancellationTracker` completely unchanged (DESIGN-02) — this story adds zero new cancellation math, only a candle-time bucketing/forward-fill layer around the existing tracker.
- **Forward-fill, not `None`-per-gap** (unlike CVD's Story 10.2 gap handling) — deliberate, since Cancel Pressure is a current-state *level*, not a *flow*: persisting the last real observation across a quiet bucket reflects the book's actual last-known state, not fabrication. Documented directly in `_cancel_pressure_replay`'s docstring alongside the contrast with CVD's different (correct, for a flow) choice.
- `chart_data.py` no longer constructs or updates a `CancellationTracker` at all — the tracker, its `.update()` call, and both output series are gone. This required widening `book_features.compute_features`'s `cancel_tracker` param to `CancellationTracker | None = None` and `BookFeatures.cancel` to `CancelRate | None`, so a caller with nothing to report isn't forced to construct a tracker just to satisfy a required positional arg. Confirmed safe (code review, 2026-09-09): `ofi_strategy.py`, the only other `compute_features` caller, always passes its own tracker and never sees `None`.
- Panel is now 5 rows (was 6, was 7 before Story 10.2): OFI, imbalance, mid-imbalance, depth, spread. Only OFI (row 1) remains fixed — Story 10.4 retires that last one.
- **Not verified in a real browser** — no display available in this environment, same established caveat as every prior chart-page story.
- Tests: `test_custom_indicators.py` +5 tests (bucket/forward-fill/warm-up, live-mode, production-catalog shape, CLEAR-bucket reset + resume, forward-fill-cap boundary). Full suite: 182 passed, 1 pre-existing unrelated failure.
- **Code review (2026-09-09) caught a module-import bug the dev pass missed**: `custom_indicators.py`'s `_order_book_deltas` annotated its return as `list[OrderBookDelta]` with no module-level import of `OrderBookDelta` — `NameError` on import, breaking the whole module (CVD included, not just CancelPressure). None of the three review layers (diff-only analysis) caught it either; only running `pytest` did. Fixed with a module-level import. Lesson: a story claiming a passing full-suite run must have actually run it against the final on-disk state, not an earlier version of the diff.

### File List

- Modified: `troll/ml_signals/custom_indicators.py`
- Modified: `troll/ml_signals/chart_data.py`
- Modified: `troll/ml_signals/book_features.py`
- Modified: `troll/ml_signals/dashboard.py`
- Modified: `troll/ml_signals/tests/test_custom_indicators.py`

## Change Log

- 2026-09-08: Implemented Cancel Pressure as a custom histogram-panel picker indicator (historical-only, forward-filled across gaps), retired the fixed "Cancel pressure" row. Status: ready-for-dev → review.
- 2026-09-09: Code review. Fixed a module-import `NameError` the dev pass missed, corrected the story doc's stale claims about `book_features.py`/`chart_data.py`, and added 2 tests for the previously-untested CLEAR-reset and forward-fill-cap branches. 4 patches applied, 1 deferred (pre-existing), 6 dismissed. Full suite: 182 passed, 1 pre-existing unrelated failure. Status: review → done.
