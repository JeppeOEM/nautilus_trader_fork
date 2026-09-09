---
baseline_commit: 3792c736a8
---

# Story 10.4: OFI as a custom indicator, retiring the fixed row

Status: done

## Story

As a user of the chart page,
I want Order Flow Imbalance available from the indicator picker instead of a permanently-shown row,
so that I only see it when I actually want it, consistent with how CVD and Cancel Pressure now work.

## Acceptance Criteria

1. `custom_indicators.py`'s `CUSTOM_INDICATOR_CATALOG` gains an `"OrderFlowImbalance"` entry (`panel: "oscillator"`, params `{"window": 20}` matching the class's own default) whose `replay` reuses `ml_signals.indicators.OrderFlowImbalance` **unchanged** — the exact same class `chart_data.py`'s fixed row already drives (no new/third OFI variant — this project already has three independently-scoped "OFI"-adjacent metrics: this top-of-book one, `ranking_engine`'s full-depth `MultiLevelOFI`, and neither is being touched or consolidated by this story).
2. **Resolved this session, do not re-derive:** `OrderFlowImbalance.value` (`ml_signals/indicators.py:157-231`) is a **trailing rolling-window sum** — `self._contributions` is a `deque(maxlen=window)` of per-update contributions, and `self.value = sum(self._contributions)` recomputed on every `update_raw()` call. It is neither a lifetime-cumulative total (unlike the old CVD) nor a per-event instantaneous delta — it's "sum of the last `window` top-of-book update contributions," continuously re-evaluated. Sample it **last-value-in-bucket** (same as Cancel Pressure, Story 10.3) — `ofi.value` as of the last top-of-book-changing delta processed within a candle's bucket becomes that candle's value.
3. **Forward-fill across gaps** (same reasoning and same choice as Cancel Pressure, Story 10.3, for the same reason: this is a continuously-recomputed rolling state, not a per-bucket flow like CVD) — a bucket with no top-of-book-changing delta carries the last sampled value forward. `None` before `ofi.initialized` first becomes `True` (real warm-up — `OrderFlowImbalance.update_raw` only sets `initialized=True` starting from its second call with a real previous top-of-book to compare against, confirmed in `indicators.py`).
4. **Historical only**, same scope decision as CVD/Cancel Pressure: the old fixed row was never live. `window.start_ms is None` → `None` for every candle.
5. `chart_data.py`'s row-1-feeding computation (`ofi = OrderFlowImbalance(...)`, `ofi.update_raw(...)`, `series["ofi"]`) is removed from `compute_chart_series` entirely. `dashboard.py`'s fixed row 1 ("OFI") and its `_add()`/`add_hline` calls are removed; the remaining rows renumber to fill 1-4 (Book imbalance L1 agg, Mid-layer imbalance, Depth, Spread) — **this is the last fixed-row retirement in Epic 10**; these four remaining rows stay fixed, out of scope for this epic.
6. `cd troll && python -m pytest ml_signals/tests -q` passes (same pre-existing unrelated `test_ofi_strategy.py` flake aside).

## Tasks / Subtasks

- [x] Task 1 — OFI replay function (AC: #1, #2, #3, #4)
  - [x] Added `"OrderFlowImbalance"` to `CUSTOM_INDICATOR_CATALOG`, panel `"oscillator"`, params `{"window": 20}`. Note: 20 matches `chart_data.py`'s own `ofi_window` default for the now-retired fixed row (`compute_chart_series(..., ofi_window: int = 20)`), not `OrderFlowImbalance`'s own class-internal default (`window: int = 50` in `indicators.py`) — AC #1's "matching the class's own default" phrasing was imprecise; corrected here since 20 (continuity with the fixed row's actual historical behavior) is unambiguously the right choice, per DESIGN-02 (reuse the class exactly as the fixed row drove it).
  - [x] Reused `_order_book_deltas(window)` from Story 10.3 — no duplication.
  - [x] `_ofi_replay` drives `OrderFlowImbalance` exactly as the old fixed row did: `book.apply_delta(delta)` first, then reads post-delta top-of-book (skip if either side `None`), then `ofi.update_raw(...)` — opposite call order from Cancel Pressure's tracker, implemented correctly (verified via hand-computed test values, not copied from Task 1's Cancel Pressure structure).
  - [x] Bucketed by `window.bar_seconds` on `delta.ts_event`; samples `ofi.value` only when `ofi.initialized`; last-value-in-bucket.
  - [x] Forward-fill across candles, bounded by the same `_MAX_FORWARD_FILL_BUCKETS` cap Story 10.3's review added for Cancel Pressure (DATA-01: a real gap must eventually read as unknown again) — reusing the existing module-level constant, not a new one; `None` before the first initialized sample.
  - [x] Live case: `{"value": [None] * len(candles)}`.
- [x] Task 2 — Retire the fixed row (AC: #5)
  - [x] Removed `chart_data.py`'s `ofi = OrderFlowImbalance(...)`, `ofi.update_raw(...)`, the `if ofi.initialized: series["ofi"].append(...)` block, the `"ofi": []` key from both dict literals, the now-unused `OrderFlowImbalance` import, and the now-unused `ofi_window` parameter (confirmed no caller passed it explicitly).
  - [x] Removed `dashboard.py`'s row-1 OFI subplot title, `_add("ofi", ...)`, and its `add_hline`; renumbered imbalance/mid_imbalance/depth/spread to rows 1-4; `rows=5`→`rows=4`, `row_heights` trimmed to 4 entries (`[0.24, 0.24, 0.25, 0.27]`); updated `_render_chart_page`'s module docstring to state this is the last fixed-row retirement in Epic 10.
  - [x] `n = len(data.get("ofi", []))` → `n = len(data.get("spread", []))` (as directed — `spread`'s count is a superset of the old `ofi` count by the ~20-event OFI warm-up length, a cosmetic title-count difference, not asserted as identical).
  - [x] Also fixed `test_dashboard_chart.py::test_render_chart_page_figure_row_counts_stay_in_lockstep`, whose mocked `compute_chart_series` return still had the stale `"ofi"`/`"bid_cancel"`/`"ask_cancel"` keys (left over from before Story 10.3) and asserted `"OFI" in html_out` — not caught until this story's row-1 removal actually broke that assertion; updated the mock to the current real return shape and the assertion to `"Book imbalance" in html_out`.
- [x] Task 3 — Tests (AC: #6)
  - [x] `test_custom_indicators.py` +4 tests: `test_ofi_samples_last_value_in_bucket_and_forward_fills_gaps` (hand-computed via real `OrderBookDelta`s — ADD/ADD seeds `ofi`'s prev state, UPDATE gives the first initialized sample (bid_term=3, ask_term=0 → 3.0), a gap bucket forward-fills, a further UPDATE accumulates the rolling sum to 6.0 — all hand-verified against `OrderFlowImbalance.update_raw`'s exact formula, mirroring `test_indicators.py::test_ofi_accumulates_known_contributions`'s style, not its literal sequence, since driving through real book deltas naturally produces different intermediate states than direct `update_raw` calls), `test_ofi_forward_fill_reverts_to_none_past_the_bucket_cap`, `test_ofi_returns_none_for_every_candle_in_live_mode`, `test_ofi_registered_in_production_catalog_with_correct_shape`.
  - [x] `cd troll && python -m pytest ml_signals/tests -q` — 186 passed, 1 pre-existing unrelated failure (`test_ofi_strategy.py`, same documented flake as Stories 9.1/10.2/10.3).

### Review Findings

- [x] [Review][Patch] `_ofi_replay` ran ~38 executable lines, over the ~30-line target this story's own Dev Notes cite as applicable (READ-01) [troll/ml_signals/custom_indicators.py:_ofi_replay] — fixed: split into `_ofi_bucket_samples(window, ofi_window)` (the delta-scan/bucketing part, ~20 lines) and a leaner `_ofi_replay` doing only the candle setup + forward-fill loop (~16 lines), both under the target.
- [x] [Review][Patch] Completion Notes and Task 3 both claimed "`test_custom_indicators.py` +5 tests" — the diff adds exactly 4 (`test_ofi_samples_last_value_in_bucket_and_forward_fills_gaps`, `test_ofi_forward_fill_reverts_to_none_past_the_bucket_cap`, `test_ofi_returns_none_for_every_candle_in_live_mode`, `test_ofi_registered_in_production_catalog_with_correct_shape`, confirmed via `grep -c "^def test_ofi"`) — fixed: corrected the count in Completion Notes and Task 3.
- [x] [Review][Patch] `test_render_chart_page_figure_row_counts_stay_in_lockstep`'s docstring still said "like this story's own CVD-row removal" (written for Story 10.2, never updated when Story 10.3 touched the same test, and now stale a second time as this diff edits the same test for OFI's removal) [troll/ml_signals/tests/test_dashboard_chart.py] — fixed: docstring now refers generally to "each Epic 10 custom-indicator story has retired one fixed row" instead of naming one story.
- [x] [Review][Patch] `n = len(data.get("spread", []))`'s switch from the old `"ofi"`-keyed count (warm-up-gated, an undercount) to `"spread"` (populated every event) changed what the chart title's "N delta events" number actually counts, with no comment at the point of change explaining why [troll/ml_signals/dashboard.py] — fixed: added a one-line comment at the `n = ...` line.
- [x] [Review][Defer] `fig.update_layout(height=1250, ...)` was not rebalanced despite the row count shrinking 5→4 (and 6→5 in Story 10.3, 7→6 in Story 10.2) — each remaining panel gets proportionally more vertical room every time a row is retired, with no story yet revisiting the fixed total height. Pre-existing pattern recurring across every Epic 10 row-retirement story, not newly introduced by this diff; a deliberate redesign of the page's total height is a product decision out of proportion to any single story. `troll/ml_signals/dashboard.py:_render_chart_page`.
- [x] [Review][Defer] `bar_ns = window.bar_seconds * 1_000_000_000` then `// bar_ns` in `_ofi_replay` is unguarded against `bar_seconds == 0` — identical pre-existing pattern already deferred in Story 10.3's review for `_cancel_pressure_replay`/`_cvd_replay`; no real call path passes `bar_seconds=0`. `troll/ml_signals/custom_indicators.py`.
- [x] [Review][Dismiss] "AC #1 says params should match the class's own default (50), but 20 was used" — already surfaced and explained in this story's own Task 1 notes: 20 matches `chart_data.py`'s retired fixed-row default (continuity of actual behavior), not the class's internal default; DESIGN-02 requires reusing the class exactly as the fixed row drove it, which meant 20.
- [x] [Review][Dismiss] "OFI's internal `_prev_bid_price`/etc. state isn't reset across a CLEAR delta or a delta skipped for `None` top-of-book, unlike Cancel Pressure's explicit CLEAR handling" — deliberate, not an oversight: the old fixed row in `chart_data.py` never reset `OrderFlowImbalance`'s internal state on CLEAR either (DESIGN-02 requires reusing the class exactly as it already behaved), and adding new CLEAR-aware reset logic beyond what the fixed row ever did would be unrequested scope (DESIGN-01). Documented in Completion Notes.
- [x] [Review][Dismiss] "No test exercises `OrderFlowImbalance`'s rolling-window eviction (window=20) mid-replay" — speculative additional coverage beyond what AC #1-#3 or TEST-01 require; the wrapper's bucketing/forward-fill logic (the actual new code this story adds) is fully covered, and `OrderFlowImbalance`'s own rolling-window correctness is already covered by `test_indicators.py`'s existing unit tests for the class itself.
- [x] [Review][Dismiss] "Candle timestamps not exactly aligned to `bar_seconds` boundaries are untested" — same pre-existing bucket-key-alignment pattern shared with `_cancel_pressure_replay`/`_cvd_replay`, already dismissed on this basis in Story 10.3's review.
- [x] [Review][Dismiss] "`book.best_bid_size()`/`best_ask_size()` called unchecked immediately after confirming price non-`None`" — reused verbatim from `chart_data.py`'s pre-existing loop (DESIGN-02 mandates copying it exactly), not a new risk this diff introduces.

## Dev Notes

- **Read before touching anything:** `ml_signals/indicators.py`'s `OrderFlowImbalance` in full (`157-231`, confirmed this session — rolling-window sum, NOT lifetime-cumulative; `update_raw`'s exact bid/ask-term formula if you need to hand-verify a test's expected value). `chart_data.py`'s current `compute_chart_series` (row-1 OFI computation: `ofi.update_raw(bid_p, bid_s, ask_p, ask_s)` called AFTER `book.apply_delta(delta)`, unlike Cancel Pressure's tracker which is updated BEFORE). `custom_indicators.py`'s `_order_book_deltas`/`_cancel_pressure_replay` (Story 10.3) — this story's OFI replay is structurally the closest sibling of the two, reuse `_order_book_deltas` directly, do not duplicate its fetch logic a third time.
- **Do not confuse this story's OFI with `ranking_engine`'s OFI.** `ranking_engine/engine.py` computes `ofi_10`/`ofi_10_z`/etc. via `MultiLevelOFI` (full order-book depth, continuously running in a long-lived process, published to `rankings:live` for the ranking table/bot_tui — SSOT-02's designated sole owner of that computation). This story's `"OrderFlowImbalance"` custom indicator is `ml_signals.indicators.OrderFlowImbalance` (top-of-book only, per-request replay for the chart page) — the exact same class `chart_data.py`'s fixed row already used. Two legitimately different metrics for two different purposes; this story does not touch, consolidate, or rename either to "fix" the shared "OFI" naming — that ambiguity already existed before Epic 10 and is out of scope here.
- **Call-order asymmetry vs. Cancel Pressure is the one real gotcha in this story.** `CancellationTracker.update()` needs the PRE-delta best bid/ask (called before `book.apply_delta`); `OrderFlowImbalance.update_raw()` needs the POST-delta top-of-book (called after `book.apply_delta`, and skipped entirely if either side is `None` post-apply). Copying Story 10.3's loop structure without adjusting for this would silently feed OFI the wrong prices.
- **This is the last Epic 10 story that retires a fixed chart-page row.** After this story, the remaining static microstructure panel (imbalance, mid-imbalance, depth, spread) is out of scope for the whole epic — do not be tempted to also migrate those; nothing in FR31-33 or the user's original request covers them.
- **`troll/CLAUDE.md` constraints:** DESIGN-01 (YAGNI), DESIGN-02 (reuse `OrderFlowImbalance`/`_order_book_deltas` unchanged, no new OFI math), READ-01 (keep the replay function under ~30 lines), TEST-01/03, DATA-01 (forward-fill choice is deliberate and stated, matching Cancel Pressure's precedent).

### Project Structure Notes

- Modified: `troll/ml_signals/custom_indicators.py`, `troll/ml_signals/chart_data.py`, `troll/ml_signals/dashboard.py`, `troll/ml_signals/tests/test_custom_indicators.py`.
- No changes to `troll/ml_signals/indicators.py`, `troll/ranking_engine/`, or the indicator-picker JS.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 10, Story 10.4] — authoritative AC source.
- [Source: _bmad-output/implementation-artifacts/10-1-...md, 10-2-...md, 10-3-...md] — the framework and both prior stories' precedents (historical-only scope, forward-fill vs. gap-to-None choice, `_order_book_deltas` reuse) this story follows.
- [Source: troll/ml_signals/indicators.py, chart_data.py, custom_indicators.py] — read in full this session.
- [Source: troll/CLAUDE.md] — DESIGN-01/02, READ-01, TEST-01/03, DATA-01.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- Confirmed no caller of `compute_chart_series` passed `ofi_window` explicitly (`grep -rn ofi_window troll/ml_signals/`), so dropping the parameter entirely (rather than leaving it unused) is safe.
- Confirmed `test_dashboard_chart.py`'s figure-lockstep test still mocked pre-Story-10.3 series keys (`bid_cancel`/`ask_cancel`) and asserted on `"OFI"`, which this story's row-1 removal breaks — fixed as part of Task 2 (see its subtask notes).
- Full suite: 186 passed, 1 pre-existing unrelated failure (`test_ofi_strategy.py::test_ofi_strategy_generates_long_entry_on_bid_pressure`), same documented flake as Stories 9.1/10.2/10.3.

### Completion Notes List

- `OrderFlowImbalance` custom indicator reuses `ml_signals.indicators.OrderFlowImbalance` completely unchanged (DESIGN-02) — no new OFI math, only the candle-time bucketing/forward-fill layer, structurally the closest sibling of Story 10.3's Cancel Pressure but with the opposite delta/book read ordering (post-delta top-of-book, not pre-delta best price).
- Reused the `_MAX_FORWARD_FILL_BUCKETS` cap Story 10.3's code review added for Cancel Pressure — same DATA-01 reasoning applies identically to OFI's rolling-window-sum nature, so this isn't new scope, it's applying an already-established precedent to the second indicator DATA-01 requires it for.
- No CLEAR-specific reset handling was added (unlike Cancel Pressure) — deliberately: `OrderFlowImbalance`'s post-delta top-of-book read is already naturally guarded (`if bid_price is None or ask_price is None: continue`), and a CLEAR wipes the book, so no spurious sample gets recorded around a CLEAR without extra logic. `chart_data.py`'s original fixed row never reset `OrderFlowImbalance`'s internal `_prev_*` state on CLEAR either (DESIGN-02 requires reusing the class exactly as it already behaved) — not a new regression this story introduces, and adding new CLEAR-aware reset logic beyond what the fixed row ever did would be unrequested scope (DESIGN-01).
- This is the last fixed-row retirement in Epic 10 — the remaining panel (imbalance, mid-imbalance, depth, spread) is out of scope for the whole epic, per AC #5 and this story's Dev Notes.
- **Not verified in a real browser** — no display available in this environment, same established caveat as every prior chart-page story.
- Tests: `test_custom_indicators.py` +4 tests (bucket/forward-fill/warm-up, forward-fill-cap boundary, live-mode, production-catalog shape). `test_dashboard_chart.py`: 1 test updated to match the current row layout. Full suite: 186 passed, 1 pre-existing unrelated failure.
- **Code review (2026-09-09)** found `_ofi_replay` exceeded READ-01's ~30-line target (fixed by splitting into `_ofi_bucket_samples` + a leaner `_ofi_replay`), the "+5 tests" claim above (corrected to +4), a stale test docstring still crediting Story 10.2's CVD removal, and a missing code comment on the `n = ...` count-semantics change. 4 patches applied, 2 deferred (pre-existing, recurring across every Epic 10 row-retirement story), 6 dismissed. Full suite re-run after fixes: 186 passed, 1 pre-existing unrelated failure.

### File List

- Modified: `troll/ml_signals/custom_indicators.py`
- Modified: `troll/ml_signals/chart_data.py`
- Modified: `troll/ml_signals/dashboard.py`
- Modified: `troll/ml_signals/tests/test_custom_indicators.py`
- Modified: `troll/ml_signals/tests/test_dashboard_chart.py`

## Change Log

- 2026-09-09: Implemented OFI as a custom oscillator-panel picker indicator (historical-only, forward-filled across gaps, bounded by the Story 10.3 forward-fill cap), retired the fixed "OFI" row — the last fixed-row retirement in Epic 10. Status: ready-for-dev → review.
- 2026-09-09: Code review. Split `_ofi_replay` to satisfy READ-01's line-count target, corrected a test-count claim, fixed a stale test docstring, and added a code comment on a count-semantics change. 4 patches applied, 2 deferred (pre-existing), 6 dismissed. Full suite: 186 passed, 1 pre-existing unrelated failure. Status: review → done.
