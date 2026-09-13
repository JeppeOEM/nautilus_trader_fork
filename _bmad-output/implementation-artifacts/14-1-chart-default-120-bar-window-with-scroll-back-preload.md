---
baseline_commit: 82dbc7606ee296c823376febaf9235a5128baad2
---

<!-- Standalone bypass-epic story, no epics.md entry -- same precedent as epics 5/6/7/9/11
     (sprint-status.yaml's own comments document this precedent). Numbered 14 (not folded
     into an existing epic) because Epic 12/13 are already real epics.md entries unrelated to
     the chart page, and epics 9/11 (the last two chart-page bypass stories) are already
     marked done. Created, dev'd, and self-reviewed in one pass at the user's explicit
     request for full end-to-end autonomy (no check-ins) -- same precedent as epics 9/11.

     Context found at story-creation time: the requested mechanism (default visible window +
     buffered preload + refill-on-scroll-back) was *already mid-implementation, uncommitted*
     in the working tree when this story was created (dashboard.py already had
     `_CANDLE_VISIBLE_BARS=100,_CANDLE_BUFFER_BARS=100,_CANDLE_REFILL_MARGIN_BARS=20`,
     `_loadDefaultCandleWindow()`, and matching JS tests, all uncommitted, alongside other
     unrelated uncommitted work -- a docs page, ranking_columns.py relabeling, Docker/env
     changes). This story's actual scope is therefore narrow: raise the two bar counts from
     100 to 120 as requested, confirm the existing refill-on-scroll-back mechanism still
     holds at the new scale, and formalize the whole (pre-existing + tuned) mechanism as a
     tracked, tested story instead of leaving it as untracked working-tree state. -->

# Story 14.1: Chart default 120-bar window with buffered scroll-back preload

Status: done

## Story

As a user of the `/chart/{id}` page,
I want the candlestick chart to default to showing the most recent 120 bars, with another 120 bars already preloaded behind that so panning back doesn't hit a visible edge, and further history to keep preloading automatically as I keep scrolling back,
so that I get a fuller default view and uninterrupted back-panning without ever seeing a "wait, no more data" gap.

## Acceptance Criteria

1. **Default visible window is 120 bars, not 100.** A bare `/chart/{id}` visit (no explicit `start`/`end` query params) zooms the candlestick chart to the most recent 120 bars (`_CANDLE_VISIBLE_BARS`).
2. **120 more bars are preloaded/buffered behind the visible window**, not 100 (`_CANDLE_BUFFER_BARS`) -- `_loadDefaultCandleWindow()`'s catalog-backed fetch reaches back `_CANDLE_VISIBLE_BARS + _CANDLE_BUFFER_BARS` bars from now, so panning left from the default view has 120 already-loaded bars before hitting the buffered edge.
3. **Scrolling back into the buffer keeps preloading further history automatically.** The existing pan-triggered refill mechanism (`_maybeLoadOlder`/`_loadOlderChunk`, Story 7.1/8.1) is unchanged in *logic* -- it already fires once only `_CANDLE_REFILL_MARGIN_BARS` (20) bars of the buffer are left unseen on scroll-back -- and continues to function correctly at the new 120/120 scale (proven by the existing JS test, which reads `_CANDLE_VISIBLE_BARS`/`_CANDLE_BUFFER_BARS`/`_CANDLE_REFILL_MARGIN_BARS` as live globals rather than hardcoding 100, so it automatically covers the new values without modification).
4. **This applies to Candles mode** (the only mode `_loadDefaultCandleWindow` drives). **Lines mode is deliberately left on its own existing time-based chunking** (`_chunkSpanMs`'s 15-minute lines chunk, `_maybeLoadOlder`'s proportional half-chunk margin for `mode==='lines'`) rather than retrofitted with an equivalent "120-bar" concept -- Lines mode has no bar-size concept at all (it's a continuous bid/ask/mid/micro/price series, not OHLC bars), so "120 bars" doesn't translate; its own pan-to-load-more mechanism (Story 7.1/8.1) already provides the same "keep preloading on scroll-back, never hit a dead edge" guarantee, just measured in wall-clock time instead of bar count. No behavior change to Lines mode in this story.
5. **No regressions.** `cd troll && python -m pytest ml_signals/tests -q` passes; the JS harness (`test_dashboard_chart_pan_js.py`, skipped automatically if `node` isn't installed) passes when run where `node` is available.

## Tasks / Subtasks

- [x] Task 1 — Raise the two bar-count constants (AC: #1, #2)
  - [x] `dashboard.py`'s `_LIVE_CHART_JS`: `_CANDLE_VISIBLE_BARS=100,_CANDLE_BUFFER_BARS=100` → `_CANDLE_VISIBLE_BARS=120,_CANDLE_BUFFER_BARS=120`. `_CANDLE_REFILL_MARGIN_BARS` left at 20 (not requested to change, and 20 remains a sensible "still plenty of runway" trigger point at the larger 120-bar buffer).
- [x] Task 2 — Confirm the refill mechanism still holds at 120/120 (AC: #3)
  - [x] Re-read `_maybeLoadOlder`/`_loadOlderChunk`/`_chunkSpanMs` (dashboard.py) -- confirmed none of the three hardcodes 100 anywhere; all three read `_CANDLE_VISIBLE_BARS`/`_CANDLE_BUFFER_BARS`/`_CANDLE_REFILL_MARGIN_BARS` as live globals, so the constant bump alone is sufficient, no other code change needed.
  - [x] The existing JS test (`test_dashboard_chart_pan_js.py`'s `_maybeLoadOlder`/`_loadDefaultCandleWindow` assertions) already derives its expected values from the live `_CANDLE_VISIBLE_BARS`/`_CANDLE_BUFFER_BARS`/`_CANDLE_REFILL_MARGIN_BARS` globals rather than hardcoding 100 -- confirmed it passes unmodified at 120/120 (ran via a direct Node extraction, since this sandbox's Python env can't import `dashboard.py` without `redis`/Docker; the project's own Docker-based `make test` skips these specific tests when `node` isn't present in the collector image).
- [x] Task 3 — No Lines-mode changes (AC: #4)
  - [x] Confirmed `_chunkSpanMs`'s `mode==='lines'` branch and `_maybeLoadOlder`'s `mode==='lines'` margin branch are untouched by this story.

## Dev Notes

- **This is a two-line constant change, not new architecture.** The default-window/buffer/refill mechanism itself (`_loadDefaultCandleWindow`, `_maybeLoadOlder`'s bar-count refill margin) already existed, uncommitted, in the working tree before this story was created -- see this file's leading comment. This story's only functional change is `100` → `120` in both places, plus writing the actual tracked story/tests for the whole mechanism.
- **Why 20 stays as the refill margin, not scaled to e.g. 24 (20% of 120):** the user asked specifically for 120/120, not for the margin to scale proportionally. 20 bars of remaining runway before a refill fires is still a comfortable margin at 120-bar buffers (a fetch completes well before the user could pan through 20 more bars), so there's no functional reason to change it, and changing an unrequested value would be scope creep (DESIGN-01/ponytail YAGNI).
- **Interacts with Story 14.2** (same session, same epic): 14.2 converts the chart page's other panel (imbalance/mid-imbalance/depth/spread) to a client-fetched panel with its own loading spinner, for a faster/non-blocking initial page load. That story is independent of this one's bar-count change -- read together only because they touch the same file in the same session.
- **Verification limitation (consistent with every prior chart-page story -- 7.1/8.1/8.2/8.4/9.1/11.1/12.2 all document the same gap):** not verified in a real browser, no display available in this environment. Recommend a manual pass: open `/chart/{id}` for any actively-subscribed instrument, confirm the candlestick chart initially shows ~120 bars, and confirm dragging left past the initially-visible window keeps loading further history without a visible gap.

### Project Structure Notes

- Modified: `troll/ml_signals/dashboard.py` (two-constant change only for this story; see Story 14.1's sibling 14.2 for the rest of this session's dashboard.py diff).
- No new files for this story.

### References

- [Source: troll/ml_signals/dashboard.py] — `_LIVE_CHART_JS`'s `_CANDLE_VISIBLE_BARS`/`_CANDLE_BUFFER_BARS`/`_CANDLE_REFILL_MARGIN_BARS`, `_loadDefaultCandleWindow`, `_maybeLoadOlder`, `_loadOlderChunk`, `_chunkSpanMs` — full module read this session.
- [Source: troll/ml_signals/tests/test_dashboard_chart_pan_js.py] — existing JS harness covering the refill/default-window mechanism, confirmed to already parametrize on the live bar-count globals rather than hardcoding 100.
- [Source: _bmad-output/planning-artifacts/epics.md, Epic 8/"Charting library decision stands"] — stays on Plotly, no new charting library; this story doesn't touch charting library choice at all.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `.venv/bin/ruff check --fix` + `.venv/bin/ruff format` applied to `dashboard.py` (shared with Story 14.2's changes in the same file/session) — import ordering and docstring formatting only, no logic changes.
- `.venv/bin/mypy troll/ml_signals/dashboard.py` — 6 pre-existing errors, none in code this story (or Story 14.2) touches (confirmed via `git diff --unified=0` hunk ranges); not introduced by this story.
- `docker compose run --rm --no-deps collector python3 -m pytest ml_signals/tests -q` — 203 passed, 5 skipped (the 5 skips are the JS-harness tests, skipped because `node` isn't installed in the collector image; verified separately via a direct Node extraction of `_LIVE_CHART_JS`, all pass).

### Completion Notes List

- Bumped `_CANDLE_VISIBLE_BARS`/`_CANDLE_BUFFER_BARS` from 100 to 120 (dashboard.py). No other code change was needed -- the refill mechanism already parametrizes on these constants rather than hardcoding 100 anywhere.
- Lines mode deliberately left untouched (AC #4) -- it has no bar-size concept, so "120 bars" doesn't apply to it; its own time-based pan-to-load-more mechanism already gives the equivalent guarantee.
- Not verified in a real browser (no display in this environment) -- see Dev Notes.

### File List

- Modified: `troll/ml_signals/dashboard.py`

## Change Log

- 2026-09-13: Bumped chart default-window bar counts from 100/100 to 120/120 per user request; confirmed the pre-existing (uncommitted) refill-on-scroll-back mechanism holds at the new scale with no other code change. Formalized as a tracked story since the mechanism previously existed only as untracked working-tree state. Full `ml_signals` test suite passes (203 passed, 5 skipped — node unavailable in the test container; verified separately via direct Node execution). Status: backlog → done.
