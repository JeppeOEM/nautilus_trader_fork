# Deferred Work

### DW-1: `LightweightChart.tsx` sizes itself from `container.clientWidth` at mount and only re-syncs on `window`'s own `resize` event -- a layout-only resize that …

origin: migrated from legacy ledger ("Deferred from: code review of 15-3-chart-page-foundation-candlestick-and-cursor-paginated-history (2026-09-15)"), 2026-10-05
location: LightweightChart.tsx
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-3-chart-page-foundation-candlestick-and-cursor-paginated-history.md` summary: `LightweightChart.tsx` sizes itself from `container.clientWidth` at mount and only re-syncs on `window`'s own `resize` event -- a layout-only resize that doesn't fire a window resize (a container inside a flex/grid parent reflowing, a hidden-tab/collapsed-panel becoming visible after mount with initial `clientWidth: 0`) leaves the chart at a stale or zero width until an unrelated window resize happens to fire. evidence: `troll/frontend/src/components/chart/LightweightChart.tsx`'s mount effect calls `createChart(container, { width: container.clientWidth, ... })` and `window.addEventListener("resize", handleResize)` only -- no `ResizeObserver` on the container itself. Not reachable via this story's own `ChartPage.tsx` (the container always renders directly, not inside a hidden/collapsed panel), so it doesn't block this story; flagged as a general chart-sizing robustness gap worth fixing once, in this one shared component, before Story 15.4 (indicator panes) and later stories build more UI around it. Surfaced independently by both Blind Hunter and Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/components/chart/LightweightChart.tsx:713-714 ResizeObserver on the container

### DW-2: `RankingsBus`'s per-`/ws/live`-listener `asyncio.Queue` (`redis_bus.py:subscribe`) has no `maxsize`/backpressure, and nothing caps the number of concurrent …

origin: migrated from legacy ledger ("Deferred from: code review of 15-2-live-coin-rankings-page (2026-09-14)"), 2026-10-05
location: /ws/live
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-2-live-coin-rankings-page.md` summary: `RankingsBus`'s per-`/ws/live`-listener `asyncio.Queue` (`redis_bus.py:subscribe`) has no `maxsize`/backpressure, and nothing caps the number of concurrent listeners, so a client whose read loop stalls without a clean disconnect grows its queue unboundedly. evidence: Confirmed by reading `RankingsBus.handle_message`'s `queue.put_nowait(message)` fan-out loop and `subscribe()` -- both real, not hypothetical. Low urgency in practice: `rankings:live` publishes roughly once per heartbeat (~5s) or on rank change, not a tick-level firehose, and `data_api` is SEC-01 localhost/SSH-tunnel-only with one operator -- but this is exactly the unbounded-queue-growth failure shape this project has been burned by before (the `DataEngine` OOM incident that motivated the standalone collector), so it's tracked rather than dismissed.
status: done 2026-10-05
resolution: already resolved: platform/views/rankings_bus.py:44,68,100 QUEUE_MAX=1000 bounded queue + put_drop_oldest

### DW-3: `RankingsBus.handle_message`'s `logger.warning` on every malformed `rankings:live` payload has no rate-limiting -- a misbehaving/partially-deployed publisher …

origin: migrated from legacy ledger ("Deferred from: code review of 15-2-live-coin-rankings-page (2026-09-14)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-15-2-live-coin-rankings-page.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-2-live-coin-rankings-page.md` summary: `RankingsBus.handle_message`'s `logger.warning` on every malformed `rankings:live` payload has no rate-limiting -- a misbehaving/partially-deployed publisher emitting malformed messages continuously would spam the log at full message-rate. evidence: Confirmed by reading the unconditional `logger.warning(..., %r, message)` call on every rejected payload with no counter/throttle. No real publisher misbehavior observed in production yet; flagged pre-emptively per this repo's TEST-04 "warnings are not noise" convention.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-live-channel-hardening
resolution-undo: 9218902ccb75097d93a8b92f08359dcf9efaeb5e4be78a0192c010aee5086dda 2026-10-05 7374617475733a206f70656e

### DW-4: `useLiveChannel`'s reconnect backoff (`RECONNECT_BASE_MS * attempt`, capped at 10s) has no jitter, so every open client losing its connection at the same …

origin: migrated from legacy ledger ("Deferred from: code review of 15-2-live-coin-rankings-page (2026-09-14)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-15-2-live-coin-rankings-page.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-2-live-coin-rankings-page.md` summary: `useLiveChannel`'s reconnect backoff (`RECONNECT_BASE_MS * attempt`, capped at 10s) has no jitter, so every open client losing its connection at the same moment (e.g. a `data_api` restart) reconnects on the same synchronized schedule. evidence: Confirmed by reading `useLiveChannel.ts`'s fixed linear-backoff formula. Low real-world impact for a single-operator internal dashboard (at most a handful of browser tabs), not fixed as part of this story.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-live-channel-hardening
resolution-undo: 9218902ccb75097d93a8b92f08359dcf9efaeb5e4be78a0192c010aee5086dda 2026-10-05 7374617475733a206f70656e

### DW-5: No explicit graceful-drain path for open `/ws/live` connections when `data_api` shuts down -- only the background `RankingsBus.run()` task is cancelled in …

origin: migrated from legacy ledger ("Deferred from: code review of 15-2-live-coin-rankings-page (2026-09-14)"), 2026-10-05
location: /ws/live
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-2-live-coin-rankings-page.md` summary: No explicit graceful-drain path for open `/ws/live` connections when `data_api` shuts down -- only the background `RankingsBus.run()` task is cancelled in `app.py`'s `lifespan`. evidence: Confirmed by reading `lifespan()`'s shutdown path, which only awaits/cancels the bus task. Relies on uvicorn/Starlette's own ASGI-level connection teardown on process exit, which is adequate for this story's actual (rare-restart, single-operator) deployment shape; a bespoke drain registry was judged out of proportion to the risk (DESIGN-01 YAGNI).
status: open

### DW-6: ~~An instrument that never trades within its ring buffer's 25h retention window loses `pct_1h`/`pct_24h`/`volatility` freshness permanently~~ -- assessed …

origin: migrated from legacy ledger ("Deferred from: code review of 13-2-in-memory-long-window-price-series (2026-09-12)"), 2026-10-05
location: troll/ranking_engine/price_series.py:PriceSeriesStore.ingest
reason: **~~An instrument that never trades within its ring buffer's 25h retention window loses `pct_1h`/`pct_24h`/`volatility` freshness permanently~~ -- assessed 2026-09-12, accepted as not a practical risk, not fixed.** `PriceSeriesStore.ingest()` only feeds from live trades (`close_price`), never from mark price, so the buffer only stays fresh if at least one trade lands somewhere in the trailing 25h window; the mark-price fallback in `catalog_stats.price_series()` only ever runs once, at the instrument's initial backfill. The mechanism is real (confirmed by reading both functions and the collector's `snapshots:raw` schema, which has no live mark-price field), but the *trigger condition* -- a market with a continuously-updating order book (so it stays "fresh" in `_LAST_SEEN`) yet zero taker trades across a full 24-25h span -- does not occur on real dYdX perpetual markets: liquidations, funding-rate arbitrage, and cross-exchange arb guarantee at least occasional taker fills well inside 24h on any market that is still genuinely trading (user domain call, 2026-09-12). A market that truly saw zero trades for a full day would already be functionally delisted/dead, a state this system has no obligation to serve fresh stats for. Not fixed; left as an accepted, theoretical-only gap rather than a scheduled follow-up. `troll/ranking_engine/price_series.py:PriceSeriesStore.ingest`, `troll/ranking_engine/engine.py:_ingest_snapshot_batch`.
status: open

### DW-7: `_backfill_new_instruments`'s per-instrument Parquet reads at cold start are a plain sequential loop, not concurrent.

origin: migrated from legacy ledger ("Deferred from: code review of 13-2-in-memory-long-window-price-series (2026-09-12)"), 2026-10-05
location: troll/ranking_engine/engine.py:_backfill_new_instruments
reason: **`_backfill_new_instruments`'s per-instrument Parquet reads at cold start are a plain sequential loop, not concurrent.** On a fresh process start with ~29 instruments all newly seen in the same 60s cycle, this serializes ~29 blocking Parquet reads (each via its own `asyncio.to_thread`) before `_SLOW_METRICS`/`metrics_store` see their first write for any of them. A deliberate YAGNI choice per the story's own Design Notes (this is a one-time cost, not the recurring-cost path being fixed), and this story's review restored a cycle-duration canary (`_slow_loop_task`'s `logger.warning` if a cycle exceeds `DB_WRITE_INTERVAL_SECONDS`) that will surface it if cold-start latency ever actually becomes a problem in practice. Revisit with `asyncio.gather`-based concurrent backfill only if the canary trips. `troll/ranking_engine/engine.py:_backfill_new_instruments`.
status: open

### DW-8: `PriceSeriesStore.backfill()`'s live-merge silently prefers the live value over a historical Parquet value sharing the same timestamp, with no check that the …

origin: migrated from legacy ledger ("Deferred from: code review of 13-2-in-memory-long-window-price-series (2026-09-12)"), 2026-10-05
location: troll/ranking_engine/price_series.py:PriceSeriesStore.backfill
reason: **`PriceSeriesStore.backfill()`'s live-merge silently prefers the live value over a historical Parquet value sharing the same timestamp, with no check that the two actually agree.** `backfill()` drops any historical point at or after the earliest already-buffered live point, assuming redundancy (correct under the documented one-point-per-instrument-per-second model) — but if a genuine data-integrity issue ever produced *different* prices for the same instrument/second between the live stream and the Parquet catalog, that divergence would be silently resolved in favor of the live value with no logging, invisible to any future investigation. Cheap to add (a value-mismatch check + `logger.warning`) but not done in this pass — flagged for a future DATA-02 hardening pass rather than blocking this story. `troll/ranking_engine/price_series.py:PriceSeriesStore.backfill`.
status: done 2026-10-05
resolution: already resolved: platform/ranking/domain/price_series.py:162-174 live/Parquet mismatch detected and returned for ledgering (engine.py:288-290 error_ledger.record)

### DW-9: `chart_indicator_config.save_config` writes directly with no temp-file+rename and no file lock.

origin: migrated from legacy ledger ("Deferred from: code review of 10-5-persisted-per-instrument-chart-indicator-configuration (2026-09-09)"), 2026-10-05
location: dydx_collector/config.py
reason: **`chart_indicator_config.save_config` writes directly with no temp-file+rename and no file lock.** A crash mid-write or two near-simultaneous saves could corrupt/lose an update. Confirmed identical, pre-existing pattern in `dydx_collector/config.py`'s own `save_config` (this story's AC #1 explicitly directs mirroring that shape) — not a regression this story introduces. Acceptable risk for a personal single-user localhost tool with no comparable hardening anywhere else in this codebase's config-writing code; revisit only if it becomes a real complaint (same standard `dydx_collector/config.py`'s own docstring already sets). `troll/ml_signals/chart_indicator_config.py`.
status: open

### DW-10: `fig.update_layout(height=1250, ...)` was never rebalanced despite the fixed-row panel count shrinking every Epic 10 story (7→6→5→4).

origin: migrated from legacy ledger ("Deferred from: code review of 10-4-ofi-as-a-custom-indicator-retiring-the-fixed-row (2026-09-09)"), 2026-10-05
location: troll/ml_signals/dashboard.py:_render_chart_page
reason: **`fig.update_layout(height=1250, ...)` was never rebalanced despite the fixed-row panel count shrinking every Epic 10 story (7→6→5→4).** Each remaining panel gets proportionally more vertical room every time a row is retired, and no story has yet revisited the total page height. A deliberate redesign of the page's total height is a product decision out of proportion to any single indicator-retirement story. `troll/ml_signals/dashboard.py:_render_chart_page`.
status: open

### DW-11: `bar_ns = window.bar_seconds * 1_000_000_000` then `// bar_ns` in `_ofi_replay` is unguarded against `bar_seconds == 0`.

origin: migrated from legacy ledger ("Deferred from: code review of 10-4-ofi-as-a-custom-indicator-retiring-the-fixed-row (2026-09-09)"), 2026-10-05
location: troll/ml_signals/custom_indicators.py
reason: **`bar_ns = window.bar_seconds * 1_000_000_000` then `// bar_ns` in `_ofi_replay` is unguarded against `bar_seconds == 0`.** Same pre-existing pattern already deferred for `_cancel_pressure_replay`/`_cvd_replay` in Story 10.3's review; no real call path currently passes `bar_seconds=0`. `troll/ml_signals/custom_indicators.py`.
status: open

### DW-12: `bar_ns = window.bar_seconds * 1_000_000_000` then `// bar_ns` is unguarded against `bar_seconds == 0`.

origin: migrated from legacy ledger ("Deferred from: code review of 10-3-cancel-pressure-as-a-custom-histogram-indicator-retiring-the-fixed-row (2026-09-09)"), 2026-10-05
location: troll/ml_signals/custom_indicators.py
reason: **`bar_ns = window.bar_seconds * 1_000_000_000` then `// bar_ns` is unguarded against `bar_seconds == 0`.** Pre-existing pattern shared verbatim with `_cvd_replay` (Story 10.2), not introduced fresh by this story; no real call path currently passes `bar_seconds=0`. `troll/ml_signals/custom_indicators.py`.
status: open

### DW-13: `_indicators_json`'s broad exception-to-400 handling now has a real trigger, not just a hypothetical one.

origin: migrated from legacy ledger ("Deferred from: code review of 10-2-cvd-as-a-custom-indicator-retiring-the-fixed-row (2026-09-08)"), 2026-10-05
location: troll/ml_signals/dashboard.py:_indicators_json
reason: **`_indicators_json`'s broad exception-to-400 handling now has a real trigger, not just a hypothetical one.** Story 10.1's review flagged this same item speculatively ("no real custom indicator exists yet to trigger this") — CVD is now that real custom indicator: `_second_snapshots`'s `catalog.query()` call can raise (missing partition/schema, IO error) and would be caught by `_indicators_json`'s broad `except Exception`, reported to the client as a generic "Invalid indicator spec" 400 and logged only at `logger.info`, indistinguishable from a user typo. This is a systemic `_indicators_json` concern (affects every current and future custom indicator identically, not CVD-specifically) — worth a proper fix (e.g. a narrower except scope, or re-raising fetch errors distinctly from parse/coercion errors) once Stories 10.3/10.4 add their own catalog-fetching replay functions and the pattern is confirmed to recur, rather than a one-off patch inside CVD alone. `troll/ml_signals/dashboard.py:_indicators_json`.
status: open

### DW-14: No catalog-level signal that an indicator is "historical only," so a user can add CVD (or Cancel Pressure/OFI once shipped) while viewing the live chart and …

origin: migrated from legacy ledger ("Deferred from: code review of 10-2-cvd-as-a-custom-indicator-retiring-the-fixed-row (2026-09-08)"), 2026-10-05
location: troll/ml_signals/custom_indicators.py
reason: **No catalog-level signal that an indicator is "historical only," so a user can add CVD (or Cancel Pressure/OFI once shipped) while viewing the live chart and get nothing, forever, with no explanation.** `coin_indicators_handler` calls `replay_indicator` unconditionally regardless of live/historical mode; CVD's live-mode `None` output is the *correct* per-indicator behavior (DATA-01: flag, don't fabricate), but nothing in `catalog_json()`'s `{params, panel}` shape or the picker UI tells the user *why* the line never appears while live. Proper fix needs a schema addition (e.g. a `supports_live: bool` field) plus a picker UI affordance — worth designing once for all three historical-only custom indicators (CVD done, Cancel Pressure/OFI to follow in Stories 10.3/10.4) rather than bolting onto CVD alone. `troll/ml_signals/custom_indicators.py`, `troll/ml_signals/dashboard.py` (picker JS).
status: open

### DW-15: No runtime enforcement that a registered `panel` value is a valid `Panel` literal, or that a `CustomIndicatorSpec.replay`'s actual shape matches `ReplayFn`.

origin: migrated from legacy ledger ("Deferred from: code review of 10-1-custom-indicator-catalog-category-tagged-picker-and-histogram-panel-type (2026-09-08)"), 2026-10-05
location: troll/ml_signals/custom_indicators.py
reason: **No runtime enforcement that a registered `panel` value is a valid `Panel` literal, or that a `CustomIndicatorSpec.replay`'s actual shape matches `ReplayFn`.** A typo'd panel value (e.g. `"Histogram"` capitalized) satisfies neither `_renderCandleTraces`' overlay check nor `_renderOscillatorPanel`'s oscillator/histogram check — the indicator is addable via the picker but renders on no panel, with no error surfaced anywhere. Pre-existing, symmetric gap on the native `chart_indicators.IndicatorSpec` catalog too (this story didn't introduce it, just added a third valid literal value to the same unvalidated field). No real custom indicator exists yet to actually trigger this. `troll/ml_signals/custom_indicators.py`, `troll/ml_signals/chart_indicators.py`.
status: open

### DW-16: `_indicators_json`'s broad `except Exception` conflates malformed user input with a future custom `replay` function's own programming bugs

origin: migrated from legacy ledger ("Deferred from: code review of 10-1-custom-indicator-catalog-category-tagged-picker-and-histogram-panel-type (2026-09-08)"), 2026-10-05
location: troll/ml_signals/dashboard.py:_indicators_json
reason: **`_indicators_json`'s broad `except Exception` conflates malformed user input with a future custom `replay` function's own programming bugs**, both collapsing to a 400 logged at `logger.info` severity — a real implementation bug in a Story 10.2-10.4 replay function (e.g. output not aligned 1:1 with `candles`) would be reported to the client as "bad input" and logged below warning/error severity, under-reporting a genuine failure class once real custom indicator logic exists. No real custom indicator exists yet to trigger this. `troll/ml_signals/dashboard.py:_indicators_json`.
status: open

### DW-17: Stale axis key on shrinking active-oscillator-indicator count across re-renders.

origin: migrated from legacy ledger ("Deferred from: code review of 9-1-fix-oscillator-panel-shared-y-axis-scaling (2026-09-08)"), 2026-10-05
location: troll/ml_signals/dashboard.py:_renderOscillatorPanel
reason: **Stale axis key on shrinking active-oscillator-indicator count across re-renders.** `_renderOscillatorPanel` rebuilds `layout` from scratch each call and calls `Plotly.react('ind-panel', traces, layout, ...)`; if a prior render had e.g. 3 active oscillator indicators (`yaxis3` present) and the next render has only 1, the new `layout` object simply omits `yaxis3` rather than explicitly clearing it. Whether Plotly's `react()` diffing actually resets/removes a previously-defined axis when the new layout doesn't mention it is real browser/Plotly.js runtime behavior this repo's pure-Node test harness (fully-stubbed `Plotly.react`) cannot verify — consistent with this page's established "not verified in a real browser" limitation (Stories 7.1/8.1/8.2/8.4). `troll/ml_signals/dashboard.py:_renderOscillatorPanel`.
status: open

### DW-18: Duplicate-instance collision when two active indicator instances share identical name+params.

origin: migrated from legacy ledger ("Deferred from: code review of 9-1-fix-oscillator-panel-shared-y-axis-scaling (2026-09-08)"), 2026-10-05
location: troll/ml_signals/dashboard.py:_seriesForIndicator,_paramsMatch
reason: **Duplicate-instance collision when two active indicator instances share identical name+params.** `_seriesForIndicator`/`_paramsMatch` (from the unrelated, pre-existing multi-instance indicator-picker WIP refactor, not part of Story 9.1) both resolve to the same backend data key, so two identical-settings instances would render duplicate overlapping traces, each consuming its own axis slot from this story's per-instance axis-assignment fix. Root cause is in the WIP refactor's lookup logic, not the axis-scaling fix; belongs to whichever story finishes that feature. `troll/ml_signals/dashboard.py:_seriesForIndicator,_paramsMatch`.
status: open

### DW-19: No upper bound on the number of oscillator indicators a user can add, hence no bound on overlaid axes.

origin: migrated from legacy ledger ("Deferred from: code review of 9-1-fix-oscillator-panel-shared-y-axis-scaling (2026-09-08)"), 2026-10-05
location: troll/ml_signals/dashboard.py:_addIndicator
reason: **No upper bound on the number of oscillator indicators a user can add, hence no bound on overlaid axes.** The unrelated WIP `_addIndicator` lets a user add the same (or different) indicator repeatedly with no limit or dedupe; each addition now creates another overlaid Plotly axis via this story's fix. Unclear Plotly rendering/perf behavior at a large axis count was not characterized (no browser available). Bounding indicator count is a product decision for the multi-instance add-list feature, not this axis-scaling bug fix. `troll/ml_signals/dashboard.py:_addIndicator`.
status: open

### DW-20: `_indicatorLabel`'s `.attr` suffix is inconsistent between panels.

origin: migrated from legacy ledger ("Deferred from: code review of 9-1-fix-oscillator-panel-shared-y-axis-scaling (2026-09-08)"), 2026-10-05
location: troll/ml_signals/dashboard.py:_renderCandleTraces,_renderOscillatorPanel
reason: **`_indicatorLabel`'s `.attr` suffix is inconsistent between panels.** `_renderCandleTraces` (overlay panel) appends `.attr` to a trace's legend name only when an indicator has more than one output attribute; `_renderOscillatorPanel` appends it unconditionally. A single-output oscillator indicator (e.g. `RelativeStrengthIndex`) therefore shows as `RelativeStrengthIndex(14).value` in the legend while an equivalent single-output overlay indicator shows as just `SimpleMovingAverage(20)`. Predates Story 9.1 (introduced when the WIP multi-instance refactor added `_indicatorLabel`); Story 9.1's diff only renamed the call site, it did not create the asymmetry. `troll/ml_signals/dashboard.py:_renderCandleTraces,_renderOscillatorPanel`.
status: open

### DW-21: Batch-level error reporting loses per-indicator granularity.

origin: migrated from legacy ledger ("Deferred from: code review of 8-4-indicator-picker-on-the-chart-page-settings-toolbar (2026-09-08)"), 2026-10-05
location: _indicators_json
reason: **Batch-level error reporting loses per-indicator granularity.** `_indicators_json` (dashboard.py:1388, from Story 8.2) fails the entire multi-indicator request on the first bad entry, returning one generic `{"error": ...}` message with no indication of which indicator/param in the pipe-separated spec was at fault. Pre-existing from Story 8.2, not introduced by Story 8.4 — Story 8.4's own fix (checking `r.ok` client-side) will at least surface that *something* failed, but not which indicator.
status: open

### DW-22: `_seriesForIndicator`'s prefix-match invariant is documented, not enforced.

origin: migrated from legacy ledger ("Deferred from: code review of 8-4-indicator-picker-on-the-chart-page-settings-toolbar (2026-09-08)"), 2026-10-05
location: troll/ml_signals/chart_indicators.py
reason: **`_seriesForIndicator`'s prefix-match invariant is documented, not enforced.** The comment above it (dashboard.py:457-460) asserts "no two catalog entries share a name-is-a-prefix-of-another relationship" — true today (verified against all 34 `INDICATOR_CATALOG` keys during Story 8.4's review), but nothing guards it if a future indicator addition violates it; a runtime assertion or catalog-level test would catch a silent cross-match regression early. `troll/ml_signals/chart_indicators.py`, `troll/ml_signals/dashboard.py`.
status: open

### DW-23: CVD/Vol d go blank ("—") during a temporary order-book gap, even though they're valid trade-volume-derived numbers computed independently of the book (unlike …

origin: migrated from legacy ledger ("Deferred from: code review of spec-normalize-rankings-table-units.md (2026-07-17)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-normalize-rankings-table-units.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-normalize-rankings-table-units.md` summary: CVD/Vol d go blank ("—") during a temporary order-book gap, even though they're valid trade-volume-derived numbers computed independently of the book (unlike Spread/u lean, which genuinely need a book-derived mid price). evidence: `_LIVE_FAST["cvd"]`/`["volume_delta"]` (dashboard.py, `_ingest_batch`) come from `tb_vol - ts_vol`/`buy_volume - sell_volume`, independent of `bid_prices`/`ask_prices`; `_LIVE_FAST["price"]` (the mid) is `None` whenever the book is empty. Because CVD/Vol d's *display* now requires `price` too (to convert to USD), a transient book gap hides a real, valid number that used to render as a raw figure before this change. This is exactly what the approved spec's I/O matrix specified ("missing price -> renders '—'"), so it isn't a bug against the spec -- but it's a real UX tradeoff the human should weigh in on: whether trade-derived columns deserve a different fallback (e.g. show the raw token count with a distinct marker) when price is transiently unavailable, versus always requiring price for these four columns as currently implemented.
status: open

### DW-24: The new "($)"/"(bps)" header-unit-suffix convention (added to CVD/Vol d/Spread/u lean only) is now inconsistent with `Vol24h`'s existing …

origin: migrated from legacy ledger ("Deferred from: code review of spec-normalize-rankings-table-units.md (2026-07-17)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-normalize-rankings-table-units.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-normalize-rankings-table-units.md` summary: The new "($)"/"(bps)" header-unit-suffix convention (added to CVD/Vol d/Spread/u lean only) is now inconsistent with `Vol24h`'s existing bare-label-with-unit-baked-into-the-value convention ("24.3M") and `pct_1h`/`pct_24h`'s inline "%" convention -- three different unit-labeling conventions coexist in one header row with no stated rule for which gets which. evidence: Confirmed by direct inspection of `RANKING_COLS`: `("volume24h", "Vol24h", lambda v: f"{v/1e6:.1f}M", None)` has no `($)` suffix despite also being USD, while the four columns touched by this change now carry an explicit unit suffix in the label itself. A repo-wide labeling-convention pass (standardize on either baked-into-value or label-suffix, not both) is out of scope for this change.
status: open

### DW-25: `live_paper/tests/conftest.py`'s session-scoped native-abort-mitigation fixture prevents verifying `node.py`'s `use_pyo3=True` logging config actually takes …

origin: migrated from legacy ledger ("Deferred from: code review of spec-normalize-rankings-table-units.md (2026-07-17)"), 2026-10-05
location: live_paper/tests/conftest.py
reason: **`live_paper/tests/conftest.py`'s session-scoped native-abort-mitigation fixture prevents verifying `node.py`'s `use_pyo3=True` logging config actually takes effect.** `NautilusKernel.__init__` (`nautilus_trader/system/kernel.py:194`) only runs its logging-setup branch (which reads `use_pyo3`) `if not is_logging_initialized()`. Because the fixture constructs a throwaway `BacktestEngine` with default (non-pyo3) logging as the very first thing each pytest session does — deliberately, to keep the log-guard count above zero and avoid the documented native abort — every subsequent `TradingNode` built in `test_node.py`, including `build_node()`'s `LoggingConfig(use_pyo3=True)`, has that branch already skipped. The tests can prove the node builds/disposes cleanly but cannot prove `use_pyo3=True` was actually honored. This is an inherent limitation of the shared-fixture mitigation (same characteristic already existed silently in `ml_signals/tests/conftest.py` since Epic 2, not introduced by this story), not something narrowly fixable without either accepting the native-abort risk or adding much heavier verification (inspecting Rust logging backend state). Not fixed here. `troll/live_paper/tests/conftest.py`, `troll/live_paper/node.py`.
status: open

### DW-26: `build_node()` doesn't dispose a partially-constructed `TradingNode` if `add_data_client_factory`/`add_exec_client_factory`/`build()` raises after …

origin: migrated from legacy ledger ("Deferred from: code review of spec-normalize-rankings-table-units.md (2026-07-17)"), 2026-10-05
location: examples/sandbox/dydx_sandbox.py
reason: **`build_node()` doesn't dispose a partially-constructed `TradingNode` if `add_data_client_factory`/`add_exec_client_factory`/`build()` raises after `TradingNode(config=...)` succeeds.** Would leak kernel resources (event loop, logger) on a startup failure. Matches the exact pattern of this repo's own reference examples (`examples/sandbox/dydx_sandbox.py`, `examples/live/dydx/dydx_market_maker.py`), which don't guard this either — not a regression introduced by this story, and disproportionate to fix at scaffold stage without a broader error-handling design pass. `troll/live_paper/node.py`.
status: open

### DW-27: `docker-compose.yml`'s `live-paper` service uses `restart: always` with no backoff

origin: migrated from legacy ledger ("Deferred from: code review of spec-normalize-rankings-table-units.md (2026-07-17)"), 2026-10-05
location: docker-compose.yml
reason: **`docker-compose.yml`'s `live-paper` service uses `restart: always` with no backoff**, inherited from the `collector`/`dashboard` convention. Harmless today (zero strategies attached, so a restart just reconnects data/exec clients), but once Story 3.2 attaches an actual strategy, a persistent bad-config or transient failure could crash-loop against dYdX's API far faster than a passive data collector ever would. Revisit alongside Story 3.2 — consider `restart: on-failure:N` or an explicit backoff. `troll/docker-compose.yml`. **Resolved in Story 3.2**: changed to `restart: on-failure:5`.
status: done 2026-10-05
resolution: already resolved: platform/docker-compose.yml:395 live-paper is profile-gated and carries no restart: always (ledger text already says resolved in Story 3.2)

### DW-28: Neither `load_paper_config` nor `load_real_money_config` rejects unknown/typo'd TOML keys — still open, unchanged by Story 3.2 (which only added new …

origin: migrated from legacy ledger ("Deferred from: code review of spec-normalize-rankings-table-units.md (2026-07-17)"), 2026-10-05
location: load_paper_config
reason: Neither `load_paper_config` nor `load_real_money_config` rejects unknown/typo'd TOML keys — still open, unchanged by Story 3.2 (which only added new, recognized keys); see original entry above.
status: done 2026-10-05
resolution: already resolved: platform/bots/infrastructure/config.py:52-59 _reject_unknown_keys

### DW-29: `DummyStrategy._maybe_trade()`'s signal-cadence mismatch: `on_timer` re-evaluates every 1s once `mlofi` initializes, but `trend.value` only updates once per …

origin: migrated from legacy ledger ("Deferred from: code review of 3-2-dummy-strategy-consumes-every-produced-signal-and-runs-live-in-paper-mode (2026-07-17)"), 2026-10-05
location: troll/live_paper/strategy.py
reason: **`DummyStrategy._maybe_trade()`'s signal-cadence mismatch: `on_timer` re-evaluates every 1s once `mlofi` initializes, but `trend.value` only updates once per bar close** (once per minute with the default `bar_spec`). Between bar closes, up to ~60 timer ticks re-evaluate a fresh `mlofi.value` against the same minute-old `trend.value` — the strategy can flip entries/exits based on OFI noise crossing `ofi_confirm_threshold` while the trend reading itself hasn't moved. The new `orders_inflight()` guard (this story's review round) prevents this from ever producing *duplicate simultaneous* orders, but doesn't change the underlying re-evaluation-frequency mismatch. Resolving this properly (e.g. holding the trend gate fixed between bar closes rather than re-checking it on every timer tick, or debouncing entries within a bar) is a real design decision beyond this story's "prove the wiring, not the trade quality" scope. Not fixed here. `troll/live_paper/strategy.py`.
status: done 2026-10-05
resolution: closed by human decision: Accepted: DummyStrategy only proves wiring; real strategies live in research/strategies
decision: 2026-10-05 Accept as is: DummyStrategy is a wiring-proof scaffold — Accepted: DummyStrategy only proves wiring; real strategies live in research/strategies

### DW-30: Neither `load_paper_config` nor `load_real_money_config` rejects unknown/typo'd TOML keys

origin: migrated from legacy ledger ("Deferred from: code review of 3-2-dummy-strategy-consumes-every-produced-signal-and-runs-live-in-paper-mode (2026-07-17)"), 2026-10-05
location: dydx_collector/config.py
reason: **Neither `load_paper_config` nor `load_real_money_config` rejects unknown/typo'd TOML keys** — both use `.get(key, default)` throughout (matching the established pattern already in `dydx_collector/config.py`), so e.g. a typo'd `subaccont` instead of `subaccount` in a real-money config silently falls back to `subaccount=0` instead of raising. For the paper config this is a generic, low-consequence TOML footgun consistent with the rest of the codebase; for the real-money config specifically it's a higher-consequence one (could route real trades through the wrong subaccount with zero error). Fixing this cleanly means deciding whether to add strict/exhaustive key validation to one or both loaders — a real design decision beyond this scaffold story's ACs, not fixed here. `troll/live_paper/config.py`.
status: done 2026-10-05
resolution: already resolved: platform/bots/infrastructure/config.py:52-59 _reject_unknown_keys

### DW-31: `classify_liquidity`'s `min_oi_usd` parameter name (`troll/dydx_collector/open_interest.py:109`) and the `liquidity_min_oi_usd` config field are stale — the …

origin: migrated from legacy ledger ("Deferred from: code review of 1-1-verify-the-data-integrity-gate-end-to-end (2026-07-01)"), 2026-10-05
location: troll/dydx_collector/open_interest.py:109
reason: `classify_liquidity`'s `min_oi_usd` parameter name (`troll/dydx_collector/open_interest.py:109`) and the `liquidity_min_oi_usd` config field are stale — the function is `volume24H`-based, not open-interest-based, since an earlier fix (pre-existing, not introduced by Story 1.1). Renaming is an API-surface change (config field + call sites) unrelated to Story 1.1's ACs; pick up as a small standalone cleanup story.
status: done 2026-10-05
resolution: already resolved: classify_liquidity/min_oi_usd/liquidity_min_oi_usd no longer exist anywhere under platform/ (grep empty); volume lives in ranking/infrastructure/volume_dydx.py

### DW-32: `_VOLUME_24H` has no staleness/failure indicator, unlike `_LIVE_FAST`'s `stale` flag — if `_volume_loop_task`'s poll starts failing silently, the default sort …

origin: migrated from legacy ledger ("Deferred from: code review of 1-2-default-the-ranking-table-to-volume-sort (2026-07-02)"), 2026-10-05
location: _VOLUME_24H
reason: `_VOLUME_24H` has no staleness/failure indicator, unlike `_LIVE_FAST`'s `stale` flag — if `_volume_loop_task`'s poll starts failing silently, the default sort and `Vol24h` column keep serving arbitrarily old data with no visual cue. Not required by any AC in Story 1.2; future hardening.
status: done 2026-10-05
resolution: already resolved: platform/ranking/application/engine.py:82-84,193-197 volume_max_age_ns staleness check on refresh_volumes

### DW-33: `parse_volume_24h` (`troll/ml_signals/dashboard.py`) duplicates `dydx_collector/open_interest.py`'s `classify_liquidity` volume-parsing one-liner …

origin: migrated from legacy ledger ("Deferred from: code review of 1-2-default-the-ranking-table-to-volume-sort (2026-07-02)"), 2026-10-05
location: troll/ml_signals/dashboard.py
reason: `parse_volume_24h` (`troll/ml_signals/dashboard.py`) duplicates `dydx_collector/open_interest.py`'s `classify_liquidity` volume-parsing one-liner (`float(market.get("volume24H") or 0)`) with no shared constant or parity test tying the two together. AD-4 only bars reusing network-I/O code across the module boundary — the pure parsing logic could be factored into a shared utility. Minor, not blocking.
status: open

### DW-34: Rankings poll interval is `setInterval(pollRankings,2000)` (2s) in `troll/ml_signals/dashboard.py`, while the Story 1.2 spec text says "1s poll" (AC2). …

origin: migrated from legacy ledger ("Deferred from: code review of 1-2-default-the-ranking-table-to-volume-sort (2026-07-02)"), 2026-10-05
location: troll/ml_signals/dashboard.py
reason: Rankings poll interval is `setInterval(pollRankings,2000)` (2s) in `troll/ml_signals/dashboard.py`, while the Story 1.2 spec text says "1s poll" (AC2). Pre-existing code, untouched by Story 1.2's diff — reconcile cadence or spec wording in a follow-up.
status: open

### DW-35: `fetch_watchlist` (`troll/ml_signals/watchlist.py:36-38`) has no error handling around `urlopen`/`json.load` — a strategy script calling it while the dashboard …

origin: migrated from legacy ledger ("Deferred from: code review of 1-3-expose-the-live-watchlist-as-a-queryable-backtest-consumable-coin-set (2026-07-15)"), 2026-10-05
location: troll/ml_signals/watchlist.py:36-38
reason: `fetch_watchlist` (`troll/ml_signals/watchlist.py:36-38`) has no error handling around `urlopen`/`json.load` — a strategy script calling it while the dashboard is down gets a raw `URLError`/`JSONDecodeError` rather than a clear diagnostic. Mirrors `_fetch_volume_24h_json`'s identical shape, but that helper's only caller (`_volume_loop_task`) wraps it in `try/except Exception`; `fetch_watchlist` is a new external-facing entry point with no equivalent safety net elsewhere. Worth a small hardening pass alongside other deferred staleness/error-signaling items.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-research-test-and-path-fixes
resolution-undo: 89c508f4086e6130fe63c3b8c7df6e501f431e414611a6838c2e01ff0641c80a 2026-10-05 7374617475733a206f70656e

### DW-36: `_is_fresh` (`troll/ml_signals/dashboard.py:151-154`) treats a `_LIVE_FAST` entry with a future timestamp as fresh indefinitely — if the system clock ever …

origin: migrated from legacy ledger ("Deferred from: code review of 1-3-expose-the-live-watchlist-as-a-queryable-backtest-consumable-coin-set (2026-07-15)"), 2026-10-05
location: troll/ml_signals/dashboard.py:151-154
reason: `_is_fresh` (`troll/ml_signals/dashboard.py:151-154`) treats a `_LIVE_FAST` entry with a future timestamp as fresh indefinitely — if the system clock ever moves backward between write and read, `now_ns - entry["ts"]` goes negative and still satisfies `<= _WATCHLIST_STALE_NS`. Same clock-comparison shape already used by `_STALE_BOOK_NS`/`_CROSSED_RESYNC_NS` elsewhere in the codebase (not introduced by this story), extremely low real-world likelihood, self-correcting on the next legitimate update.
status: open

### DW-37: Persisted `ts` reflects `compute_all()`'s start time (`now_ns` captured before the per-instrument thread-pool sweep), but the merged `rank`/`volume24h` reflect …

origin: migrated from legacy ledger ("Deferred from: code review of 1-4-persist-and-query-historical-coin-ranking (2026-07-16)"), 2026-10-05
location: troll/ml_signals/dashboard.py:1224-1229
reason: Persisted `ts` reflects `compute_all()`'s start time (`now_ns` captured before the per-instrument thread-pool sweep), but the merged `rank`/`volume24h` reflect live `_LIVE_FAST`/`_VOLUME_24H` state read after `compute_all()` returns — a skew of unknown but bounded magnitude between the stored timestamp and the actual live-state capture time. Pre-existing characteristic of `_slow_loop_task`'s design inherited by rank/volume24h, not introduced by this story (every persisted column already shared this same ts-vs-actual-capture skew before Story 1.4). `troll/ml_signals/dashboard.py:1224-1229`.
status: open

### DW-38: Collector resync/sync-issue log volume uncharacterized.

origin: migrated from legacy ledger ("Flagged during: Epic 1 retrospective (2026-07-16)"), 2026-10-05
location: logger.warning
reason: **Collector resync/sync-issue log volume uncharacterized.** The collector has only been verified in the Docker test environment, never run against sustained live dYdX traffic. User reports logs "spammed" with sync issues from a past run, but the exact channel (routine `logger.warning` "Resyncing desynced order book" / `housekeeping_logger` known-cause sequence gaps from Story 1.6 / `critical_logger.critical` crossed-book escalation from Story 1.7) and root cause (flaky connection vs. a false-positive resync loop) are both unconfirmed — no log evidence was available to inspect during this retrospective (no container running, no persisted log files found locally). Does not block Epic 2 (no Epic 2 AC depends on resolving it — 2.1/2.2 are code-structure stories, 2.3/2.4 are backtest-mechanics stories, neither depends on the underlying data being desync-free), but it does affect whether any *research conclusion* Epic 2 eventually produces from captured data can be trusted. Recommended next step whenever picked up: run the collector against live traffic for a sustained period and read the actual `dydx_collector.critical`/`dydx_collector.housekeeping`/default logger output to characterize frequency and root cause before treating catalog data as ground truth for real trading decisions.
status: done 2026-10-05
resolution: closed by human decision: Superseded by Story 5.1 permanent incident reports and the capture/ rewrite; reopen with fresh log evidence if spam recurs
decision: 2026-10-05 Close as superseded by capture-context rewrite and incident-report ledger — Superseded by Story 5.1 permanent incident reports and the capture/ rewrite; reopen with fresh log evidence if spam recurs

### DW-39: `BacktestEngine` construction across test-file boundaries causes a fatal native abort, root cause not identified.

origin: migrated from legacy ledger ("Flagged during: Story 2.2 implementation (2026-07-16)"), 2026-10-05
location: troll/ml_signals/tests/test_ofi_strategy_indicator_consistency.py
reason: **`BacktestEngine` construction across test-file boundaries causes a fatal native abort, root cause not identified.** Adding a second test file that constructs a `nautilus_trader.backtest.engine.BacktestEngine` (`troll/ml_signals/tests/test_ofi_strategy_indicator_consistency.py`, new in Story 2.2) crashes with `Fatal Python error: Aborted` inside `nautilus_trader/system/kernel.py:231` (native logging/kernel initialization) partway through `test_ofi_strategy.py`'s own second `BacktestEngine` construction — but **only** if the new file collects (alphabetically) *before* `test_ofi_strategy.py`. Confirmed via a byte-for-byte copy of `test_ofi_strategy.py`'s own first test (identical config, identical synthetic data, run from a separate module) that this is **not** specific to any particular test's parameters or content — purely about `BacktestEngine` being constructed from two different test modules in sequence within one pytest process. Ruled out as causes: `engine.reset()`/`dispose()` ordering (already correct in both files), an explicit `gc.collect()` after disposal (no effect), reducing the new file to a single engine construction (still crashes). **Current mitigation:** `test_ofi_strategy_indicator_consistency.py` is named to collect after `test_ofi_strategy.py`, which avoids the crash in the current suite. This is fragile — it depends on filename-alphabetical pytest collection order, which any future new `BacktestEngine`-based test file could disturb. Worth a proper investigation (likely a `nautilus_pyo3`/tracing-subscriber global-singleton re-initialization issue specific to this pinned `nautilus_trader` version) before the test suite grows more `BacktestEngine`-based files. `troll/ml_signals/tests/test_ofi_strategy_indicator_consistency.py`, `troll/ml_signals/tests/test_ofi_strategy.py`. **Mitigation robustness caveat (found during code review):** the alphabetical-filename mitigation only holds under the currently-used test invocation path (`make test`'s `docker compose run ... python3 -m pytest ml_signals/tests dydx_collector/tests` — confirmed the Docker image at `/app` does not copy in the repo-root `pyproject.toml`, so its `[tool.pytest.ini_options]` `addopts = "-ra --new-first --failed-first ..."` is not in effect there). If these tests are ever run via a host `.venv` from the repo root instead (a real, previously-used workflow per project history), that `addopts` would reorder collection by recency/failure-cache rather than filename, potentially breaking the ordering assumption and reintroducing the crash. Not fixed here (would require either a `conftest.py`-level ordering hook or root-causing the actual native issue, both disproportionate to this documentation-level finding) — flagged so whoever eventually root-causes the crash also accounts for this invocation-method sensitivity.
status: done 2026-10-05
resolution: already resolved: platform/research/tests/conftest.py:_keep_nautilus_log_guard_alive session fixture keeps LogGuard alive (root cause fixed)

### DW-40: `backtest_ofi.py`'s `__main__` block has the same `engine.reset()`/`engine.dispose()`-after-`BacktestNode.run()` bug fixed in `backtest_dydx.py` by this story.

origin: migrated from legacy ledger ("Flagged during: Story 2.3 implementation (2026-07-16)"), 2026-10-05
location: backtest_ofi.py
reason: **`backtest_ofi.py`'s `__main__` block has the same `engine.reset()`/`engine.dispose()`-after-`BacktestNode.run()` bug fixed in `backtest_dydx.py` by this story.** `BacktestNode.run()` already disposes its own engines internally; calling `engine.reset()` afterward raises `AttributeError: 'NoneType' object has no attribute 'is_margin_account'` (via an invalid `DISPOSED -> RESET` state transition inside `SimulatedExchange.reset()`) — reproduced directly while verifying Story 2.3's `bar_interval` change in `backtest_dydx.py`, which had the identical pattern before this story fixed it. `backtest_ofi.py` is out of scope for Story 2.3 (per its Project Structure Notes), so not fixed here. Fix (already applied in `backtest_dydx.py` as precedent): replace the `engine.reset(); engine.dispose()` pair with a single `node.dispose()` call. `troll/ml_signals/backtest_ofi.py` (end of file, `__main__` block).
status: done 2026-10-05
resolution: already resolved: platform/research/strategies/backtest_ofi.py __main__ now calls run() -> run_snapshot_backtest; no engine.reset()/dispose()

### DW-41: `ofi_strategy.py`'s 30-minute trend-EMA gate (`trend_bar_type_str = "30-MINUTE-LAST-EXTERNAL"`) has likely never activated against real collected data.

origin: migrated from legacy ledger ("Flagged during: Story 2.3 implementation (2026-07-16)"), 2026-10-05
location: ofi_strategy.py
reason: **`ofi_strategy.py`'s 30-minute trend-EMA gate (`trend_bar_type_str = "30-MINUTE-LAST-EXTERNAL"`) has likely never activated against real collected data.** `BacktestDataConfig`'s `bar_spec`/`bar_types` params only ever construct an `-EXTERNAL` catalog query for pre-existing `Bar` rows — they never aggregate anything (confirmed via `nautilus_trader/backtest/config.py`'s `BacktestDataConfig.query` property). The collector never writes `Bar` objects to the catalog at all (`ml_signals/catalog_stats.py`'s own comment confirms this explicitly, and no writer path exists anywhere in `troll/`). So `backtest_ofi.py`'s `BacktestDataConfig(data_cls=Bar, bar_spec="1-MINUTE-LAST")` almost certainly streams zero rows against a real catalog, meaning `OFIStrategy`'s trend-EMA gate (`subscribe_bars`/`on_bar`) has silently never triggered in any real backtest run. Discovered during Story 2.3's research into candlestick-timeframe backtesting (which deliberately chose Nautilus's `-INTERNAL` aggregation from `TradeTick` instead, avoiding this exact trap). `ofi_strategy.py`/`backtest_ofi.py` are out of scope for Story 2.3 — not fixed here. Real fix would be switching `trend_bar_type_str` to an `-INTERNAL` bar type (matching this story's `backtest_dydx.py` precedent) so it aggregates from the already-streamed `TradeTick`/`OrderBookDelta` data instead of querying for `Bar` rows that don't exist. `troll/ml_signals/ofi_strategy.py`, `troll/ml_signals/backtest_ofi.py`.
status: done 2026-10-05
resolution: already resolved: platform/research/strategies/ofi_strategy.py has no trend_bar_type/EXTERNAL bar subscription; backtest_ofi.py no longer queries Bar rows

### DW-42: Addendum to the `BacktestEngine` cross-module-abort finding above: the same crash also occurs WITHIN a single file when a custom (`register_arrow`-registered …

origin: migrated from legacy ledger ("Flagged during: Story 2.3 implementation (2026-07-16)"), 2026-10-05
location: troll/ml_signals/tests/test_snapshot_strategy.py
reason: **Addendum to the `BacktestEngine` cross-module-abort finding above: the same crash also occurs WITHIN a single file when a custom (`register_arrow`-registered, `CustomData`-wrapped) `Data` type is involved.** While writing `troll/ml_signals/tests/test_snapshot_strategy.py` (Story 2.3), two `BacktestEngine` constructions in the SAME test file (not across files) crashed identically (`Fatal Python error: Aborted` at `nautilus_trader/system/kernel.py:231`) once `DydxSecondSnapshot` (a custom `Data` type) was involved via `CustomData`-wrapped `engine.add_data(..., client_id=...)`. This is a strictly worse manifestation than the original finding, which only occurred across module boundaries — here it occurred within one file's own two test functions. Not investigated further (same conclusion as the original finding: a pre-existing native fragility, not specific to any one test's content). **Mitigation applied:** `test_snapshot_strategy.py` was reduced to exactly one test function/engine construction rather than the two originally planned (a "no trade below threshold" negative-case test was dropped). Any future `BacktestEngine`-based test involving a custom registered `Data` type should budget for exactly one engine construction per file until this is root-caused. `troll/ml_signals/tests/test_snapshot_strategy.py`.
status: done 2026-10-05
resolution: already resolved: platform/research/tests/conftest.py:_keep_nautilus_log_guard_alive (DW-51 fix covers the in-file custom-Data variant)

### DW-43: `make test` (this session's host) fails ~30 `dydx_collector/tests` at setup with `OSError: No username set in the environment`, unrelated to any story-2.3 …

origin: migrated from legacy ledger ("Flagged during: Story 2.3 implementation (2026-07-16)"), 2026-10-05
location: dydx_collector/tests
reason: **`make test` (this session's host) fails ~30 `dydx_collector/tests` at setup with `OSError: No username set in the environment`, unrelated to any story-2.3 change.** `docker-compose.yml`'s `collector` service runs as `user: "1000:1000"` (pre-existing, present well before this story) with no matching `/etc/passwd` entry inside the image; pytest's own `tmp_path`/`tmpdir` fixture calls `getpass.getuser()` to build its base temp dir and raises when that lookup fails. Only affects test files using the `tmp_path` fixture (`test_config.py`, `test_prune_catalog.py`, `test_collector_resilience.py`, `test_collector_snapshot.py`); `ml_signals/tests` uses `tempfile.TemporaryDirectory()` directly and is unaffected — confirmed by running with `-e HOME=/tmp -e USER=collector` added to `docker compose run`, which fixes all ~30 errors (203 passed, only the pre-existing `test_ofi_strategy.py` failure remains). Not fixed here (would mean editing `docker-compose.yml`'s `environment:` block or the `Makefile`'s `test` target, out of scope for a backtest-infrastructure story) — flagged so `make test` gets a `HOME`/`USER` env default next time `dydx_collector/tests` is touched.
status: done 2026-10-05
resolution: already resolved: platform/Makefile:193 test target passes -e HOME=/tmp -e USER=collector

### DW-44: `backtest_dydx.run()` only surfaces skipped Watchlist symbols via a log warning, not via the return value itself.

origin: migrated from legacy ledger ("Flagged during: code review of 2-4-multi-coin-backtest-runs-across-the-live-watchlist (2026-07-16)"), 2026-10-05
location: troll/ml_signals/backtest_dydx.py
reason: **`backtest_dydx.run()` only surfaces skipped Watchlist symbols via a log warning, not via the return value itself.** A symbol with no matching catalog instrument (or any other per-symbol `BacktestRunConfig` construction failure) is skipped and logged at the aggregate level, but the returned `dict[str, BacktestResult]` gives a caller no programmatic way to detect "some requested symbols were silently dropped" without diffing their input `symbols` list against the returned dict's keys themselves. Expanding the return type (e.g. to also return a `skipped: list[str]`, or a small result object instead of a bare dict) would close this, but changing `run()`'s return contract again is a real API design decision beyond what Story 2.4's ACs asked for (the ACs require distinguishable per-coin results, not a skip-reporting contract) — not fixed here. Revisit if/when a caller (dashboard, notebook, a future scheduled multi-coin sweep) actually needs to alert on partial coverage rather than just log it. `troll/ml_signals/backtest_dydx.py`.
status: done 2026-10-05
resolution: closed by human decision: Accepted: skipped symbols are logged in one aggregate warning; callers can diff keys; revisit if a caller needs alerting
decision: 2026-10-05 Accept log-only reporting — Accepted: skipped symbols are logged in one aggregate warning; callers can diff keys; revisit if a caller needs alerting

### DW-45: Second addendum: the crash is broader still — it also occurs with a `BacktestNode` (not just a raw `BacktestEngine`) and with ZERO custom Data types involved …

origin: migrated from legacy ledger ("Flagged during: code review of 2-4-multi-coin-backtest-runs-across-the-live-watchlist (2026-07-16)"), 2026-10-05
location: troll/ml_signals/tests/test_timeframe_backtest.py
reason: **Second addendum: the crash is broader still — it also occurs with a `BacktestNode` (not just a raw `BacktestEngine`) and with ZERO custom Data types involved, purely from module-collection order.** While writing what became `troll/ml_signals/tests/test_timeframe_backtest.py` (originally `test_backtest_dydx.py`) for Story 2.3, a file containing only a `BacktestNode`+`BacktestDataConfig(data_cls=TradeTick)` run (no `CustomData`, no `DydxSecondSnapshot`, nothing beyond types already exercised safely elsewhere in the suite) still crashed identically at `nautilus_trader/system/kernel.py:231` whenever its filename sorted alphabetically before `test_ofi_strategy.py` (`'b' < 'o'`). This rules out "custom Data type" as a necessary trigger — the true condition appears to be purely "any second/later `BacktestEngine`- or `BacktestNode`-backed construction in the pytest process, regardless of content, unless `test_ofi_strategy.py` (which is inexplicably immune across 3 of its own constructions) has already run." **Mitigation applied:** renamed the file to `test_timeframe_backtest.py` (`'t' > 'o'`) so it collects after `test_ofi_strategy.py`; no change to test content was needed. This further weakens confidence in the "custom Data type" theory from the first addendum above and strengthens the case for treating this as a generic native-runtime singleton/logging-reinitialization bug scoped to the whole pinned `nautilus_trader` version, not to any one code path in `troll/`. `troll/ml_signals/tests/test_timeframe_backtest.py`.
status: done 2026-10-05
resolution: already resolved: platform/research/tests/conftest.py:_keep_nautilus_log_guard_alive removes collection-order sensitivity

### DW-46: `crates/common/src/logging/logger.rs`'s `LogGuard::drop()` resets a global `LOGGING_INITIALIZED` flag to `false` once the *last* live `LogGuard` is dropped …

origin: migrated from legacy ledger ("Resolved: root cause of the BacktestEngine/BacktestNode native abort (2026-07-17)"), 2026-10-05
location: crates/common/src/logging/logger.rs
reason: `crates/common/src/logging/logger.rs`'s `LogGuard::drop()` resets a global `LOGGING_INITIALIZED` flag to `false` once the *last* live `LogGuard` is dropped (count 1 → 0) — legitimate behavior for a process whose Nautilus system lifecycle has genuinely ended (e.g. a script that inits, runs, disposes, and exits).
status: open

### DW-47: The underlying `log` crate's `set_boxed_logger()` (called from `init_with_config` in `crates/common/src/logging/mod.rs`) can only ever succeed once per …

origin: migrated from legacy ledger ("Resolved: root cause of the BacktestEngine/BacktestNode native abort (2026-07-17)"), 2026-10-05
location: crates/common/src/logging/mod.rs
reason: The underlying `log` crate's `set_boxed_logger()` (called from `init_with_config` in `crates/common/src/logging/mod.rs`) can only ever succeed **once per process, permanently** — a hard constraint of the `log` crate itself, not a Nautilus decision.
status: open

### DW-48: So within a single long-lived process that keeps constructing more engines (a pytest session), if the guard count is ever allowed to hit zero mid-session, the …

origin: migrated from legacy ledger ("Resolved: root cause of the BacktestEngine/BacktestNode native abort (2026-07-17)"), 2026-10-05
location: crates/common/src/ffi/logging.rs
reason: So within a single long-lived process that keeps constructing more engines (a pytest session), if the guard count is ever allowed to hit zero mid-session, the *next* engine construction attempts a real re-init, `set_boxed_logger()` fails at the `log`-crate level, and that failure is `.expect()`-panicked in the C-FFI wrapper (`crates/common/src/ffi/logging.rs`'s `logging_init`) — the panic crosses an `extern "C"` boundary with no `catch_unwind`, aborting the whole Python process. Confirmed via a minimal, non-pytest repro (looping plain `BacktestEngine()` construction/disposal) that reproduces the exact panic message ("attempted to set a logger after the logging system was already initialized") deterministically on the process's *third* real logging init.
status: open

### DW-49: This also explains "`test_ofi_strategy.py`'s own 3 constructions are inexplicably immune": whether the crash triggers is pure Python refcounting timing …

origin: migrated from legacy ledger ("Resolved: root cause of the BacktestEngine/BacktestNode native abort (2026-07-17)"), 2026-10-05
location: test_ofi_strategy.py
reason: This also explains "`test_ofi_strategy.py`'s own 3 constructions are inexplicably immune": whether the crash triggers is pure Python refcounting timing (whichever engine happens to hold the last live guard, and exactly when Python releases it relative to the next `__init__`) — the old filename-alphabetical-ordering mitigation only ever worked by accidentally avoiding that exact timing window, not because of anything about `test_ofi_strategy.py`'s content.
status: open

### DW-50: Confirmed upstream already knows this and guards against it

origin: migrated from legacy ledger ("Resolved: root cause of the BacktestEngine/BacktestNode native abort (2026-07-17)"), 2026-10-05
location: tests/conftest.py:181-197
reason: **Confirmed upstream already knows this and guards against it**: `tests/conftest.py:181-197` has a session-scoped, autouse `bypass_logging` fixture that calls `init_logging()` once and keeps the returned guard alive for the whole session — its own comment reads "Return guard to keep it alive for the session lifetime, avoiding garbage collection." `troll/ml_signals/tests` never had an equivalent, since it deliberately doesn't share test infrastructure with the main `nautilus_trader` suite.
status: open

### DW-51: Fix applied (`troll/ml_signals/tests/conftest.py`, new): a session-scoped autouse fixture matching upstream's own idiom — construct one throwaway …

origin: migrated from legacy ledger ("Resolved: root cause of the BacktestEngine/BacktestNode native abort (2026-07-17)"), 2026-10-05
location: troll/ml_signals/tests/conftest.py
reason: **Fix applied** (`troll/ml_signals/tests/conftest.py`, new): a session-scoped autouse fixture matching upstream's own idiom — construct one throwaway `BacktestEngine` as the first thing pytest does each session and never dispose/release it, so the live-`LogGuard` count never reaches zero mid-session. Verified deterministically fixed by re-running the previously-crashing file combinations (`test_timeframe_backtest.py` + `test_ofi_strategy.py`, both original and fully-reversed collection order) and the full suite — no crash, same pre-existing unrelated `test_ofi_strategy.py` failure as always.
status: done 2026-10-05
resolution: already resolved: platform/research/tests/conftest.py:_keep_nautilus_log_guard_alive exists as described

### DW-52: The existing per-file "one `BacktestEngine` construction per file" / filename-ordering mitigations (Story 2.2/2.3 test files) are now redundant safety margin …

origin: migrated from legacy ledger ("Resolved: root cause of the BacktestEngine/BacktestNode native abort (2026-07-17)"), 2026-10-05
location: test_snapshot_strategy.py
reason: The existing per-file "one `BacktestEngine` construction per file" / filename-ordering mitigations (Story 2.2/2.3 test files) are now redundant safety margin, not load-bearing — left in place since removing them (e.g. restoring `test_snapshot_strategy.py`'s dropped negative-case test) is separate test-coverage work, not part of this fix.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-research-test-and-path-fixes
resolution-undo: 89c508f4086e6130fe63c3b8c7df6e501f431e414611a6838c2e01ff0641c80a 2026-10-05 7374617475733a206f70656e

### DW-53: `metrics_store.latest()/history()/nearest()` lack the `_lock` guard `write()` has.

origin: migrated from legacy ledger ("Deferred from: code review of 1-8-volatility-based-ranking-mode-via-a-dedicated-ranking-engine (2026-07-24)"), 2026-10-05
location: troll/ranking_engine/metrics_store.py:87-125
reason: **`metrics_store.latest()/history()/nearest()` lack the `_lock` guard `write()` has.** Confirmed present (missing) in the file before this story's relocation via `git show` at the story's baseline commit — pre-existing, not introduced by the `git mv` into `ranking_engine/`. Real risk: concurrent reads from the same process (e.g. dashboard serving two simultaneous HTTP requests via separate `asyncio.to_thread` calls) hitting the same shared `sqlite3.Connection` without synchronization. `troll/ranking_engine/metrics_store.py:87-125`.
status: done 2026-10-05
resolution: already resolved: platform/ranking/infrastructure/metrics_store.py:164,182,190 latest/history/nearest take self._lock

### DW-54: `metrics_store.py:76`'s `INSERT OR REPLACE` line is 106 characters

origin: migrated from legacy ledger ("Deferred from: code review of 1-8-volatility-based-ranking-mode-via-a-dedicated-ranking-engine (2026-07-24)"), 2026-10-05
location: metrics_store.py:76
reason: **`metrics_store.py:76`'s `INSERT OR REPLACE` line is 106 characters**, over this project's enforced 100-character limit — confirmed already present at that length in the file before relocation (`git show` at baseline). `troll/ranking_engine/metrics_store.py:76`.
status: done 2026-10-05
resolution: already resolved: platform/ranking/infrastructure/metrics_store.py:147 INSERT OR REPLACE now an f-string within 100 cols (no >100 SQL line remains)

### DW-55: Collector's `metrics.db` docker mount stays read-write

origin: migrated from legacy ledger ("Deferred from: code review of 1-8-volatility-based-ranking-mode-via-a-dedicated-ranking-engine (2026-07-24)"), 2026-10-05
location: troll/dydx_collector/
reason: **Collector's `metrics.db` docker mount stays read-write** despite this story's compose comments now framing `ranking_engine` as metrics.db's "sole writer." Confirmed unchanged from baseline; `dydx_collector`'s code never imports `metrics_store` (grep-confirmed across `troll/dydx_collector/`), so this is a loose, unused permission predating this story, not a live risk today, but worth tightening to `:ro` if the mount is even still needed. `troll/docker-compose.yml` (collector service).
status: done 2026-10-05
resolution: already resolved: platform/docker-compose.yml collector service (63-125) has no metrics mount; only ranking_engine:285 (rw) and data_api:314 (:ro)

### DW-56: Independent rank-computation cadences

origin: migrated from legacy ledger ("Deferred from: code review of 1-8-volatility-based-ranking-mode-via-a-dedicated-ranking-engine (2026-07-24)"), 2026-10-05
location: metrics.db
reason: **Independent rank-computation cadences**: the rank persisted to `metrics.db` (via the relocated 60s slow loop) and the rank published on `rankings:live` (continuous, change+heartbeat) are computed independently at different moments from the same mutable state, so they can disagree briefly at a given wall-clock instant. This is AD-9's own deliberate two-path design (live path vs. historical path serve genuinely different needs), not a regression this story introduced — noted here only so a future reviewer doesn't mistake it for drift.
status: open

### DW-57: `ranking_state._handle_rankings_message` only validates that `ranks` is list-shaped

origin: migrated from legacy ledger ("Deferred from: code review of 4-1-scaffold-the-tui-shell-and-live-coins-pane (2026-07-28)"), 2026-10-05
location: dashboard.py
reason: **`ranking_state._handle_rankings_message` only validates that `ranks` is list-shaped** — it never checks for a `mode` key or that each row has `rank`/`instrument_id`/`volume24h`/`volatility_score`. `coins_pane.coin_rows()` and `app._format_coin_row()` then index those fields unguarded, so a `ranking_engine` message that is list-shaped but otherwise malformed raises an uncaught `KeyError`/`TypeError` inside `_build_body()` (on the redraw path, not inside `_redis_listener`'s own try/except), crashing the whole TUI process rather than logging and skipping the bad message. Matches this codebase's established AD-3 "readers trust the gate" convention (identical to `dashboard.py`'s own trust boundary) and the story's own Dev Notes explicitly instruct against duplicating that validation here — deferred rather than fixed. Worth revisiting only if `ranking_engine` is ever observed to emit a malformed message in practice. `troll/bot_tui/ranking_state.py`, `troll/bot_tui/coins_pane.py`.
status: open

### DW-58: `BotTuiApp.run()`'s `finally` block cancels the listener/redraw asyncio tasks but never awaits their cancellation

origin: migrated from legacy ledger ("Deferred from: code review of 4-1-scaffold-the-tui-shell-and-live-coins-pane (2026-07-28)"), 2026-10-05
location: troll/bot_tui/app.py:run
reason: **`BotTuiApp.run()`'s `finally` block cancels the listener/redraw asyncio tasks but never awaits their cancellation** (no `loop.run_until_complete(...)` after `MainLoop.run()` returns), so the aioredis connection isn't given a chance to close cleanly on `:q`. Likely to emit a harmless "Task was destroyed but it is pending!" warning at shutdown. Cosmetic only — the process exits immediately afterward. `troll/bot_tui/app.py:run`.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-bot-tui-robustness
resolution-undo: ddee5d273c5815dadfe57f0176738c0b9a9a64ff679763c9b494d5992cf1f027 2026-10-05 7374617475733a206f70656e

### DW-59: The working tree already contained substantial uncommitted deployment wiring for Story 1.8

origin: migrated from legacy ledger ("Deferred from: code review of 4-1-scaffold-the-tui-shell-and-live-coins-pane (2026-07-28)"), 2026-10-05
location: troll/docker-compose.yml
reason: **The working tree already contained substantial uncommitted deployment wiring for Story 1.8** (a new `ranking_engine` compose service; `dashboard`'s `metrics.db` bind mount migrated from a single file to a directory) that this story's own baseline-to-working-tree diff necessarily swept in alongside `bot_tui`'s changes, even though neither is part of Story 4.1's scope. Not a defect — Story 1.8 is marked `done` in sprint-status.yaml, this is simply uncommitted follow-through that predates Story 4.1. Recommend committing Story 1.8's deployment changes and Story 4.1's `bot_tui` changes as separate commits before Story 4.2 builds further on this tree. `troll/docker-compose.yml`.
status: open

### DW-60: Staleness detection (`is_stale`, `_LATEST_RANKING_RECEIVED_AT`) is built on wall-clock `time.time()`, not `time.monotonic()`

origin: migrated from legacy ledger ("Deferred from: code review of 4-2-coins-pane-interaction-and-attention-only-visual-polish (2026-07-28)"), 2026-10-05
location: ml_signals/dashboard.py
reason: **Staleness detection (`is_stale`, `_LATEST_RANKING_RECEIVED_AT`) is built on wall-clock `time.time()`, not `time.monotonic()`** -- vulnerable to NTP adjustments/manual clock changes causing false stale/fresh flips, and if the clock ever moves backward, `is_stale` can under-report staleness entirely (negative `now - received_at` never exceeds the threshold). Mirrors `ml_signals/dashboard.py`'s own already-shipped, already-accepted identical pattern this story was explicitly instructed to reuse verbatim -- not a new risk, and a near-identical clock-skew item is already deferred against `dashboard.py`'s own `_is_fresh` (Story 1.3's review). `troll/bot_tui/ranking_state.py`.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-bot-tui-robustness
resolution-undo: ddee5d273c5815dadfe57f0176738c0b9a9a64ff679763c9b494d5992cf1f027 2026-10-05 7374617475733a206f70656e

### DW-61: Nothing in the Coins pane displays which Ranking Mode is currently active.

origin: migrated from legacy ledger ("Deferred from: code review of 4-2-coins-pane-interaction-and-attention-only-visual-polish (2026-07-28)"), 2026-10-05
location: troll/bot_tui/app.py
reason: **Nothing in the Coins pane displays which Ranking Mode is currently active.** After pressing `m` and waiting for `ranking_engine`'s confirmation, a builder has no way to visually verify the toggle took effect beyond inferring it from raw score magnitude. Not required by any of Story 4.2's ACs -- a real UX gap worth a small follow-up (e.g. a mode label in the breadcrumb or footer), possibly bundled with Story 4.8's accessibility/voice-tone pass. `troll/bot_tui/app.py`.
status: open

### DW-62: `publish_mode_toggle` opens a fresh Redis connection per `m` press with no connect/socket timeout configured

origin: migrated from legacy ledger ("Deferred from: code review of 4-2-coins-pane-interaction-and-attention-only-visual-polish (2026-07-28)"), 2026-10-05
location: troll/bot_tui/ranking_state.py:publish_mode_toggle
reason: **`publish_mode_toggle` opens a fresh Redis connection per `m` press with no connect/socket timeout configured** -- an unreachable Redis could leave a fire-and-forget task hanging indefinitely. The per-call-connection design itself is a deliberate, documented YAGNI choice (Story 4.2's Task 4); adding a timeout is a small, low-risk follow-up but wasn't required for this low-frequency interactive action. `troll/bot_tui/ranking_state.py:publish_mode_toggle`.
status: open

### DW-63: `ranking_state._handle_rankings_message` only validates that `ranks` is list-shaped, not that `mode`/per-row keys are present

origin: migrated from legacy ledger ("Deferred from: code review of 4-2-coins-pane-interaction-and-attention-only-visual-polish (2026-07-28)"), 2026-10-05
location: troll/bot_tui/ranking_state.py
reason: **`ranking_state._handle_rankings_message` only validates that `ranks` is list-shaped, not that `mode`/per-row keys are present** -- a list-shaped-but-otherwise-malformed message could raise an uncaught `KeyError`/`TypeError` inside `coin_rows()`/`_format_coin_row`, crashing the whole TUI. Matches this codebase's established AD-3 "readers trust the gate" convention and was already deferred against Story 4.1's own review -- restated here since Story 4.2 added a second call site (`_toggle_mode`'s `.get("mode")`) that shares the same upstream trust assumption. `troll/bot_tui/ranking_state.py`, `troll/bot_tui/coins_pane.py`.
status: open

### DW-64: Ladder padding: the shorter side is padded with blank strings past its own length when the other side is much longer/deeper

origin: migrated from legacy ledger ("Deferred from: code review of 4-3-coin-detail-drill-down-with-collapsible-order-book-depth.md (2026-07-29)"), 2026-10-05
location: troll/bot_tui/app.py:321-327
reason: **Ladder padding: the shorter side is padded with blank strings past its own length when the other side is much longer/deeper**, e.g. an empty bid side (`["no bids"]`, length 1) against a fully populated 20-row ask side renders 19 blank-bid rows rather than repeating/omitting cleanly. A disclosed judgment call from this story's own Dev Notes (Task 3: "a display-layout detail, not a data-shape concept"), but the manual smoke check only exercised a symmetric-thinness book (3 levels both sides), so this asymmetric visual gap was never actually observed. Cosmetic only -- the `no bids`/`no asks` sentinel still renders correctly on row 0. `troll/bot_tui/app.py:321-327`.
status: open

### DW-65: `coin_detail_state._handle_snapshot_batch`'s docstring overstates its own defensiveness

origin: migrated from legacy ledger ("Deferred from: code review of 4-3-coin-detail-drill-down-with-collapsible-order-book-depth.md (2026-07-29)"), 2026-10-05
location: ranking_state.py
reason: **`coin_detail_state._handle_snapshot_batch`'s docstring overstates its own defensiveness** ("a row missing instrument_id or shaped unexpectedly is skipped, not fatal") -- a `KeyError` from a field genuinely missing on the matched row is not caught inside the function itself; it propagates up and is only caught two layers up by `_redis_listener`'s per-message try/except, which drops the entire message rather than the one bad row the docstring implies is being defended against. Matches this codebase's already-accepted AD-3 "readers trust the gate" convention (same shape-trust assumption already deferred for `ranking_state.py` against Stories 4.1/4.2's reviews) -- a docstring-wording fix only, not a behavior change. `troll/bot_tui/coin_detail_state.py:95-101`.
status: open

### DW-66: `_open_dashboard_chart`'s `webbrowser.open()` call isn't guarded against stdout/stderr bleed-through from a failed headless launch corrupting the urwid …

origin: migrated from legacy ledger ("Deferred from: code review of 4-3-coin-detail-drill-down-with-collapsible-order-book-depth.md (2026-07-29)"), 2026-10-05
location: try/except Exception
reason: **`_open_dashboard_chart`'s `webbrowser.open()` call isn't guarded against stdout/stderr bleed-through from a failed headless launch corrupting the urwid full-screen display.** This product's own `main()` already redirects Python logging to a file specifically to avoid stray output corrupting the screen (`logging.basicConfig(..., filename=_LOG_PATH)`), but a subprocess-based browser backend (e.g. `xdg-open` under the documented headless Docker/SSH deployment shape) can still write directly to the terminal on failure -- the `try/except Exception` around the call only catches Python-level exceptions, not subprocess fd bleed-through. Self-healing within one redraw tick (~0.5s) since the periodic redraw loop repaints the full screen; not a crash. `troll/bot_tui/app.py:448-461`.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-bot-tui-robustness
resolution-undo: ddee5d273c5815dadfe57f0176738c0b9a9a64ff679763c9b494d5992cf1f027 2026-10-05 7374617475733a206f70656e

### DW-67: `webbrowser.open()` runs synchronously on the single asyncio event-loop thread

origin: migrated from legacy ledger ("Deferred from: code review of 4-3-coin-detail-drill-down-with-collapsible-order-book-depth.md (2026-07-29)"), 2026-10-05
location: troll/bot_tui/app.py:458
reason: **`webbrowser.open()` runs synchronously on the single asyncio event-loop thread**, with no offload to an executor -- could briefly block Redis ingestion and the redraw loop for the duration of the subprocess launch. Low risk: this is a rare, human-triggered action (pressing `o` once per Coin-detail visit), not a hot path. `troll/bot_tui/app.py:458`.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-bot-tui-robustness
resolution-undo: ddee5d273c5815dadfe57f0176738c0b9a9a64ff679763c9b494d5992cf1f027 2026-10-05 7374617475733a206f70656e

### DW-68: The `d` keypress handler (and `_open_coin_detail`) call `_build_coin_detail_body()` with no try/except, unlike the redraw loop's identical call, which is …

origin: migrated from legacy ledger ("Deferred from: code review of 4-3-coin-detail-drill-down-with-collapsible-order-book-depth.md (2026-07-29)"), 2026-10-05
location: collector.py:721-724
reason: **The `d` keypress handler (and `_open_coin_detail`) call `_build_coin_detail_body()` with no try/except, unlike the redraw loop's identical call, which is guarded.** A malformed snapshot (mismatched-length `bid_prices`/`bid_sizes` lists) would crash the keypress handler but not the periodic redraw tick. The only realistic trigger -- the collector ever emitting mismatched-length price/size lists -- is itself unreachable: `collector.py:721-724` constructs both lists from the same `bid_levels`/`ask_levels` source via parallel comprehensions, guaranteeing equal length by construction. `troll/bot_tui/app.py:530-534`.
status: open

### DW-69: `esc` in Coin-detail calls `coin_detail_state.close_coin()` before checking whether `_pop_view` will actually leave the view.

origin: migrated from legacy ledger ("Deferred from: code review of 4-3-coin-detail-drill-down-with-collapsible-order-book-depth.md (2026-07-29)"), 2026-10-05
location: troll/bot_tui/app.py:537-540
reason: **`esc` in Coin-detail calls `coin_detail_state.close_coin()` before checking whether `_pop_view` will actually leave the view.** If the view stack were ever empty while `self._view == "coin_detail"` (not currently reachable -- `coin_detail` is only ever entered via `_open_coin_detail`, which always pushes onto the stack first), state would be cleared while the view stayed stranded on `coin_detail`. Flagged for future-proofing only. `troll/bot_tui/app.py:537-540`.
status: open

### DW-70: Two independent Redis pubsub connections now exist per `bot_tui` process

origin: migrated from legacy ledger ("Deferred from: code review of 4-3-coin-detail-drill-down-with-collapsible-order-book-depth.md (2026-07-29)"), 2026-10-05
location: dashboard.py
reason: **Two independent Redis pubsub connections now exist per `bot_tui` process** (`ranking_state._redis_listener` and `coin_detail_state._redis_listener`), each with its own uncoordinated 2-second reconnect backoff, rather than multiplexing onto `ranking_state`'s existing connection (as `dashboard.py` itself does for both its channels). An explicit, already-reasoned-about tradeoff documented in `coin_detail_state.py`'s own module docstring (avoiding a larger, riskier change to already-shipped/reviewed Story 4.1/4.2 code) -- not a new gap, restated here for visibility. `troll/bot_tui/coin_detail_state.py:16-30`.
status: open

### DW-71: `docker-compose.yml`'s `bot_tui` service has no `depends_on` on the services that publish `snapshots:raw`/`rankings:live`.

origin: migrated from legacy ledger ("Deferred from: code review of 4-3-coin-detail-drill-down-with-collapsible-order-book-depth.md (2026-07-29)"), 2026-10-05
location: docker-compose.yml
reason: **`docker-compose.yml`'s `bot_tui` service has no `depends_on` on the services that publish `snapshots:raw`/`rankings:live`.** Running `docker compose run bot_tui` before `dashboard`/`ranking_engine`/the collector are actually publishing will show "warming up…"/empty ladders indefinitely -- consistent with the rest of this stack's existing tolerant, reconnect-loop-based design (every listener already retries every 2s regardless of startup order), not a regression this story introduced. `troll/docker-compose.yml`.
status: open

### DW-72: `open_coin()`/`close_coin()` mutate five module globals across multiple statements with no lock/atomic guard

origin: migrated from legacy ledger ("Deferred from: code review of 4-3-coin-detail-drill-down-with-collapsible-order-book-depth.md (2026-07-29)"), 2026-10-05
location: troll/bot_tui/coin_detail_state.py:63-92
reason: **`open_coin()`/`close_coin()` mutate five module globals across multiple statements with no lock/atomic guard**, relying entirely on an unstated "no `await` occurs between these lines" invariant to stay race-free against the concurrently-running `_redis_listener` task. Currently safe (both functions are fully synchronous), but the invariant isn't documented as a constraint on future edits -- a future refactor that inserts an `await` here could silently reintroduce a window where `_CURRENT_INSTRUMENT_ID` points at a new coin while `_MICROPRICE`/`_OFI`/`_OBI` still reference the previous one's stale instances. `troll/bot_tui/coin_detail_state.py:63-92`.
status: done 2026-10-05
resolution: already resolved: coin_detail_state.py no longer exists (platform/bot_tui has no such module); invariant moot

### DW-73: Bots-pane body has no persistent-object/scroll-preservation treatment, unlike the Coins pane's Story 4.3 fix.

origin: migrated from legacy ledger ("Deferred from: code review of 4-4-bots-pane-with-start-stop-control.md (2026-09-01)"), 2026-10-05
location: troll/bot_tui/app.py:_build_bots_body
reason: **Bots-pane body has no persistent-object/scroll-preservation treatment, unlike the Coins pane's Story 4.3 fix.** `_build_bots_body()` is rebuilt fresh every redraw tick (~0.5s) and on every `bots:status` heartbeat, resetting `ListBox.focus_position` (scroll/highlight) each time. A deliberate, disclosed YAGNI scope cut for this story: there is no drill-in/`esc` round trip through the Bots pane yet to actually exercise scroll preservation (Bot-detail's `Enter`/`esc` is Story 4.5) -- Story 4.3 only built its own equivalent fix once its own AC6 made the gap real. Revisit if/when Story 4.5 adds a round trip through this pane, mirroring Story 4.3's `_refresh_coins_body`/`_set_coins_rows` pattern. `troll/bot_tui/app.py:_build_bots_body`.
status: done 2026-10-05
resolution: already resolved: platform/bot_tui/app.py:480-492,1556 persistent _bots_listbox + _refresh_bots_body preserve scroll

### DW-74: `_toggle_bot`'s start/stop direction is decided from `bots_state`'s last-known `running` field, which can be stale

origin: migrated from legacy ledger ("Deferred from: code review of 4-4-bots-pane-with-start-stop-control.md (2026-09-01)"), 2026-10-05
location: troll/bot_tui/app.py:_toggle_bot
reason: **`_toggle_bot`'s start/stop direction is decided from `bots_state`'s last-known `running` field, which can be stale** (up to `_BOT_STALE_SECONDS` = 15s old, or older if the bot has crashed and stopped heartbeating entirely -- `_LATEST_STATUSES` never expires an entry, it just ages toward the stale badge). Pressing `s` against a crashed bot's lingering "running: true" sends a "stop" into the void; harmless (published to a channel no live process is consuming) but not actually correct feedback. No optimistic local-state fix is possible without violating AC3's "no optimistic local state change" -- the honest fix would be disabling/relabeling `s` once a row's stale badge is active, not attempted here. `troll/bot_tui/app.py:_toggle_bot`.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-bot-tui-robustness
resolution-undo: ddee5d273c5815dadfe57f0176738c0b9a9a64ff679763c9b494d5992cf1f027 2026-10-05 7374617475733a206f70656e

### DW-75: `bot_status.run()`'s reconnect loop can leak an orphaned `_control_loop` task on a partial failure.

origin: migrated from legacy ledger ("Deferred from: code review of 4-4-bots-pane-with-start-stop-control.md (2026-09-01)"), 2026-10-05
location: coin_detail_state.py
reason: **`bot_status.run()`'s reconnect loop can leak an orphaned `_control_loop` task on a partial failure.** If `_heartbeat_loop` raises inside `asyncio.gather(...)`, the sibling `_control_loop` task is not explicitly cancelled -- it keeps running until its own connection (closed by the `async with` block unwinding) errors out on its own, at which point it's an unretrieved-exception warning, not a crash. Explicitly disclosed in `run()`'s own docstring as an accepted tradeoff mirroring `coin_detail_state.py`'s own already-precedented two-independent-connections looseness -- not worth extra supervision machinery for a personal, single-bot tool. `troll/live_paper/bot_status.py:run`.
status: done 2026-10-05
resolution: already resolved: bot_status.py removed; platform/bots/application/supervise.py:159 uses a TaskGroup so a failing loop cancels its sibling

### DW-76: `bots:status`/`bots:control` add a third independent `bot_tui` Redis pubsub connection

origin: migrated from legacy ledger ("Deferred from: code review of 4-4-bots-pane-with-start-stop-control.md (2026-09-01)"), 2026-10-05
location: troll/bot_tui/bots_state.py:_redis_listener
reason: **`bots:status`/`bots:control` add a third independent `bot_tui` Redis pubsub connection** (`bots_state._redis_listener`), alongside `ranking_state`'s and `coin_detail_state`'s own (Story 4.3's already-documented tradeoff), each with its own uncoordinated 2-second reconnect backoff. Same reasoning restated for a third channel, not a new gap. `troll/bot_tui/bots_state.py:_redis_listener`.
status: open

### DW-77: `live_paper/tests/test_node.py` needed a new autouse `_fresh_event_loop` fixture (`live_paper/tests/conftest.py`) to keep working after this story.

origin: migrated from legacy ledger ("Deferred from: code review of 4-4-bots-pane-with-start-stop-control.md (2026-09-01)"), 2026-10-05
location: live_paper/tests/test_node.py
reason: **`live_paper/tests/test_node.py` needed a new autouse `_fresh_event_loop` fixture (`live_paper/tests/conftest.py`) to keep working after this story.** `build_node()` now eagerly calls `loop.create_task(bot_status.run(...))`, which raises `RuntimeError: Event loop is closed` when a second `TradingNode` is constructed in the same pytest process after an earlier test's `node.dispose()` closed the thread-global "current" event loop `TradingNode`'s own kernel construction reuses via `asyncio.get_event_loop()`. Fixed by forcing a fresh loop before every test in this file (mirrors `bot_tui/app.py`'s own `run()` doing the same for the identical Python 3.14 "no current event loop" removal). This is a test-only artifact -- real production use only ever calls `build_node()` once per process. `troll/live_paper/tests/conftest.py:_fresh_event_loop`.
status: open

### DW-78: `bot_id` uniqueness between a bot's paper and live-mode configs is unenforced

origin: migrated from legacy ledger ("Deferred from: code review of 4-4-bots-pane-with-start-stop-control.md (2026-09-01)"), 2026-10-05
location: config.toml
reason: **`bot_id` uniqueness between a bot's paper and live-mode configs is unenforced** -- restating the architecture's own already-flagged operator-discipline gap (epic-4-context.md's Technical Decisions) now that Story 4.4 is the first story to actually wire `bot_id` into a live, addressable Redis identity. A cloned config that keeps the same `bot_id` across `config.toml`/a real-money config would silently merge that bot's status/control history across paper and real-money trading. No automated check exists. `troll/live_paper/config.py`.
status: done 2026-10-07
resolution: resolved by sweep bundle dw-bot-id-collision-guard
resolution-undo: fd028024f90e6292adc9ecc2b8da84736b14431843b0558d204d256376c65e4e 2026-10-07 7374617475733a206f70656e
decision: 2026-10-05 Detect collision at startup via Redis bots:status presence from another config/mode — Refuse to start (or warn loudly) when a live bot_id is already heartbeating from a different mode/config

### DW-79: `usePickerIndicatorValues`'s `resetAndLoad` wipes and re-fetches history for every configured picker indicator whenever any one indicator is …

origin: migrated from legacy ledger ("Deferred from: code review of 15-6-per-coin-indicator-configuration (2026-09-16)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: `usePickerIndicatorValues`'s `resetAndLoad` wipes and re-fetches history for every configured picker indicator whenever any one indicator is added/removed/param-applied, not just the one that changed -- a user who scrolled back through days of history for indicator A loses that scroll-back the moment they add unrelated indicator B. evidence: `troll/frontend/src/hooks/usePickerIndicatorValues.ts`'s `resetAndLoad` unconditionally clears `itemsRef`/`seriesByKey` for the whole hook (not per-key) and re-anchors every series to the current view on any `entries` change; the hook's own docstring documents this as deliberate ("each picker change is a materially different request... no unbounded/stitched-across-requests state"). Fixing this properly means per-indicator incremental fetch/merge rather than whole-hook reset -- a real architecture change, not a trivial patch.
status: done 2026-10-05
resolution: closed by human decision: Accepted: deliberate design, request is cheap and re-anchors to view
decision: 2026-10-05 Accept whole-hook reset — Accepted: deliberate design, request is cheap and re-anchors to view

### DW-80: One bad/stale persisted indicator entry (e.g. referencing a renamed/removed catalog indicator) fails the entire `GET /api/coin/{iid}/indicator-values` request …

origin: migrated from legacy ledger ("Deferred from: code review of 15-6-per-coin-indicator-configuration (2026-09-16)"), 2026-10-05
location: GET /api/coin/{iid}/indicator-values
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: One bad/stale persisted indicator entry (e.g. referencing a renamed/removed catalog indicator) fails the entire `GET /api/coin/{iid}/indicator-values` request, and the frontend surfaces that failure only via `console.error` -- a user sees all their picker panes go blank with zero on-screen explanation. evidence: `indicators.py`'s `get_indicator_values` wraps the whole per-entry replay loop (`_values_by_time`) in one try/except -> single 400 for the whole request (matches the retired `dashboard.py._indicators_json`'s documented behavior, not a regression). `usePickerIndicatorValues.ts`'s `loadPage` `.catch` only logs; no error state reaches `IndicatorPicker`/`ChartPage`. Consistent with every other chart-history hook's identical "log and keep previous state" failure handling in this frontend (`useCandles`, `useIndicatorSeries`) -- fixing it here alone would be inconsistent with the rest of the app; a proper fix needs a repo-wide error-surfacing convention, not a one-off change.
status: done 2026-10-05
resolution: already resolved: platform/data_api/routes/indicators.py returns per-entry `errors` and platform/frontend/src/hooks/usePickerIndicatorValues.ts:97 passes them to onErrors

### DW-81: `data_api/routes/indicators.py`'s `CATALOG_PATH` and `ml_signals/custom_indicators.py`'s own `_CATALOG_PATH` are two independently-set module globals that must …

origin: migrated from legacy ledger ("Deferred from: code review of 15-6-per-coin-indicator-configuration (2026-09-16)"), 2026-10-05
location: data_api/routes/indicators.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: `data_api/routes/indicators.py`'s `CATALOG_PATH` and `ml_signals/custom_indicators.py`'s own `_CATALOG_PATH` are two independently-set module globals that must agree for a custom-indicator values request to be coherent, with no startup assertion that they do. evidence: Confirmed both constants are separately derived from env vars with no cross-reference; the new test file even has to `monkeypatch` both separately to keep them in sync (`test_indicator_values_dispatches_custom_indicator_via_replay_window`). This mirrors the established, deliberate pattern of `candles.py`/`indicator_series.py` each keeping their own independent `CATALOG_PATH` copy (module docstring: "same non-circular-import pattern... established") -- consistent with existing architecture, not a story-specific regression.
status: done 2026-10-05
resolution: already resolved: platform/data_api/routes/indicators.py:359 passes _candles.CATALOG_PATH/CANDLES_DB_DIR into the replay; single source, no second global

### DW-82: A slow-warm-up native indicator (e.g. default-period SMA/EMA) spends a large fraction of a freshly-added picker pane's small initial page as `None`, since …

origin: migrated from legacy ledger ("Deferred from: code review of 15-6-per-coin-indicator-configuration (2026-09-16)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: A slow-warm-up native indicator (e.g. default-period SMA/EMA) spends a large fraction of a freshly-added picker pane's small initial page as `None`, since `chart_indicators.replay_indicator` restarts from cold state on every page fetch -- pre-existing behavior, but now directly user-exposed since the picker gives users free choice of arbitrary indicator/period combinations. evidence: Same per-page-reset replay behavior already present in `candles.py`/`indicator_series.py` (AD-F2/SSOT-01: no new indicator math added by this story). Not fixable within this story's scope without either persisting indicator state across pages or widening the query window, both out of proportion to a config-persistence story.
status: done 2026-10-05
resolution: closed by human decision: Accepted: cold-start Nones are the honest warm-up gap, shown not fabricated
decision: 2026-10-05 Accept; None is honest warm-up gap — Accepted: cold-start Nones are the honest warm-up gap, shown not fabricated

### DW-83: `PUT /api/coin/{iid}/indicators`' read-modify-write (`load_config` -> mutate -> `save_config`, a full-TOML-rewrite) has no locking -- two concurrent `PUT`s …

origin: migrated from legacy ledger ("Deferred from: code review of 15-6-per-coin-indicator-configuration (2026-09-16)"), 2026-10-05
location: PUT /api/coin/{iid}/indicators
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: `PUT /api/coin/{iid}/indicators`' read-modify-write (`load_config` -> mutate -> `save_config`, a full-TOML-rewrite) has no locking -- two concurrent `PUT`s, even for different `instrument_id`s, can each read the same on-disk config and the second write silently discards the first's change. evidence: Confirmed by reading `put_coin_indicator_config` and `chart_indicator_config.save_config` (full-file rewrite, no lock). Broader than the already-deferred Story 15.5-era `dashboard`-vs-`data_api` dual-writer race (transitional until Story 15.10's cutover) -- this is the same root cause (no locking on `chart_indicators.toml`) but manifests even within `data_api` alone, e.g. two browser tabs saving different coins' configs at once. Not fixed here: locking/atomic-write is a real design decision (file lock vs. temp-file+rename vs. per-instrument sharding) beyond a trivial patch.
status: done 2026-10-05
resolution: already resolved: platform/data_api/routes/indicators.py:76 PREFERENCES_LOCK serialises read-modify-write; cross-process limit documented as Known limit with flock upgrade path

### DW-84: `GET /api/coin/{iid}/indicator-values`' `has_more` can prematurely report `False` for a sparse/gapped instrument -- if the single bounded query window happens …

origin: migrated from legacy ledger ("Deferred from: code review of 15-6-per-coin-indicator-configuration (2026-09-16)"), 2026-10-05
location: GET /api/coin/{iid}/indicator-values
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: `GET /api/coin/{iid}/indicator-values`' `has_more` can prematurely report `False` for a sparse/gapped instrument -- if the single bounded query window happens to contain zero candles even though older data exists further back, the route returns immediately without probing further, permanently stalling scroll-back pagination. evidence: Confirmed the `if not kept: return IndicatorValuesResponse(items=[], has_more=False)` early return has no retry/widen-window fallback. Identical, pre-existing shape in `candles.py`'s own `get_candles` (same `if not kept: return CandlesResponse(items=[], has_more=False)`), which this route explicitly reuses as its own reference pattern (Code Map) -- a shared, pre-existing limitation across the whole cursor-paginated API surface, not unique to this story.
status: done 2026-10-05
resolution: already resolved: platform/views/chart_series.py:937 candle_page derives has_more from archive coverage ranges (has_older_data), not from window emptiness

### DW-85: `_build_bot_detail_body()`'s PnL coloring locates the PnL segment via `str.index()` substring search rather than a structured return from …

origin: migrated from legacy ledger ("Deferred from: code review of 4-5-bot-detail-live-snapshot-view.md (2026-09-01)"), 2026-10-05
location: bots_pane.py
reason: **`_build_bot_detail_body()`'s PnL coloring locates the PnL segment via `str.index()` substring search rather than a structured return from `bots_pane.bot_detail_lines()`.** Works correctly today because `format_pnl`'s output is deterministically embedded as the first thing after `"pnl "` in line 2 and can't coincidentally appear earlier in that same string, but it's a more fragile coupling between `bots_pane.py`'s text layout and `app.py`'s coloring logic than the ladder's own bid/ask `urwid.Columns` pairing (Story 4.3) or the Bots-pane row's own `_build_bot_row_widget` (Story 4.4, which builds its markup list directly rather than searching rendered text for a segment). A cleaner fix would have `bot_detail_lines` return the PnL line as `(prefix, pnl_text, suffix)` or similar instead of one fused string -- not attempted here since it would touch `bot_detail_lines`'s already-tested return shape for a purely cosmetic-robustness win. `troll/bot_tui/app.py:_build_bot_detail_body`.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-bot-tui-robustness
resolution-undo: ddee5d273c5815dadfe57f0176738c0b9a9a64ff679763c9b494d5992cf1f027 2026-10-05 7374617475733a206f70656e

### DW-86: Bot-detail's own `s`-triggered `_toggle_bot()` call is not exercised by an automated test that reaches `asyncio.ensure_future(...)`

origin: migrated from legacy ledger ("Deferred from: code review of 4-5-bot-detail-live-snapshot-view.md (2026-09-01)"), 2026-10-05
location: troll/bot_tui/app.py:_toggle_bot
reason: **Bot-detail's own `s`-triggered `_toggle_bot()` call is not exercised by an automated test that reaches `asyncio.ensure_future(...)`** -- confirmed the same established, deliberate testing-boundary Story 4.4 already documented for `_toggle_bot`/`_toggle_mode` (needs a running event loop, left to the manual smoke check). This story's own `test_active_bot_id_reads_open_bot_from_bot_detail` confirms the *targeting* logic (which bot `s` would act on) without invoking the scheduling call itself -- an initial version of this story's test suite violated this boundary and was caught failing under the real combined-suite Docker invocation before being fixed (see the story's own Debug Log). Not a new gap, but worth restating since it bit this story once already. `troll/bot_tui/app.py:_toggle_bot`.
status: open

### DW-87: No manual/interactive smoke check performed this session

origin: migrated from legacy ledger ("Deferred from: code review of 4-5-bot-detail-live-snapshot-view.md (2026-09-01)"), 2026-10-05
location: live_paper
reason: **No manual/interactive smoke check performed this session** (no running `live_paper` process publishing genuine `bots:status` heartbeats, no interactive TTY available) -- same disclosed gap as Story 4.4, not silently normalized into a permanent skip. A real keypress-driven `Enter` → `s` → `esc` check against a running bot is recommended before production reliance. See the story's own Dev Agent Record.
status: open

### DW-88: `git stash`-based "confirm mypy/lint findings are pre-existing" checks are unreliable in this repository

origin: migrated from legacy ledger ("Deferred from: code review of 4-5-bot-detail-live-snapshot-view.md (2026-09-01)"), 2026-10-05
location: git stash
reason: **`git stash`-based "confirm mypy/lint findings are pre-existing" checks are unreliable in this repository** -- nothing between Epic 4 stories has ever been committed, so `git stash` reverts *all* uncommitted work back to the last real commit (Story 4.3's baseline as of this writing), not just the current story's own diff. Story 4.4's Dev Agent Record made this exact claim ("3 pre-existing mypy errors, confirmed via git stash") using a check that, on inspection, could not have isolated Story 4.4's own changes from Story 4.3's either. Not re-verified retroactively here (out of this story's scope), but flagged so a future story doesn't repeat the same unreliable check -- use `git diff <file>` plus direct reasoning about which lines the current story's own edits touch instead. No specific file/line -- a process note, not a code defect.
status: open

### DW-89: AC #2's "no jump on pan" behavior relies on `Plotly.react()` preserving zoom/pan across a redraw when no explicit `xaxis.range` is set — never exercised in a …

origin: migrated from legacy ledger ("Deferred from: code review of 7-1-candle-chart-drag-to-pan-history (2026-09-06)"), 2026-10-05
location: troll/ml_signals/dashboard.py
reason: **AC #2's "no jump on pan" behavior relies on `Plotly.react()` preserving zoom/pan across a redraw when no explicit `xaxis.range` is set — never exercised in a real browser**, only via a Node harness that stubs `Plotly` itself and can't observe this. User chose to verify this themselves directly in a browser (drag the chart, confirm the view doesn't reset) rather than have it fixed/simulated here. `troll/ml_signals/dashboard.py` (`_renderCandleChart`/`_renderTickChart`).
status: open

### DW-90: `_second_rolling`/`_ind_rolling` maxlen bump (300→3600) and the oldest-bucket-drop in `_live_candles_json`

origin: migrated from legacy ledger ("Deferred from: code review of 7-1-candle-chart-drag-to-pan-history (2026-09-06)"), 2026-10-05
location: troll/ml_signals/dashboard.py:110,116,855-856
reason: **`_second_rolling`/`_ind_rolling` maxlen bump (300→3600) and the oldest-bucket-drop in `_live_candles_json`** predate story 7.1's own work — per the story's Dev Notes, both changes and their tests were made earlier in the same session (a separate, already-tested bugfix bundled into the same uncommitted working tree), not introduced by this story's tasks. `troll/ml_signals/dashboard.py:110,116,855-856`.
status: open

### DW-91: `_historical_ticks_json` returns a `size` field per trade that `_renderTickChart` never reads.

origin: migrated from legacy ledger ("Deferred from: code review of 7-1-candle-chart-drag-to-pan-history (2026-09-06)"), 2026-10-05
location: troll/ml_signals/dashboard.py:899
reason: **`_historical_ticks_json` returns a `size` field per trade that `_renderTickChart` never reads.** Harmless unused payload; plausible future use (tooltip/marker sizing by trade size). Not worth its own diff. `troll/ml_signals/dashboard.py:899`.
status: open

### DW-92: `_open_dashboard_bot`'s `assert self._bot_detail_bot_id is not None` is unreachable by construction today but would build a silently-wrong …

origin: migrated from legacy ledger ("Deferred from: code review of spec-4-7-bot-detail-trades-blotter-and-pnl-over-time-chart.md (2026-09-02)"), 2026-10-05
location: http://.../bot/None
reason: source_spec: `_bmad-output/implementation-artifacts/spec-4-7-bot-detail-trades-blotter-and-pnl-over-time-chart.md` summary: `_open_dashboard_bot`'s `assert self._bot_detail_bot_id is not None` is unreachable by construction today but would build a silently-wrong `http://.../bot/None` URL instead of failing loudly if that invariant were ever violated, since asserts are stripped under `python -O`; this mirrors the pre-existing, already-shipped `_open_dashboard_chart`'s identical pattern rather than introducing a new one, per this story's own spec instruction to mirror it exactly. evidence: `troll/bot_tui/app.py` -- both `_open_dashboard_chart` (Story 4.2/4.3) and the new `_open_dashboard_bot` (Story 4.7) use the same `assert ... is not None` shape ahead of URL construction; neither is guarded by an explicit `if`/`raise`. A single shared fix (or at least a joint decision to accept the risk) would be cleaner than fixing one call site and not the other.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-bot-tui-robustness
resolution-undo: ddee5d273c5815dadfe57f0176738c0b9a9a64ff679763c9b494d5992cf1f027 2026-10-05 7374617475733a206f70656e

### DW-93: Pressing `o` in Bot-detail opens a `/bot/{bot_id}` dashboard URL that 404s today -- the web dashboard has no per-bot route, and the TUI gives no in-app signal …

origin: migrated from legacy ledger ("Deferred from: code review of spec-4-7-bot-detail-trades-blotter-and-pnl-over-time-chart.md (2026-09-02)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-4-7-bot-detail-trades-blotter-and-pnl-over-time-chart.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-4-7-bot-detail-trades-blotter-and-pnl-over-time-chart.md` summary: Pressing `o` in Bot-detail opens a `/bot/{bot_id}` dashboard URL that 404s today -- the web dashboard has no per-bot route, and the TUI gives no in-app signal that this is a dead link before the operator's browser opens it. evidence: `troll/bot_tui/bots_pane.py:dashboard_bot_url`'s own docstring discloses the missing route; `troll/ml_signals/dashboard.py`'s route list has no bot-shaped endpoint (confirmed by inspection during this story's planning). Building the dashboard-side page is explicitly out of this story's scope (cross-module, TUI-only story) -- same accepted-gap shape as the pre-existing Coin-detail `o` key, but worth a future story once the web dashboard grows a bot view.
status: done 2026-10-07
resolution: resolved by sweep bundle dw-bot-tui-remove-dead-deeplink
resolution-undo: 1921b265bcbca070605b3491f747713a1ecb0d027b0c8cb4b910399c3ff99648 2026-10-07 7374617475733a206f70656e
decision: 2026-10-05 Remove the `o` key from Bot-detail — Remove dashboard_bot_url and the o binding until a web bot view exists

### DW-94: Bot-detail's three stacked regions (snapshot + trades blotter + PnL sparkline) sit inside a plain `urwid.Filler(valign="top")` with a fixed-height blotter …

origin: migrated from legacy ledger ("Deferred from: code review of spec-4-7-bot-detail-trades-blotter-and-pnl-over-time-chart.md (2026-09-02)"), 2026-10-05
location: bot_tui/tests
reason: source_spec: `_bmad-output/implementation-artifacts/spec-4-7-bot-detail-trades-blotter-and-pnl-over-time-chart.md` summary: Bot-detail's three stacked regions (snapshot + trades blotter + PnL sparkline) sit inside a plain `urwid.Filler(valign="top")` with a fixed-height blotter (`_BOT_DETAIL_BLOTTER_HEIGHT = 8`) -- no test renders this against a real or artificially small terminal size, so how it degrades (clip vs. scroll vs. error) on a short screen is unverified. Matches this whole module's existing testing convention (no urwid screen is rendered anywhere in `bot_tui/tests`), not a gap unique to this story. evidence: `troll/bot_tui/app.py:_build_bot_detail_body`; confirmed no test in `bot_tui/tests` constructs a `urwid.raw_display`/canvas or asserts on rendered dimensions anywhere in the suite, for any view.
status: open

### DW-95: `trades_blotter_lines()` returns up to Story 4.6's full 500-trade cap as individual `urwid.Text` widgets, rebuilt from scratch on every redraw tick (~0.5s …

origin: migrated from legacy ledger ("Deferred from: code review of spec-4-7-bot-detail-trades-blotter-and-pnl-over-time-chart.md (2026-09-02)"), 2026-10-05
location: app.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-4-7-bot-detail-trades-blotter-and-pnl-over-time-chart.md` summary: `trades_blotter_lines()` returns up to Story 4.6's full 500-trade cap as individual `urwid.Text` widgets, rebuilt from scratch on every redraw tick (~0.5s, `app.py`'s existing `_redraw_loop`) while Bot-detail is open, not just when the underlying history data actually changes (~15-30s cadence) -- a real, recurring rebuild cost with no measurement of its actual impact. evidence: `troll/bot_tui/bots_pane.py:trades_blotter_lines` has no internal cap beyond the wire contract's own 500; `troll/bot_tui/app.py:_build_bot_detail_body` is called unconditionally by the same per-tick redraw path every other view already uses. Likely tolerable for a personal, single-operator tool (this codebase's own established tradeoff elsewhere, e.g. `bot_status.py`'s docstring), but not measured.
status: open

### DW-96: `_current_ranks()`'s read of `_SLOW_METRICS.get(iid, {})` checks only presence, not age, so `pct_1h`/`pct_24h`/`volatility`/fallback `price` served into …

origin: migrated from legacy ledger ("Deferred from: code review of spec-13-1-bound-compute-all-catalog-read-concurrency.md (2026-09-12)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-13-1-bound-compute-all-catalog-read-concurrency.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-13-1-bound-compute-all-catalog-read-concurrency.md` summary: `_current_ranks()`'s read of `_SLOW_METRICS.get(iid, {})` checks only presence, not age, so `pct_1h`/`pct_24h`/`volatility`/fallback `price` served into `rankings:live` can go silently stale for longer once a `_slow_loop_task` cycle takes longer than before (pre-existing gap, not introduced by this story, but made more likely to bite by narrowing `compute_all`'s concurrency from 32 to 4 workers) -- in tension with troll/CLAUDE.md DATA-01's "never display stale values as live... flag the gap visually" rule. evidence: `troll/ranking_engine/engine.py` around line 400/406-408 (`_current_ranks()`); `_SLOW_METRICS` is a plain dict updated once per `DB_WRITE_INTERVAL_SECONDS` cycle with no stored `updated_at`/staleness field for consumers to check, and no existing test exercises a stale-`_SLOW_METRICS` scenario. Surfaced by Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/ranking/domain/board.py:72,462 SLOW_METRICS_MAX_AGE_NS age check on slow metrics

### DW-97: HIGH, cross-cutting, confirmed via direct reproduction -- `ranking_engine/metrics_store.py`'s `_conn()` unconditionally runs `PRAGMA journal_mode=WAL` and a …

origin: migrated from legacy ledger ("Deferred from: code review of spec-12-1-read-only-data-api-fastapi-service.md (2026-09-12)"), 2026-10-05
location: ranking_engine/metrics_store.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-12-1-read-only-data-api-fastapi-service.md` summary: **HIGH, cross-cutting, confirmed via direct reproduction** -- `ranking_engine/metrics_store.py`'s `_conn()` unconditionally runs `PRAGMA journal_mode=WAL` and a `CREATE TABLE IF NOT EXISTS` on every first-open of a db_path in a process, which requires write access even when the database is already in WAL mode and no schema change is needed; this makes `metrics_store.history()`/`nearest()` (and therefore any of `data_api`'s `/metrics/*` routes, and `dashboard.py`'s `_render_history_page`/`rank_history_json_handler`) fail with `sqlite3.OperationalError` against a `:ro`-mounted metrics directory -- which is exactly the mount both the pre-existing `dashboard` compose service and this story's new `data_api` compose service use. evidence: Reproduced directly against the real built `troll-collector:latest` image using Docker's actual `-v ...:/app/metrics_dir:ro` bind mount (not a permission simulation) and the real, shipped `ranking_engine/metrics_store.py`, both against a synthetic db and against the real production `dydx_collector/metrics/metrics.db` copy present in this dev checkout: `metrics_store.nearest(...)` raised `sqlite3.OperationalError: unable to open database file` / `attempt to write a readonly database` in every trial, including the realistic case where a live writer process already held the WAL/SHM sidecar files open. Since `docker-compose.yml`'s pre-existing `dashboard` service (`troll/docker-compose.yml` dashboard block) mounts `./dydx_collector/metrics:/app/metrics_dir:ro` identically and already calls `metrics_store.history()/nearest()`, this strongly suggests `dashboard.py`'s coin-ranking history feature is currently broken in production today, independent of this story. Root cause lives in `troll/ranking_engine/metrics_store.py:_conn()`, a file this story did not modify -- fixing it (e.g. detect read-only and skip the PRAGMA/schema statements, or open via a `file:...?mode=ro` URI when the caller is read-only) is out of this story's scope but should be treated as an urgent, separate investigation per troll/CLAUDE.md DATA-02, not folded quietly into Story 12.2's dashboard-wiring work.
status: done 2026-10-05
resolution: already resolved: platform/ranking/infrastructure/metrics_store.py:217 readers open read-only `?mode=ro` per call; writer keeps WAL sidecars (NO_CKPT_ON_CLOSE)

### DW-98: `data_api/app.py`'s sync `def` route handlers run in FastAPI's own threadpool, meaning `metrics_store.history()`/`nearest()` can now be invoked concurrently …

origin: migrated from legacy ledger ("Deferred from: code review of spec-12-1-read-only-data-api-fastapi-service.md (2026-09-12)"), 2026-10-05
location: data_api/app.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-12-1-read-only-data-api-fastapi-service.md` summary: `data_api/app.py`'s sync `def` route handlers run in FastAPI's own threadpool, meaning `metrics_store.history()`/`nearest()` can now be invoked concurrently from multiple threads against `metrics_store`'s shared module-global `sqlite3.Connection` -- a genuinely new concurrency pattern this codebase has never exercised before (the only existing caller, `dashboard.py`, is single-threaded asyncio). `write()` already wraps its own access in `metrics_store._lock`; `history()`/`nearest()`/`latest()` do not. evidence: `troll/ranking_engine/metrics_store.py:57-69,102-125` -- `_conn()`/`write()` under `with _lock:`, but `history()`/`nearest()`/`latest()` call `db.execute(...)` with no lock. Python's `sqlite3` module with `check_same_thread=False` against a default "serialized"-mode SQLite build is generally safe for this pattern in practice, so no concrete failure was reproduced (unlike the WAL/`:ro` finding above), but it is an unreviewed risk surfaced by this story's own choice of concurrency model. Surfaced by Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/ranking/infrastructure/metrics_store.py readers open a per-call connection (no shared module-global connection), commit f7c9e3ea38/88abf70269

### DW-99: `/catalog/chart-series/{symbol}` and `/catalog/snapshots/{iid}` accept caller-supplied `start_ns`/`end_ns` with no maximum window enforced, so a client can …

origin: migrated from legacy ledger ("Deferred from: code review of spec-12-1-read-only-data-api-fastapi-service.md (2026-09-12)"), 2026-10-05
location: /catalog/chart-series/{symbol}
reason: source_spec: `_bmad-output/implementation-artifacts/spec-12-1-read-only-data-api-fastapi-service.md` summary: `/catalog/chart-series/{symbol}` and `/catalog/snapshots/{iid}` accept caller-supplied `start_ns`/`end_ns` with no maximum window enforced, so a client can request an unbounded time range in one call -- the exact anti-pattern troll/CLAUDE.md MEM-01 warns against ("never load full catalog slices into memory... always use time-bounded queries"). evidence: `troll/data_api/app.py:catalog_chart_series`/`catalog_snapshots` pass `start_ns`/`end_ns` straight through to `ml_signals.chart_data.compute_chart_series()`/`ml_signals.catalog_stats.query_second_snapshots()` with no range-width check; this characteristic pre-exists in the wrapped functions themselves (this story calls them verbatim per NAUT-02) but is now reachable over the network for the first time. Surfaced by both Blind Hunter and Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-data-api-legacy-routes
resolution-undo: 0494e1d5631fbe7841cf0d7cfefba53aa18067a35474cd982d05d4012b815cd0 2026-10-05 7374617475733a206f70656e

### DW-100: No route in `data_api/app.py` catches exceptions from the underlying catalog/metrics reads (e.g. Arrow schema/precision conflicts that …

origin: migrated from legacy ledger ("Deferred from: code review of spec-12-1-read-only-data-api-fastapi-service.md (2026-09-12)"), 2026-10-05
location: data_api/app.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-12-1-read-only-data-api-fastapi-service.md` summary: No route in `data_api/app.py` catches exceptions from the underlying catalog/metrics reads (e.g. Arrow schema/precision conflicts that `ml_signals/catalog_stats.py`'s own `coverage()`/`price_series()` already special-case elsewhere for the same data paths) -- an unhandled exception becomes a generic 500 with no client-meaningful 404/400 translation, and no test exercises this path. evidence: `troll/data_api/app.py` -- all 4 route handlers call their wrapped function directly with no `try`/`except`; `troll/data_api/tests/test_data_api.py` only covers happy-path plus one "no data" branch (`nearest` returning `null`), never an exception path. Surfaced by both Blind Hunter and Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-data-api-legacy-routes
resolution-undo: 0494e1d5631fbe7841cf0d7cfefba53aa18067a35474cd982d05d4012b815cd0 2026-10-05 7374617475733a206f70656e

### DW-101: `data_api/app.py`'s `METRICS_DB_PATH` default is computed by an independent expression that a code comment merely asserts "mirrors `dashboard.py:85-86` …

origin: migrated from legacy ledger ("Deferred from: code review of spec-12-1-read-only-data-api-fastapi-service.md (2026-09-12)"), 2026-10-05
location: data_api/app.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-12-1-read-only-data-api-fastapi-service.md` summary: `data_api/app.py`'s `METRICS_DB_PATH` default is computed by an independent expression that a code comment merely asserts "mirrors `dashboard.py:85-86` exactly" -- nothing enforces the two stay identical if one is edited later (no shared constant/import, no test comparing them). evidence: `troll/data_api/app.py:44-46` vs `troll/ml_signals/dashboard.py:85-86` -- both compute `str(Path(CATALOG_PATH).parent / "metrics" / "metrics.db")` independently; a future edit to one's default with no compiler/lint/test signal to update the other. Surfaced by Blind Hunter review of this story's diff.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-data-api-legacy-routes
resolution-undo: 0494e1d5631fbe7841cf0d7cfefba53aa18067a35474cd982d05d4012b815cd0 2026-10-05 7374617475733a206f70656e

### DW-102: Minor unvalidated inputs with low-probability, non-crashing failure modes: `/metrics/history/{symbol}?days=` accepts zero/negative/absurdly large `days` (the …

origin: migrated from legacy ledger ("Deferred from: code review of spec-12-1-read-only-data-api-fastapi-service.md (2026-09-12)"), 2026-10-05
location: /metrics/history/{symbol}?days=
reason: source_spec: `_bmad-output/implementation-artifacts/spec-12-1-read-only-data-api-fastapi-service.md` summary: Minor unvalidated inputs with low-probability, non-crashing failure modes: `/metrics/history/{symbol}?days=` accepts zero/negative/absurdly large `days` (the underlying store's 31-day retention already bounds the real answer regardless); `start_ns > end_ns` on the two `/catalog/*` routes is never rejected (naturally yields an empty result rather than an error); no route has a `/health` check despite `restart: always`; routes declare no `response_model` so a NaN/Infinity value from upstream book-imbalance math would serialize as non-standard JSON tokens; no test exercises an unknown/never-subscribed instrument ID or malformed symbol path segment. evidence: `troll/data_api/app.py` (all 4 routes, no input validation beyond FastAPI's own type coercion); `troll/data_api/tests/test_data_api.py` (every test seeds exactly the data it then reads back). Surfaced by Blind Hunter and Edge Case Hunter review of this story's diff; each individually low severity, grouped here as one entry since none block real functionality today.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-data-api-legacy-routes
resolution-undo: 0494e1d5631fbe7841cf0d7cfefba53aa18067a35474cd982d05d4012b815cd0 2026-10-05 7374617475733a206f70656e

### DW-103: `make test` (inside the `troll-collector` image) now emits a new `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install …

origin: migrated from legacy ledger ("Deferred from: code review of spec-12-1-read-only-data-api-fastapi-service.md (2026-09-12)"), 2026-10-05
location: data_api/tests/test_data_api.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-12-1-read-only-data-api-fastapi-service.md` summary: `make test` (inside the `troll-collector` image) now emits a new `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead`, introduced by this story's `data_api/tests/test_data_api.py` importing `fastapi.testclient.TestClient`. Per troll/CLAUDE.md TEST-04 this is recorded rather than suppressed; not fixed in this story since the fix is an unverified dependency swap outside this story's declared scope (adding `fastapi`/`uvicorn`/`httpx` per the spec's Code Map, not `httpx2`). evidence: Warning fires unconditionally at `import starlette.testclient` (starlette 1.6.0, installed via `troll-requirements.txt`'s `fastapi==0.141.1` -> `starlette` dep), re-exported by `fastapi/testclient.py:1`; reproduced directly via `python3 -c "import starlette.testclient"` inside the built `troll-collector:latest` container. `httpx2` is confirmed to exist as a real published PyPI package (checked `https://pypi.org/pypi/httpx2/json`, latest `2.12.0`) so a fix path exists, but swapping `troll-requirements.txt`'s `httpx==0.28.1` pin for `httpx2` and re-verifying `data_api/tests` still pass has not been attempted or validated here.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-httpx-test-client-swap
resolution-undo: 4c1afab883e641f497ef3955752a01d50bf2edd5105183651c9b7aba7c97f1dd 2026-10-05 7374617475733a206f70656e

### DW-104: The module-level global `_http_session` in `ml_signals/dashboard.py` has no re-entrancy guard against two `make_app()` instances sharing/fighting over one …

origin: migrated from legacy ledger ("Deferred from: code review of spec-12-2-dashboard-remote-data-mode-local-run-docs.md (2026-09-12)"), 2026-10-05
location: ml_signals/dashboard.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-12-2-dashboard-remote-data-mode-local-run-docs.md` summary: The module-level global `_http_session` in `ml_signals/dashboard.py` has no re-entrancy guard against two `make_app()` instances sharing/fighting over one shared `aiohttp.ClientSession` in the same process (one instance's `on_cleanup` could close the session out from under another). evidence: `troll/ml_signals/dashboard.py:_get_http_session`/`_close_http_session` -- plain module `global`, no lock. Consistent with every other piece of module-global state already in this file (`_LATEST_RANKING`, `_second_rolling`, etc.), all of which already assume a single `make_app()` per process (enforced today by `dashboard.py`'s own `__main__` block). Not a live bug -- no code path in this repo constructs two `make_app()` instances in one process -- so adding locking now would be speculative (DESIGN-01 YAGNI). Surfaced by Blind Hunter review of this story's diff.
status: open

### DW-105: `_close_http_session` (registered via `app.on_cleanup`) closes the shared `aiohttp.ClientSession` without draining in-flight requests first, so a request …

origin: migrated from legacy ledger ("Deferred from: code review of spec-12-2-dashboard-remote-data-mode-local-run-docs.md (2026-09-12)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-12-2-dashboard-remote-data-mode-local-run-docs.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-12-2-dashboard-remote-data-mode-local-run-docs.md` summary: `_close_http_session` (registered via `app.on_cleanup`) closes the shared `aiohttp.ClientSession` without draining in-flight requests first, so a request coroutine mid-await inside `_fetch_json` during app shutdown could see its remote fetch aborted with a connection-closed error instead of completing or failing cleanly. evidence: `troll/ml_signals/dashboard.py:_close_http_session`/`make_app`. Same class of risk this file's pre-existing `redis_subscriber_ctx` already accepts for its own background task cancellation (not introduced by this story); a proper request-draining shutdown is a bigger feature than an opt-in convenience mode warrants. Surfaced by Edge Case Hunter review of this story's diff.
status: open

### DW-106: The shared `aiohttp.ClientSession` has a total request timeout (added by this story's review pass) but no connector/connection-pool tuning (max connections …

origin: migrated from legacy ledger ("Deferred from: code review of spec-12-2-dashboard-remote-data-mode-local-run-docs.md (2026-09-12)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-12-2-dashboard-remote-data-mode-local-run-docs.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-12-2-dashboard-remote-data-mode-local-run-docs.md` summary: The shared `aiohttp.ClientSession` has a total request timeout (added by this story's review pass) but no connector/connection-pool tuning (max connections, per-host limits, keep-alive) -- currently just aiohttp's defaults. evidence: `troll/ml_signals/dashboard.py:_get_http_session` -- `aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))`, no `connector=` argument. Reasonable default for a single-operator, low-concurrency personal dashboard; revisit only if usage patterns change (e.g. multiple simultaneous dashboard viewers hammering one `data_api` instance). Surfaced by Blind Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: 989aca2fb0 retired dashboard.py (and its aiohttp session); data_api is the sole web surface

### DW-107: No schema/type contract pins `data_api`'s JSON response shape to what `dashboard.py`'s `_price_series_rows`/`_build_chart_page_html`/`_history_page_from_rows` …

origin: migrated from legacy ledger ("Deferred from: code review of spec-12-2-dashboard-remote-data-mode-local-run-docs.md (2026-09-12)"), 2026-10-05
location: dashboard.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-12-2-dashboard-remote-data-mode-local-run-docs.md` summary: No schema/type contract pins `data_api`'s JSON response shape to what `dashboard.py`'s `_price_series_rows`/`_build_chart_page_html`/`_history_page_from_rows` expect -- verified compatible today only by reading both sides' code and by this story's own equality-based integration tests, not by a shared type or schema check. A future `data_api` serialization change could silently break dashboard's remote mode. evidence: `troll/data_api/app.py`'s `_snapshot_to_dict`/route return types vs. `troll/ml_signals/dashboard.py`'s consumers -- no shared dataclass/TypedDict/schema between the two modules, only a docstring claim ("a superset -- 4 extra OHLC keys ignored, no reshaping needed") backed by `test_dashboard_remote_mode.py`'s fixture-specific assertions. `data_api`'s contract stability is Story 12.1's scope, not this story's; already structurally mitigated by the shared render helpers reading only a known key subset (schema-tolerant by construction). Surfaced by Blind Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: 989aca2fb0 retired dashboard.py; no remote-mode consumer of data_api JSON remains

### DW-108: `LiveCandleBus._apply_to_buffer` assumes snapshots for a watched instrument always arrive in non-decreasing `ts_event` order with no duplicates -- an …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-5-live-candle-edge.md (2026-09-16)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-15-5-live-candle-edge.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-5-live-candle-edge.md` summary: `LiveCandleBus._apply_to_buffer` assumes snapshots for a watched instrument always arrive in non-decreasing `ts_event` order with no duplicates -- an out-of-order or duplicate snapshot (plausible around a Redis reconnect) can wrongly reset the current-bucket buffer mid-bucket or fold into the wrong bar. evidence: `troll/data_api/live_candles.py:_apply_to_buffer` compares only `buffer[-1].ts_event` against the incoming snapshot's bucket, with no monotonicity/dedup check. Likelihood is low in practice (Redis pub/sub preserves per-channel publish order from the collector, the sole publisher), so not fixed in this pass; independently surfaced by both Blind Hunter and Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/views/live_candles.py:229 and :409 skip rows with ts_event <= last buffered ts_event (monotonic + dedup guard)

### DW-109: Neither `_CandleSubscriptions` (per-connection) nor `LiveCandleBus` (process-wide) caps how many distinct `(instrument_id, bar_seconds)` pairs can be …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-5-live-candle-edge.md (2026-09-16)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-15-5-live-candle-edge.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-5-live-candle-edge.md` summary: Neither `_CandleSubscriptions` (per-connection) nor `LiveCandleBus` (process-wide) caps how many distinct `(instrument_id, bar_seconds)` pairs can be subscribed to at once -- a buggy or malicious client could spam `subscribe` messages with many `bar_seconds` values, growing unbounded server-side buffers/listener sets/forwarder tasks with no ceiling. evidence: `troll/data_api/live_candles.py`'s `LiveCandleBus.subscribe`/`troll/data_api/ws/live.py`'s `_CandleSubscriptions.subscribe`, no limit check in either. Low real-world risk for this single-operator internal tool (per spec-15-5's own Design Notes), so not fixed in this pass; surfaced by Blind Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/data_api/ws/live.py:56,119 _MAX_SUBSCRIPTIONS=32 and :80 bar_seconds bound

### DW-110: The new live-candle relay path introduces several unbounded `asyncio.Queue()` instances (per-listener queues in `LiveCandleBus`, each `_forward()`'s source …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-5-live-candle-edge.md (2026-09-16)"), 2026-10-05
location: ws/live.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-5-live-candle-edge.md` summary: The new live-candle relay path introduces several unbounded `asyncio.Queue()` instances (per-listener queues in `LiveCandleBus`, each `_forward()`'s source queue, `ws/live.py`'s per-connection `outbox`) with no maxsize/drop policy -- the same unbounded-queue-growth shape CLAUDE.md cites as the root cause of a prior live-trading OOM incident, though in a much lower-throughput context here (~1 msg/sec/instrument, single-operator tool). evidence: `troll/data_api/live_candles.py`/`troll/data_api/ws/live.py`, all new `asyncio.Queue()` calls take no `maxsize`. Mirrors the pre-existing, already-accepted `RankingsBus` pattern in `troll/data_api/redis_bus.py`, so not a new risk profile introduced uniquely by this story; not fixed in this pass. Surfaced by Blind Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/views/live_candles.py:263 and platform/data_api/ws/live.py:182 queues bounded by QUEUE_MAX

### DW-111: A client subscribing with a non-canonical numeric `bar_seconds` string (e.g. `"060"` vs `"60"`) registers a second, duplicate `LiveCandleBus` listener for the …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-5-live-candle-edge.md (2026-09-16)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-15-5-live-candle-edge.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-5-live-candle-edge.md` summary: A client subscribing with a non-canonical numeric `bar_seconds` string (e.g. `"060"` vs `"60"`) registers a second, duplicate `LiveCandleBus` listener for the same underlying `(iid, 60)` pair, since `_CandleSubscriptions._entries` is keyed by the raw channel string rather than the normalized `(iid, bar_seconds)` tuple -- causing doubled bar-update messages to that client. evidence: `troll/data_api/ws/live.py:_CandleSubscriptions.subscribe`, `self._entries[channel] = ...` keyed by the literal channel string passed in. Requires an unusual client; not fixed in this pass. Surfaced by Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/data_api/ws/live.py:76-82,149 parse via int() and rebuild canonical channel before keying _entries

### DW-112: `useLiveCandle.ts`'s `isLiveCandleMessage` type guard checks only that `channel` is a string and `bar` is an object, never that `bar.t/o/h/l/c` are actually …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-5-live-candle-edge.md (2026-09-16)"), 2026-10-05
location: useLiveCandle.ts
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-5-live-candle-edge.md` summary: `useLiveCandle.ts`'s `isLiveCandleMessage` type guard checks only that `channel` is a string and `bar` is an object, never that `bar.t/o/h/l/c` are actually numeric -- a malformed bar from a future backend change would pass the guard and produce a `NaN` datum handed to the chart series. evidence: `troll/frontend/src/hooks/useLiveCandle.ts:isLiveCandleMessage`. Low risk today since the backend is same-repo/own-invention, not external input; not fixed in this pass. Surfaced by Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/hooks/useLiveCandle.ts:31 guard checks Number.isFinite on t/o/h/l/c

### DW-113: `ml_signals/tests/test_dashboard_chart.py::test_microfeatures_json_decimates_and_reports_true_pre_decimation_count` fails on `develop`/this branch independent …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-5-live-candle-edge.md (2026-09-16)"), 2026-10-05
location: ml_signals/tests/test_dashboard_chart.py::test_microfeatures_json_decimates_and_reports_true_pre_decimation_count
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-5-live-candle-edge.md` summary: `ml_signals/tests/test_dashboard_chart.py::test_microfeatures_json_decimates_and_reports_true_pre_decimation_count` fails on `develop`/this branch independent of this story's changes (`assert 2500 <= 2000`) -- pre-existing, unrelated to `live_candles`/`ws/live` code. evidence: Reproduced with this story's diff fully reverted via `git stash` -- failure is identical and present either way. Root cause not investigated (out of this story's scope); surfaced incidentally while running this story's full-regression verification command.
status: done 2026-10-05
resolution: already resolved: 989aca2fb0 deleted ml_signals/tests/test_dashboard_chart.py along with dashboard.py

### DW-114: `chart_indicators.toml` now has two independent, unlocked writers (`dashboard`'s aiohttp handler and the new `data_api` FastAPI route), both doing an unguarded …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16)"), 2026-10-05
location: chart_indicators.toml
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: `chart_indicators.toml` now has two independent, unlocked writers (`dashboard`'s aiohttp handler and the new `data_api` FastAPI route), both doing an unguarded load-modify-save full-file rewrite -- a save from one racing a save from the other can silently drop the losing write with no error to either caller. This is a new characteristic introduced by this story (previously exactly one process wrote the file), not a pre-existing one. evidence: `troll/ml_signals/dashboard.py`'s `save_coin_indicator_config_handler` and `troll/data_api/routes/indicators.py`'s `put_coin_indicator_config` both do `load_config` -> mutate -> `save_config` with no file lock between the two processes. Low real-world likelihood for a single-operator internal tool (a deliberate, infrequent Save-button action, not a high-frequency write path); the dual-writer situation is itself transitional and will disappear once Story 15.10 retires `dashboard.py`. Surfaced independently by both Blind Hunter and Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: 989aca2fb0 retired the dashboard writer; remaining writes serialized by PREFERENCES_LOCK (data_api/routes/indicators.py:75) and atomic _write_atomic

### DW-115: The catalog's `panel` classification (`overlay`/`oscillator`/`histogram`) reaches the frontend via `GET /api/indicators/catalog` but is never used when …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16)"), 2026-10-05
location: GET /api/indicators/catalog
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: The catalog's `panel` classification (`overlay`/`oscillator`/`histogram`) reaches the frontend via `GET /api/indicators/catalog` but is never used when building panes -- every picker-added indicator becomes its own detached `Line` sub-pane, so an "overlay" indicator (SMA/EMA/Bollinger Bands), whose whole purpose is being drawn on the same price axis, instead renders in an unrelated-scale sub-pane. evidence: `troll/frontend/src/pages/ChartPage.tsx`'s `panes` `useMemo` ignores `IndicatorCatalogEntry.panel` entirely. Fixing this correctly requires extending `LightweightChart.tsx` (previously a stable, already-reviewed component from Stories 15.4/15.5) with a new "attach to the main pane" placement mode -- real scope beyond a same-pass patch. Surfaced by Blind Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/pages/ChartPage.tsx:109-111,467 panelForKey/placement overlay uses catalog panel

### DW-116: `GET /api/indicators/catalog`'s catalog-collision `ValueError` (`_merged_indicator_catalog`) and `PUT .../indicators`'s `save_config` …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16)"), 2026-10-05
location: GET /api/indicators/catalog
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: `GET /api/indicators/catalog`'s catalog-collision `ValueError` (`_merged_indicator_catalog`) and `PUT .../indicators`'s `save_config` `OSError`/`PermissionError` are both uncaught in the new route module, surfacing as a generic 500 rather than a clear error. evidence: `troll/data_api/routes/indicators.py`'s `get_indicators_catalog`/`put_coin_indicator_config`. Verified pre-existing, not a regression: the retired aiohttp handlers this story relocates verbatim (`ml_signals/dashboard.py`'s `indicators_catalog_handler`/`save_coin_indicator_config_handler`) never caught these either, and AD-F1 required verbatim relocation. Surfaced by Edge Case Hunter review of this story's diff.
status: open

### DW-117: `IndicatorEntryRow`'s free-text param `<input>` fields have several silent-coercion edge cases: an emptied numeric field becomes `0` (not rejected), a …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: `IndicatorEntryRow`'s free-text param `<input>` fields have several silent-coercion edge cases: an emptied numeric field becomes `0` (not rejected), a non-canonical boolean string like `"1"`/`"True"` becomes `false`, and an array/object-typed param is silently stringified on Apply -- and there is no dropdown constraining an enum-typed param (e.g. `price_type`) to its actual valid names, so a typo only surfaces as a 400 from the values route with no client-side hint. evidence: `troll/frontend/src/components/chart/IndicatorPicker.tsx`'s `coerceParamValue` and `IndicatorEntryRow`. Low severity for a single-operator internal tool (self-inflicted, recoverable by re-editing); fixing all cases properly needs per-type input widgets, not a one-line patch. Surfaced by Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/components/chart/paramCoercion.ts isValidParamText/invalidParamKeys plus ParamInputs.tsx choices dropdown

### DW-118: The indicator-values route's dispatch (`_replay_entry`) passes request params straight to `replay_indicator` with no `_coerce_indicator_params`-style type …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16)"), 2026-10-05
location: dashboard.py._indicators_json
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: The indicator-values route's dispatch (`_replay_entry`) passes request params straight to `replay_indicator` with no `_coerce_indicator_params`-style type coercion, unlike the retired `dashboard.py._indicators_json` it otherwise claims to reuse "unchanged" -- a param sent as a different JSON type than the catalog's declared default no longer gets best-effort-coerced before replay. evidence: `troll/data_api/routes/indicators.py`'s `_replay_entry`/`_values_by_time` vs. `ml_signals/dashboard.py`'s retired `_coerce_indicator_params` call site. Low risk today since the picker (`IndicatorPicker.tsx`'s `coerceParamValue`) is the only current caller and already coerces client-side before sending; a future API consumer sending raw/untyped params would be the first to hit this gap. This pass's broadened `except Exception` around the dispatch (see Review Triage Log) ensures a resulting type error still fails loud as a clean 400, not a 500. Surfaced by Blind Hunter review of this story's diff.
status: open

### DW-119: The picker's "Add" control only supports one configured instance per indicator name (`handleAdd`'s duplicate guard is keyed on `name` alone), even though the …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: The picker's "Add" control only supports one configured instance per indicator name (`handleAdd`'s duplicate guard is keyed on `name` alone), even though the backend's `_indicator_id(name, params)` scheme fully supports two differently-parameterized instances of the same indicator side by side (e.g. RSI(14) and RSI(21)) -- a UI capability gap, not a storage/wire-format limitation. evidence: `troll/frontend/src/components/chart/IndicatorPicker.tsx`'s `handleAdd`. A feature gap, not a bug -- no data corruption, matches the picker's v1 scope. Surfaced by Blind Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/components/chart/IndicatorPicker.tsx:208 hasInstance(..., multiInstance) allows differently-parameterized instances

### DW-120: `PUT /api/coin/{iid}/indicators` deliberately reads the raw request body (`Request.json()`, not a typed Pydantic body parameter) to preserve a `400` instead of …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16)"), 2026-10-05
location: PUT /api/coin/{iid}/indicators
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: `PUT /api/coin/{iid}/indicators` deliberately reads the raw request body (`Request.json()`, not a typed Pydantic body parameter) to preserve a `400` instead of FastAPI's automatic `422` for a malformed payload -- a documented, correct tradeoff, but it means this endpoint's request-body shape never appears in the generated `openapi.json`/`schema.ts`, so `client.ts`'s hand-maintained `saveCoinIndicatorConfig` request type has nothing in the codegen pipeline to catch future drift from the Python handler. evidence: `troll/data_api/routes/indicators.py`'s `put_coin_indicator_config` docstring explains the tradeoff explicitly; confirmed against the live `openapi.json` that no `requestBody` is generated for this operation. Surfaced by Blind Hunter review of this story's diff.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-data-api-input-validation
resolution-undo: 91b47c52cc0253ee68fc7a1fe5a9d3fd9432e6964681548c0f72e9cd0679be68 2026-10-05 7374617475733a206f70656e

### DW-121: `PUT /api/coin/{iid}/indicators` persists any `name`/`category` string without checking it against `_merged_indicator_catalog()` -- a typo'd or stale entry …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16, third pass)"), 2026-10-05
location: PUT /api/coin/{iid}/indicators
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: `PUT /api/coin/{iid}/indicators` persists any `name`/`category` string without checking it against `_merged_indicator_catalog()` -- a typo'd or stale entry saves successfully and only surfaces as a `400` later, at `GET .../indicator-values` time, with no feedback at save time that the entry is bogus. evidence: `troll/data_api/routes/indicators.py`'s `put_coin_indicator_config` builds `IndicatorEntry` objects straight from the payload with no catalog lookup. Confirmed pre-existing, not a regression: the retired `ml_signals/dashboard.py:save_coin_indicator_config_handler` (line ~2035) this story relocates verbatim has the identical gap. Surfaced by Blind Hunter and Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/data_api/routes/indicators.py:210 PUT rejects names not in merged_catalog with 400

### DW-122: `GET /api/coin/{iid}/indicators`'s corrupt-TOML except tuple (`tomllib.TOMLDecodeError, KeyError, TypeError`) does not cover `UnicodeDecodeError`, which …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16, third pass)"), 2026-10-05
location: GET /api/coin/{iid}/indicators
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: `GET /api/coin/{iid}/indicators`'s corrupt-TOML except tuple (`tomllib.TOMLDecodeError, KeyError, TypeError`) does not cover `UnicodeDecodeError`, which `tomllib.load()` raises for a file containing invalid UTF-8 -- that specific corruption mode falls through as an unhandled 500 instead of the intended clean "chart_indicators.toml is corrupt" response. evidence: `troll/data_api/routes/indicators.py`'s `get_coin_indicator_config`. Confirmed pre-existing: `ml_signals/dashboard.py:coin_indicator_config_handler` (line ~2017) uses the identical except tuple, verbatim relocation per AD-F1. Surfaced by Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/data_api/routes/indicators.py:134 except tuple includes UnicodeDecodeError

### DW-123: `PUT /api/coin/{iid}/indicators`'s payload-parsing except tuple (`json.JSONDecodeError, KeyError, TypeError, tomllib.TOMLDecodeError`) does not cover …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16, third pass)"), 2026-10-05
location: PUT /api/coin/{iid}/indicators
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: `PUT /api/coin/{iid}/indicators`'s payload-parsing except tuple (`json.JSONDecodeError, KeyError, TypeError, tomllib.TOMLDecodeError`) does not cover `AttributeError` -- a payload entry that isn't a dict (e.g. a bare string or list) fails on `e.get("params", {})` with an uncaught `AttributeError`, surfacing as an unhandled 500 instead of the documented 400 for a malformed payload. evidence: `troll/data_api/routes/indicators.py`'s `put_coin_indicator_config`. Confirmed pre-existing: `ml_signals/dashboard.py:save_coin_indicator_config_handler` (line ~2035) has the identical except tuple and the identical gap, verbatim relocation per AD-F1. Surfaced by Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/data_api/routes/indicators.py:220 except tuple includes AttributeError

### DW-124: `IndicatorEntryRow`'s Apply flow gives no visual feedback when `coerceParamValue` silently rejects an unparsable keystroke and keeps the prior value -- the …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16, third pass)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: `IndicatorEntryRow`'s Apply flow gives no visual feedback when `coerceParamValue` silently rejects an unparsable keystroke and keeps the prior value -- the input just appears to ignore what was typed, with no inline error text explaining why. evidence: `troll/frontend/src/components/chart/IndicatorPicker.tsx`'s `IndicatorEntryRow` `onChange` handler discards an unparsable edit via `coerceParamValue`'s documented fallback with no user-visible signal. Low severity (self-correcting on the next valid keystroke); overlaps thematically with an already-logged "no client-side hint" coercion entry from an earlier pass, restated here since it's specifically about the missing feedback rather than the coercion logic itself. Surfaced by Blind Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/components/chart/paramCoercion.ts invalidParamKeys shows invalid text, never silently reverts

### DW-125: `chart_indicators.toml` still has two independent, unlocked writers (`dashboard`'s aiohttp handler and `data_api`'s FastAPI route) with an unguarded …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16, third pass)"), 2026-10-05
location: chart_indicators.toml
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: `chart_indicators.toml` still has two independent, unlocked writers (`dashboard`'s aiohttp handler and `data_api`'s FastAPI route) with an unguarded load-modify-save full-file rewrite -- re-confirmed still present and unaddressed as of this pass. evidence: Same as the already-logged entry from the first review pass; re-surfaced independently by both Blind Hunter and Edge Case Hunter in this (third) pass, confirming it remains unresolved.
status: done 2026-10-05
resolution: already resolved: 989aca2fb0 retired the dashboard writer; PREFERENCES_LOCK + _write_atomic in views/preferences.py:251

### DW-126: The catalog's `panel` classification is still fetched but never consumed when building panes -- re-confirmed still present and unaddressed as of this pass.

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16, third pass)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: The catalog's `panel` classification is still fetched but never consumed when building panes -- re-confirmed still present and unaddressed as of this pass. evidence: Same as the already-logged entry from the first review pass (`ChartPage.tsx`'s `panes` `useMemo` still ignores `IndicatorCatalogEntry.panel`); re-surfaced by Blind Hunter in this (third) pass.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/pages/ChartPage.tsx:467 placement overlay by catalog panel

### DW-127: The picker's one-instance-per-name UI limit is still present -- re-confirmed still unaddressed as of this pass.

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16, third pass)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: The picker's one-instance-per-name UI limit is still present -- re-confirmed still unaddressed as of this pass. evidence: Same as the already-logged entry from the first review pass (`IndicatorPicker.tsx`'s `handleAdd` duplicate guard); re-surfaced by Blind Hunter in this (third) pass.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/components/chart/IndicatorPicker.tsx:208 multiInstance guard

### DW-128: A single bad/unknown entry in a batched `GET .../indicator-values` request still fails the entire request rather than just that entry -- re-confirmed still …

origin: migrated from legacy ledger ("Deferred from: code review of spec-15-6-per-coin-indicator-configuration.md (2026-09-16, third pass)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-15-6-per-coin-indicator-configuration.md` summary: A single bad/unknown entry in a batched `GET .../indicator-values` request still fails the entire request rather than just that entry -- re-confirmed still present and unaddressed as of this pass. evidence: Same as the already-logged entry from the second review pass (`indicators.py`'s `get_indicator_values`/`_values_by_time`); re-surfaced by Edge Case Hunter in this (third) pass.
status: done 2026-10-05
resolution: already resolved: platform/data_api/routes/indicators.py:341-346 IndicatorValuesResponse.errors serves other entries when one fails

### DW-129: When a queried page's rows are all crossed-book (filtered to empty) but real older history exists further back, `has_more` is reported `False`, silently …

origin: migrated from legacy ledger ("Deferred from: code review of 15-7-lines-mode (2026-09-16)"), 2026-10-05
location: _bmad-output/implementation-artifacts/15-7-lines-mode.md
reason: source_spec: `_bmad-output/implementation-artifacts/15-7-lines-mode.md` summary: When a queried page's rows are all crossed-book (filtered to empty) but real older history exists further back, `has_more` is reported `False`, silently truncating scroll-back pagination instead of probing further back. evidence: `troll/data_api/routes/snapshots.py:get_snapshots`'s `if not kept: return SnapshotSeriesResponse(items=[], has_more=False)`. Pre-existing, identical behavior in the sibling route this story was instructed to mirror exactly: `troll/data_api/routes/candles.py:get_candles`'s own `if not kept: return CandlesResponse(items=[], has_more=False)` (Story 15.3). Not introduced by this story -- fixing it here alone would diverge from the reused design rather than fix the shared root cause. Surfaced by Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/views/chart_series.py:477 reader no longer filters crossed seconds; snapshots route returns snapshot_series_page has_more directly

### DW-130: `data_api/app.py`'s own `METRICS_DB_PATH` default comment ("mirrors dashboard.py:85-86 exactly") cites the wrong line numbers -- `dashboard.py`'s actual …

origin: migrated from legacy ledger ("Deferred from: code review of 17-2-unpark-and-complete-story-15-8-31-day-metrics-history (2026-09-17)"), 2026-10-05
location: data_api/app.py
reason: source_spec: `_bmad-output/implementation-artifacts/17-2-unpark-and-complete-story-15-8-31-day-metrics-history.md` summary: `data_api/app.py`'s own `METRICS_DB_PATH` default comment ("mirrors dashboard.py:85-86 exactly") cites the wrong line numbers -- `dashboard.py`'s actual `METRICS_DB_PATH` assignment is at lines 96-98 -- and this story's new `data_api/routes/metrics.py` copied the same stale citation verbatim into its own comment. evidence: Confirmed by grepping `ml_signals/dashboard.py` for `METRICS_DB_PATH`, which resolves to lines 96-98, not 85-86. The inaccurate citation predates this story (already wrong in `app.py` before this diff); this story's own file only inherited it. Surfaced by Blind Hunter review of this story's diff.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-data-api-legacy-routes
resolution-undo: 0494e1d5631fbe7841cf0d7cfefba53aa18067a35474cd982d05d4012b815cd0 2026-10-05 7374617475733a206f70656e

### DW-131: `HistoryPage.tsx`'s `toMetricDatum` treats only `null`/`undefined` as a gap; a `NaN`/`Infinity` metric value (were one ever to reach the route from an upstream …

origin: migrated from legacy ledger ("Deferred from: code review of 17-2-unpark-and-complete-story-15-8-31-day-metrics-history (2026-09-17)"), 2026-10-05
location: HistoryPage.tsx
reason: source_spec: `_bmad-output/implementation-artifacts/17-2-unpark-and-complete-story-15-8-31-day-metrics-history.md` summary: `HistoryPage.tsx`'s `toMetricDatum` treats only `null`/`undefined` as a gap; a `NaN`/`Infinity` metric value (were one ever to reach the route from an upstream computation bug) would be passed to `lightweight-charts` as real plotted data rather than being treated as a gap. evidence: `toMetricDatum(tsNs, value)`'s `value == null ? { time } : { time, value }` check has no `Number.isFinite` guard. No evidence this actually occurs -- `metrics_store` columns are written from `ranking_engine`'s own float computations, which are not known to ever emit `NaN`/`Infinity` -- so this is a defensive hardening gap, not an observed bug. Surfaced by Edge Case Hunter review of this story's diff.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/pages/HistoryPage.tsx:39 !Number.isFinite(value) treated as gap

### DW-132: screener_columns.toml is written non-atomically (docker single-file bind mount cannot be renamed over); a concurrent GET can see a truncated file.

origin: migrated from legacy ledger ("Deferred from: code review of epic 17 (2026-09-19)"), 2026-10-05
location: n/a
reason: screener_columns.toml is written non-atomically (docker single-file bind mount cannot be renamed over); a concurrent GET can see a truncated file.
status: done 2026-10-05
resolution: already resolved: platform/views/preferences.py:251 _write_atomic (temp+fsync+os.replace) used by save_screener_columns; directory mount since 32.5

### DW-133: PUT technicals-columns validates param type only, not values (e.g. period 0); the bad config makes GET technicals-values 400 until fixed in the picker.

origin: migrated from legacy ledger ("Deferred from: code review of epic 17 (2026-09-19)"), 2026-10-05
location: n/a
reason: PUT technicals-columns validates param type only, not values (e.g. period 0); the bad config makes GET technicals-values 400 until fixed in the picker.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-data-api-input-validation
resolution-undo: 91b47c52cc0253ee68fc7a1fe5a9d3fd9432e6964681548c0f72e9cd0679be68 2026-10-05 7374617475733a206f70656e

### DW-134: technicals-values cache is single-key with no single-flight; 60s client poll > 30s TTL means little hit-rate.

origin: migrated from legacy ledger ("Deferred from: code review of epic 17 (2026-09-19)"), 2026-10-05
location: n/a
reason: technicals-values cache is single-key with no single-flight; 60s client poll > 30s TTL means little hit-rate.
status: open

### DW-135: `=` filter operator on float values is strict equality; tech filters hide all rows while values are loading/errored on the Performance tab.

origin: migrated from legacy ledger ("Deferred from: code review of epic 17 (2026-09-19)"), 2026-10-05
location: =
reason: `=` filter operator on float values is strict equality; tech filters hide all rows while values are loading/errored on the Performance tab.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-screener-filter-equality
resolution-undo: 90ba95829001d719ed6b8bfa449998ead31f76789d534b0bd14e5f0058919a08 2026-10-05 7374617475733a206f70656e

### DW-136: 16.x minute_rollup partial_start does not cover gap-created partial minutes (outside epic 17).

origin: migrated from legacy ledger ("Deferred from: code review of epic 17 (2026-09-19)"), 2026-10-05
location: n/a
reason: 16.x minute_rollup partial_start does not cover gap-created partial minutes (outside epic 17).
status: open

### DW-137: Alert horizontal-line conditions are resolved to a static price at creation; later line drags don't move the alert.

origin: migrated from legacy ledger ("Deferred from: code review of epic-20 (2026-09-19)"), 2026-10-05
location: n/a
reason: Alert horizontal-line conditions are resolved to a static price at creation; later line drags don't move the alert.
status: done 2026-10-05
resolution: closed by human decision: Alerts on horizontal lines are by design a static price snapshot; documented in alert.py
decision: 2026-10-05 Accept static price (current documented design) — Alerts on horizontal lines are by design a static price snapshot; documented in alert.py

### DW-138: `AlertStore` in-place TOML rewrite: a crash mid-write corrupts the file (fails loudly on load).

origin: migrated from legacy ledger ("Deferred from: code review of epic-20 (2026-09-19)"), 2026-10-05
location: AlertStore
reason: `AlertStore` in-place TOML rewrite: a crash mid-write corrupts the file (fails loudly on load).
status: done 2026-10-05
resolution: closed by human decision: Accepted: in-place rewrite of a tiny file; corrupt file raises on load rather than silently resetting
decision: 2026-10-05 Accept: crash mid-write fails loudly on load — Accepted: in-place rewrite of a tiny file; corrupt file raises on load rather than silently resetting

### DW-139: Alert run state is in-memory: first tick after a data_api restart can't fire; once_per_bar may re-fire within the same bar.

origin: migrated from legacy ledger ("Deferred from: code review of epic-20 (2026-09-19)"), 2026-10-05
location: n/a
reason: Alert run state is in-memory: first tick after a data_api restart can't fire; once_per_bar may re-fire within the same bar.
status: open

### DW-140: Trendline anchor with no bar in the active mode's data (click right of last bar; 1s Lines-mode anchor viewed in Candles mode) has no `timeToCoordinate`, so the …

origin: migrated from legacy ledger ("Deferred from: code review of story-18.2 (2026-09-19)"), 2026-10-05
location: timeToCoordinate
reason: Trendline anchor with no bar in the active mode's data (click right of last bar; 1s Lines-mode anchor viewed in Candles mode) has no `timeToCoordinate`, so the line silently isn't drawn. Needs snapping or extrapolation.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/lib/drawings.ts:275 snapIndex + TrendlinePrimitive.ts:105 draw anchor on the latest bar at or before it

### DW-141: Trendline color is resolved once via `cssVar` at creation; won't follow theme changes.

origin: migrated from legacy ledger ("Deferred from: code review of story-18.2 (2026-09-19)"), 2026-10-05
location: cssVar
reason: Trendline color is resolved once via `cssVar` at creation; won't follow theme changes.
status: open

### DW-142: No rubber-band preview between first and second click (candidate for 18.10).

origin: migrated from legacy ledger ("Deferred from: code review of story-18.2 (2026-09-19)"), 2026-10-05
location: n/a
reason: No rubber-band preview between first and second click (candidate for 18.10).
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/components/chart/LightweightChart.tsx:1677 trendline preview line follows cursor (pendingAnchor)

### DW-143: Measurement drag past the last bar freezes at the last valid point (no coordinate->time there); same root cause as 18.2's empty-margin item.

origin: migrated from legacy ledger ("Deferred from: code review of story-18.3 (2026-09-19)"), 2026-10-05
location: n/a
reason: Measurement drag past the last bar freezes at the last valid point (no coordinate->time there); same root cause as 18.2's empty-margin item.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-chart-edge-coordinates
resolution-undo: 988e15f4b5ef3bc15b0c3918fdddb8822929edd35fe148958cc5976427cb70dd 2026-10-05 7374617475733a206f70656e

### DW-144: Measure mousedown on price/time axis strips starts a measurement; mouse events only (no touch); label unclamped at pane edges; O(n) `computeMeasurement` per …

origin: migrated from legacy ledger ("Deferred from: code review of story-18.3 (2026-09-19)"), 2026-10-05
location: computeMeasurement
reason: Measure mousedown on price/time axis strips starts a measurement; mouse events only (no touch); label unclamped at pane edges; O(n) `computeMeasurement` per mousemove; forming live bar not in the label counts.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-chart-edge-coordinates
resolution-undo: 988e15f4b5ef3bc15b0c3918fdddb8822929edd35fe148958cc5976427cb70dd 2026-10-05 7374617475733a206f70656e

### DW-145: Replay: no follow-scroll -- revealed bars may end up off-screen right after `setData`; verify in a real browser and add `scrollToPosition`/`scrollToRealTime` …

origin: migrated from legacy ledger ("Deferred from: code review of story-18.4 (2026-09-19)"), 2026-10-05
location: setData
reason: Replay: no follow-scroll -- revealed bars may end up off-screen right after `setData`; verify in a real browser and add `scrollToPosition`/`scrollToRealTime` if so.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-chart-replay-polish
resolution-undo: d46e45e5a8206fc33cb2bd6cecda4ae20ede7cce2c87830feddf1aecf8a9bcff 2026-10-05 7374617475733a206f70656e

### DW-146: Replay polish: silent no-op when picking a gap; Play at newest bar does nothing visibly; Step back can pass the start marker; play interval restarts on any …

origin: migrated from legacy ledger ("Deferred from: code review of story-18.4 (2026-09-19)"), 2026-10-05
location: candles
reason: Replay polish: silent no-op when picking a gap; Play at newest bar does nothing visibly; Step back can pass the start marker; play interval restarts on any `candles` identity change; marker color resolved at construction.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-chart-replay-polish
resolution-undo: d46e45e5a8206fc33cb2bd6cecda4ae20ede7cce2c87830feddf1aecf8a9bcff 2026-10-05 7374617475733a206f70656e

### DW-147: `GET /api/candles` `has_more` probes only one query window back, so a collector outage longer than that window (limit*bar_seconds*3, capped at 7 days -- ~25h …

origin: migrated from legacy ledger ("Deferred from: story 18.5 backend verification (2026-09-19)"), 2026-10-05
location: GET /api/candles
reason: `GET /api/candles` `has_more` probes only one query window back, so a collector outage longer than that window (limit*bar_seconds*3, capped at 7 days -- ~25h at 1-minute bars) makes pagination report exhaustion while older data exists. Affects how far back FRVP scroll-back can reach across outages. Pre-existing; not truncation of a requested range.
status: done 2026-10-05
resolution: already resolved: platform/views/chart_series.py:883 has_more = has_older_data(catalog file ranges), no fixed probe window; candle_page rewritten (31.8)

### DW-148: `VolumeProfilePrimitive.xAnchor` is a pixel x; range-pinned profiles (FRVP, Session) need a time-based anchor so pan/zoom doesn't strand them -- handle when …

origin: migrated from legacy ledger ("Deferred from: code review of story-18.5 (2026-09-19)"), 2026-10-05
location: VolumeProfilePrimitive.xAnchor
reason: `VolumeProfilePrimitive.xAnchor` is a pixel x; range-pinned profiles (FRVP, Session) need a time-based anchor so pan/zoom doesn't strand them -- handle when 18.6 places the first fixed profile.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/components/chart/primitives/VolumeProfilePrimitive.ts:25 xAnchor accepts { time: Time }

### DW-149: POC color hardcoded (`#ffff55`), Value Area band reuses `upColor`; rows with null y-spans can bridge a Value Area gap; row gaps not bitmap-pixel-snapped.

origin: migrated from legacy ledger ("Deferred from: code review of story-18.5 (2026-09-19)"), 2026-10-05
location: #ffff55
reason: POC color hardcoded (`#ffff55`), Value Area band reuses `upColor`; rows with null y-spans can bridge a Value Area gap; row gaps not bitmap-pixel-snapped.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-volume-profile-polish
resolution-undo: 338049af4fa3f4cf58d1fa9adcb5d39eb86748330420f70c6444135f7b50f1d1 2026-10-05 7374617475733a206f70656e

### DW-150: FRVP: dragging past the last bar has no coordinate->time so the endpoint is dropped/stale (same root as 18.2/18.3); edge ghost not cancelled if the edge effect …

origin: migrated from legacy ledger ("Deferred from: code review of story-18.6 (2026-09-19)"), 2026-10-05
location: n/a
reason: FRVP: dragging past the last bar has no coordinate->time so the endpoint is dropped/stale (same root as 18.2/18.3); edge ghost not cancelled if the edge effect is torn down mid-drag; no hover cursor on edges; shared settings panel only appears once a profile is placed; removal is a text button outside the chart rather than an in-chart x.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-chart-edge-coordinates
resolution-undo: 988e15f4b5ef3bc15b0c3918fdddb8822929edd35fe148958cc5976427cb70dd 2026-10-05 7374617475733a206f70656e

### DW-151: VRVP recomputes on every pan frame (no rAF throttle or range quantization); a visible range extending past the loaded candles is clamped to the loaded part …

origin: migrated from legacy ledger ("Deferred from: code review of story-18.7 (2026-09-19)"), 2026-10-05
location: n/a
reason: VRVP recomputes on every pan frame (no rAF throttle or range quantization); a visible range extending past the loaded candles is clamped to the loaded part without a cue; fixed 150px width is not clamped to narrow panes; VRVP settings are not persisted across remounts.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-volume-profile-polish
resolution-undo: 338049af4fa3f4cf58d1fa9adcb5d39eb86748330420f70c6444135f7b50f1d1 2026-10-05 7374617475733a206f70656e

### DW-152: Session profiles: `respondsToZoom` is a draw-time gap tweak only; partly loaded sessions draw narrow (0.7 x loaded span; zero width for a single bar) …

origin: migrated from legacy ledger ("Deferred from: code review of story-18.8 (2026-09-19)"), 2026-10-05
location: respondsToZoom
reason: Session profiles: `respondsToZoom` is a draw-time gap tweak only; partly loaded sessions draw narrow (0.7 x loaded span; zero width for a single bar); `sinceSeconds` is fixed when the profile is added (no UTC-midnight re-anchor); every Sessions edit re-pages history from now; replay far in the past has no session history before the fetch window; an empty server page across a long outage stops paging early (see 18.5's `has_more` ceiling).
status: done 2026-10-05
resolution: resolved by sweep bundle dw-volume-profile-polish
resolution-undo: 338049af4fa3f4cf58d1fa9adcb5d39eb86748330420f70c6444135f7b50f1d1 2026-10-05 7374617475733a206f70656e

### DW-153: Session/periodic profiles: every period/count change re-pages from now (no debounce or abort of in-flight pages, no loading indicator); `sinceSeconds` is not …

origin: migrated from legacy ledger ("Deferred from: code review of story-18.9 (2026-09-19)"), 2026-10-05
location: sinceSeconds
reason: Session/periodic profiles: every period/count change re-pages from now (no debounce or abort of in-flight pages, no loading indicator); `sinceSeconds` is not re-anchored at a period rollover; the PVP period choice is not persisted; `SESSION_PRESETS.period` is a fixed period for SVP but only the dropdown default for PVP; dropdown shows raw values.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-volume-profile-polish
resolution-undo: 338049af4fa3f4cf58d1fa9adcb5d39eb86748330420f70c6444135f7b50f1d1 2026-10-05 7374617475733a206f70656e

### DW-154: Live-app §A8.2 walkthrough owed for all of Epic 18

origin: migrated from legacy ledger ("Deferred from: story 18.10 parity audit (2026-09-19)"), 2026-10-05
location: n/a
reason: **Live-app §A8.2 walkthrough owed for all of Epic 18** (18.1-18.10 are UI verified only in jsdom with a mocked lightweight-charts): pan/zoom, fit/latest, pane resize hit zone, every drawing tool's real mouse mechanics, replay, all volume-profile variants' actual drawing (right-axis anchoring, time-anchored session/FRVP widths, edge grab, respondsToZoom).
status: done 2026-10-05
resolution: closed by human decision: Moved to DEPLOY_CHECKLIST deferred operator actions Operator: the §A8.2 live walkthrough is added to DEPLOY_CHECKLIST's deferred operator actions, to run after the Epic 33 chart stories.
decision: 2026-10-05 Close; fold into DEPLOY_CHECKLIST deferred operator actions (OPS-01) — Moved to DEPLOY_CHECKLIST deferred operator actions Operator: the §A8.2 live walkthrough is added to DEPLOY_CHECKLIST's deferred operator actions, to run after the Epic 33 chart stories.

### DW-155: Crosshair readout status bar (O/H/L/C/time on hover) does not exist.

origin: migrated from legacy ledger ("Deferred from: story 18.10 parity audit (2026-09-19)"), 2026-10-05
location: n/a
reason: Crosshair readout status bar (O/H/L/C/time on hover) does not exist.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/components/chart/legend.ts:10 legend is the chart's crosshair readout (Story 32.3)

### DW-156: Indicator legend (gear / eye / x per indicator, top-left of its pane) does not exist; indicators are managed in the picker list (dropdown + Add, inline params …

origin: migrated from legacy ledger ("Deferred from: story 18.10 parity audit (2026-09-19)"), 2026-10-05
location: n/a
reason: Indicator legend (gear / eye / x per indicator, top-left of its pane) does not exist; indicators are managed in the picker list (dropdown + Add, inline params + Apply, Remove), and there is no visibility toggle.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/components/chart/legend.ts + IndicatorPicker.tsx:10 legend eye/gear/x per indicator (Story 32.3)

### DW-157: Trendline placement is two-click, not click-drag.

origin: migrated from legacy ledger ("Deferred from: story 18.10 parity audit (2026-09-19)"), 2026-10-05
location: n/a
reason: Trendline placement is two-click, not click-drag.
status: open
decision: 2026-10-05 Build click-drag placement via rangeDrag — Trendline placed by press-drag-release like the other range tools, keeping two-click as fallback

### DW-158: Alerts: Epic 20.

origin: migrated from legacy ledger ("Deferred from: story 18.10 parity audit (2026-09-19)"), 2026-10-05
location: n/a
reason: Alerts: Epic 20.
status: done 2026-10-05
resolution: already resolved: _bmad-output/implementation-artifacts/sprint-status.yaml:266-268 stories 20-1..20-3 done (alerts)

### DW-159: `_publish_snapshot_batch`'s Redis publish failure is a bare `logger.warning` and swallowed, so a permanently down Redis is invisible in `/api/errors` (DATA-07) …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: /api/errors
reason: source_spec: `_bmad-output/implementation-artifacts/22-1-troll-collector-core-extracted-from-the-bybit-and-hyperliquid-collectors.md` summary: `_publish_snapshot_batch`'s Redis publish failure is a bare `logger.warning` and swallowed, so a permanently down Redis is invisible in `/api/errors` (DATA-07) — now from three collector processes, not one. evidence: Pre-existing dYdX design ("missing one tick is acceptable") copied verbatim into `collector_core` as the story specified; the WARNING fires once per second per venue with no ledger count. Decide once for all collectors (ledger with rate limiting, or a `collector:status` heartbeat) when 22.2 moves dYdX onto the core.
status: done 2026-10-05
resolution: already resolved: platform/capture/application/capture_service.py:47,1552 failed live publish ledgered as collector.snapshot_publish

### DW-160: The stale-trade age filter compares venue `ts_event` to the host wall clock, so a host clock more than `stale_trade_seconds` fast drops every live trade on …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/22-1-troll-collector-core-extracted-from-the-bybit-and-hyperliquid-collectors.md
reason: source_spec: `_bmad-output/implementation-artifacts/22-1-troll-collector-core-extracted-from-the-bybit-and-hyperliquid-collectors.md` summary: The stale-trade age filter compares venue `ts_event` to the host wall clock, so a host clock more than `stale_trade_seconds` fast drops every live trade on every venue, and `_report_stale_trades` reports it at INFO under the reassuring label "subscribe-time trade history". evidence: Inherited from `dydx_collector` (D-01's filter), now venue-wide. A persistent non-zero count after the subscribe window is indistinguishable from Hyperliquid's real subscribe replay (D-36: 21/19 trades). Fix candidate: escalate to `error_ledger` when drops continue past the first flush after subscribe, or compare against the venue's own message time.
status: done 2026-10-05
resolution: already resolved: platform/capture/domain/trade_intake.py:26 staleness now ts_init - ts_event (arrival), not host wall clock; ledgered collector.stale_trade (sites.py:29)

### DW-161: `DydxCollector._resync_book` calls `_clear_book_state` after the awaited resubscribe, so a snapshot ingested during the awaits can be wiped; it also bypasses …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/22-2-dydx-collector-onto-the-core.md
reason: source_spec: `_bmad-output/implementation-artifacts/22-2-dydx-collector-onto-the-core.md` summary: `DydxCollector._resync_book` calls `_clear_book_state` after the awaited resubscribe, so a snapshot ingested during the awaits can be wiped; it also bypasses the core's `_resync` retry (`_resync_pending`) on failure. evidence: ported verbatim from the pre-22.2 `Collector._resync_book`; the core's `_resync` clears first and retries, so dYdX could simply use it.
status: done 2026-10-05
resolution: already resolved: platform/capture/application/capture_service.py:696-723 _resync clears book first then resubscribes with retry; dYdX uses the shared service

### DW-162: dYdX `_apply_deltas` has no snapshot-first guard (core's does), so incremental deltas after a resync can build a shallow book.

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/22-2-dydx-collector-onto-the-core.md
reason: source_spec: `_bmad-output/implementation-artifacts/22-2-dydx-collector-onto-the-core.md` summary: dYdX `_apply_deltas` has no snapshot-first guard (core's does), so incremental deltas after a resync can build a shallow book. evidence: pre-existing dYdX behaviour, deliberately preserved for the oracle tests; revisit once dYdX's resubscribe is confirmed to always emit Clear + snapshot.
status: done 2026-10-05
resolution: already resolved: platform/capture/domain/live_book.py:20-21,148 deltas before a snapshot baseline are dropped and counted

### DW-163: `troll/Makefile`'s `redeploy-all` (`:96`) and `build-insecure` (`:132`) name their services one by one and neither lists `bybit_collector` or …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: troll/Makefile
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-8-spine-rules-and-docs-updated-for-the-multi-venue-core.md` summary: `troll/Makefile`'s `redeploy-all` (`:96`) and `build-insecure` (`:132`) name their services one by one and neither lists `bybit_collector` or `hyperliquid_collector`, so the two newer collectors are silently never rebuilt/restarted by the redeploy workflow. evidence: `redeploy-all` runs `$(COMPOSE) up -d --build collector ranking_engine data_api` plus the live-paper/tui profiles; `docker-compose.yml` defines `bybit_collector` (`:63`) and `hyperliquid_collector` (`:87`) as ordinary non-profiled services. Code change (Makefile), out of scope for this docs-only story; story 22.8 records the trap in `troll/CLAUDE.md`'s "Adding a venue" step 6 but cannot fix it.
status: done 2026-10-05
resolution: already resolved: platform/Makefile:124 redeploy-all builds bybit_collector hyperliquid_collector (and :478)

### DW-164: `ml_signals.error_ledger` state is per-process and `GET /api/errors` returns only `data_api`'s own counters, so collector-side ledger sites …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: GET /api/errors
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-8-spine-rules-and-docs-updated-for-the-multi-venue-core.md` summary: `ml_signals.error_ledger` state is per-process and `GET /api/errors` returns only `data_api`'s own counters, so collector-side ledger sites (`collector.crossed_book`, `collector.book_sequence`, `collector.flush_write`, `collector.resync`) never reach the frontend `<ErrorBar>` — DATA-07's "visible without reading Dozzle" promise does not hold across container boundaries. evidence: Surfaced by the 22.8 review while checking DATA-07/DATA-08 canary claims. Three collector containers plus `data_api` are separate processes; the ledger has no shared store. Needs a real mechanism (Redis-backed counters, or a `collector:errors` channel), not a doc change — deliberately not asserted either way in the 22.8 rewrite.
status: done 2026-10-05
resolution: already resolved: platform/observability/error_ledger.py:24 durable per-service ledger (23.3); platform/data_api/app.py:169 /api/errors reports per-service summaries

### DW-165: Epic 15 drift left over after 22.8: `ARCHITECTURE-SPINE.md`'s mermaid diagram still has a `DASH[dashboard]` node, and AD-9/AD-10's prose still names …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: ARCHITECTURE-SPINE.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-8-spine-rules-and-docs-updated-for-the-multi-venue-core.md` summary: Epic 15 drift left over after 22.8: `ARCHITECTURE-SPINE.md`'s mermaid diagram still has a `DASH[dashboard]` node, and AD-9/AD-10's prose still names `dashboard` as a live reader; `_bmad-output/project-context.md:20` still describes the "Dashboard stack" as aiohttp + plotly. evidence: `ml_signals/dashboard.py` was deleted by Story 15.10; `data_api` (FastAPI + React SPA) replaced it. Story 22.8 repointed only the lines it was already amending (Design Paradigm, namespace mapping, AD-3's binding, the Redis conventions row, the structural seed, the two Deferred dashboard entries, and `DATABASE_SETUP.md`); a full `dashboard`→`data_api` sweep of AD-9/AD-10 and the diagram is Epic 15 cleanup, not Epic 22 scope.
status: open

### DW-166: `dydx_collector/collector.py:361`'s open-interest poll calls `self._on_data(item)`, routing REST data through `_process_data` and stamping …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: dydx_collector/collector.py:361
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-8-spine-rules-and-docs-updated-for-the-multi-venue-core.md` summary: `dydx_collector/collector.py:361`'s open-interest poll calls `self._on_data(item)`, routing REST data through `_process_data` and stamping `_last_feed_message_ns` — so every poll tick marks the dYdX WS feed alive whether or not the socket is, contradicting `collector_core/collector.py:290-291`'s own written contract that Bybit (`bybit_collector/collector.py:145-148`) obeys. evidence: Verified in this worktree by reading all three sites. Consequence is a misreported staleness reason (`_stale_reason` can say "instrument silent, feed alive" where "feed dead" holds), not bad data — both verdicts skip the sample. DATA-02 applies. Two-line code fix in `dydx_collector`; out of scope for a docs-only story. Recorded in the spine's Deferred section by the 22.8 review pass.
status: done 2026-10-05
resolution: already resolved: platform/capture/application/capture_service.py:2356 REST-polled data goes straight to the buffer, never through _on_data

### DW-167: The empty-top-of-book skip in the write gate (`collector_core/collector.py:627`) drops the second's data with no log line, no `error_ledger` entry and no …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: collector_core/collector.py:627
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-8-spine-rules-and-docs-updated-for-the-multi-venue-core.md` summary: The empty-top-of-book skip in the write gate (`collector_core/collector.py:627`) drops the second's data with no log line, no `error_ledger` entry and no counter, while the adjacent crossed (`:630`) and stale (`:633`) paths both log — so an instrument that never presents both sides produces a permanent, unexplained catalog gap. evidence: Verified by reading `_sample_tick` (`:612-670`). Directly contradicts AD-2's "a `logging.WARNING` line records the full offending payload and the specific reason" and DATA-07's "a bare `logger.warning(...); continue` at a data-dropping site is not acceptable" — here there is not even the warning. Needs a rate-limited warning plus a ledger site; code change, out of scope for a docs-only story.
status: done 2026-10-05
resolution: already resolved: platform/capture/application/capture_service.py:1403-1408 rate-limited warning + collector.empty_top ledger site

### DW-168: `troll/data_api.dockerfile:27-30` copies only `dydx_collector`, `ml_signals`, `ranking_engine` and `data_api`, but `data_api` imports …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: troll/data_api.dockerfile:27-30
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-8-spine-rules-and-docs-updated-for-the-multi-venue-core.md` summary: `troll/data_api.dockerfile:27-30` copies only `dydx_collector`, `ml_signals`, `ranking_engine` and `data_api`, but `data_api` imports `collector_core.second_snapshot` (`app.py:50`, `live_candles.py:43`, `routes/snapshots.py:44`) and `common.venues` (`routes/{snapshots,indicators,indicator_series,candles}.py`), and the compose service mounts no source and sets no `PYTHONPATH`. evidence: Verified against the dockerfile and `troll/docker-compose.yml:143`. Dates from story 22.3, which moved `second_snapshot` into `collector_core`; surfaced now because the 22.8 review re-grounded those exact import citations. Two `COPY` lines fix it — a code change, out of scope for a docs-only story.
status: done 2026-10-05
resolution: already resolved: platform/data_api.dockerfile:27-33 now copies observability, kernel, candles, views, alerting, ranking, data_api

### DW-169: `troll/scripts/capture_hl_ws.py` cannot capture a venue it does not already know — `--venue` is `choices=("hyperliquid", "bybit")` (`:118`) and the subscribe …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: troll/scripts/capture_hl_ws.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-8-spine-rules-and-docs-updated-for-the-multi-venue-core.md` summary: `troll/scripts/capture_hl_ws.py` cannot capture a venue it does not already know — `--venue` is `choices=("hyperliquid", "bybit")` (`:118`) and the subscribe payload is a hard two-way branch (`:41`), so a new venue's `--url` is sent Hyperliquid's subscribe JSON. evidence: Verified by reading the script. `troll/CLAUDE.md`'s "Adding a venue" step 1 makes a raw-frame capture mandatory and DATA-08's rule depends on that evidence, so the harness needs a third branch before a fourth venue can be investigated. Secondary: `_BYBIT_URL` is `/public/linear` only, so it also cannot close the Bybit-spot `u` gap DATA-08 flags as an open DATA-02 question. Story 22.8 documents the limitation in step 1 but cannot fix it (docs-only).
status: done 2026-10-05
resolution: resolved by sweep bundle dw-capture-script-and-docs
resolution-undo: 9077e58b03eea8f8c9d2570018bc9c06100b365969d0b85199174491807bf96f 2026-10-05 7374617475733a206f70656e

### DW-170: `data_api/routes/snapshots.py:129-133` re-implements the gate's empty-top-of-book and crossed-book checks in a reader (`if bp >= ap: continue`), which AD-3 …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: data_api/routes/snapshots.py:129-133
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-8-spine-rules-and-docs-updated-for-the-multi-venue-core.md` summary: `data_api/routes/snapshots.py:129-133` re-implements the gate's empty-top-of-book and crossed-book checks in a reader (`if bp >= ap: continue`), which AD-3 bans outright — the skip was not removed with `dashboard.py`, it was relocated verbatim with `_price_series_rows` by Story 15.10. evidence: Verified by reading `_price_series_rows` and its module docstring (`:30-34`), which states the relocation and names the crossed-book skip as part of it; the pre-deletion original is at `git show 989aca2fb0^:troll/ml_signals/dashboard.py`. A prior Deferred entry in the spine had been struck as "Moot" on the false premise that `data_api` never ported it; the 22.8 review pass un-struck and repointed it. Removing the skip is a code change (and needs AD-3's own gate guarantee re-confirmed against the live catalog first), so out of scope for a docs-only story.
status: done 2026-10-05
resolution: already resolved: platform/data_api/routes/snapshots.py:26 reader-side skips removed (Story 24.2); views/chart_series.py:477 reader never re-validates gate

### DW-171: `ranking_engine` publishes a fabricated `volume24h` of 0 for every Bybit and Hyperliquid instrument, then sorts the default ranking mode on it — a DATA-01 / …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-22-8-spine-rules-and-docs-updated-for-the-multi-venue-core.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-8-spine-rules-and-docs-updated-for-the-multi-venue-core.md` summary: `ranking_engine` publishes a fabricated `volume24h` of 0 for every Bybit and Hyperliquid instrument, then sorts the default ranking mode on it — a DATA-01 / audit-D-27 fabricated-zero, not a missing value. evidence: `ranking_engine/engine.py:180-211` fetches 24 h volume from dYdX's indexer alone and keys it `f"{ticker}-PERP.DYDX"`; `:435` reads it back as `_VOLUME_24H.get(iid, 0.0)` and `:461` sorts descending on that. The same function's `:208-209` comment states the correct policy ("No volume is not zero volume (DATA-01): leave the coin out, loudly") and applies it only to an unparseable dYdX value. Dates from stories 19.3/19.4, which added the two venues without a volume source. Story 22.10 ("rankings show every collected coin across venues") is the natural owner; the 22.8 review recorded the trap in `troll/CLAUDE.md`'s "Adding a venue" step 7 but cannot fix it (docs-only).
status: done 2026-10-05
resolution: already resolved: platform/ranking/infrastructure/volume_bybit.py and volume_hyperliquid.py supply real USD volume; rows without fresh volume left out (ranking/domain/board.py:236)

### DW-172: `troll/frontend/src/pages/docs/kbData.ts:143,195,202` — the knowledge-base page `data_api` serves at `/docs` — still cites the deleted module constant …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: troll/frontend/src/pages/docs/kbData.ts:143,195,202
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-8-spine-rules-and-docs-updated-for-the-multi-venue-core.md` summary: `troll/frontend/src/pages/docs/kbData.ts:143,195,202` — the knowledge-base page `data_api` serves at `/docs` — still cites the deleted module constant `_CROSSED_RESYNC_NS` and `troll/ml_signals/dashboard.py` as live references, and `troll/docs/DATA_DICTIONARY.md` still names `dashboard` in seven places while its own `:309` records that Story 15.10 retired it. evidence: Verified by grep in this worktree. `_CROSSED_RESYNC_NS` became `CoreConfig.crossed_resync_seconds` in story 22.2 and `dashboard.py` was deleted by 15.10. The `.ts` file is frontend source, not a `.md` document of record, so both it and the wider `DATA_DICTIONARY` sweep fall outside a docs-only story whose acceptance criterion is "only `.md` files appear in the diff".
status: done 2026-10-05
resolution: resolved by sweep bundle dw-capture-script-and-docs
resolution-undo: 9077e58b03eea8f8c9d2570018bc9c06100b365969d0b85199174491807bf96f 2026-10-05 7374617475733a206f70656e

### DW-173: `troll/bot_tui/coin_detail_state.py:24`'s docstring states "snapshots:raw carries every currently-published instrument's batch every tick", an invariant that …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: troll/bot_tui/coin_detail_state.py:24
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-8-spine-rules-and-docs-updated-for-the-multi-venue-core.md` summary: `troll/bot_tui/coin_detail_state.py:24`'s docstring states "snapshots:raw carries every currently-published instrument's batch every tick", an invariant that stopped holding when Bybit and Hyperliquid became second and third producers on that channel. evidence: Each tick now delivers three batches, one per venue. The behaviour is safe — `_handle_snapshot_batch` (`:80-95`) scans for the matching `instrument_id` and ignores non-matches — so this is a false written invariant, not a live bug; it is a `.py` file and therefore outside a docs-only story's diff.
status: done 2026-10-05
resolution: already resolved: phrase absent from tree (grep 'every currently-published' finds nothing in platform/); bot_tui docstring gone

### DW-174: `ARCHITECTURE-SPINE.md`'s Stack table claims `plotly 6.9.0` and `pandas 3.0.5` ("re-verified 2026-07-24"); the tree pins `plotly==6.8.0` / `pandas==3.0.4` …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: ARCHITECTURE-SPINE.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-8-spine-rules-and-docs-updated-for-the-multi-venue-core.md` summary: `ARCHITECTURE-SPINE.md`'s Stack table claims `plotly 6.9.0` and `pandas 3.0.5` ("re-verified 2026-07-24"); the tree pins `plotly==6.8.0` / `pandas==3.0.4` (`troll/troll-requirements.txt`) and `pandas>=2.3.3,<3.0.0` (root `pyproject.toml:31`), and no `troll/` module imports plotly at all since Story 15.10. evidence: Verified by grep. Pre-dates Epic 22 and is unrelated to the multi-venue re-scoping, so the 22.8 rewrite left both rows alone; the plotly row is also a dependency with no remaining consumer, which is a separate question from its version being wrong.
status: done 2026-10-05
resolution: already resolved: DDD spine ARCHITECTURE-SPINE.md:400 lists plotly 6.8.0 and :520 documents the pandas split; the cited 07-01 table is superseded

### DW-175: dYdX's `ranking_engine.engine.parse_volume_24h` still reads a market whose `volume24H` field is absent or null as a fabricated 0.0 …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-22-10-rankings-show-every-collected-coin-across-venues-with-an-exchange-filter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-10-rankings-show-every-collected-coin-across-venues-with-an-exchange-filter.md` summary: dYdX's `ranking_engine.engine.parse_volume_24h` still reads a market whose `volume24H` field is absent or null as a fabricated 0.0 (`float(market.get("volume24H") or 0)`), and it accepts `"nan"`/`"inf"`/negative strings. Story 22.10's Bybit/Hyperliquid parsers reject all of these via `_parse_usd_volume`. evidence: `troll/ranking_engine/engine.py` `parse_volume_24h`; `test_parse_volume_24h_missing_field_defaults_to_zero` enshrines the 0. Story 22.10's spec explicitly kept this parser unchanged. D-27 fixed only the unparseable-string case. The fix is to route it through `_parse_usd_volume`, ledger and skip the value, and replace that test. The docs (`DATA_DICTIONARY.md`, `frontend/src/pages/docs/data.ts`, audit D-54) now name this exception.
status: done 2026-10-05
resolution: already resolved: platform/ranking/infrastructure/volume_dydx.py:40 uses parse_usd_volume, unparseable values ledgered and skipped

### DW-176: dYdX collector's `_prune_loop` (`prune_instrument`) deletes catalog files without taking the catalog maintenance flock that consolidate/rebuild/prune use, so …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-22-13-raw-trade-archive-exact-fold-nightly-rebuild-and-kline-reconciliation.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-22-13-raw-trade-archive-exact-fold-nightly-rebuild-and-kline-reconciliation.md` summary: dYdX collector's `_prune_loop` (`prune_instrument`) deletes catalog files without taking the catalog maintenance flock that consolidate/rebuild/prune use, so it can race a running consolidation of the same leaf. evidence: `troll/dydx_collector/collector.py` `_prune_all_instruments`/`_prune_delta_retention` call `prune_instrument` directly on a timer; consolidate_catalog (22.11) holds `.consolidate.lock` only for its own process. Pre-existing since 22.11; an overlap surfaces as a `consolidate.error` (sources kept), not data loss.
status: done 2026-10-05
resolution: already resolved: platform/archive/prune_catalog.py:44 -- the dYdX in-process _prune_loop was deleted (Story 25.1); only archive prune deletes, under the maintenance flock (prune_catalog.py:205); no prune_instrument left (test_one_deleter_one_rewriter.py:162-170)

### DW-177: `observability.incidents.IncidentHandler` debounces on `(incident_type, iid)`, so every unclassified WARNING+ that has no instrument shares the key …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-23-1-observability-context-and-migration-guardrails.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-23-1-observability-context-and-migration-guardrails.md` summary: `observability.incidents.IncidentHandler` debounces on `(incident_type, iid)`, so every unclassified WARNING+ that has no instrument shares the key `("unclassified", None)`. Unrelated warnings within the debounce window (10 s for dYdX) after the first one get no incident report. evidence: the debounce key has been built this way since before Story 23.1, which moved the code unchanged; the handler is now the generic, reusable one. A fix would key unclassified reports on the logger name and message template, or exempt them from debounce.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-observability-ledger-fixes
resolution-undo: f29f38bea3e637c6b62e34f9b17b6105f4546cafc7682de177659092884f975f 2026-10-05 7374617475733a206f70656e

### DW-178: `ml_signals/strategies/example_strategy.py:84` subscribes a bar type whose pandas offset alias `'d'` is deprecated (`Pandas4Warning: 'd' is deprecated ... use …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: ml_signals/strategies/example_strategy.py:84
reason: source_spec: `_bmad-output/implementation-artifacts/spec-23-1-observability-context-and-migration-guardrails.md` summary: `ml_signals/strategies/example_strategy.py:84` subscribes a bar type whose pandas offset alias `'d'` is deprecated (`Pandas4Warning: 'd' is deprecated ... use 'D'`), and `nautilus_trader/backtest/node.py:668` plus `ml_signals/tests/test_snapshot_strategy.py:192` surface `Timestamp.utcnow` deprecations; a pandas 4 bump removes them (TEST-04). evidence: the full `make test` list in the collector image (second review pass of 23.1) prints 15 warnings, all of this family; none is DeprecationWarning from `platform/` shims. The `'d'` alias is in our own strategy config; the `utcnow` calls are in nautilus_trader's backtest node (upstream, FORK-01) and one of our tests. Pre-dates 23.1, which changed nothing in `ml_signals/strategies`.
status: open

### DW-179: 17 pre-existing `ruff` findings (pinned v0.15.16, repo config) survive in files Story 23.1 edited on lines it did not touch: `data_api/alerts.py` (UP035 …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: data_api/alerts.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-23-1-observability-context-and-migration-guardrails.md` summary: 17 pre-existing `ruff` findings (pinned v0.15.16, repo config) survive in files Story 23.1 edited on lines it did not touch: `data_api/alerts.py` (UP035, D401), `data_api/app.py` (UP035, SIM105), `data_api/live_candles.py` (UP035), `dydx_collector/collector.py:609` (ASYNC240), `ranking_engine/engine.py` (C901, D401), `ranking_engine/tests/test_engine.py` (S306 x2), `ml_signals/catalog_stats.py:198` (DTZ007), plus D401/C408/PT018 in five more. evidence: the same files carried 22 findings at the story's baseline `0cb42a5838` and 17 at its end; `platform/observability` and `platform/tests` are clean, as the spec's verification promises. The pre-commit hook would block a commit touching those lines, so they should go in one lint-only change, not be folded into a feature story.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-lint-and-test-hygiene
resolution-undo: 36635e4be7c38ae86fb67ecab9a11a98778641ab03dc21803191ad05a7466469 2026-10-05 7374617475733a206f70656e

### DW-180: the full `make test` list run with `-W default` prints ~140 `ResourceWarning: unclosed database in <sqlite3.Connection>` from `collector_core/collector.py` and …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: collector_core/collector.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-23-1-observability-context-and-migration-guardrails.md` summary: the full `make test` list run with `-W default` prints ~140 `ResourceWarning: unclosed database in <sqlite3.Connection>` from `collector_core/collector.py` and `second_snapshot.py` (the candle store connection a test-built `Collector` opens and never closes; loudest in `collector_core/tests/test_collector.py`, `dydx_collector/tests/test_build_candles.py`, `test_collector_resilience.py`), plus one unclosed event loop; the default filter hides all of them (TEST-04). evidence: third review pass of 23.1, collector image `story-23-1/collector:latest`: 163 warnings under `-W default` versus 15 under the default filter; none from `platform/observability` or `platform/tests`. Pre-dates 23.1, which touched neither the candle store nor those tests.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-lint-and-test-hygiene
resolution-undo: 36635e4be7c38ae86fb67ecab9a11a98778641ab03dc21803191ad05a7466469 2026-10-05 7374617475733a206f70656e

### DW-181: one file-name parser (`kernel.clocks.CatalogFileSpan.from_path`), two policies for a `*.parquet` whose stem the catalog did not write inside an instrument …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-23-2-kernel-shared-kernel.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-23-2-kernel-shared-kernel.md` summary: one file-name parser (`kernel.clocks.CatalogFileSpan.from_path`), two policies for a `*.parquet` whose stem the catalog did not write inside an instrument leaf: `prune_catalog._parsed_files` skips it with a warning and never prunes it, while `kernel.catalog_files` (`data_file_ranges`/`files_by_day`/`query_second_ohlc`), `compare_klines.instruments_on_day` and `rebuild_seconds.covered_from`/`trade_files` let the `ValueError` abort the whole instrument (a `data_api` request 500s, `compare_klines` aborts, the rebuild refuses the instrument); nothing documents which is intended. evidence: both review hunters of the 23.2 second pass flagged it independently. Pre-dates 23.2: the former `catalog_stats._stamp_to_ns` raised `ValueError` at the same sites and `prune_catalog` already caught it alone (`git show 7bd64952fd:platform/collector_core/prune_catalog.py:198-206`); the move preserved both behaviours verbatim. A foreign file only reaches a leaf by hand (the collector quarantines unreadable files out of the leaf), so no live path produces it today.
status: done 2026-10-06
resolution: resolved by sweep bundle dw-catalog-readers-skip-and-ledger
resolution-undo: 9452af3d15aa48b5980ac2177e29ca6f20152d75e05d99ce21efca1f22e644c8 2026-10-06 7374617475733a206f70656e
decision: 2026-10-05 All readers skip and ledger it (like reconcile_day._overlaps_day) — Make CatalogFileSpan callers in kernel.catalog_files and rebuild_day skip+ledger foreign names.

### DW-182: `kernel.archive_markers.decode`/`ArchiveGap.encode`'s new inverted-span refusal (this story's third pass) has no companion check-before-rollout: if any …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-23-2-kernel-shared-kernel.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-23-2-kernel-shared-kernel.md` summary: `kernel.archive_markers.decode`/`ArchiveGap.encode`'s new inverted-span refusal (this story's third pass) has no companion check-before-rollout: if any already-deployed `_archive_gaps/<iid>.jsonl` was written by the pre-fix `record_gap` (which this same story found could emit an inverted span on a backward wall-clock step), the nightly rebuild will now hard-refuse that instrument-day on the first read instead of the old silent tolerance. evidence: triage log pass 2 item 1 (`spec-23-2-kernel-shared-kernel.md`) confirms the pre-fix write path was capable of the inverted span this decode now refuses. No `_archive_gaps` file exists in this worktree's `platform/data/` (pre-rollout, per `DEPLOY_CHECKLIST.md` §5 / Epic 22 memory), so nothing is known to be affected today, but this wasn't checked against the actual VPS collector's historical files before merge, and should be a one-line grep (`decode` every line, catch `ValueError`) added to the rollout checklist rather than discovered by a failed rebuild.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-capture-script-and-docs
resolution-undo: 9077e58b03eea8f8c9d2570018bc9c06100b365969d0b85199174491807bf96f 2026-10-05 7374617475733a206f70656e

### DW-183: the catalog read helpers list files and then open them in a second step with no guard for a file that vanished in between …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-23-2-kernel-shared-kernel.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-23-2-kernel-shared-kernel.md` summary: the catalog read helpers list files and then open them in a second step with no guard for a file that vanished in between (`kernel.catalog_files._ohlc_rows`/`second_ohlc_arrays` call `pq.ParquetFile(path)` on paths from an earlier `snapshot_files` glob), so a `data_api` candle or paging request that overlaps `prune_catalog`/`consolidate_catalog`/`rebuild_seconds` rewriting that instrument's files raises `FileNotFoundError` and 500s instead of skipping the one file. evidence: flagged by the 23.2 fourth pass's edge-case hunter. Pre-dates 23.2 and was moved verbatim: `git show 2d7dd5ab6e:platform/ml_signals/catalog_stats.py:157-166` has the same list-then-open shape with no `try`. Real on the deployed box because `make nightly` runs consolidate/prune against the same catalog `data_api` serves from, but not yet observed — the VPS rollout (DEPLOY_CHECKLIST.md §5) has not run. Fix is a `try/except (FileNotFoundError, OSError): continue` per file, which is a policy decision (skip silently vs. ledger the skip) rather than a mechanical patch, so it belongs with the archive context move in 25.1.
status: done 2026-10-06
resolution: resolved by sweep bundle dw-catalog-readers-skip-and-ledger
resolution-undo: 9452af3d15aa48b5980ac2177e29ca6f20152d75e05d99ce21efca1f22e644c8 2026-10-06 7374617475733a206f70656e
decision: 2026-10-05 Skip and ledger the vanished file — Skip with error_ledger record.

### DW-184: two readers of the same snapshot rows disagree at the window's lower edge — `kernel.catalog_files.query_second_ohlc` widens the file span by …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-23-2-kernel-shared-kernel.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-23-2-kernel-shared-kernel.md` summary: two readers of the same snapshot rows disagree at the window's lower edge — `kernel.catalog_files.query_second_ohlc` widens the file span by `READ_SPAN_MARGIN_NS` on *both* sides, while `ml_signals.catalog_stats.query_second_snapshots` widens only the end (`start=start_ns, end=end_ns + READ_SPAN_MARGIN_NS`), so a row whose venue clock ran ahead of ours (`ts_init < ts_event`) and whose `ts_event` is within 60 s of `start_ns` is dropped by the snapshot reader and kept by the candle reader. evidence: flagged by the 23.2 fifth pass's adversarial hunter. Pre-dates 23.2 and was moved verbatim (`git show 2d7dd5ab6e:platform/ml_signals/catalog_stats.py:70-78`, same `start=start_ns`), but this story is what made its justifying comment provably wrong: `kernel/clocks.py`'s new `READ_SPAN_MARGIN_NS` docstring and `Collector._check_skew_budget` both document the second skew direction the comment denies ("ts_init >= ts_event, so the start needs no margin"). The fifth pass replaced that comment with a `Known limit:` naming the ceiling and the upgrade path rather than changing the margin, because AC2 freezes the read helpers' exact file-selection margins. The fix is `start=start_ns - READ_SPAN_MARGIN_NS`; the exact `ts_event` filter at the tail already makes it safe (widening can only add candidate rows, never admit out-of-window ones). Belongs with 24.2's views/read-model work, where the reader-side margin policy is decided once for both readers.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-archive-skew-margin-spans
resolution-undo: 503907f1821fa3d9323ad14b8c3f6d3be2067e07f8adbfeaa0b3aad39585a6c5 2026-10-05 7374617475733a206f70656e

### DW-185: `collector_core/crosscheck_errors.py` reads the catalog through `ml_signals.catalog_stats.query_second_ohlc` and the 300 s bound …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: collector_core/crosscheck_errors.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-23-3-durable-error-ledger-and-day-long-data-error-crosscheck.md` summary: `collector_core/crosscheck_errors.py` reads the catalog through `ml_signals.catalog_stats.query_second_ohlc` and the 300 s bound `collector_core.archive_gaps.ARRIVAL_MARGIN_NS`, and parses venues through `ml_signals.venue`/`common.venues`, because Story 23.2's `platform/kernel/` does not exist at this baseline (23.2 was deferred and is being re-driven). Every such import carries a `# 23.2 moves this to kernel.<symbol>.` comment; 23.2 (or 25.1 for the archive move) must repoint them to `kernel.catalog_files.query_second_ohlc` / `kernel.catalog_files.SNAPSHOT_DIRNAME` / `kernel.clocks.MAX_TS_INIT_SKEW_NS` / `kernel.clocks.NS_PER_S`/`NS_PER_DAY` / `kernel.venues`, and drop this module's local `NS_PER_S`, `NS_PER_DAY`, `SNAPSHOT_DIRNAME` and `_MAX_TS_INIT_SKEW_NS` constants. evidence: the spec's Design Notes ("Why this story does not use `kernel/`") sanction the substitution: each replacement is the exact module 23.2 moves into the kernel, and every symbol used is already mapped `KERNEL` in `platform/tests/test_boundaries.py`'s `LEGACY_SYMBOL_TO_CONTEXT`/`LEGACY_MODULE_TO_CONTEXT`, so the ARCHIVE->KERNEL edge is legal today and needs no `LEGACY_EDGES_UNTIL` entry. AC5's intent (bounded, streaming, no `ParquetDataCatalog` construction) is met exactly; only the import path differs.
status: done 2026-10-05
resolution: already resolved: platform/archive/application/crosscheck.py:97-103 imports kernel.catalog_files/kernel.clocks/kernel.venues; no '23.2 moves' comments remain

### DW-186: `observability.error_ledger._FileSink._admit`'s minute-bucket read happens under the sink's lock, but the `ts_ns` two concurrent `record()` calls from …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-23-3-durable-error-ledger-and-day-long-data-error-crosscheck.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-23-3-durable-error-ledger-and-day-long-data-error-crosscheck.md` summary: `observability.error_ledger._FileSink._admit`'s minute-bucket read happens under the sink's lock, but the `ts_ns` two concurrent `record()` calls from different threads present to it is captured by each caller before that lock is acquired, so at an exact minute boundary two racing threads can be admitted in an order that does not match wall-clock order, narrowly misattributing which of the two gets the bucket reset. evidence: `record()` is only reached from failure paths (rare, low call frequency per process), and the window is exactly one minute boundary under real thread contention on the same site; carried over from the preserved attempt's review round as real but low-consequence, and not scoped for this story pass.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-observability-ledger-fixes
resolution-undo: f29f38bea3e637c6b62e34f9b17b6105f4546cafc7682de177659092884f975f 2026-10-05 7374617475733a206f70656e

### DW-187: `observability.error_ledger.services()`/`ledger_files()` list every `<service>.jsonl*` file under `ERROR_LEDGER_DIR` with no concept of "this service was …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: GET /api/errors
reason: source_spec: `_bmad-output/implementation-artifacts/spec-23-3-durable-error-ledger-and-day-long-data-error-crosscheck.md` summary: `observability.error_ledger.services()`/`ledger_files()` list every `<service>.jsonl*` file under `ERROR_LEDGER_DIR` with no concept of "this service was renamed or retired" -- a stale file from an old collector name stays visible in `GET /api/errors` and `collector_core.crosscheck_errors` indefinitely, with no pruning mechanism or staleness marker. evidence: inherent to an append-only-JSONL-file design (no registry of "current" service names to diff against) and consistent with how this codebase already treats other `platform/data/*` stores as operator-cleaned rather than self-pruning. Not a correctness bug today (no service has been renamed), so not scoped for the initial pass.
status: open

### DW-188: A corrupt or truncated `candles_<venue>.db` aborts the whole nightly `prune_catalog` step with an unledgered `sqlite3.DatabaseError`, instead of treating that …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md` summary: A corrupt or truncated `candles_<venue>.db` aborts the whole nightly `prune_catalog` step with an unledgered `sqlite3.DatabaseError`, instead of treating that venue's days as unverified and continuing. evidence: `collector_core/prune_catalog.py:_leaf_statuses` catches only `MalformedInstrumentId`; the sqlite read beneath it (now `candles.infrastructure.verified_days.VerifiedDaysDir`, previously `candle_store.connect_ro` + `verified_status`) can raise `sqlite3.DatabaseError`, which propagates out of `plan_trade_prune` and ends the nightly chain. Pre-existing — the same hole is in the baseline at `7cd2f91aa2` — and unverified days are fail-safe (files are kept), so this is a robustness gap rather than a data-loss one. DATA-07 wants the continue-past-failure ledgered at `prune.verified_days`.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-archive-prune-repair-safety
resolution-undo: dcc0700427eddf493cae780ae257b14adaad477dcd1d81d72caf42af892bf694 2026-10-05 7374617475733a206f70656e

### DW-189: `Collector._catch_up_candle_store` still calls `error_ledger.record("collector.candle_store_catch_up", ...)` inside its per-instrument loop, the exact shape …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md` summary: `Collector._catch_up_candle_store` still calls `error_ledger.record("collector.candle_store_catch_up", ...)` inside its per-instrument loop, the exact shape Story 24.1's first review pass removed from `_apply_to_candle_store` 30 lines above for the ledger's 60-lines-per-site-per-minute write cap. evidence: pre-existing — identical per-instrument `record` at the baseline (`git show 7cd2f91aa2:platform/collector_core/collector.py`, `_catch_up_candle_store`), moved verbatim onto the port. Reachable: a fault that lets `watermarks()` succeed but fails every `apply` (disk full, a corrupt catalog day) emits one line per instrument, and dYdX's `_MAX_COLLECTED_INSTRUMENTS = 30` plus `run_forever`'s 1s/2s/4s restart backoff can put several starts inside one minute, so the site blows its cap and the `suppressed` carry hides the detail. Lower urgency than the flush path (once per start, not every 30 s), and the fix is the same consolidation already applied there.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-capture-service-lifecycle
resolution-undo: 0048a9d04f44fc28ce1ff055a57d3d109c101be1016932f3bf35d6b4fc4e6436 2026-10-06 7374617475733a206f70656e

### DW-190: `_catch_up_candle_store` iterates `self._second_sink.watermarks()`, which only lists instruments that already have a stored row, so an instrument whose every …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md` summary: `_catch_up_candle_store` iterates `self._second_sink.watermarks()`, which only lists instruments that already have a stored row, so an instrument whose every apply has failed since the store was created is never caught up — its archived seconds are folded only by a manual `python -m candles.rebuild`. evidence: pre-existing (`candle_store.watermarks(self._candle_db).items()` at the baseline, same shape). Narrow but real: after a run whose store writes all failed for one instrument, that instrument has no `built_through` row, the catch-up skips it entirely and live applies then start from the current second, leaving the earlier archived seconds unfolded with nothing reporting it. The fix is to fall back to `now_ns - _CATCH_UP_MAX_NS` for a subscribed instrument absent from the watermarks, which is a policy choice (how far back to reach for a never-applied instrument) rather than a mechanical patch.
status: open

### DW-191: the catch-up's ">24 h behind" branch is a bare `logger.warning(...); continue` with no `error_ledger.record`, so a store that has fallen permanently behind the …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: GET /api/errors
reason: source_spec: `_bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md` summary: the catch-up's ">24 h behind" branch is a bare `logger.warning(...); continue` with no `error_ledger.record`, so a store that has fallen permanently behind the archive is invisible to `GET /api/errors` and the frontend `<ErrorBar>` — visible only to whoever reads Dozzle. evidence: pre-existing and explicitly sanctioned by this story's own I/O matrix ("Instrument skipped with the existing 'run build_candles' warning | Warning, no store write"), so it is not a deviation — but DATA-07 names exactly this shape ("A bare `logger.warning(...); continue` at a data-dropping site is not acceptable") and DATA-05 states the derived store counts as data. `make nightly` runs `candles.rebuild` automatically, which is why this has not bitten; a box whose nightly chain is failing is precisely when it would. Changing it means deciding whether a permanently-behind store is an error or an expected operator task, which is a rule-level call.
status: done 2026-10-05
resolution: already resolved: platform/capture/application/capture_service.py:1226-1232 ledgers the >24 h behind branch at sites.CANDLE_STORE_BEHIND

### DW-192: the epic's "exactly two folds exist in `platform/`" invariant (this story's AC #4) has no automated guard — it is asserted only by a one-shot `grep` in the …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md` summary: the epic's "exactly two folds exist in `platform/`" invariant (this story's AC #4) has no automated guard — it is asserted only by a one-shot `grep` in the spec's Verification block, so the next hand-rolled aggregation reintroduces a third fold with nothing failing. evidence: `grep -rn "fold_arrays" platform/tests platform/candles/tests` finds no structural assertion, only docstrings. The repo already has the machinery for this class of invariant (`tests/test_boundaries.py`, `test_images.py`, `test_namespace.py`, `test_hotpath.py` are all static source guards), so this is a gap in coverage rather than a missing capability. Checked and *not* a violation today: `data_api/routes/indicator_series.py`'s `snapshot.ts_event // bar_ns` bucketing samples indicator values per bucket and produces no OHLCV, so it is not a seconds->bars fold; a guard would have to be written to admit it deliberately.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-lint-and-test-hygiene
resolution-undo: 36635e4be7c38ae86fb67ecab9a11a98778641ab03dc21803191ad05a7466469 2026-10-05 7374617475733a206f70656e

### DW-193: this story moved candle-store ownership to the three venue entrypoints without giving them a teardown — `CandleStore.close()` is called only by …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: candles/rebuild.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md` summary: this story moved candle-store ownership to the three venue entrypoints without giving them a teardown — `CandleStore.close()` is called only by `candles/rebuild.py`, `candles/application/rebuild.py` and tests, so no entrypoint and no `Collector.run` `finally` ever closes the store a composition root opened. evidence: `grep -rn "\.close()" platform/*_collector/collector.py` finds nothing, and `run_forever` (`collector_core/collector.py`) calls `build()` for the next attempt while the failed collector is still reachable from the `except Exception` traceback, so the same file is transiently open read-write twice — under a class whose docstring asserts writer ownership. Committed data survives (WAL + `synchronous=NORMAL`), so this is a lifecycle/ownership gap, not data loss; the fix (an `AbstractContextManager` on `CandleStore`, or a `Collector` teardown hook for injected resources) is a small design decision about who owns injected-resource shutdown, which belongs with the composition-root work rather than a patch here.
status: done 2026-10-06
resolution: already resolved: commit 988fd26230: CandleSink.close() (platform/candles/application/sink.py:55-57) closes the store and CaptureService.run's finally calls self._second_sink.close() (platform/capture/application/capture_service.py:2536, ledgered as SECOND_SINK_CLOSE); all three venue __main__ roots inject CandleSink(store_from_env(...)), so every composition-root-opened store is now torn down
decision: 2026-10-05 Make CandleStore an AbstractContextManager used by each __main__ root — Context-manage the store in the three composition roots.

### DW-194: `make build-candles` passes no `--venue`, so on the shared three-venue catalog it folds Bybit and Hyperliquid instruments into `candles_dydx.db` (where …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: CLAUDE.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md` summary: `make build-candles` passes no `--venue`, so on the shared three-venue catalog it folds Bybit and Hyperliquid instruments into `candles_dydx.db` (where `data_api` can never read them) and repairs neither of the other two stores — while `CLAUDE.md` DATA-05 and `docs/DATA_INTEGRITY_AUDIT.md` D-35 point the operator at that target as *the* repair for a `collector.candle_store` failure, which is a venue-neutral ledger site all three collectors emit. evidence: `platform/Makefile:220-222` runs `python3 -m candles.rebuild --catalog /app/catalog --db /app/candles_dir/candles_dydx.db`; `candles/rebuild.py:_jobs` falls back to `all_instruments(catalog)`, which enumerates every id under the one catalog root. Pre-existing and unchanged in shape: the baseline at `7cd2f91aa2` has the identical target against `collector_core.build_candles`, whose `--venue` flag was equally optional. `candles/tests/test_rebuild.py:151` proves the filter is load-bearing. The fix is a per-venue target (or a `VENUE=` variable like `make nightly` already takes), which is a Makefile-surface decision rather than a patch inside this story's diff.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-makefile-venue-candles
resolution-undo: 1a6b7740085079c55586e14fa69e2d038c3f8ef250f63ca0a0f98c58703adbc1 2026-10-06 7374617475733a206f70656e

### DW-195: the hourly retention prune runs `store.prune()` synchronously on the collector's event loop, and the `candles` table's primary key leads with `instrument_id` …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-24-1-candles-context-behind-the-secondsink-port.md` summary: the hourly retention prune runs `store.prune()` synchronously on the collector's event loop, and the `candles` table's primary key leads with `instrument_id`, so the `DELETE ... WHERE bar_seconds = ? AND t < ?` cannot use it — on a large store the scan blocks ingestion and per-second sampling for its whole duration. evidence: `candles/application/prune.py`'s `prune_loop` awaits only `asyncio.sleep`, never `asyncio.to_thread`; `_SCHEMA` (frozen under AD-D12) declares `PRIMARY KEY (instrument_id, bar_seconds, t) WITHOUT ROWID` and no secondary index. Pre-existing: the baseline's `Collector._candle_prune_loop` called `candle_store.prune(self._candle_db)` the same way on the same loop, so this story moved the shape rather than introducing it. The two fixes pull in opposite directions — `asyncio.to_thread` hands a second thread a connection the store's single-writer invariant says it owns alone, and an index on `(bar_seconds, t)` touches the frozen schema — so it needs a decision, not a patch.
status: done 2026-10-06
resolution: resolved by sweep bundle dw-candle-prune-secondary-index
resolution-undo: dcec435856f63a0a78f742e8ca01075037bf4d1a50125f8b003fefd5e7288433 2026-10-06 7374617475733a206f70656e
decision: 2026-10-05 Add a secondary index (bar_seconds, t) (schema change; nothing frozen until prod) — Add index in _SCHEMA with migration for existing stores.

### DW-196: the web frontend still hand-mirrors the ranking-table columns (`frontend/src/pages/RankingsPage.tsx`'s TS copy of `RANKING_COLS`) and its own coin-detail …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: frontend/src/pages/RankingsPage.tsx
reason: source_spec: `_bmad-output/implementation-artifacts/spec-24-2-views-read-models-and-reader-side-revalidation-removed.md` summary: the web frontend still hand-mirrors the ranking-table columns (`frontend/src/pages/RankingsPage.tsx`'s TS copy of `RANKING_COLS`) and its own coin-detail metric groups, so after Story 24.2 the two UIs share one `views` source only on the Python side; `views.coin_detail.COIN_DETAIL_GROUPS` and `views.ranking_columns.RANKING_COLS` have `bot_tui` as their only consumer, and a column added in `views/` reaches the TUI but not the web UI with nothing failing. evidence: `RankingsPage.tsx` documents itself as a "hand-declared TS mirror of views/ranking_columns.py's RANKING_COLS"; no `data_api` route serves either table. Pre-existing (the mirror predates this story, which only moved the Python module); the fix — serving the column/group metadata over an endpoint the frontend renders from, or a codegen step like the OpenAPI→TS pipeline — is a frontend-contract decision outside a relocation story.
status: done 2026-10-05
resolution: already resolved: platform/data_api/tests/test_ranking_columns_mirror.py fails on drift between RankingsPage.tsx:62-65 mirror and views/ranking_columns.py, so a column added in views no longer reaches only the TUI silently

### DW-197: `alerting.infrastructure.toml_store.AlertStore._save` truncates `alerts.toml` (`open("wb")`) and then dumps, so a crash or full disk mid-write leaves a …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: alerts.toml
reason: source_spec: `_bmad-output/implementation-artifacts/spec-24-3-alerting-context-as-forming-bar-observer.md` summary: `alerting.infrastructure.toml_store.AlertStore._save` truncates `alerts.toml` (`open("wb")`) and then dumps, so a crash or full disk mid-write leaves a truncated file that `_load` deliberately refuses — and `data_api` then fails at import, because `data_api.alert_wiring` builds the store at module load. evidence: `_save` has no temp-file + `os.replace`; `_load` raises on a corrupt file by design ("a corrupt file raises on load rather than starting empty"). Pre-existing: moved verbatim from `data_api/alerts.py` at `18244a90ce`. The fix (atomic write-then-rename, text unchanged) is small but touches every config-persistence writer's durability contract (`views/preferences.py` writes the same way), so it belongs in one focused pass over all of them.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-alert-store-durability
resolution-undo: c907cf861979e0c8c50225092f3c43a1eeac190c0807cb7f77cbaadbae2e1433 2026-10-06 7374617475733a206f70656e

### DW-198: `AlertStore._load` builds `Alert(entry)` without checking `frequency` against `FiringPolicy`, so a hand-edited or legacy `alerts.toml` entry with an unknown …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: alerts.toml
reason: source_spec: `_bmad-output/implementation-artifacts/spec-24-3-alerting-context-as-forming-bar-observer.md` summary: `AlertStore._load` builds `Alert(**entry)` without checking `frequency` against `FiringPolicy`, so a hand-edited or legacy `alerts.toml` entry with an unknown frequency falls through `evaluate` to fire on every cross and, since `record_fire` sets `triggered` only for `only_once`, never stops firing. evidence: `alerting/domain/policy.py`'s `evaluate` treats any frequency other than `once_per_bar_close`/`once_per_bar` as fire-on-cross; `/api/alerts` validates with a `Literal[...]`, so only a file edit reaches it. Pre-existing (identical in the baseline's `data_api/alerts.py`). The fix — reject or ledger an unknown frequency (and non-positive `bar_seconds`) at load — is a load-contract decision: raising makes `data_api` refuse to start on one bad entry, skipping drops a user's alert.
status: open
decision: 2026-10-05 Skip the entry and ledger it — Drop the entry with an error_ledger record (alert is lost).

### DW-199: `alerting.infrastructure.toml_store.AlertStore.add`/`delete` mutate the in-memory list before `_save()`, so a failed write (disk full, permission) returns 500 …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: /api/alerts
reason: source_spec: `_bmad-output/implementation-artifacts/spec-24-3-alerting-context-as-forming-bar-observer.md` summary: `alerting.infrastructure.toml_store.AlertStore.add`/`delete` mutate the in-memory list before `_save()`, so a failed write (disk full, permission) returns 500 from `/api/alerts` while the unsaved alert keeps firing (add), or while the deleted alert stays gone until a restart brings it back and `engine.forget` is skipped (delete). evidence: `add` appends and then `_save()`s under the lock, and `delete` reassigns `self._alerts` and then `_save()`s, with no rollback on exception. Pre-existing: moved verbatim from the baseline's `data_api/alerts.py` at `18244a90ce`. Belongs with the atomic-write entry above, in the same pass over config-persistence writers.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-alert-store-durability
resolution-undo: c907cf861979e0c8c50225092f3c43a1eeac190c0807cb7f77cbaadbae2e1433 2026-10-06 7374617475733a206f70656e

### DW-200: `research.strategies.backtest_dydx.run` and `backtest_snapshot.run` default `catalog_path` to the cwd-relative `"platform/data/catalog"`, while …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: "platform/data/catalog"
reason: source_spec: `_bmad-output/implementation-artifacts/spec-24-4-research-pure-consumer-and-broken-tests-repaired.md` summary: `research.strategies.backtest_dydx.run` and `backtest_snapshot.run` default `catalog_path` to the cwd-relative `"platform/data/catalog"`, while `docs/BOT_OPERATIONS.md` tells the user to `cd platform` first, so the documented call resolves `platform/platform/data/catalog`: `backtest_dydx` then skips every symbol and returns `{}`, and `backtest_snapshot` raises `IndexError` on `instruments(...)[0]`. evidence: both defaults are literal relative strings, and `backtest_ofi` alone derives its path from `__file__`. Pre-existing: the baseline `ml_signals.strategies.backtest_*` had the same defaults, and the baseline BOT_OPERATIONS.md had the same `cd platform` instruction. This story only renamed the module paths. The fix (one `__file__`-anchored default shared by all three runners, plus how the collector image's `/app/catalog` mount is addressed) spans the runners and the image layout, so it belongs with Story 27.8's research-in-image work.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-research-test-and-path-fixes
resolution-undo: 89c508f4086e6130fe63c3b8c7df6e501f431e414611a6838c2e01ff0641c80a 2026-10-05 7374617475733a206f70656e

### DW-201: A `pruned` archive-gap marker spans the deleted file's `ts_init` name range, but the rebuild tests gaps on the row's `ts_event`, so rows in `[start …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: A `pruned` archive-gap marker spans the deleted file's `ts_init` name range, but the rebuild tests gaps on the row's `ts_event`, so rows in `[start - MAX_TS_INIT_SKEW_NS, start)` are treated as covered after their trades were deleted. evidence: archive/application/prune.py records `CatalogFileSpan` start/end, and archive/domain/gaps.py `Coverage.covers(ts_event)`. This predates 25.1 (old prune_catalog did the same).
status: done 2026-10-05
resolution: resolved by sweep bundle dw-archive-skew-margin-spans
resolution-undo: 503907f1821fa3d9323ad14b8c3f6d3be2067e07f8adbfeaa0b3aad39585a6c5 2026-10-05 7374617475733a206f70656e

### DW-202: `rebuild_day` rewrites a day's files one at a time, so a refusal on file k leaves files 1..k-1 rebuilt while the day is reported "refused, untouched".

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: `rebuild_day` rewrites a day's files one at a time, so a refusal on file k leaves files 1..k-1 rebuilt while the day is reported "refused, untouched". evidence: archive/application/rebuild_day.py per-file rewrite loop. This predates 25.1 (old `_replace_file` loop).
status: done 2026-10-05
resolution: already resolved: platform/archive/application/rebuild_day.py:_rebuild_files stages every file first and discards all on refusal, commits together; a partial commit is reported as 'PARTIALLY rebuilt' (rebuild_day.py ~483-490)

### DW-203: A stored `verified_days = pass` is not invalidated when a later rebuild or repair changes that day's seconds and the reconcile then errors, is refused, or is …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: A stored `verified_days = pass` is not invalidated when a later rebuild or repair changes that day's seconds and the reconcile then errors, is refused, or is skipped. Prune can then release the trades behind stale proof. evidence: Documented as a Known limit in archive/domain/archive_day.py. The fix needs rebuild to report changed rows per instrument and reconcile to refuse to leave a stale pass.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-archive-prune-repair-safety
resolution-undo: dcc0700427eddf493cae780ae257b14adaad477dcd1d81d72caf42af892bf694 2026-10-05 7374617475733a206f70656e

### DW-204: repair_catalog runs `delete_data_range` then `write_data` per row with no backup or atomicity; a crash between them loses that second's snapshot.

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: repair_catalog runs `delete_data_range` then `write_data` per row with no backup or atomicity; a crash between them loses that second's snapshot. evidence: archive/application/repair.py `repair_instrument`. This predates 25.1.
status: done 2026-10-06
resolution: resolved by sweep bundle dw-archive-repair-atomic-and-guarded
resolution-undo: ff6a7f0c9b9ea1178362256386cc4688080f4686f5efd4dbee70862f78023d36 2026-10-06 7374617475733a206f70656e
decision: 2026-10-05 Rewrite via CatalogFiles.rewrite (verified temp-then-rename) instead of delete+write — Replace the flagged rows in-file through the maintenance rewriter.

### DW-205: The retention `file_days` rule widens only backwards (the previous day). A file ending just before midnight can hold venue-clock-ahead trades of the next day …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: The retention `file_days` rule widens only backwards (the previous day). A file ending just before midnight can hold venue-clock-ahead trades of the next day, and that day is never required to be verified. evidence: archive/domain/retention.py `file_days`, and `kernel.clocks.CatalogFileSpan` documents `ts_init < ts_event`. This predates 25.1.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-archive-skew-margin-spans
resolution-undo: 503907f1821fa3d9323ad14b8c3f6d3be2067e07f8adbfeaa0b3aad39585a6c5 2026-10-05 7374617475733a206f70656e

### DW-206: repair_catalog's "never run on a rebuilt day" rule is enforced only by its docstring; nothing checks the rebuild or `verified_days` state before clearing …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: repair_catalog's "never run on a rebuilt day" rule is enforced only by its docstring; nothing checks the rebuild or `verified_days` state before clearing trades. evidence: archive/repair_catalog.py. This predates 25.1.
status: done 2026-10-06
resolution: resolved by sweep bundle dw-archive-repair-atomic-and-guarded
resolution-undo: ff6a7f0c9b9ea1178362256386cc4688080f4686f5efd4dbee70862f78023d36 2026-10-06 7374617475733a206f70656e
decision: 2026-10-05 Refuse rows at or after the instrument's trade-archive coverage start (pre-archive rows only) — Use archive Coverage.start to refuse repairing covered rows.

### DW-207: One stray `*.parquet` with an unparsable name in a snapshot or trade leaf crashes `compare_klines` (`instruments_on_day`) before any instrument is compared …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: One stray `*.parquet` with an unparsable name in a snapshot or trade leaf crashes `compare_klines` (`instruments_on_day`) before any instrument is compared, every night, until someone removes it. evidence: archive/application/reconcile_day.py calls `CatalogFileSpan.from_path` with no guard. This predates 25.1.
status: done 2026-10-05
resolution: already resolved: platform/archive/application/reconcile_day.py:228-236 _overlaps_day ledgers 'reconcile.error' and skips a non-catalog file name instead of aborting compare

### DW-208: The dropped-instrument rule deletes a dropped dYdX coin's instrument-definition leaves while its unverified `trade_tick` days are kept, so those days can never …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: The dropped-instrument rule deletes a dropped dYdX coin's instrument-definition leaves while its unverified `trade_tick` days are kept, so those days can never be reconciled and are kept forever. evidence: archive/domain/retention.py rule (c) covers every type except trade_tick, the same set as the old `prune_instrument`. Semantics predate 25.1.
status: done 2026-10-06
resolution: resolved by sweep bundle dw-archive-retention-rule-ordering
resolution-undo: a743b1aba6f42e24f54f556698a89012d8221c6df629ae682bdbd109fed850ab 2026-10-06 7374617475733a206f70656e
decision: 2026-10-05 Keep definition leaves until the coin's trade days are verified or released — Gate rule (c) definition deletion on trade days.

### DW-209: Capture appends `_archive_gaps/*.jsonl` without a lock, so a nightly `load_gaps` can read a torn last line, raise ValueError and refuse the instrument-day.

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: Capture appends `_archive_gaps/*.jsonl` without a lock, so a nightly `load_gaps` can read a torn last line, raise ValueError and refuse the instrument-day. evidence: archive/infrastructure/gap_markers.py `load_gaps`, collector_core/gap_markers.py append. This predates 25.1.
status: done 2026-10-06
resolution: already resolved: Resolved by e6272ad9c6 (sweep DW-211): platform/capture/infrastructure/gap_markers.py:83-104 and archive/infrastructure/gap_markers.py append under flock(LOCK_EX) with O_APPEND, one _write_all+fsync, and _truncate_back on any failure, which is the chosen 'single O_APPEND write + truncate-back' decision
decision: 2026-10-05 Single O_APPEND os.write per line plus truncate-back on OSError — Make marker appends one write, truncate back on failure.

### DW-210: repair_catalog deletes a flagged snapshot with `delete_data_range(..., snap.ts_event, snap.ts_event)`, but the catalog range-filters on `ts_init`, which …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: repair_catalog deletes a flagged snapshot with `delete_data_range(..., snap.ts_event, snap.ts_event)`, but the catalog range-filters on `ts_init`, which venue-time capture stamps after `ts_event`, so the delete can miss and `write_data` then adds a duplicate second. evidence: archive/application/repair.py (was collector_core/repair_catalog.py:93 at baseline 983d0c792c); test_repair.py builds every snapshot with ts_init == ts_event. This predates 25.1.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-archive-skew-margin-spans
resolution-undo: 503907f1821fa3d9323ad14b8c3f6d3be2067e07f8adbfeaa0b3aad39585a6c5 2026-10-05 7374617475733a206f70656e

### DW-211: `record_gap` can leave a torn `_archive_gaps/*.jsonl` line on a mid-write OSError (e.g. ENOSPC), and `load_gaps` then raises ValueError every later night …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: `record_gap` can leave a torn `_archive_gaps/*.jsonl` line on a mid-write OSError (e.g. ENOSPC), and `load_gaps` then raises ValueError every later night, refusing that instrument's rebuild until the file is hand-edited. evidence: archive/infrastructure/gap_markers.py and collector_core/gap_markers.py write with f.write + fsync and no truncate-back on failure. This predates 25.1 (collector_core/archive_gaps.py:66).
status: done 2026-10-05
resolution: resolved by sweep bundle dw-archive-prune-repair-safety
resolution-undo: dcc0700427eddf493cae780ae257b14adaad477dcd1d81d72caf42af892bf694 2026-10-05 7374617475733a206f70656e

### DW-212: The venue kline paging loops (dYdX/Bybit/Hyperliquid) have no iteration cap or strict-progress check, so a server returning a page that does not advance the …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: The venue kline paging loops (dYdX/Bybit/Hyperliquid) have no iteration cap or strict-progress check, so a server returning a page that does not advance the cursor loops forever. evidence: archive/infrastructure/klines_{dydx,bybit,hyperliquid}.py, moved as-is from collector_core/compare_klines.py:248,287,329. This predates 25.1.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-archive-prune-repair-safety
resolution-undo: dcc0700427eddf493cae780ae257b14adaad477dcd1d81d72caf42af892bf694 2026-10-05 7374617475733a206f70656e

### DW-213: consolidate_catalog exits 1 on any refused day, so one standing mixed-schema day stops candles.rebuild, compare_klines and prune in every nightly saga run, not …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: consolidate_catalog exits 1 on any refused day, so one standing mixed-schema day stops candles.rebuild, compare_klines and prune in every nightly saga run, not just the prune the Known limit names. evidence: archive/consolidate_catalog.py exit code and application/nightly.py stop-at-first-failure. This predates 25.1 (collector_core/consolidate_catalog.py:53).
status: done 2026-10-07
resolution: resolved by sweep bundle dw-consolidate-refusals-exit-findings
resolution-undo: ce1dd07646eda9038bef1a77c17db91f1bf79210c72047e02c611a29e6f6ab61 2026-10-07 7374617475733a206f70656e
decision: 2026-10-05 Exit 2 (findings) for refused days — consolidate exits 2 on refused days/leaves so the saga continues past it, ledgered as findings

### DW-214: The prune's `pruned` marker covers the deleted trade file's raw `ts_init` span, but a trade's `ts_event` can precede its `ts_init` by up to …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: The prune's `pruned` marker covers the deleted trade file's raw `ts_init` span, but a trade's `ts_event` can precede its `ts_init` by up to `MAX_TS_INIT_SKEW_NS`, so a later rebuild of that day re-folds the edge seconds without the pruned trades. evidence: archive/application/prune.py `_delete` records `ArchiveGap(f.iid, *span, "pruned", 0)`, while the rebuild tests gap membership on the snapshot row's `ts_event`. This predates 25.1 (collector_core/prune_catalog.py:278 at baseline 983d0c792c).
status: done 2026-10-05
resolution: resolved by sweep bundle dw-archive-skew-margin-spans
resolution-undo: 503907f1821fa3d9323ad14b8c3f6d3be2067e07f8adbfeaa0b3aad39585a6c5 2026-10-05 7374617475733a206f70656e

### DW-215: `make prune` (order_book_deltas age rule, 14 days) deletes raw deltas of a dYdX instrument whose plan entry says `retain_hours = None` (unlimited), because the …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1-archive-context-archiveday-one-deleter-one-rewriter.md` summary: `make prune` (order_book_deltas age rule, 14 days) deletes raw deltas of a dYdX instrument whose plan entry says `retain_hours = None` (unlimited), because the age rule is decided independently of the plan's per-instrument delta retention. evidence: platform/Makefile `prune` target and archive/domain/retention.py, where rule (b) runs regardless of rule (d)'s None. This predates 25.1 (baseline Makefile prune target, plus the old `_prune_loop`).
status: done 2026-10-06
resolution: resolved by sweep bundle dw-archive-retention-rule-ordering
resolution-undo: a743b1aba6f42e24f54f556698a89012d8221c6df629ae682bdbd109fed850ab 2026-10-06 7374617475733a206f70656e
decision: 2026-10-05 Plan wins: skip age rule for unlimited-plan instruments — Age rule excludes dYdX instruments whose plan retain_hours is None

### DW-216: bot_tui Bot-detail's `o` deep-link opens `<DASHBOARD_BASE_URL>/bot/{bot_id}`, which the web app has no route for, so the only remaining TUI deep-link (and the …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-1a-rankings-web-only-mode-toggle-on-web-tui-coins-pane-deleted.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1a-rankings-web-only-mode-toggle-on-web-tui-coins-pane-deleted.md` summary: bot_tui Bot-detail's `o` deep-link opens `<DASHBOARD_BASE_URL>/bot/{bot_id}`, which the web app has no route for, so the only remaining TUI deep-link (and the open_listener.go hand-off now serving it) lands on a blank page. evidence: platform/bot_tui/bots_pane.py `dashboard_bot_url` docstring states the route does not exist; platform/frontend/src/App.tsx routes are `/`, `/chart/:iid`, `/history/:iid`, `/alerts`, `/docs/*`. This predates 25.1a (the URL builder and route gap were there at baseline f00ab8aeea).
status: done 2026-10-07
resolution: resolved by sweep bundle dw-bot-tui-remove-dead-deeplink
resolution-undo: 1921b265bcbca070605b3491f747713a1ecb0d027b0c8cb4b910399c3ff99648 2026-10-07 7374617475733a206f70656e
decision: 2026-10-05 Remove the deep-link key — Remove the `o` key and open_listener hand-off for bots

### DW-217: The ranking slow loop writes a `metrics.db` row stamped with the current `ts` for an instrument that is stale (>30 s silent) but not yet aged out (<1 h) …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-2-ranking-context-rankingboard-replaces-module-globals.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-2-ranking-context-rankingboard-replaces-module-globals.md` summary: The ranking slow loop writes a `metrics.db` row stamped with the current `ts` for an instrument that is stale (>30 s silent) but not yet aged out (<1 h), carrying its last price/pct/book metrics as if current. evidence: platform/ranking/domain/board.py `slow_rows` iterates every held instrument, as the pre-move `_slow_loop_once` iterated all of `_LAST_SEEN` (forever, before 25.2's age_out bounded it to 1 h). DATA-01: the rows should be skipped or null the live fields. This predates 25.2.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-ranking-board-ingest-guards
resolution-undo: 501612be81209b39f912be5f86707e9a8fae0a2082d36ca5e084f5430ccfda9c 2026-10-06 7374617475733a206f70656e

### DW-218: Ranking ingest has no ordering/finiteness guard beyond the price series: a duplicate or out-of-order `snapshots:raw` entry is fed to OFI/OBI, the 300-snapshot …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-2-ranking-context-rankingboard-replaces-module-globals.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-2-ranking-context-rankingboard-replaces-module-globals.md` summary: Ranking ingest has no ordering/finiteness guard beyond the price series: a duplicate or out-of-order `snapshots:raw` entry is fed to OFI/OBI, the 300-snapshot rolling window and `VolatilityTracker` again, a NaN top-of-book passes the `mid <= 0` check, and a catalog backfill series is not deduplicated or checked for non-positive prices. evidence: platform/ranking/domain/board.py `ingest`/`_feed_indicators`, domain/volatility.py `update`, domain/price_series.py `backfill`, infrastructure/catalog_prices.py; the same code paths in ranking_engine/engine.py:589-624 and price_series.py at baseline a046e0839a. This predates 25.2.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-ranking-board-ingest-guards
resolution-undo: 501612be81209b39f912be5f86707e9a8fae0a2082d36ca5e084f5430ccfda9c 2026-10-06 7374617475733a206f70656e

### DW-219: The two UI preference TOMLs (`platform/data/{chart_indicators,screener_columns}.toml`) are tracked in git yet rewritten in place by data_api at runtime, so …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: platform/data/{chart_indicators,screener_columns}.toml
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-2-ranking-context-rankingboard-replaces-module-globals.md` summary: The two UI preference TOMLs (`platform/data/{chart_indicators,screener_columns}.toml`) are tracked in git yet rewritten in place by data_api at runtime, so every VPS `git pull` that touches them is refused until the live copy is set aside and restored. evidence: docker-compose.yml mounts both `:rw` for data_api's PUT routes; the Story 25.2 DEPLOY_CHECKLIST step works around it. The conflict existed at their old `ml_signals/` path at baseline a046e0839a; 25.2 only moved them.
status: done 2026-10-08
resolution: resolved by sweep bundle dw-untrack-runtime-data-files
resolution-undo: 9c3c3ed117779a15e1b47516286bef3b3c844668f4fd3bc6f9f9429ee504f428 2026-10-08 7374617475733a206f70656e
decision: 2026-10-05 Untrack, gitignore, seed defaults at startup — Stop tracking live files; ship *.default.toml and copy if missing

### DW-220: The ranking price-series mark-price fallback (`CatalogPriceHistory._mark_prices`) loads every mark price since `start_ns` with no rate bound, so a venue …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-2-ranking-context-rankingboard-replaces-module-globals.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-2-ranking-context-rankingboard-replaces-module-globals.md` summary: The ranking price-series mark-price fallback (`CatalogPriceHistory._mark_prices`) loads every mark price since `start_ns` with no rate bound, so a venue publishing marks faster than 1 Hz overflows the `25 h × 3600`-slot ring buffer (keeping only the newest ~90k points, `pct_24h` silently None or short-spanned) and holds the whole unbounded query in memory. evidence: platform/ranking/infrastructure/catalog_prices.py `_mark_prices` (`catalog.query(MarkPriceUpdate, ..., start=start_ns)` with no end or downsample) feeding domain/price_series.py `_RingBuffer(capacity=lookback_hours*3600)`, whose sizing assumes at most one close per second; the same fallback existed in ml_signals/catalog_stats.py at baseline a046e0839a.
status: done 2026-10-05
resolution: already resolved: f7c9e3ea38 (Story 31.3) deleted the mark-price fallback; platform/ranking/infrastructure/catalog_prices.py:21 reads trade closes only

### DW-221: The in-app knowledge base's "One paragraph" overview still says a web dashboard and a terminal UI both read the two Redis feeds and names dYdX only, though …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-2-ranking-context-rankingboard-replaces-module-globals.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-2-ranking-context-rankingboard-replaces-module-globals.md` summary: The in-app knowledge base's "One paragraph" overview still says a web dashboard and a terminal UI both read the two Redis feeds and names dYdX only, though Story 25.1a made rankings web-only and the collectors cover three venues. evidence: platform/frontend/src/pages/docs/kbData.ts line 9 ("A web dashboard and a terminal UI both read the same two Redis feeds"); unchanged by 25.2, stale since 25.1a (a046e0839a).
status: done 2026-10-05
resolution: resolved by sweep bundle dw-capture-script-and-docs
resolution-undo: 9077e58b03eea8f8c9d2570018bc9c06100b365969d0b85199174491807bf96f 2026-10-05 7374617475733a206f70656e

### DW-222: The bots' `fills.db` reads run synchronously on the TradingNode's one event loop -- every 30 s each bot's history refresh runs 4 ranges x 4 queries (two …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md` summary: The bots' `fills.db` reads run synchronously on the TradingNode's one event loop -- every 30 s each bot's history refresh runs 4 ranges x 4 queries (two full-history `pnl_by_day` scans per range) and every 5 s `win_rate_stats` -- under the lock the executor's fill writes hold, a cost that grows without bound with the file. evidence: platform/bots/application/history.py `refresh` and application/supervise.py `build_status` call the `SqliteFillsStore` directly; only `record_fill` is offloaded (`run_in_executor`). The same inline reads existed in live_paper/trade_history.py and bot_status.py at baseline 88abf70269.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-bots-persistence-and-host
resolution-undo: b98e299a1973c6cd760dd890fe03e85313515bcb9f8a9937413ae821024a54e1 2026-10-06 7374617475733a206f70656e

### DW-223: Per-fill realized PnL attribution loses its invariant across a restart or a flip -- the pending partial-close estimates live only in memory, so a close after a …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md` summary: Per-fill realized PnL attribution loses its invariant across a restart or a flip -- the pending partial-close estimates live only in memory, so a close after a restart records `total - 0` over estimates already in `fills.db` (double count), and a fill on the new entry side of a flipped position returns `(None, None)`, dropping the closed leg's round trip from win rate and PnL. evidence: platform/bots/domain/fill_ledger.py `FillLedger.attribute` (`_pending` in-process dict; `fill.order_side == position.entry` short-circuit), moved from live_paper/trade_history.py `_fill_pnl`/`_pending_realized_pnl` at baseline 88abf70269.
status: done 2026-10-07
resolution: resolved by sweep bundle dw-bots-realized-pnl-from-position-events
resolution-undo: 3206e8850d0aee0ad813276ab5ba09b37ad0ad6c8c2dfead773678f8c4a0c9ca 2026-10-07 7374617475733a206f70656e
decision: 2026-10-05 Derive realized PnL from Nautilus position events only — Replace estimate scheme with per-position-close attribution Operator: record each PositionClosed live as it fires; never read closed positions back from the Cache (NETTING overwrites them, cf. DW-225 / Story 4.6).

### DW-224: `fills.db` has no idempotency key (no `trade_id`, no unique constraint), so a re-delivered `OrderFilled` (e.g. exec-path reconciliation after a restart) …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md` summary: `fills.db` has no idempotency key (no `trade_id`, no unique constraint), so a re-delivered `OrderFilled` (e.g. exec-path reconciliation after a restart) appends a duplicate row that permanently inflates closed_trades, win rate and PnL in the append-only store. evidence: platform/bots/infrastructure/fills_store.py `_SCHEMA`/`write_fill` and domain/fill_ledger.py `FillRecord` (no trade id); identical schema in live_paper/fills_store.py at baseline 88abf70269.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-bots-persistence-and-host
resolution-undo: b98e299a1973c6cd760dd890fe03e85313515bcb9f8a9937413ae821024a54e1 2026-10-06 7374617475733a206f70656e

### DW-225: `bots:status.realized_pnl` sums `cache.positions_closed(strategy_id=...)`, which keeps only a NETTING position's latest round trip, so after any reopen the …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md` summary: `bots:status.realized_pnl` sums `cache.positions_closed(strategy_id=...)`, which keeps only a NETTING position's latest round trip, so after any reopen the status figure shows the current cycle only and disagrees with `bots:history` (from `fills.db`). evidence: platform/bots/infrastructure/cache_reader.py `positions()`; the Cache overwrite is documented in bots/domain/fill_ledger.py and was the reason Story 4.6 moved closed_trades/win_rate to fills.db. Same computation in live_paper/bot_status.py `build_status` at baseline 88abf70269; changing it changes a frozen payload value.
status: done 2026-10-07
resolution: resolved by sweep bundle dw-bots-realized-pnl-from-position-events
resolution-undo: 3206e8850d0aee0ad813276ab5ba09b37ad0ad6c8c2dfead773678f8c4a0c9ca 2026-10-07 7374617475733a206f70656e
decision: 2026-10-05 Source from fills.db — realized_pnl from fills.db total, consistent with bots:history

### DW-226: `DummyStrategy._maybe_trade` decides from `portfolio.is_flat/is_net_long/is_net_short(instrument_id)`, which are account-and-instrument wide, so two paper bots …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: portfolio.is_flat/is_net_long/is_net_short(instrument_id)
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md` summary: `DummyStrategy._maybe_trade` decides from `portfolio.is_flat/is_net_long/is_net_short(instrument_id)`, which are account-and-instrument wide, so two paper bots on one instrument (allowed by the config) read each other's positions -- an AD-11 violation the status path already avoids by strategy-scoped reads. evidence: platform/bots/strategies/dummy.py `_maybe_trade`, unchanged from live_paper/strategy.py at baseline 88abf70269; contrast bots/infrastructure/cache_reader.py (`positions_open(strategy_id=...)`).
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-bots-persistence-and-host
resolution-undo: b98e299a1973c6cd760dd890fe03e85313515bcb9f8a9937413ae821024a54e1 2026-10-06 7374617475733a206f70656e

### DW-227: bot_tui docstrings still cite the deleted `live_paper/bot_status.py`/`trade_history.py` and say "one live_paper process = one bot", and its …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: live_paper/bot_status.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md` summary: bot_tui docstrings still cite the deleted `live_paper/bot_status.py`/`trade_history.py` and say "one live_paper process = one bot", and its `STRATEGY_SOURCE_PATH` default is `/app/live_paper/strategy.py` (compose now mounts `bots/strategies/dummy.py` there); Story 25.3 was barred from touching bot_tui. evidence: platform/bot_tui/{bots_state.py:19,42, bots_pane.py:19,337, bot_history_state.py:20, bot_incidents_state.py:18, app.py:117-125}; platform/docker-compose.yml bot_tui strategy mount.
status: done 2026-10-05
resolution: already resolved: grep of platform/bot_tui finds no live_paper/bot_status/trade_history references; STRATEGY_SOURCE_DIR at platform/bot_tui/app.py:133

### DW-228: When Redis is unreachable at the one-shot incident-log seed, the bot starts from `[process_start]` alone and its first staleness transition `SET`s that short …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md` summary: When Redis is unreachable at the one-shot incident-log seed, the bot starts from `[process_start]` alone and its first staleness transition `SET`s that short list over the previous life's `bots:incidents:{bot_id}`, which was never read -- the prior incident history is lost whenever Redis and `live-paper` restart together. evidence: platform/bots/application/supervise.py `Supervisor.seed` (no retry; the fallback `self.bot.start([], ...)`) and `heartbeat_tick`'s whole-list `connection.set`; the same seed-once-then-overwrite flow in live_paper/bot_status.py `run` at baseline 88abf70269.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-bots-persistence-and-host
resolution-undo: b98e299a1973c6cd760dd890fe03e85313515bcb9f8a9937413ae821024a54e1 2026-10-06 7374617475733a206f70656e

### DW-229: `_cache_config` passes `urlparse(REDIS_URL).username/password` to the Nautilus Cache still percent-encoded, while redis-py's `from_url` (the `bots:*` bus) …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: urlparse(REDIS_URL).username/password
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md` summary: `_cache_config` passes `urlparse(REDIS_URL).username/password` to the Nautilus Cache still percent-encoded, while redis-py's `from_url` (the `bots:*` bus) decodes them, so a password containing an encoded character authenticates the bus but not the Cache. evidence: platform/bots/infrastructure/nautilus_host.py `_cache_config` (no `urllib.parse.unquote`); redis-py `parse_url` unquotes username/password. Same code in live_paper/node.py at baseline 88abf70269.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-bots-persistence-and-host
resolution-undo: b98e299a1973c6cd760dd890fe03e85313515bcb9f8a9937413ae821024a54e1 2026-10-06 7374617475733a206f70656e

### DW-230: `HistoryPublisher.record_fill` calls `FillLedger.attribute` outside any guard, so an exception there (e.g. `Position.calculate_pnl` on an unexpected fill) …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-3-bots-context-paper-and-exec-types-nautilus-acl.md` summary: `HistoryPublisher.record_fill` calls `FillLedger.attribute` outside any guard, so an exception there (e.g. `Position.calculate_pnl` on an unexpected fill) propagates into the strategy's message-bus dispatch and the fill is neither written nor ledgered as `bots.fill_lost`. evidence: platform/bots/application/history.py `record_fill` (only `_write` catches); the unguarded `_fill_pnl` call in live_paper/trade_history.py `_on_order_event` at baseline 88abf70269.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-bots-persistence-and-host
resolution-undo: b98e299a1973c6cd760dd890fe03e85313515bcb9f8a9937413ae821024a54e1 2026-10-06 7374617475733a206f70656e

### DW-231: A `collector:control` command arriving within `config_reload_seconds` (30 s) of a hand edit of the dYdX plan file overwrites that edit silently …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-4-collection-control-plan-intent-vs-applied-set.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-4-collection-control-plan-intent-vs-applied-set.md` summary: A `collector:control` command arriving within `config_reload_seconds` (30 s) of a hand edit of the dYdX plan file overwrites that edit silently -- `TomlPlanStore.save` re-reads the file but replaces the plan keys with the service's in-memory plan without checking the file still holds the plan last loaded or saved (no optimistic-concurrency check). evidence: platform/collection_control/infrastructure/plan_store.py `save` (re-read, `raw.update(plan_toml_fields(plan))`, no compare against the previous plan); the same last-writer-wins race existed in dydx_collector/config.py `save_config` at baseline c9fab9c5d7.
status: done 2026-10-08
resolution: resolved by sweep bundle dw-collection-control-plan-integrity
resolution-undo: eb2f16247fb68736ab7f5da11af445dd55533e1490425ccce68bfca42a482c6f 2026-10-08 7374617475733a206f70656e

### DW-232: `collector:control start` accepts any non-empty id -- a typo (`BTC-USD-PERP` without `.DYDX`) or another venue's id is saved to the dYdX plan file, holds one …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-4-collection-control-plan-intent-vs-applied-set.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-4-collection-control-plan-intent-vs-applied-set.md` summary: `collector:control start` accepts any non-empty id -- a typo (`BTC-USD-PERP` without `.DYDX`) or another venue's id is saved to the dYdX plan file, holds one of the 30 slots and stays `pending` across restarts until someone removes it, although capture already knows the venue's listed markets (`Collector._listed`) and could refuse it at command time. evidence: platform/collection_control/domain/plan.py `CollectionPlan.add` and application/control.py `_command` (no listed-market check); the same unchecked append existed in dydx_collector/collector.py `_handle_control_message` at baseline c9fab9c5d7 (it then subscribed and raised on the wire).
status: done 2026-10-08
resolution: resolved by sweep bundle dw-collection-control-plan-integrity
resolution-undo: eb2f16247fb68736ab7f5da11af445dd55533e1490425ccce68bfca42a482c6f 2026-10-08 7374617475733a206f70656e

### DW-233: Turning `store_order_book_deltas` on for an instrument that is already collected (a hand edit picked up by the reload) starts its raw `OrderBookDeltas` archive …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-25-4-collection-control-plan-intent-vs-applied-set.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-4-collection-control-plan-intent-vs-applied-set.md` summary: Turning `store_order_book_deltas` on for an instrument that is already collected (a hand edit picked up by the reload) starts its raw `OrderBookDeltas` archive mid-stream, with no Clear + snapshot at its head, so a backtest cannot rebuild that instrument's book from the archived deltas until the next reconnect or resync. evidence: platform/dydx_collector/collector.py `DydxCollector.apply` (swaps `_delta_store`, forces no resync for newly stored ids) and `_apply_deltas` (buffers from the next message); the same swap without a resync existed in dydx_collector/collector.py `_apply_config` at baseline c9fab9c5d7 (line 342).
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-capture-service-lifecycle
resolution-undo: 0048a9d04f44fc28ce1ff055a57d3d109c101be1016932f3bf35d6b4fc4e6436 2026-10-06 7374617475733a206f70656e

### DW-234: Four pub/sub subscribers use a bare `pubsub.listen()` with no liveness check, so a half-open Redis connection leaves them frozen until their process restarts. …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: bot_tui/collector_state.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-25-1b-archive-service-nightly-maintenance-scheduled-no-host-cron.md` summary: Four pub/sub subscribers use a bare `pubsub.listen()` with no liveness check, so a half-open Redis connection leaves them frozen until their process restarts. They are `views.rankings_bus.RankingsBus.run`, `bot_tui/collector_state.py`, `bot_tui/bots_state.py` and `bot_tui/bot_history_state.py`. evidence: Story 25.1b's review found this pattern in the new archive:status readers. They were fixed with a heartbeat-silence resubscribe (`views/archive_status_bus.py` `_receive`, `bot_tui/archive_state.py` `_receive`). The pre-existing subscribers keep the bare `async for message in pubsub.listen()` loop, which only reconnects on a raised error, and a silently dropped TCP connection raises none.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-live-channel-hardening
resolution-undo: 9218902ccb75097d93a8b92f08359dcf9efaeb5e4be78a0192c010aee5086dda 2026-10-05 7374617475733a206f70656e

### DW-235: A reconnect backfill that admits more unseen trades than the `seen_trade_ids` window (2000 by default; dYdX can page up to 20 x 1000 rows) evicts …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-26-1-livebook-tradeintake-feedgroup-pure-secondsampler-in-place.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-26-1-livebook-tradeintake-feedgroup-pure-secondsampler-in-place.md` summary: A reconnect backfill that admits more unseen trades than the `seen_trade_ids` window (2000 by default; dYdX can page up to 20 x 1000 rows) evicts, oldest-first, the ids of the post-gap trades the live feed had already archived, so when those newest REST rows are reached they pass the dedup check and are archived a second time. evidence: platform/collector_core/application/trade_backfill.py `admit_backfill` checks `intake.first_feed` and `intake.register`s one trade at a time into the bounded FIFO window (`TradeIntake`); the same per-trade check-then-register existed in `Collector._apply_backfill` at baseline aae75c5707.
status: done 2026-10-05
resolution: already resolved: platform/capture/domain/trade_intake.py:37-52,87,249 dedup window now evicts only past DEDUP_HORIZON_NS, not a fixed count

### DW-236: `frontend/scripts/gen-api-types.mjs` emits a string `enum` (e.g. `ArchiveStatusResponse.backup: "enabled" | "disabled"`) as plain `string`, so a mistyped …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: frontend/scripts/gen-api-types.mjs
reason: source_spec: `_bmad-output/implementation-artifacts/spec-26-1b-offsite-backup-explicit-setting-off-until-storage-exists.md` summary: `frontend/scripts/gen-api-types.mjs` emits a string `enum` (e.g. `ArchiveStatusResponse.backup: "enabled" | "disabled"`) as plain `string`, so a mistyped literal comparison in the frontend type-checks and silently never matches. evidence: `frontend/openapi.json` carries `"enum": ["enabled", "disabled"]` for `backup`, while the regenerated `frontend/src/api/schema.ts` has `backup?: string | null`; the generator has no enum branch (pre-existing, affects every Literal field).
status: open

### DW-237: `CaptureService.poll_loop` sleeps one full period (`open_interest_poll_seconds`, 300 s) before its first fetch, so a collector crash-looping faster than that …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-26-2-capture-package-and-venue-packages-with-entrypoints.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-26-2-capture-package-and-venue-packages-with-entrypoints.md` summary: `CaptureService.poll_loop` sleeps one full period (`open_interest_poll_seconds`, 300 s) before its first fetch, so a collector crash-looping faster than that (`run_forever` backs off at most 60 s) never records open interest, and nothing is ledgered; the gap shows only in the data. evidence: platform/capture/application/capture_service.py `poll_loop` (`await asyncio.sleep(every_seconds)` precedes the first `fetch()`, now marked Known limit); the same sleep-first order in dydx_collector/collector.py and bybit_collector/collector.py `_open_interest_loop` at baseline 7fbdb4fe76.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-capture-service-lifecycle
resolution-undo: 0048a9d04f44fc28ce1ff055a57d3d109c101be1016932f3bf35d6b4fc4e6436 2026-10-06 7374617475733a206f70656e

### DW-238: `platform/tests/test_skew_constants.py` checks dYdX's `hold_back_seconds` against `READ_SPAN_MARGIN_NS` by reading the committed 0-byte placeholder …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: platform/tests/test_skew_constants.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-26-2-capture-package-and-venue-packages-with-entrypoints.md` summary: `platform/tests/test_skew_constants.py` checks dYdX's `hold_back_seconds` against `READ_SPAN_MARGIN_NS` by reading the committed 0-byte placeholder `dydx_collector/config.toml`, so the operator's real plan (`data/dydx_config.toml`) never enters the read-margin check; only the runtime `_check_skew_budget` guards it. evidence: platform/tests/test_skew_constants.py `_VENUE_CONFIGS` dYdX entry; the same placeholder path at baseline 7fbdb4fe76.
status: open

### DW-239: The in-app docs (`frontend/src/pages/docs/kbData.ts`) show `bar_intervals = ["1-MINUTE"]` under the dYdX `[[instruments]]` example, a key the strict loader …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: frontend/src/pages/docs/kbData.ts
reason: source_spec: `_bmad-output/implementation-artifacts/spec-26-2-capture-package-and-venue-packages-with-entrypoints.md` summary: The in-app docs (`frontend/src/pages/docs/kbData.ts`) show `bar_intervals = ["1-MINUTE"]` under the dYdX `[[instruments]]` example, a key the strict loader rejects (`_DYDX_ENTRY_KEYS` is id/store_order_book_deltas/retain_hours), so an operator copying it gets a collector that refuses to start. evidence: platform/frontend/src/pages/docs/kbData.ts line 50; identical at baseline 7fbdb4fe76; loader in platform/capture/infrastructure/config.py.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-capture-script-and-docs
resolution-undo: 9077e58b03eea8f8c9d2570018bc9c06100b365969d0b85199174491807bf96f 2026-10-05 7374617475733a206f70656e

### DW-240: `archive/tools/measure_lag.py` `_default_instruments` reads fixed in-tree paths: for dYdX inside a container that is the bind-mounted live plan whose …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: archive/tools/measure_lag.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-26-2-capture-package-and-venue-packages-with-entrypoints.md` summary: `archive/tools/measure_lag.py` `_default_instruments` reads fixed in-tree paths: for dYdX inside a container that is the bind-mounted live plan whose `[[instruments]]` are tables (so bare `--venue dydx` yields dicts as ids), and for Bybit/Hyperliquid it ignores `BYBIT_COLLECTOR_CONFIG`/`HYPERLIQUID_COLLECTOR_CONFIG`. evidence: platform/archive/tools/measure_lag.py `_default_instruments` (`tomllib.load(f).get("instruments", [])` returned as-is); same logic at baseline 7fbdb4fe76.
status: done 2026-10-08
resolution: resolved by sweep bundle dw-measure-lag-venue-config-env
resolution-undo: 0239a31cfe5d8d37a50619a5cbb53c6a13a11c3352994d485e4d6d76fe2d584a 2026-10-08 7374617475733a206f70656e

### DW-241: `run_forever` calls `build()` outside its try/backoff block, so a build failure on a restart attempt (config load, candle store, Redis, control plane) escapes …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-26-2-capture-package-and-venue-packages-with-entrypoints.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-26-2-capture-package-and-venue-packages-with-entrypoints.md` summary: `run_forever` calls `build()` outside its try/backoff block, so a build failure on a restart attempt (config load, candle store, Redis, control plane) escapes and exits the process instead of backing off; only compose's `restart: always` recovers it. evidence: platform/capture/application/capture_service.py `run_forever` (`collector = build()` precedes the `try`); same structure in collector_core/collector.py at baseline 7fbdb4fe76.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-capture-service-lifecycle
resolution-undo: 0048a9d04f44fc28ce1ff055a57d3d109c101be1016932f3bf35d6b4fc4e6436 2026-10-06 7374617475733a206f70656e

### DW-242: `bot_tui/app.py` `_MAX_COLLECTED_INSTRUMENTS = 29`, whose comment says it must match dYdX's `DYDX_MAX_COLLECTED_INSTRUMENTS = 30`, so the TUI refuses a `start` …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: bot_tui/app.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-26-2-capture-package-and-venue-packages-with-entrypoints.md` summary: `bot_tui/app.py` `_MAX_COLLECTED_INSTRUMENTS = 29`, whose comment says it must match dYdX's `DYDX_MAX_COLLECTED_INSTRUMENTS = 30`, so the TUI refuses a `start` one instrument short of the collector's real cap. evidence: platform/bot_tui/app.py line 118 against platform/capture/venues/dydx/config.py line 47; at baseline 7fbdb4fe76 the comment cited a `dydx_collector/collector.py` `_MAX_COLLECTED_INSTRUMENTS` that did not exist, and the value was already 29.
status: done 2026-10-05
resolution: already resolved: no _MAX_COLLECTED_INSTRUMENTS remains in platform/bot_tui (grep empty); dYdX cap at platform/capture/venues/dydx/config.py:45

### DW-243: `core_config_from_dict` accepts TOML `nan`/`inf` for its float thresholds (`nan <= 0` is False, so a `nan` `stale_book_seconds` disables the stale gate and …

origin: migrated from legacy ledger ("Deferred from: code review of story 22.1 (2026-09-20)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-26-2-capture-package-and-venue-packages-with-entrypoints.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-26-2-capture-package-and-venue-packages-with-entrypoints.md` summary: `core_config_from_dict` accepts TOML `nan`/`inf` for its float thresholds (`nan <= 0` is False, so a `nan` `stale_book_seconds` disables the stale gate and `inf` overflows `int(x * 1e9)`), and silently truncates a float or bool `flush_interval_seconds`/`seen_trade_ids` through `int()`. evidence: platform/capture/application/config.py `core_config_from_dict` (`float(raw.get(...))`, `int(raw.get(...))`, then `<= 0` checks only); the same code in collector_core/config.py at baseline 7fbdb4fe76.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-capture-service-lifecycle
resolution-undo: 0048a9d04f44fc28ce1ff055a57d3d109c101be1016932f3bf35d6b4fc4e6436 2026-10-06 7374617475733a206f70656e

### DW-244: The platform images install `pandas==3.0.4` (`platform/requirements.txt:2`) over nautilus_trader 1.229.0's own `pandas>=2.3.3,<3.0.0` (`pyproject.toml:31` …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: platform/requirements.txt:2
reason: source_spec: `_bmad-output/implementation-artifacts/spec-26-3-closeout-shims-gone-spines-reconciled.md` summary: The platform images install `pandas==3.0.4` (`platform/requirements.txt:2`) over nautilus_trader 1.229.0's own `pandas>=2.3.3,<3.0.0` (`pyproject.toml:31`, `uv.lock` 2.3.3), so `pip check` fails in the collector image and host-side test runs (pandas 2.3.3) never exercise the pandas major version production runs. Resolve by pinning `pandas==2.3.3` or by proving pandas 3.x on the catalog read/backtest path; recorded as a DDD spine Deferred entry. evidence: `pip check` inside `platform-collector:latest` built 2026-09-28: "nautilus-trader 1.229.0 has requirement pandas<3.0.0 … but you have pandas 3.0.4" (reviews/review-versions-2026-09-28.md H-1); predates 26.3, which changed no dependency.
status: done 2026-10-08
resolution: resolved by sweep bundle dw-pandas-3-prove-and-document
resolution-undo: 597fcc352f16d793125065514dfcd167cd67ab3e0aafba8780a49ccec359251f 2026-10-08 7374617475733a206f70656e
decision: 2026-10-05 Prove 3.x — Run catalog read/backtest suite on pandas 3.x and document override

### DW-245: `platform/views/tests/test_live_candles.py::test_seed_wide_bar_reads_raw_seconds_plus_unflushed_tail` is wall-clock flaky -- it floors `time.time_ns()` to a …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: platform/views/tests/test_live_candles.py::test_seed_wide_bar_reads_raw_seconds_plus_unflushed_tail
reason: source_spec: `_bmad-output/implementation-artifacts/spec-27-1-research-domain-analysis-values-and-application-ports.md` summary: `platform/views/tests/test_live_candles.py::test_seed_wide_bar_reads_raw_seconds_plus_unflushed_tail` is wall-clock flaky -- it floors `time.time_ns()` to a bucket and then feeds a snapshot at `now - 1s`, which falls in the previous bucket whenever the test runs within the first second of a bucket. evidence: failed once in the 27.1 follow-up review's full-suite run (2026-09-28), then passed 3/3 in isolation; the test reads `time.time_ns()` at line 233 and builds `_snapshot(now_ns - 1_000_000_000, ...)` at line 248; `views` imports nothing 27.1 changed.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-lint-and-test-hygiene
resolution-undo: 36635e4be7c38ae86fb67ecab9a11a98778641ab03dc21803191ad05a7466469 2026-10-05 7374617475733a206f70656e

### DW-246: `research/application/quotes.py`'s `derived_quotes` (moved unchanged out of `snapshot_backtest.py`) builds each `QuoteTick` from level 0 without checking `bid …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: research/application/quotes.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-27-1-research-domain-analysis-values-and-application-ports.md` summary: `research/application/quotes.py`'s `derived_quotes` (moved unchanged out of `snapshot_backtest.py`) builds each `QuoteTick` from level 0 without checking `bid < ask`, so a crossed or locked dYdX second (DATA-04) replays as a crossed quote and the simulated exchange can fill against it (e.g. a market buy at an ask below the bid books a profit the venue never offered). evidence: `derived_quotes` reads `bid_prices[0]`/`ask_prices[0]` and constructs the tick with no crossed-book branch; dYdX books cross by design (platform/CLAUDE.md DATA-04) and the live gate writes crossed seconds (DATA-01, Story 24.2); the derivation predates 27.1, which only relocated it.
status: open
decision: 2026-10-05 Keep open: dYdX-deferred — Operator 2026-10-05: dYdX work is deferred in general; this stays open as a recorded dYdX issue, not built.

### DW-247: `research/strategies/snapshot_backtest.py`'s `run` is a second backtest path with semantics `NodeRunner` has since corrected -- no `MAX_TS_INIT_SKEW_NS` …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: research/strategies/snapshot_backtest.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-27-1-research-domain-analysis-values-and-application-ports.md` summary: `research/strategies/snapshot_backtest.py`'s `run` is a second backtest path with semantics `NodeRunner` has since corrected -- no `MAX_TS_INIT_SKEW_NS` widening of the derived tops, an unbounded quote data config, an inclusive `end`, `params` spread after `instrument_id` (overridable), and its result taken as `node.run()[0]` (list position) -- while NAUT-03 now routes notebooks through `NodeRunner` only; port its callers to `NodeRunner` and retire it, or align it. evidence: platform/research/strategies/snapshot_backtest.py `run` vs platform/research/application/backtest_runner.py (`end_ns - 1`, skew-widened quotes, `RESERVED_PARAMS`, `get_engine(config_id)`); 27.1's task list kept `snapshot_backtest` behaviour-unchanged on purpose.
status: open
decision: 2026-10-05 Port callers to NodeRunner and delete snapshot_backtest — Retire the second path (NAUT-03)

### DW-248: `research/strategies/ofi_strategy.py`'s `OFIStrategy.on_data` evaluates its carried pre-gap OFI z-score on the first row after a gap over `MAX_GAP_NS` …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: research/strategies/ofi_strategy.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-27-3-microstructure-notebook.md` summary: `research/strategies/ofi_strategy.py`'s `OFIStrategy.on_data` evaluates its carried pre-gap OFI z-score on the first row after a gap over `MAX_GAP_NS`: `clear_prev_state()` makes `MultiLevelOFI.update_raw` return without a new reading, yet `self._ofi.initialized` stays True, so `_evaluate` can enter on a signal from before the outage (and stores it as `_prev_ofi`). evidence: `on_data` calls `clear_prev_state()`, then `update_raw` (which returns early when `_prev_bid_prices is None`, leaving `value` untouched), then `_evaluate(self._ofi.value, ...)` gated only on `initialized` and warm-up; predates 27.3, which only renamed `_MAX_GAP_NS`; 27.3's replay shows that row as NaN and documents the divergence as a `Known limit:`.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-research-test-and-path-fixes
resolution-undo: 89c508f4086e6130fe63c3b8c7df6e501f431e414611a6838c2e01ff0641c80a 2026-10-05 7374617475733a206f70656e

### DW-249: `PUT /api/rankings/technicals-columns` (and the chart-indicator PUT) validate only indicator names, never enum param values, so a saved column with `"pattern" …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: PUT /api/rankings/technicals-columns
reason: source_spec: `_bmad-output/implementation-artifacts/spec-27-7-candlestick-pattern-detector-kernel-chart-screener-scanner.md` summary: `PUT /api/rankings/technicals-columns` (and the chart-indicator PUT) validate only indicator names, never enum param values, so a saved column with `"pattern": "hammer"` or an unknown `ma_type` is accepted with 200 and then makes every `technicals-values` GET return 400, blanking the whole Technicals tab until the file is hand-edited. evidence: `data_api/routes/rankings.py` PUT checks `_require_known_indicators` names only; `views.indicator_picker._resolve_enum_params` raises on the bad name only at replay time; predates 27.7 (`ma_type`/`price_type` had the same gap), 27.7's `choices` now makes the valid set available for server-side validation.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-data-api-input-validation
resolution-undo: 91b47c52cc0253ee68fc7a1fe5a9d3fd9432e6964681548c0f72e9cd0679be68 2026-10-05 7374617475733a206f70656e

### DW-250: The chart and Technicals replay (`views.indicator_picker.replay_native` over `queries.window`) feeds only traded candles, so an untraded or missing bucket is …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-27-7-candlestick-pattern-detector-kernel-chart-screener-scanner.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-27-7-candlestick-pattern-detector-kernel-chart-screener-scanner.md` summary: The chart and Technicals replay (`views.indicator_picker.replay_native` over `queries.window`) feeds only traded candles, so an untraded or missing bucket is skipped and multi-bar indicators (now including two/three-bar candlestick patterns) compare non-adjacent bars across the hole. evidence: `candles` `window` returns rows with `o IS NOT NULL` only and the replay loop has no bucket-adjacency check; the scanner notebook resets at holes (`research/application/patterns.py`'s `scan`) but the spec forbids a pattern special case in `replay_native`, so a hole-aware replay is a cross-indicator change for its own story.
status: open
decision: 2026-10-05 Own story: reset indicators at bucket holes — Hole-aware replay in views.indicator_picker.replay_native

### DW-251: The picker's `price_type` param (now a dropdown of BID/ASK/MID/LAST/MARK/...) is ignored by the replay, which always feeds the candle close, so choosing BID …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-27-7-candlestick-pattern-detector-kernel-chart-screener-scanner.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-27-7-candlestick-pattern-detector-kernel-chart-screener-scanner.md` summary: The picker's `price_type` param (now a dropdown of BID/ASK/MID/LAST/MARK/...) is ignored by the replay, which always feeds the candle close, so choosing BID for an SMA silently shows the close SMA. evidence: `views/indicator_picker.py` `_feed_values` maps feed fields to candle keys only (`close`), never reading `price_type`; the param predates 27.7, whose `choices` only made the unsupported options visible.
status: open
decision: 2026-10-05 Feed the chosen price series — Needs bid/ask/mark data in candles

### DW-252: The chart and Technicals replay (`views.indicator_picker.replay_native` via `views.chart_series`/`views.ranking_columns._recent_candles`) feeds candles flagged …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: research/application/patterns.bar_grid
reason: source_spec: `_bmad-output/implementation-artifacts/spec-27-7-candlestick-pattern-detector-kernel-chart-screener-scanner.md` summary: The chart and Technicals replay (`views.indicator_picker.replay_native` via `views.chart_series`/`views.ranking_columns._recent_candles`) feeds candles flagged `partial` (under 90% of the span observed) as if they were the bucket's real OHLC, so after a capture outage a truncated bar can read as a DOJI/HARAMI on the chart and screener while the scanner (`research/application/patterns.bar_grid`) blanks and resets on the same bar. evidence: `research/application/patterns.py` turns a `partial` row into a NaN row (27.7 Spec Change Log), but the replay loop has no `partial` check and the spec forbids a pattern special case in `replay_native`; distinct from the untraded-bucket hole entry above because the partial bar is present, just truncated. Affects every native indicator, so it belongs with the hole-aware replay story.
status: open
decision: 2026-10-05 Bundle into hole-aware replay story — Treat partial bars as NaN/reset in replay_native Operator rule: if a bar is not 100% true it cannot be used for anything -- any bar not fully observed (not only below the 90% `partial` threshold) is a hole for the replay AND the scanner; the story must revisit the 90% partial threshold.

### DW-253: Delete the `platform/research/BACKTESTING.md` redirect stub in the first story of the next research epic, and re-point any link that still names it at …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: platform/research/BACKTESTING.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-27-9-closeout-research-readme-rules-legacy-notebooks-gone.md` summary: Delete the `platform/research/BACKTESTING.md` redirect stub in the first story of the next research epic, and re-point any link that still names it at `research/README.md`. evidence: Story 27.9 moved the content into `research/README.md` ("Backtesting & Strategy Development") and left a stub for one release that promises this deletion; nothing else tracks the promise.
status: done 2026-10-05
resolution: already resolved: platform/research/BACKTESTING.md no longer exists and no non-archive file links it

### DW-254: `platform/ranking/tests/test_metrics_store.py::test_price_near_days_ago_returns_price_at_or_before_target_per_instrument` is wall-clock flaky: `_NOW = …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: platform/ranking/tests/test_metrics_store.py::test_price_near_days_ago_returns_price_at_or_before_target_per_instrument
reason: source_spec: `_bmad-output/implementation-artifacts/spec-29-1-exchange-symbol-columns-and-mode-toggle-on-web-rankings.md` summary: `platform/ranking/tests/test_metrics_store.py::test_price_near_days_ago_returns_price_at_or_before_target_per_instrument` is wall-clock flaky: `_NOW = time.time_ns()` is taken at import while `price_near_days_ago(7)` reads the real clock at call time, so when more than ~5 s pass between collection and the call (a slow full-suite run), the ETH row at `_NOW - week - 5 s` falls after the target and the assertion fails. evidence: 2026-09-28, full `make test`-equivalent run: that test failed in the full suite and passed alone (22/22) and with `ranking/tests data_api/tests`; neither the test nor `ranking/infrastructure/metrics_store.py` is touched by Story 29.1. Fix: inject a clock into `SqliteMetricsStore.price_near_days_ago` (or freeze it in the test).
status: done 2026-10-05
resolution: already resolved: platform/ranking/tests/test_metrics_store.py:278 stamps rows from time.time_ns() at call time (fixed in 31-3, f7c9e3ea38)

### DW-255: `bot_tui`'s Collector pane still sends `collector:control` commands for a venue whose collector is gone: `bot_tui/collector_state.py`'s `command_refusal` looks …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: bot_tui/collector_state.py
reason: source_spec: `_bmad-output/implementation-artifacts/spec-29-3-venue-cutover-bybit-hyperliquid-proven-then-dydx-stopped.md` summary: `bot_tui`'s Collector pane still sends `collector:control` commands for a venue whose collector is gone: `bot_tui/collector_state.py`'s `command_refusal` looks only at the last cached `collector:status` plan (`accepts_commands`), never at `plan_is_stale(venue)`, so after `make down-dydx` (or a crashed dYdX collector) a pin/start on a DYDX row is published with no consumer and no error shown (DATA-07). evidence: `command_refusal` reads `_LATEST_PLANS.get(venue)` and returns None for a dYdX plan with `accepts_commands` whatever its age, and `plan_is_stale` is not consulted; predates 29.3, which only documents the behaviour in DEPLOY_CHECKLIST §8 check 6. Story 29.4 (venue field on `collector:control`) is the natural home for the fix. **Resolved in Story 29.4** (partly, by design): `command_refusal(venue)` now refuses, before anything is published, (a) every venue with no aggregate cached, dYdX included (`waiting for <VENUE> plan on collector:status`: a TUI started after `make down-dydx`), and (b) a venue whose last aggregate is older than `_STATUS_STALE_SECONDS` (3600 s: `<VENUE>: no collector:status for over 60 min (collector down?)`). Still open, as a `Known limit:` in `command_refusal`: a collector stopped less than an hour ago keeps getting commands with no error shown, because aggregates republish only every 1800 s; upgrade path: a faster aggregate heartbeat. Tests: `bot_tui/tests/test_app_collector.py`'s `test_a_plan_whose_status_went_stale_refuses_commands` and `test_a_venue_with_no_aggregate_cached_refuses_commands_even_for_dydx`.
status: open

### DW-256: `collection_control`'s `ControlService` accepts a `start` for any id carrying the plan's venue suffix, whether or not the venue lists it or the client can …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-29-4-runtime-collection-control-bybit-hyperliquid.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-29-4-runtime-collection-control-bybit-hyperliquid.md` summary: `collection_control`'s `ControlService` accepts a `start` for any id carrying the plan's venue suffix, whether or not the venue lists it or the client can subscribe it (e.g. `BTCUSD-INVERSE.BYBIT`, a Hyperliquid spot id, a typo), saves it to the plan file, and capture then keeps it `pending` forever with one "not listed on the venue ... never retried" ledger entry; the id survives restarts, and a market listed later is never retried until a restart. evidence: `ControlService._command` checks only `venue_of(id) == plan.venue` (and the domain's cap/exclude rules); `CaptureService._subscribe_added` (`capture/application/capture_service.py`) ledgers and returns False for an id outside `self._listed`, and `_subscription_retry_loop` retries only failed subscribes of listed ids. The same path predates 29.4 for dYdX; 29.4 exposes it on Bybit/Hyperliquid. Fix: a `listed` query on the `Capture` port, refused at command time; Story 29.5's market browser reduces but does not remove it (`:start` stays typed).
status: done 2026-10-08
resolution: resolved by sweep bundle dw-collection-control-plan-integrity
resolution-undo: eb2f16247fb68736ab7f5da11af445dd55533e1490425ccce68bfca42a482c6f 2026-10-08 7374617475733a206f70656e

### DW-257: `bot_tui`'s collector actions report `sent: <action> <id>` even when the publish failed: `collector_state.publish_control` only logs a WARNING on a Redis error …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-29-5-market-browser-search-by-name-and-add-in-collector-pane.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-29-5-market-browser-search-by-name-and-add-in-collector-pane.md` summary: `bot_tui`'s collector actions report `sent: <action> <id>` even when the publish failed: `collector_state.publish_control` only logs a WARNING on a Redis error and returns nothing, and `_publish_collector_action` sets the footer before its fire-and-forget task runs; for a market-browser add the row then reads `pending` and `a` is refused for up to `ADD_ANSWER_TIMEOUT_SECONDS` (120 s) although nothing was sent, before it reads `no answer from <VENUE> collector`. evidence: `publish_control` swallows the exception (`logger.warning("failed to publish collector:control ...")`), shared by `p`/`x`/`:start`/`:pintop` since Story 6.1; 29.5 only adds the sent-add registry on top. Fix: return whether the publish succeeded (and its receiver count, 0 meaning no collector subscribed), set the footer from the task's result, and drop the sent add on failure.
status: done 2026-10-05
resolution: already resolved: platform/bot_tui/collector_state.py:302-325 publish_control returns bool; platform/bot_tui/app.py:1165-1168 sets 'failed to send' footer on False

### DW-258: `bot_tui`'s Collector-pane and Bots-pane help both advertise `j/k, up/down move selection`, but only up/down work there: no urwid `command_map` entry or pane …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: j/k, up/down move selection
reason: source_spec: `_bmad-output/implementation-artifacts/spec-29-5-market-browser-search-by-name-and-add-in-collector-pane.md` summary: `bot_tui`'s Collector-pane and Bots-pane help both advertise `j/k, up/down move selection`, but only up/down work there: no urwid `command_map` entry or pane handler maps `j`/`k`, and the rows' `keypress` passes keys through unchanged. evidence: `bot_tui/app.py`'s HELP text (Bots `j/k` line ~149, Collector ~174); `_handle_bots_pane_key`/`_handle_collector_pane_key` handle only `enter`/`s` and `p`/`x`/`/`; Story 29.5's follow-up review added `_VimListBox` for the market browser only. Fix: use `_VimListBox` for those panes' ListBoxes too (or map `j`/`k` in urwid's `command_map`), with a test per pane.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-bot-tui-robustness
resolution-undo: ddee5d273c5815dadfe57f0176738c0b9a9a64ff679763c9b494d5992cf1f027 2026-10-05 7374617475733a206f70656e

### DW-259: On the live dYdX feed, the `live-paper` node's Cache L2 order book for `BTC-USD-PERP.DYDX` reported a best bid about 2% below the real market while quotes and …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-29-6-bots-pane-take-profit-stop-loss-and-position-details.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-29-6-bots-pane-take-profit-stop-loss-and-position-details.md` summary: On the live dYdX feed, the `live-paper` node's Cache L2 order book for `BTC-USD-PERP.DYDX` reported a best bid about 2% below the real market while quotes and Sandbox fills agreed with each other. Anything reading that book is skewed: `DummyStrategy`'s MultiLevelOBI/OFI inputs (`on_timer` reads `cache.order_book`) and a `BID_ASK` emulation trigger. The root cause is untraced (DATA-02). evidence: 2026-09-29, third `make bots-churn-check` run (Story 29.6): an emulated stop at 83,792 released at a Cache-book "bid" of 82,133 and filled at 83,832 milliseconds after the entry. 29.6 switched its exits to `TriggerType.LAST_PRICE` to stay off that book (`bots/strategies/exits.py` docstring) and did not investigate the book itself. Same shape on the final run (07:07:25 UTC): a 0.0001 BTC market BUY filled by the Sandbox (L1_MBP, `bar_execution`/`trade_execution` on by default) at 84,037 while the last quote's mid was 83,979.5, about 7 bps, against a real spread of about $1. Candidate loci: the Sandbox L1 book being moved by bar/trade execution next to the quotes; the node's dYdX data client book-delta handling (snapshot/CLEAR replay, the crossed-book class in platform/.planning/debug/crossed-book-root-cause.md) versus the quote stream.
status: open
decision: 2026-10-05 Open an investigation story — Trace root cause in live-paper book handling Operator: first check whether the Cache L2 skew reproduces on Hyperliquid and Bybit books (it may affect them, not only dYdX); root cause per DATA-02.

### DW-260: `archive.repair_catalog --apply` writes its replacement snapshot files through Nautilus's `write_data` without `kernel.parquet_compat.apply_zstd_default()` …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-30-1-compact-parquet-encoding-for-consolidated-and-rewritten-files.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-30-1-compact-parquet-encoding-for-consolidated-and-rewritten-files.md` summary: `archive.repair_catalog --apply` writes its replacement snapshot files through Nautilus's `write_data` without `kernel.parquet_compat.apply_zstd_default()` ever running in that process, so they land snappy-compressed, contrary to `parquet_compat`'s "the writers make it zstd" contract. evidence: `archive/application/repair.py:148` calls `catalog.write_data([cleared])`; neither `repair.py` nor `archive/repair_catalog.py` imports or calls `apply_zstd_default` (only `capture/infrastructure/parquet_writer.py` and `archive/application/backfill_bars.py` do), and before Story 30.1 the only other call sat inside `CatalogFiles`' rewrite, which the repair path never reaches before its `write_data`. Pre-existing; such a file stays snappy until a consolidation merge or an `archive.tools.recompress --apply` run re-encodes it.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-archive-prune-repair-safety
resolution-undo: dcc0700427eddf493cae780ae257b14adaad477dcd1d81d72caf42af892bf694 2026-10-05 7374617475733a206f70656e

### DW-261: The "Price" label means the slow loop's trade close on the history page (metrics.db `price`) but the live mid on the rankings page.

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-31-3-derived-signals-against-independent-reference-implementations.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-31-3-derived-signals-against-independent-reference-implementations.md` summary: The "Price" label means the slow loop's trade close on the history page (metrics.db `price`) but the live mid on the rankings page. evidence: `ranking/application/engine.py` slow pass persists the `price_stats_from_series` trade close, while `RankingBoard.fast_metrics` publishes `price` = mid (DATA_DICTIONARY §3.3/§3.4). The mismatch was already there before Story 31.3; the 31.3 review surfaced it.
status: open
decision: 2026-10-05 Rename labels (Close vs Mid) — Distinct labels on both pages

### DW-262: `research/tests/test_backtest_runner.py::test_an_order_fills_at_the_top_of_book_after_its_latency` (5 parametrizations) errors with `TypeError …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: research/tests/test_backtest_runner.py::test_an_order_fills_at_the_top_of_book_after_its_latency
reason: source_spec: `_bmad-output/implementation-artifacts/spec-31-3-derived-signals-against-independent-reference-implementations.md` summary: `research/tests/test_backtest_runner.py::test_an_order_fills_at_the_top_of_book_after_its_latency` (5 parametrizations) errors with `TypeError: DydxSecondSnapshot.__init__() missing 2 required positional arguments`: its fixture builder at `:427` still calls the pre-Story-30.2 constructor. evidence: the same `DydxSecondSnapshot(` call is at line 426 of the baseline revision 5e324bbb8e, and the 5 errors show in both 31.3 review passes' full pytest runs, so the fixture was never updated for 30.2's precision arguments. It is pre-existing and not caused by 31.3.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-research-test-and-path-fixes
resolution-undo: 89c508f4086e6130fe63c3b8c7df6e501f431e414611a6838c2e01ff0641c80a 2026-10-05 7374617475733a206f70656e

### DW-263: Bybit prints BTCUSDT spot trades below the instrument's 0.1 tick (e.g. `84528.67`), and capture archives them rounded (`84528.7`) with no trace, so the stored …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-31-4-trades-proven-id-by-id-against-the-venue.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-31-4-trades-proven-id-by-id-against-the-venue.md` summary: Bybit prints BTCUSDT spot trades below the instrument's 0.1 tick (e.g. `84528.67`), and capture archives them rounded (`84528.7`) with no trace, so the stored trade price and that second's OHLC are not what the venue published (audit D-91, OPEN). evidence: `verification.trades` on the 2026-09-29 13:00-15:00Z soak: `BTCUSDT-SPOT` `mismatch_price` 8 of 131,534 and 3 `off_grid` seconds. The rounding is in `crates/adapters/bybit/src/common/parse.rs` `parse_price_with_precision` (f64, then `Price::new_checked` at the instrument precision). FORK-01 forbids a fix there, so a follow-up story must keep the exact venue price in capture, or ledger every rounding.
status: open

### DW-264: A collector that has never written a coverage line has no `coverage/<venue>.jsonl`, so `verification.conservation` and `verification.trades` fail a clean day …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-31-4-trades-proven-id-by-id-against-the-venue.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-31-4-trades-proven-id-by-id-against-the-venue.md` summary: A collector that has never written a coverage line has no `coverage/<venue>.jsonl`, so `verification.conservation` and `verification.trades` fail a clean day as "coverage record MISSING" (audit D-92, OPEN). Capture should create and fsync the file at start. evidence: `CaptureService._write_coverage` returns on an empty flush (`if not lines: return`). On the soak, `data/coverage/bybit.jsonl` was still absent at 15:16Z, 2 h 17 min into a clean Bybit run. This blocks a passing Bybit verdict in Story 31.11.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-capture-service-lifecycle
resolution-undo: 0048a9d04f44fc28ce1ff055a57d3d109c101be1016932f3bf35d6b4fc4e6436 2026-10-06 7374617475733a206f70656e

### DW-265: Every `verification.*` day tool reports PASS (exit 0) over nothing when the venue's plan is an explicit `instruments = []`: nothing refuses an empty plan …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-31-8-candles-and-klines-on-every-timeframe-with-pass-rates.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-31-8-candles-and-klines-on-every-timeframe-with-pass-rates.md` summary: Every `verification.*` day tool reports PASS (exit 0) over nothing when the venue's plan is an explicit `instruments = []`: nothing refuses an empty plan, which is the "exit 0 over nothing" shape DATA-07/D-123 refuses elsewhere. evidence: `verification/domain/plan_file.py` accepts `instruments = []` as "a plan that records nothing", and `plan_of` (`verification/conservation.py:120`) returns it unchanged. `conservation`, `trades`, `book`, `derivs`, `catalog` and the new `candles` then iterate zero instruments and print a passing verdict. This is a cross-tool pattern from 31.2 onwards, not new in 31.8, so it needs one shared refusal in `plan_of`.
status: done 2026-10-08
resolution: resolved by sweep bundle dw-verification-refuse-empty-plan
resolution-undo: dd670b686fc312ca8202a12fe70f67cfbd2a01d8db3bbaa7f23931020ef9b99c 2026-10-08 7374617475733a206f70656e

### DW-266: A collector's plan can grow at runtime (`collector:control`) past the size its compose `mem_limit` was measured for, with nothing warning before the cgroup …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-28-1-capture-hotpath-metrics-cpu-priority-and-vps-profile.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-28-1-capture-hotpath-metrics-cpu-priority-and-vps-profile.md` summary: A collector's plan can grow at runtime (`collector:control`) past the size its compose `mem_limit` was measured for, with nothing warning before the cgroup OOM-kills it into a restart loop. A memory-pressure canary is needed: the collector reads its cgroup `memory.current`/`memory.max` each flush and ledgers above a threshold, or refuses a plan change that projects past it. evidence: Story 28.1 set `mem_limit` from a 4-instrument Bybit / 1-instrument Hyperliquid peak × 1.5 (362m/248m). The `docker-compose.yml` Known limit estimates ~16 MiB per instrument, so the Bybit limit is reached at about 11 instruments, while Epic 28 targets 30+. Today the only signal is `OOMKilled=true` after the fact (DEPLOY_CHECKLIST §7).
status: open
decision: 2026-10-05 Read cgroup memory each flush and ledger above threshold — Memory canary ledger

### DW-267: On shutdown `CaptureService.run()` cancels `_ingest_loop` as soon as `_stop` is set. Any backlog still in `_ingest_queue`, and whatever the client pushes …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-28-2-capture-python-overhead-removed-baseline-lowered.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-28-2-capture-python-overhead-removed-baseline-lowered.md` summary: On shutdown `CaptureService.run()` cancels `_ingest_loop` as soon as `_stop` is set. Any backlog still in `_ingest_queue`, and whatever the client pushes before `_disconnect`, is abandoned without being processed and without a ledger line, which breaks DATA-05's "never silent". The fix is to await the ingest task to its stop sentinel (bounded) before cancelling the other loops, and to ledger `_ingest_backlog()` if the bound is hit. evidence: `run()`'s `finally` (`capture_service.py` ~:2383) cancels every task right after `asyncio.wait(FIRST_COMPLETED)` returns on `stop_task`. `_ingest_loop` only yields every `_INGEST_YIELD_EVERY` (64) messages, so the rest are dropped. This predates 28.2: the 1 s `wait_for` poll was cancelled the same way. 28.2's sentinel makes a drain possible, and the gap is documented as a `Known limit:` on `stop()`.
status: done 2026-10-06
resolution: resolved by sweep bundle dw2-capture-service-lifecycle
resolution-undo: 0048a9d04f44fc28ce1ff055a57d3d109c101be1016932f3bf35d6b4fc4e6436 2026-10-06 7374617475733a206f70656e

### DW-268: One persisted stale/invalid `source` makes the whole indicator-values request 422 and blanks every picker series, instead of a per-entry error.

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-32-3-legend-is-the-indicator-control-surface-larger-type-gear-settings-remove-in-place.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-3-legend-is-the-indicator-control-surface-larger-type-gear-settings-remove-in-place.md` summary: One persisted stale/invalid `source` makes the whole indicator-values request 422 and blanks every picker series, instead of a per-entry error. evidence: contract mandates 422; `usePickerIndicatorValues` sends all entries in one request, so the gear (which needs loaded series keys) is unreachable to repair it.
status: open
decision: 2026-10-05 Client drops/repairs invalid entries before request — Per-entry handling client-side

### DW-269: Settings modal cannot reset an output's style to the pane palette default, and legend action buttons are invisible (opacity 0) on touch devices; keyboard focus …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-32-3-legend-is-the-indicator-control-surface-larger-type-gear-settings-remove-in-place.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-3-legend-is-the-indicator-control-surface-larger-type-gear-settings-remove-in-place.md` summary: Settings modal cannot reset an output's style to the pane palette default, and legend action buttons are invisible (opacity 0) on touch devices; keyboard focus is lost when a legend row is rebuilt after an eye toggle. evidence: Apply merges only touched keys; `.chart-legend-actions` hover-only; `renderLegends` replaceChildren on signature change.
status: open

### DW-270: Frontend mirrors backend `indicator_id` for exotic param spellings (1e-7, nested values); upgrade path is returning each entry's id from the values response …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-32-3-legend-is-the-indicator-control-surface-larger-type-gear-settings-remove-in-place.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-3-legend-is-the-indicator-control-surface-larger-type-gear-settings-remove-in-place.md` summary: Frontend mirrors backend `indicator_id` for exotic param spellings (1e-7, nested values); upgrade path is returning each entry's id from the values response; Volume eye is not persisted until Story 32.6. evidence: documented as Known limit in `lib/indicatorId.ts` and ChartPage.
status: open

### DW-271: `IndicatorPicker.persist` applies saves optimistically and unserialized, rolling a failure back to its own `previous`: when two quick legend actions overlap …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-32-3-legend-is-the-indicator-control-surface-larger-type-gear-settings-remove-in-place.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-3-legend-is-the-indicator-control-surface-larger-type-gear-settings-remove-in-place.md` summary: `IndicatorPicker.persist` applies saves optimistically and unserialized, rolling a failure back to its own `previous`: when two quick legend actions overlap and the first PUT fails after the second succeeded, the picker list, the chart and the saved file disagree. Two PUTs that reach the server in reverse order can also leave the file at the older list. evidence: `persist` (`frontend/src/components/chart/IndicatorPicker.tsx`) calls `applyEntries(previous)` in its catch, with no queue and no refetch, and the PUT is a full-list rewrite (`put_coin_indicator_config`). This optimistic, unserialized design predates 32.3 (Story 15.6/17.5). The legend eye makes back-to-back saves much more likely. Fix: serialize saves through one in-flight chain, or refetch the config after a failure.
status: open

### DW-272: Nothing server-side refuses two chart indicator entries with the same `indicator_id`: `PUT /api/coin/{iid}/indicators` stores duplicates, and the GET's …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: PUT /api/coin/{iid}/indicators
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-3-legend-is-the-indicator-control-surface-larger-type-gear-settings-remove-in-place.md` summary: Nothing server-side refuses two chart indicator entries with the same `indicator_id`: `PUT /api/coin/{iid}/indicators` stores duplicates, and the GET's unservable-source fallback to `close` can turn a hand-edited `SMA(20) close` + `SMA(20) <bad source>` pair into two identical entries that the next save writes back. evidence: `put_coin_indicator_config` (`platform/data_api/routes/indicators.py`) checks names and sources only; duplicates are refused only client-side (`IndicatorPicker.hasInstance`); `_servable_source` rewrites the source without checking for a collision. Two entries with one id share one series key, so the legend acts on the first only. Refusing duplicates in the PUT needs a decision on files that already hold them (they would make every save of that coin a 400). Trigger is a hand edit only.
status: done 2026-10-05
resolution: closed by human decision: Accepted
decision: 2026-10-05 Accept hand-edit only — Accepted

### DW-273: `GET /api/candles/{iid}` reads the catalog's instrument definition on every request (including scroll-back pages) and 404s when absent.

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: GET /api/candles/{iid}
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-5-fibonacci-retracement-and-long-short-position-tools-every-drawing-stays-on-the-chart.md` summary: `GET /api/candles/{iid}` reads the catalog's instrument definition on every request (including scroll-back pages) and 404s when absent. evidence: `routes/candles.py` calls `instrument_precision` per request; Known limit comment names the per-instrument cache upgrade path.
status: open

### DW-274: Drawings PUT is a whole-list overwrite without a version, so two browsers editing one coin lose edits (last write wins).

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-32-5-fibonacci-retracement-and-long-short-position-tools-every-drawing-stays-on-the-chart.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-5-fibonacci-retracement-and-long-short-position-tools-every-drawing-stays-on-the-chart.md` summary: Drawings PUT is a whole-list overwrite without a version, so two browsers editing one coin lose edits (last write wins). evidence: `routes/drawings.py`; Known limit comment names version field + 409 + merge as the upgrade.
status: open

### DW-275: Drawing colours resolved from the theme at creation are saved as literals, so they do not follow later theme changes and the Fib context-menu colour picker …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-32-5-fibonacci-retracement-and-long-short-position-tools-every-drawing-stays-on-the-chart.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-5-fibonacci-retracement-and-long-short-position-tools-every-drawing-stays-on-the-chart.md` summary: Drawing colours resolved from the theme at creation are saved as literals, so they do not follow later theme changes and the Fib context-menu colour picker overwrites per-level colours. evidence: `ChartPage.tsx` creation handlers persist resolved colours; `LightweightChart.tsx` menu colour uses `menuSpec.color`.
status: open

### DW-276: One corrupt instrument table in `chart_drawings.toml` makes GET and PUT fail for every coin.

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: chart_drawings.toml
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-5-fibonacci-retracement-and-long-short-position-tools-every-drawing-stays-on-the-chart.md` summary: One corrupt instrument table in `chart_drawings.toml` makes GET and PUT fail for every coin. evidence: `load_chart_drawings` validates all tables; fail-loud was intended but blast radius is the whole file.
status: open

### DW-277: One malformed coin table in `chart_layouts.toml` makes every coin's layout GET/PUT return 500; isolate per-coin failures.

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: chart_layouts.toml
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-6-chart-layout-restored-on-return-and-default-setup-for-a-new-coin.md` summary: One malformed coin table in `chart_layouts.toml` makes every coin's layout GET/PUT return 500; isolate per-coin failures. evidence: `load_chart_layouts` raises on the first bad table and every route loads the whole file.
status: open

### DW-278: A failed layout GET draws no chart at all instead of falling back to the built-in layout with a visible warning.

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-32-6-chart-layout-restored-on-return-and-default-setup-for-a-new-coin.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-6-chart-layout-restored-on-return-and-default-setup-for-a-new-coin.md` summary: A failed layout GET draws no chart at all instead of falling back to the built-in layout with a visible warning. evidence: `ChartForCoin` renders only an alert and retries every 5 s until the layout loads.
status: open

### DW-279: `pane_heights` accumulates ids of removed indicators and never prunes them.

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-32-6-chart-layout-restored-on-return-and-default-setup-for-a-new-coin.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-6-chart-layout-restored-on-return-and-default-setup-for-a-new-coin.md` summary: `pane_heights` accumulates ids of removed indicators and never prunes them. evidence: `handlePaneHeights` merges into the previous map without removing ids no longer present.
status: done 2026-10-05
resolution: already resolved: platform/frontend/src/pages/ChartPage.tsx:838-841 handlePaneHeights prunes ids of removed indicators

### DW-280: A corrupt `chart_indicators.toml` (a server-side condition) is answered 400 "invalid indicator config payload" by the indicators PUT, while the layout routes …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: chart_indicators.toml
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-6-chart-layout-restored-on-return-and-default-setup-for-a-new-coin.md` summary: A corrupt `chart_indicators.toml` (a server-side condition) is answered 400 "invalid indicator config payload" by the indicators PUT, while the layout routes answer the same condition 500. evidence: `data_api/routes/indicators.py` `_store_entries` maps `KeyError`/`TypeError` from `load_chart_indicators` to 400; `data_api/routes/layout.py` `_file_errors` maps them to 500.
status: done 2026-10-05
resolution: resolved by sweep bundle dw-data-api-input-validation
resolution-undo: 91b47c52cc0253ee68fc7a1fe5a9d3fd9432e6964681548c0f72e9cd0679be68 2026-10-05 7374617475733a206f70656e

### DW-281: A `[default]` indicator later dropped from the catalog makes the first-open GET of every coin without an indicator list a 500, so no new coin draws a chart …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-32-6-chart-layout-restored-on-return-and-default-setup-for-a-new-coin.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-6-chart-layout-restored-on-return-and-default-setup-for-a-new-coin.md` summary: A `[default]` indicator later dropped from the catalog makes the first-open GET of every coin without an indicator list a 500, so no new coin draws a chart until the default is re-saved. evidence: `data_api/routes/layout.py` `_write_template_indicators` revalidates the template on every seed and maps a stale entry to 500 (`test_an_invalid_default_indicator_fails_the_seed_loudly_and_writes_nothing`).
status: done 2026-10-05
resolution: closed by human decision: Accepted: fail-loud intended
decision: 2026-10-05 Keep loud failure — Accepted: fail-loud intended

### DW-282: The session volume profile's session count and the Periodic-vs-Session preset are not in the layout's field list, so a Periodic profile on "daily" comes back …

origin: migrated from legacy ledger ("Deferred from: story 26.3 spine version lens (2026-09-28)"), 2026-10-05
location: _bmad-output/implementation-artifacts/spec-32-6-chart-layout-restored-on-return-and-default-setup-for-a-new-coin.md
reason: source_spec: `_bmad-output/implementation-artifacts/spec-32-6-chart-layout-restored-on-return-and-default-setup-for-a-new-coin.md` summary: The session volume profile's session count and the Periodic-vs-Session preset are not in the layout's field list, so a Periodic profile on "daily" comes back as the Session Volume Profile and the count resets to 5. evidence: `pages/ChartPage.tsx` `initialSessionConfig` derives the preset from `session`/`hd` and sets `sessionCount: DEFAULT_SESSION_COUNT`; the spec's enumerated `volume_profile` keys have no field for either.
status: open
decision: 2026-10-05 Extend layout schema with preset and session_count — Persist and restore both Note: the session count is already persisted on troll by DW-153 (`sessions`); what remains is the Periodic-vs-Session preset.

### DW-283: Extract the heartbeat-silence `_receive` loop, now copied in four pub/sub subscribers, into one shared helper taking an ingest callback

origin: migrated from legacy ledger (flat append from sweep bundle dw-live-channel-hardening, bmad-dev-auto-result-live-channel-hardening.md), 2026-10-05
location: platform/views/archive_status_bus.py, platform/views/rankings_bus.py, platform/bot_tui/archive_state.py, platform/bot_tui/collector_state.py
source_spec: `_bmad-output/implementation-artifacts/bmad-dev-auto-result-live-channel-hardening.md`
reason: The heartbeat-silence `_receive` loop is now copied in four modules (`views/archive_status_bus.py`, `views/rankings_bus.py`, `bot_tui/archive_state.py`, `bot_tui/collector_state.py`) and two more subscribers (`bot_tui/bots_state.py`, `bot_tui/bot_history_state.py`) still need it, so it should become one shared helper taking an ingest callback. evidence: The four `_receive` bodies are line-for-line the same poll/`heard`/`ConnectionError` loop with differently named constants (`STALE_AFTER_SECONDS`, `SILENCE_RESUBSCRIBE_SECONDS`, `_SILENCE_RESUBSCRIBE_SECONDS`); a shared home has to respect `tests/test_boundaries.py`'s context edges (views and bot_tui may not import each other).
status: done 2026-10-08
resolution: resolved by sweep bundle dw-pubsub-silence-receive-helper
resolution-undo: c09ababc4da7e725960b0cd269186b3e4444e4e671192f01c2fccbdbb96e00ef 2026-10-08 7374617475733a206f70656e

### DW-284: `GET /api/coin/{iid}/indicator-values` builds request params with no magnitude cap, so `HullMovingAverage {period: 10**9}` stalls `data_api` ~16 s per entry

origin: migrated from legacy ledger (flat append from spec-dw-data-api-input-validation.md), 2026-10-05
location: platform/data_api/routes/indicators.py
source_spec: `_bmad-output/implementation-artifacts/spec-dw-data-api-input-validation.md`
reason: `GET /api/coin/{iid}/indicator-values` builds whatever params its `entries` query carries with no magnitude cap, so a request for `HullMovingAverage {period: 10**9}` still stalls `data_api` for ~16 s per entry; save-time validation (`check_params`) now covers both PUTs and the technicals-values GET, but not this read path. evidence: `data_api/routes/indicators.py` `get_indicator_values` runs only `_parse_entries` + `_check_sources` before `chart_series.indicator_values_page`; the fix must keep the route's per-entry `errors` contract (one bad entry must not blank the others), e.g. by calling `indicator_picker.check_params` inside the per-entry replay and reporting its `ValueError` per entry. Predates the input-validation bundle.
status: open

### DW-285: An FRVP whose range ends on the forming live bar is built without that bar and never rebuilt once it closes, so the profile stops one bar short

origin: migrated from legacy ledger (flat append from spec-chart-edge-coordinates.md), 2026-10-05
location: platform/frontend/src/pages/ChartPage.tsx
source_spec: `_bmad-output/implementation-artifacts/spec-chart-edge-coordinates.md`
reason: An FRVP whose range (placement or edge drag) ends on the forming live bar builds its profile from `candles`/`fullVolume`, which exclude that bar, and is not rebuilt once the bar closes, so the profile silently stops one bar short of its drawn range. evidence: `ChartPage.tsx` `handleRangeSelect`/`handleEdgeCommit` call `buildRangeProfile(candles, fullVolume, ...)`; the refill at ~806 only rebuilds profiles that are not `filled`; the live bar was already reachable through `coordinateToTime` before DW-143/DW-150, which only made the margin drag land there more easily.
status: open

### DW-286: The knowledge base's "Redis channel reference" table lists five channels and misses five live ones (`collector:status`/`control`, `archive:status`/`control`, `markets:live`)

origin: migrated from legacy ledger (flat append from spec-dw-capture-script-and-docs.md), 2026-10-05
location: platform/frontend/src/pages/docs/kbData.ts
source_spec: `_bmad-output/implementation-artifacts/spec-dw-capture-script-and-docs.md`
reason: The in-app knowledge base's "Redis channel reference" table (`platform/frontend/src/pages/docs/kbData.ts`) lists only five channels (`snapshots:raw`, `rankings:live`, `ranking:control`, `bots:status`, `bots:control`), while the module map beside it names `collector:status`, `archive:status`, `markets:live` and `collector:control`, and `archive:control` is live too, so a reader who takes the table as the channel contract misses five live channels. evidence: `bot_tui/collector_pane.py`/`collector_state.py` (collector:status/control), `archive/infrastructure/redis_bus.py` + `archive/scheduler.py` (archive:status/control), `bot_tui/markets_state.py` (markets:live); the table predates this bundle, which only corrected the module-map prose.
status: open

### DW-287: TPO overflow bar takes the colour of the last block, losing the up/down split of hidden touches

origin: migrated from legacy ledger (flat append from spec-32-7-remaining-tradingview-profiles-auto-anchored-anchored-vp-anchored-vwap-and-tpo.md), 2026-10-05
location: VolumeProfilePrimitive drawTpo overflow path (platform/frontend)
source_spec: `_bmad-output/implementation-artifacts/spec-32-7-remaining-tradingview-profiles-auto-anchored-anchored-vp-anchored-vwap-and-tpo.md`
reason: TPO overflow bar takes the colour of the last block, losing the up/down split of hidden touches. evidence: VolumeProfilePrimitive drawTpo overflow path.
status: open

### DW-288: Catalog readers behind `candle_page` list files then open them, so a concurrent nightly consolidation makes a read raise an unledgered `FileNotFoundError` (bare 500)

origin: migrated from legacy ledger (flat append from spec-32-8-volume-footprint-bars-from-the-raw-trade-archive-toggled-from-the-indicators-menu.md), 2026-10-05
location: platform/kernel/catalog_files.py
source_spec: `_bmad-output/implementation-artifacts/spec-32-8-volume-footprint-bars-from-the-raw-trade-archive-toggled-from-the-indicators-menu.md`
reason: The views' other catalog readers (`kernel.catalog_files.query_second_ohlc` and its siblings behind `candle_page`) list files and then open them, so a nightly consolidation that removes minute files after writing their day file makes a concurrent read raise an unledgered `FileNotFoundError` (a bare 500). evidence: `archive/consolidate_catalog.py` writes the merged day file and then removes its sources; no reader in `views/` or `kernel/catalog_files.py` other than the new `query_trade_columns` (Story 32.8 follow-up review) catches `FileNotFoundError` or lists again.
status: done 2026-10-06
resolution: resolved by sweep bundle dw-catalog-readers-skip-and-ledger
resolution-undo: 9452af3d15aa48b5980ac2177e29ca6f20152d75e05d99ce21efca1f22e644c8 2026-10-06 7374617475733a206f70656e

### DW-289: Catalog readers behind `candle_page` let a corrupt Parquet file's `ArrowInvalid`/`OSError` escape unmapped as a bare 500 no `error_ledger.record` site counts (DATA-07)

origin: migrated from legacy ledger (flat append from spec-32-8-volume-footprint-bars-from-the-raw-trade-archive-toggled-from-the-indicators-menu.md), 2026-10-05
location: platform/kernel/catalog_files.py
source_spec: `_bmad-output/implementation-artifacts/spec-32-8-volume-footprint-bars-from-the-raw-trade-archive-toggled-from-the-indicators-menu.md`
reason: `kernel.catalog_files.query_second_ohlc` and the other pre-existing catalog readers behind `candle_page` let a truncated or corrupt Parquet file's `pyarrow.ArrowInvalid`/`OSError` escape unmapped, so the chart request fails as a bare 500 that no `error_ledger.record` site counts (DATA-07). evidence: Only the new `query_trade_columns` (Story 32.8 second follow-up review) maps an unreadable file to a ledgered error; `query_second_ohlc` and its siblings call `pq.read_table`/`pq.read_schema` with no handler, and `data_api/routes/candles.py` maps only `ImpossibleCandle`.
status: done 2026-10-06
resolution: resolved by sweep bundle dw-catalog-readers-skip-and-ledger
resolution-undo: 9452af3d15aa48b5980ac2177e29ca6f20152d75e05d99ce21efca1f22e644c8 2026-10-06 7374617475733a206f70656e

### DW-290: `CaptureService._run` failures between `_connect` and the loop-guarding `try` leave the client connected with no disconnect, drain or final flush

origin: migrated from legacy ledger (flat append from spec-dw-capture-service-lifecycle.md, 2026-10-06 follow-up review of the DW-267 drain), 2026-10-06
location: platform/capture/application/capture_service.py
source_spec: `_bmad-output/implementation-artifacts/spec-dw-capture-service-lifecycle.md`
reason: Anything raising in `CaptureService._run` between `_connect` and the `try` that guards the loops (`subscribe_global`, `_catch_up_candle_store`'s unguarded `self._second_sink.watermarks()`, `apply`) leaves the client connected and feeding a service that is gone, with no `_disconnect`, no drain and no final flush, so the messages already queued are dropped without a ledger line. Evidence: pre-existing: at baseline e6272ad9c6 `_run` has the same order (`await self._connect(...)` ... `self._catch_up_candle_store()` / `await self.apply(...)` before `try:`), and `_catch_up_candle_store` iterates `self._second_sink.watermarks()` with no handler there either; `run()`'s `finally` only closes the second sink. Surfaced by the 2026-10-06 follow-up review of the DW-267 drain, which covers only failures after the loops start.
status: open

### DW-291: Runtime-rewritten data files (`platform/data/alerts/alerts.toml`, `platform/data/preferences/*.toml`) are git-tracked, so an upstream change makes the VPS `git pull` refuse the locally rewritten file

origin: migrated from legacy ledger (flat append from spec-dw-197-199-alert-store-durability.md), 2026-10-06
location: platform/data/alerts/alerts.toml
source_spec: `_bmad-output/implementation-artifacts/spec-dw-197-199-alert-store-durability.md`
reason: Runtime-rewritten data files are git-tracked (`platform/data/alerts/alerts.toml`, as `platform/data/preferences/*.toml` already are), so any upstream change to a tracked copy makes the VPS `git pull` refuse the locally rewritten file. Evidence: The 32-5 and DW-197 DEPLOY_CHECKLIST entries both need `git checkout --` surgery for exactly this; upgrade path is gitignoring the files (keeping a `.gitkeep`), since `AlertStore._load` and the preference readers already start empty on a missing file.
status: done 2026-10-08
resolution: resolved by sweep bundle dw-untrack-runtime-data-files
resolution-undo: 9c3c3ed117779a15e1b47516286bef3b3c844668f4fd3bc6f9f9429ee504f428 2026-10-08 7374617475733a206f70656e

### DW-292: `mypy platform/kernel/catalog_files.py` fails at line 382: `SecondOHLC(...)` gets a `*Generator[float | None, ...]` star-arg where it expects `float`, so a None open/high/low/close is not excluded by the types

origin: migrated from legacy ledger (flat append from spec-dw-208-215-archive-retention-rule-ordering.md), 2026-10-07
location: platform/kernel/catalog_files.py:382
source_spec: `_bmad-output/implementation-artifacts/spec-dw-208-215-archive-retention-rule-ordering.md`
reason: `mypy platform/kernel/catalog_files.py` fails with one pre-existing error at line 382: `SecondOHLC(...)` gets a `*Generator[float | None, ...]` star-arg where it expects `float`, so a None open/high/low/close is not excluded by the types. Evidence: The error is reproduced on HEAD 6ca8d50d6a with this review's patches stashed (`../.venv/bin/mypy archive/domain/retention.py archive/application/prune.py kernel/catalog_files.py` from `platform/`: 1 error, `[arg-type]`). Line 382 is outside the DW-208/215 diff, which only adds `DEFINITION_DIRNAMES` near line 98. It surfaced because this pass ran mypy over the whole module.
status: open

### DW-293: Epic 33.12's "Bar Replay works in Lines mode too" is retracted by the operator: Replay is a candle-chart feature, Lines-mode replay is not planned

origin: operator decision 2026-10-07 (17:20 UTC, clarified 17:25 UTC) during Story 33.12
location: platform/frontend/src/pages/ChartPage.tsx, platform/frontend/src/hooks/useReplay.ts
source_spec: `_bmad-output/implementation-artifacts/spec-33-12-symbol-search-watchlist-fullscreen-shortcuts-time-zone-countdown.md`
reason: The epic's Story 33.12 AC sentence "Bar Replay works in **Lines mode** too (the five snapshot series cut at the replay time like the candles)" is retracted, not deferred: Bar Replay is supported only on candle charts (candles mode, every `CHART_TYPES` entry incl. Line/Area), and Lines mode (the snapshot-seconds view) never gets Replay. Nothing is to be built. evidence: the Replay button is disabled in Lines mode with the visible reason "Replay is available on candle charts", Alt+R is a no-op there (`ChartPage.test.tsx`: "is disabled in Lines mode, saying why", "ignores Alt+R in Lines mode"); `useReplay.ts` is unchanged from the baseline; `epics.md` Story 33.12 carries the dated `[amended 2026-10-07: operator]` strike-through.
status: done 2026-10-07
resolution: closed by human decision: Retracted by the operator; Lines-mode replay is not planned (Replay is a candle-chart feature)
decision: 2026-10-07 Operator: Bar Replay only on candle charts; Lines mode never gets Replay

### DW-294: Story 33.2's planned `price_kind` and nullable `confirmed` columns on `kernel.liquidation.Liquidation` leave `custom_liquidation/` with mixed-schema files; 33.2 must prove the catalog reads and consolidates them, or rewrite the 33.1 files

origin: migrated from legacy ledger (flat append from spec-33-1-bybit-liquidations-captured-over-a-second-socket-into-one-shared-liquidation-type.md), 2026-10-08 (epic-33 merge)
location: platform/kernel/liquidation.py
source_spec: `_bmad-output/implementation-artifacts/spec-33-1-bybit-liquidations-captured-over-a-second-socket-into-one-shared-liquidation-type.md`
reason: Story 33.2's planned `price_kind` (`dictionary<int8,string>`) and nullable `confirmed` (`bool`) columns on `kernel.liquidation.Liquidation` will leave `custom_liquidation/` holding Bybit files written under 33.1's 9-column schema beside files with the extended schema, so 33.2 must prove `ParquetDataCatalog` reads and consolidates the mixed-schema directory, or rewrite the 33.1 files (`price_kind="bankruptcy"`, `confirmed=null`) in the same story. Evidence: The epic assigns both columns to 33.2 ("added to the type in this story"); 33.1's `Liquidation.schema()` has neither, and `register_arrow` binds one schema per class, so files written before and after 33.2 differ in column set. Raised by the 33.1 Blind Hunter review (finding 10).
status: open

### DW-295: `verification.catalog`'s structure check does not know the `custom_liquidation` data type, so a real Bybit day holding liquidation files is likely flagged by the catalog tool

origin: migrated from legacy ledger (flat append from spec-33-3-per-bar-order-flow-and-liquidation-aggregates-in-the-candle-store-folded-once.md), 2026-10-08 (epic-33 merge)
location: platform/verification/application/catalog.py
source_spec: `_bmad-output/implementation-artifacts/spec-33-3-per-bar-order-flow-and-liquidation-aggregates-in-the-candle-store-folded-once.md`
reason: `verification.catalog`'s structure check does not know the `custom_liquidation` data type (Story 33.1), so a real Bybit day whose catalog holds liquidation files is likely flagged by the catalog tool; it needs a `NautilusReads.known` entry (or equivalent) and a test over an on-disk liquidation directory. Evidence: Story 33.3's implementation had to monkeypatch `LiquidationCatalog.first_ts_event` in `verification/tests/test_catalog.py` because writing a real `Liquidation` into the fixture catalog made the structure check fail; raised by the 33.3 Blind Hunter review (loop-1 re-derivation pass, finding 16).
status: open

### DW-296: The Volume overlays dialog lists VRVP, the session slot and placed FRVPs but not the Anchored VP / Anchored VWAP drawings

origin: migrated from legacy ledger (flat append from spec-quick-volume-overlays-modal-and-grouped-tool-rail.md), 2026-10-08 (epic-33 merge)
location: platform/frontend/src/components/chart/VolumeOverlaysDialog.tsx
source_spec: `_bmad-output/implementation-artifacts/spec-quick-volume-overlays-modal-and-grouped-tool-rail.md`
reason: The Volume overlays dialog lists VRVP, the session slot and placed FRVPs but not the Anchored VP / Anchored VWAP drawings, which are volume overlays too and are still edited only from their chart context menu. Evidence: `VolumeOverlaysDialog.tsx` takes no drawings; Anchored VP/VWAP live in `useChartDrawings` with their own `DrawingSettingsDialog` (Story 32.7). Raised by the quick-dev Blind Hunter review; left out because the operator's request named FRVPs only and these already have a working edit path.
status: open

### DW-297: The rail's last-used tool per group (`ChartPage`'s `toolMemory`) is not persisted, so a reload shows each group's first tool again

origin: migrated from legacy ledger (flat append from spec-quick-volume-overlays-modal-and-grouped-tool-rail.md), 2026-10-08 (epic-33 merge)
location: platform/frontend/src/pages/ChartPage.tsx
source_spec: `_bmad-output/implementation-artifacts/spec-quick-volume-overlays-modal-and-grouped-tool-rail.md`
reason: The rail's last-used tool per group (`ChartPage`'s `toolMemory`) is not persisted, so a page reload shows each group's first tool again (Known limit in `ChartPage.tsx`). Evidence: TradingView keeps the last-used tool across sessions; persisting it needs a per-viewer UI preference beside the coin layout, since the layout table's shape (`lib/chartLayout.ts`, `views/preferences.py`) was out of scope for this change.
status: open

### DW-298: Every `/ws/live` connection gets the full `rankings:live` and alerts relay unconditionally, and the frontend opens one socket per live hook, so a chart holds 3 sockets that each discard every rankings snapshot

origin: migrated from legacy ledger (flat append from spec-33-4-derivatives-and-liquidations-read-models-api-and-live-channel.md), 2026-10-08 (epic-33 merge)
location: platform/data_api/ws/live.py
source_spec: `_bmad-output/implementation-artifacts/spec-33-4-derivatives-and-liquidations-read-models-api-and-live-channel.md`
reason: Every `/ws/live` connection is subscribed to the full `rankings:live` and alerts relay unconditionally, and the frontend opens one socket per live hook (`liveSubscription.ts`), so a chart with candles, derivs and liquidations hooks holds 3 sockets that each receive and discard every rankings snapshot. Evidence: `data_api/ws/live.py`'s `ws_live` adds the rankings/alerts listeners before any control message (pre-existing with `useLiveCandle`); Story 33.4's two new hooks multiply it. Upgrade path: one shared multiplexed socket per page, or opt-in rankings relay. Raised by the 33.4 Blind Hunter review (finding 5).
status: open

### DW-299: A bot stopped over `bots:control` cannot be started again in-process: Nautilus refuses `Strategy.start()` from `STOPPED`, so the bot stays stopped with only an ERROR log and no ledger entry

origin: migrated from legacy ledger (flat append from spec-33-14-liquidation-cascade-bot-shorts-into-a-long-liquidation-cascade-backtested-and-paper-run.md), 2026-10-08 (epic-33 merge)
location: platform/bots/application/supervise.py
source_spec: `_bmad-output/implementation-artifacts/spec-33-14-liquidation-cascade-bot-shorts-into-a-long-liquidation-cascade-backtested-and-paper-run.md`
reason: A bot stopped over `bots:control` cannot be started again in-process: `BotSupervisor.handle_control`'s `start` calls `StrategyCacheReader.start` → `Strategy.start()`, which Nautilus refuses from `STOPPED` (only `RESUME`/`RESET` leave it), so the bot stays stopped with only an ERROR log and the error ledger never sees it. Evidence: `nautilus_trader/common/component.pyx`'s FSM table has `(STOPPED, RESUME)`, `(STOPPED, RESET)`, no `(STOPPED, START)`; reproduced in the 33.14 follow-up review on a registered `LiquidationCascadeStrategy` (start, stop, start leaves `state == STOPPED` and `on_start` is not re-run). Pre-existing for every hosted strategy (`bots/application/supervise.py:237-239`, `bots/infrastructure/cache_reader.py:178`); the fix (resume, or reset then start, and what a new signal-log segment means for parity) is the bots context's.
status: open

### DW-300: `AlertStore._save` rewrites `alerts.toml` in place, so a write that fails partway leaves a truncated file the next `data_api` start refuses to load

origin: migrated from legacy ledger (flat append from spec-33-8-alert-conditions-beyond-a-price-cross-and-an-alerts-page-that-creates-and-edits.md), 2026-10-08 (epic-33 merge)
location: platform/alerting/infrastructure/toml_store.py
source_spec: `_bmad-output/implementation-artifacts/spec-33-8-alert-conditions-beyond-a-price-cross-and-an-alerts-page-that-creates-and-edits.md`
reason: `AlertStore._save` rewrites `alerts.toml` in place (`open("wb")` then `tomli_w.dump`), so a write that fails partway (a full disk) leaves a truncated file that the next `data_api` start refuses to load, which loses every alert. Temp-file-plus-rename cannot fix it while compose bind-mounts the single file (`./data_api/alerts.toml:/app/data_api/alerts.toml:rw`), because a rename over a bind-mount target fails with EBUSY. Evidence: `platform/alerting/infrastructure/toml_store.py` `_save`; `platform/docker-compose.yml:320`. The in-place write predates Story 33.8 (Story 20.1). `views.preferences` solved the same problem in Story 32.5 by mounting the directory, and `AlertRepository.update`'s docstring now carries this as a `Known limit:`. The fix moves `ALERTS_PATH`, which is frozen (AD-D12), into a mounted directory, so it needs a compose change and a deploy step. Raised by the 33.8 follow-up Blind Hunter review.
status: done 2026-10-08
resolution: already resolved: DW-197/DW-199 (sweep dw2-alert-store-durability, 5792c82c16) made `AlertStore`'s save atomic (temp file, fsync, rename, directory fsync) and moved `ALERTS_PATH` into the mounted `platform/data/alerts/` directory; the epic-33 merge carried Story 33.8's `update` onto that save (`_save_or_restore`) and removed the `Known limit:` from `AlertRepository.update`'s docstring

### DW-301: `nextDrawingId` reuses a deleted trendline's id, so a `trendline_cross` alert naming a deleted `trendline-N` silently re-attaches to the next trendline placed

origin: migrated from legacy ledger (flat append from spec-33-10-drawing-tools-two-ray-vline-rectangle-channel-text-arrow-magnet-undo-lock.md), 2026-10-08 (epic-33 merge)
location: platform/frontend/src/lib/drawings.ts
source_spec: `_bmad-output/implementation-artifacts/spec-33-10-drawing-tools-two-ray-vline-rectangle-channel-text-arrow-magnet-undo-lock.md`
reason: `nextDrawingId` reuses a deleted trendline's id (max+1 over the current list), so a `trendline_cross` alert naming a deleted `trendline-N` silently re-attaches to the next trendline placed; Story 33.10's Delete all makes this likely. Evidence: `frontend/src/lib/drawings.ts` `nextDrawingId` (pre-existing since 32.5) and `data_api/alert_inputs.py` `DrawingFileReader.trendline`, which resolves by id only; nothing invalidates or removes an alert when its drawing is deleted.
status: open

- source_spec: `_bmad-output/implementation-artifacts/spec-dw-283-pubsub-silence-receive-helper.md`
  summary: The two `markets:live` subscribers disagree on whether the channel can be legitimately silent: `views/markets_bus.py` raises and resubscribes after 180 s of silence ("Every venue publishes each 60 s cycle"), while `bot_tui/markets_state.py` PING-probes instead because "no venue has a fresh volume source, so the ranking engine publishes nothing" is a healthy quiet.
  evidence: `platform/views/markets_bus.py` `_receive` docstring + `STALE_AFTER_SECONDS` vs `platform/bot_tui/markets_state.py` `_receive` docstring; both subscribe the same `"markets:live"`. Pre-existing (the views copy raised on silence before DW-283, which kept behaviour byte-identical); one docstring is wrong, or `data_api` reconnects a healthy connection every 180 s during that quiet.
- source_spec: `_bmad-output/implementation-artifacts/spec-dw-283-pubsub-silence-receive-helper.md`
  summary: The PING-after-silence liveness loop (the deliberate exception to `observability.pubsub_liveness.receive_until_silent`) is still copied in four subscribers with no shared helper, so a fix to one misses the others.
  evidence: `platform/archive/infrastructure/redis_bus.py` `_messages` (~line 100), `platform/collection_control/infrastructure/redis.py` (~line 94), `platform/bots/infrastructure/liquidation_data_client.py` (~line 332) and `platform/bot_tui/markets_state.py` `_receive` each poll `get_message`, send a PING after a silence window and raise on an unanswered one. Pre-existing; outside DW-283's listed callers.
