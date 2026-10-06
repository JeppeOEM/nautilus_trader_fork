---
title: 'Story 33.6: Order-flow indicators from the stored per-bar aggregates'
type: 'feature'
created: '2026-10-06'
status: 'done'
baseline_revision: '18d4ac6bc7fc9b072d757fbad2cefb7b0b2ef0b3'
final_revision: '44367c116c06c385a0acfa0c29bb5770da12e0cf'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Story 33.3 stores exact per-bar order flow and liquidation totals on every candle: `buy_v`, `sell_v`, `buy_n`, `sell_n`, `pv`, `liq_long_v`, `liq_short_v` and `liq_n`, with per-row `price_precision`/`size_precision`. Only CVD reads them. The trader cannot see delta, forced flow, participation, trade size or an exact VWAP per bar. The Volume pane is one flat colour.

Two defects block this:
- CVD's `session` anchor sums only from the first bar of the requested page, not from the session start. A 1m page starting at 10:00 restarts at 10:00.
- Custom indicator legends ignore the instrument precision.

**Approach:**
- Add seven custom catalog entries computed from the bar dicts alone: `VolumeDelta`, `OrganicDelta`, `ForcedShare`, `TradeCount`, `AverageTradeSize`, `StoredVWAP` (modes `bar|session`) and `DepthWithinBps`. The last is the one raw-seconds reader, kept to 7 days.
- Keep the per-bar formulas in `kernel/indicators.py` (SSOT-01).
- Seed the session and anchored sums from an exact store prefix, which also fixes the CVD `session` defect.
- Story 32.7 has landed, so the anchored VWAP mode is a drawing: the 32.7 Anchored VWAP drawing gains a `stored` source, served by an unlisted catalog entry, `AnchoredStoredVWAP`.
- Add a per-output `units` table to the catalog so legends format through `lib/units.ts`.
- Add a Volume `colour by` setting, `direction|delta`, persisted in the layout.

## Boundaries & Constraints

**Always:**
- **Integer arithmetic.** Every sum is exact integer units, rescaled to the finest precision present (`10**k`), as `_cvd_replay` already does. A value becomes a `float` only in the replay's output (DATA-04).
- **Null means unknown, never 0 (DATA-01).**
  - A bar whose flow is null (`buy_v is None`) gives None.
  - A bar whose liquidations are null (no feed, or before the feed start) gives None for `OrganicDelta`/`ForcedShare`.
  - A cumulative mode carries its total across a null bar, as CVD does.
  - Nothing is clamped: a `ForcedShare` above 1 is served as is, with a `Known limit:` saying why it can happen (DATA-07).
- **Sign mapping, in the docstring and a test:** a long liquidation is a forced sell, a short liquidation a forced buy. So `OrganicDelta = (buy_v − liq_short_v) − (sell_v − liq_long_v)`.
- **No raw-seconds read except `DepthWithinBps`.**
  - Its read is capped at `MAX_QUERY_SPAN_SECONDS` back from the window end, applied inside the replay itself, because `ranking_columns.technicals_values` passes an uncapped window.
  - It is column-projected: two passes, never 7 days of decoded 20-level books in Python (MEM-01).
- **No new dependency.** Never touch `nautilus_trader/` or `crates/`.
- **Added keys only (AD-D12).** The catalog entry gains `units`, regenerated into `openapi.json`/`schema.ts`. The layout gains an optional key.
- **Docs.** Docstrings carry `Known limit:` comments naming the ceiling and the upgrade path. Every touched doc stays truthful (DESIGN-03, MR4).

**Block If:**
- A catalog name collides with a native one (`merged_catalog` raises) and no unambiguous rename exists.
- The 33.3 columns are absent from `candles/domain/fold.py` `AGGREGATE_KEYS`.

**Never:**
- No replay of raw seconds for any flow indicator.
- No anchored mode exposed through the picker as an `anchor_t` param.
- No browser-side indicator formula: the browser computes only the Volume colour mapping, as a plotting conversion.
- No change to `nautilus_trader.indicators`-backed native entries.
- No fabricated prefix: an uncovered prefix gives None or an error, never a partial sum passed off as whole.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Delta | bar `buy_v=70, sell_v=30, sp=1` | `VolumeDelta` 4.0 | — |
| Organic delta | `buy_v=70, sell_v=30, liq_long_v=10, liq_short_v=5, sp=1` | `(70−5)−(30−10)=45` → 4.5 | — |
| No liquidation feed | `liq_*` null, flow known | `OrganicDelta`/`ForcedShare` None; `VolumeDelta` value | — |
| Quiet bar | `buy_v=sell_v=0, buy_n=sell_n=0` | delta 0; `ForcedShare`, `AverageTradeSize`, `StoredVWAP(bar)` None | — |
| Pre-migration bar | `buy_v` null | every flow indicator None; cumulative total carries on | — |
| Mixed precision | bars with `sp` 1 and 2 | sums rescaled to sp 2, exact | — |
| TradeCount split | `buy_n=7, sell_n=3, split=true` | outputs `buys` 7, `sells` −3 | — |
| VWAP bar | `pv=1_000_050, buy_v+sell_v=10, pp=2, sp=1` | `pv / (V·10^pp)` = 1000.05 | — |
| Session prefix covered | 1m page from 10:00, store holds bars from 00:00 | CVD/VWAP session include [00:00, 10:00) exactly | — |
| Session prefix uncovered | store's first bar of the width after the session start, page does not reach it | bars of that session in the page None | — |
| Anchored before store | anchor earlier than store's first bar of the width | entry error naming the anchor and the store start | per-entry `errors`, legend shows it |
| Depth, no snapshot in a bar | no snapshot row in `[t, t+bar)` | `bid`/`ask` None | — |
| Depth, edge past stored book | `depth_within_bps` NaN | None | — |
| Depth, old bars | bar ends before `end − 7 d` | None (no read) | — |

</intent-contract>

## Code Map

- `platform/views/indicator_picker.py`:
  - `ReplayWindow` (:512), `CustomIndicatorSpec` (:533), `CUSTOM_INDICATOR_CATALOG`, `replay_custom` (:552), `custom_catalog_json` (:566), `_bucket_ns` (:574).
  - `_cvd_replay`/`_cvd_prefix` (:586-664): the template for exact rescaled sums and docstrings.
  - `_CATALOG_PATH` (:508); `merged_catalog` (:892); `check_params`/`_check_param_type` (:928/:970); `indicator_id` (:1019); `values_by_time` (:1061).
- `platform/candles/application/queries.py`: `open_store`, `flow_delta_before` (:280), the one exact SQLite aggregate.
- `platform/candles/domain/fold.py`: `AGGREGATE_KEYS` (:159), `bucket_start_ms`, `stored_bar`.
- `platform/kernel/indicators.py`: `volume_delta` (:780), `snapshot_depth` (:827), `depth_within_bps` (:872), `basis_bps` (:944, the pure-function style).
- `platform/kernel/catalog_files.py`:
  - `snapshot_files`, `_read_snapshot_columns` (:165).
  - `query_top_of_book`/`_top_rows` (:229-262): the column-projected template.
  - `kernel/second_snapshot.py`: `decode_book_prices`, `unit_float`.
- `platform/views/chart_series.py`: `indicator_values_page` (:707; the window's 7-day cap and its comment at ~:753), `MAX_QUERY_SPAN_SECONDS` (:219).
- `platform/views/ranking_columns.py`: `technicals_values` (:197) and `_latest_of_group` (:254). Its window is uncapped, and one raising column fails every coin.
- `platform/data_api/routes/indicators.py`: `IndicatorCatalogEntry` (:86), `check_indicator_entries` (:219, refuses names outside `merged_catalog`), and the values route (:362, which runs no `check_params`).
- `platform/views/preferences.py`:
  - `VWAP_SOURCES` (:98), shared by Anchored VP and VWAP, and the anchored_vwap source check (:414).
  - `_LAYOUT_KEYS` (~:545) and `validate_layout` (~:873, the optional `footprint`/`derivatives` precedent).
- `platform/views/tests/`:
  - `test_indicator_picker_custom.py`: `_flow_bar` (:113), `_hourly` (:124), `_MATRIX`, and the CVD tests with a real `CandleStore` (:143-226).
  - `test_indicator_picker_params.py:32`: `test_every_catalog_default_passes`.
  - `test_chart_drawings.py`, `test_chart_layouts.py:412`: the mirror tests.
- Frontend (`platform/frontend/src/`):
  - `pages/ChartPage.tsx`:
    - picker panes (:705-734, `panel` → kind and placement);
    - the volume pane (:692-703, `configurable: false`);
    - the Anchored VWAP drawing block (:559-628);
    - `usePickerIndicatorValues` (:647);
    - the catalog fetch (:848).
  - `hooks/usePickerIndicatorValues.ts`: the paging values hook. A second instance serves the stored drawings.
  - `hooks/useCandles.ts` `toVolumeDatum` (:70) drops `buy_v`/`sell_v`. `hooks/useLiveCandle.ts` `LiveCandleMessage` (:14) is typed with base keys only, but `views/live_candles.py` `_publish` sends `forming_bar`'s aggregates.
  - `components/chart/LightweightChart.tsx`:
    - `IndicatorPaneSpec.format` (:118);
    - per-point colour (`paintedData` :453-479);
    - the forming-bar volume update (~:1388).
  - `lib/units.ts`: `formatDecimal`, `formatPercent`. `lib/drawings.ts`: `safeDecimal`.
  - `lib/anchoredVwap.ts`: `VWAP_SOURCES`. `lib/drawings.ts`: `AnchoredVwapDrawing` (:111).
  - `lib/chartLayout.ts`: `ChartLayout`, `normalizeLayout`, `layoutForSave`, `sameLayout`, `footprintOf`.
  - `components/chart/FootprintSettingsDialog.tsx`: the precedent for a settings dialog on `SettingsDialogShell`.
  - `pages/docs/data.ts` `INDICATORS` (:50): the Indicator Reference.
- Docs:
  - `platform/docs/DATA_DICTIONARY.md` §2.7 (:2896), §2.15 (:3285), §4 lineage (~:3894).
  - `platform/docs/DATA_INTEGRITY_AUDIT.md`: the last row is D-185.
  - `platform/docs/DEPLOY_CHECKLIST.md`.
  - `platform/CLAUDE.md` SSOT-06.

## Tasks & Acceptance

**Execution:**

*Backend: formulas, prefix, catalog*
- [x] `platform/kernel/indicators.py`: pure per-bar functions over integer units, each with a docstring stating units and the null rule:
  - `organic_delta_units(buy_v, sell_v, liq_long_v, liq_short_v) -> int`, stating the sign mapping;
  - `units_ratio(num_units, num_precision, den_units, den_precision) -> float | None`, None at a zero denominator, for `ForcedShare` and `AverageTradeSize`;
  - `bar_vwap(pv_units, volume_units, price_precision) -> float | None`: `pv / (volume · 10^pp)`, an exact `Fraction`, float only at return, None at 0 volume.
  - Tests in `kernel/tests/test_indicators.py` with hand values.
- [x] `platform/candles/application/queries.py`: generalise `flow_delta_before` into `flow_totals(db, iid, bar_seconds, before_ms, since_ms=None)`.
  - Returns `FlowTotals(delta_units, volume_units, size_precision, pv_units, pv_precision)` or None.
  - One SQLite aggregate `GROUP BY price_precision, size_precision`, rescaled exactly, with the same observed-only and corrupt-row rules.
  - Add `first_bar_t(db, iid, bar_seconds) -> int | None`.
  - Repoint CVD `all` at `flow_totals` and delete `flow_delta_before` (DESIGN-03).
  - Tests in `candles/tests/test_order_flow.py` (where the 33.3 query tests live) or a new `candles/tests/test_flow_totals.py`: mixed precisions, the `since_ms` bound, unmigrated → None.
- [x] `platform/views/indicator_picker.py`: the replays.
  - `CustomIndicatorSpec` gains:
    - `units: dict[str, str]`: output → `price|size|count|ratio`;
    - `listed: bool = True`: `custom_catalog_json` and therefore `merged_catalog` skip unlisted entries, so the picker, the indicator-config PUT and the Technicals refuse them, while the values route still serves them.
  - `custom_catalog_json` serves `units`.
  - A shared helper computes the exact store prefix for a mode start `start_ms`.
    - It is covered when the page's first bar ≤ `start_ms`, or when `first_bar_t` of `stored_bar(bar_seconds)` ≤ `start_ms`.
    - Otherwise it is uncovered. No `candles_dir`, or no store, also counts as uncovered.
  - CVD `session`: each session's start is seeded from the prefix; an uncovered session gives None for its bars in the page.
  - Register `VolumeDelta` (histogram, `size`).
  - Register `OrganicDelta` (histogram, `size`).
  - Register `ForcedShare` (histogram, `ratio`).
  - Register `TradeCount`: histogram, `count`, param `split: false`. With `split` the outputs are `buys` (+) and `sells` (−); otherwise `value`.
  - Register `AverageTradeSize` (oscillator, `size`).
  - Register `StoredVWAP`: overlay, `price`, `mode: bar|session`, default `session`.
    - Session is cumulative Σpv/ΣV from the UTC day start, carrying across null bars.
    - At 1D and wider, each bar is its own session.
  - Register `AnchoredStoredVWAP`: unlisted, overlay, `price`, param `anchor_t` (a string of epoch ms, digits only, checked by `check_params`).
    - None before the anchor.
    - An anchor older than the store's first bar of the width (when the page starts after it) raises a `ValueError` naming both times.
  - Register `DepthWithinBps`: oscillator, `size`, param `bps: 10.0` (0 < bps ≤ 1000), outputs `bid`/`ask`.
    - Each bar's last snapshot in `[t, t+bar)` feeds `snapshot_depth` → `depth_within_bps`.
    - NaN → None.
    - Bars ending before `end_ms − MAX_QUERY_SPAN_SECONDS` get None.
    - `start_ms is None` → all None.
    - Its `Known limit:` is the 7-day window.
  - CVD gains `units` `size`.
  - Each replay is ≤ ~30 lines; shared walking goes in helpers.
- [x] `platform/kernel/catalog_files.py`: two column-projected readers on the `query_top_of_book` pattern.
  - `query_snapshot_times(catalog_path, iid, start_ns, end_ns) -> np.ndarray`: sorted `ts_event`, one column.
  - `query_books_at(catalog_path, iid, ts_events) -> dict[int, dict]`: only the selected rows' book list columns and precisions, decoded to `as_floats`-shaped `bid_prices/bid_sizes/ask_prices/ask_sizes`.
  - Tests over a real written snapshot file.
- [x] `platform/data_api/routes/indicators.py`: `IndicatorCatalogEntry.units: dict[str, str] = {}`. Regenerate `frontend/openapi.json` (`data_api/export_openapi.py`) and `schema.ts` (`npm run codegen`).
- [x] `platform/views/preferences.py`:
  - `ANCHORED_VWAP_SOURCES = (*VWAP_SOURCES, "stored")`, used only by the anchored_vwap check. Anchored VP is unchanged.
  - Optional layout key `volume_color_by` in `VOLUME_COLOR_MODES = ("direction", "delta")`. Absent means `direction`; any other value gets a 422 naming the key.
  - Mirror tests against the frontend constants in `test_chart_drawings.py`/`test_chart_layouts.py`.
  - Amend SSOT-06 in `platform/CLAUDE.md`.
- [x] `platform/views/tests/test_indicator_picker_custom.py`:
  - Every I/O-matrix row, against hand folds.
  - The null fixture, the sign mapping and the split.
  - CVD and VWAP `session` with a covered prefix (a real `CandleStore`) and an uncovered one.
  - The anchored before-store error.
  - `DepthWithinBps` over a real snapshot catalog, including the 7-day cap.
  - The unlisted entry absent from `merged_catalog`, while the values route still replays it.
  - Plus `views/tests/test_ranking_columns_closed_bar.py`: `technicals_values` evaluates `VolumeDelta` and `StoredVWAP` on the latest closed bar.

*Frontend*
- [x] `platform/frontend/src/pages/ChartPage.tsx` + `lib/indicatorFormat.ts` (new, tested): `formatIndicatorValue(value, unit, precision)`.
  - `price` → `formatDecimal(value, precision.price)`.
  - `size` → `formatDecimal(value, precision.size)`.
  - `count` → `formatDecimal(value, 0)`.
  - `ratio` → `formatPercent`.
  - Guarded like `safeDecimal`.
  - A picker pane spec whose catalog entry has a unit for its output sets `format`. Entries without units keep `formatLegendValue`.
- [x] Stored Anchored VWAP:
  - `lib/anchoredVwap.ts` adds `ANCHORED_VWAP_SOURCES` with `stored`.
  - The drawing's settings offer it and disable bands for it, with the note "bands need per-trade prices".
  - `ChartPage` builds `{name: "AnchoredStoredVWAP", params: {anchor_t: String(time*1000)}}` entries for stored-source drawings, served by a second `usePickerIndicatorValues` instance.
  - The drawing's line and legend come from those values (gaps break the line). An entry error shows in that drawing's legend row.
  - Tests go in `ChartPage.test.tsx` and `anchoredVwap.test.ts`.
- [x] Volume colour:
  - `hooks/useCandles.ts` keeps `buy_v`/`sell_v` on the volume datum's source. `useLiveCandle.ts` types the optional aggregates.
  - `lib/volumeColor.ts` (new, tested): `volumeBarColor(bar, mode, up, down)`.
    - `direction`: up if `c ≥ o`, else down.
    - `delta`: the sign of `buy_v − sell_v`, alpha scaled by `|buy_v − sell_v| / (buy_v + sell_v)`, with a floor so a bar never vanishes.
    - Null flow or 0 volume → the neutral pane colour.
    - A `Known limit:` covers JSON numbers past 2^53.
  - `LightweightChart` paints per-point colours, including the forming bar.
  - `lib/chartLayout.ts` gains `volume_color_by` (normalise, save, compare).
  - The volume legend becomes configurable: its gear opens `VolumeSettingsDialog.tsx` (new, on `SettingsDialogShell`) with `colour by`.
  - Tests in `ChartPage.test.tsx` and `chartLayout.test.ts`.

*Docs*
- [x] `platform/docs/DATA_DICTIONARY.md`:
  - §2.7: one row per indicator with its formula, units, null rule and window.
  - §2.15: note the readers.
  - §4: lineage rows.
- [x] `platform/frontend/src/pages/docs/data.ts`: one `INDICATORS` entry each, with the formula, covering the 7 listed entries, the stored anchored source, and the Volume colour mode. Rewrite the CVD text for the session fix.
- [x] `platform/docs/DATA_INTEGRITY_AUDIT.md`: rows from D-186.
  - The CVD session defect and its fix.
  - An uncovered prefix gives None.
  - A `ForcedShare` above 1, unclamped.
  - Depth's 7-day window and last-row choice.
  - The stored VWAP is the second-close VWAP.
  - The volume colour past 2^53.
- [x] `platform/docs/DEPLOY_CHECKLIST.md`: a 33-6 deferred operator action (rebuild `data_api`) with verify steps.

**Acceptance Criteria:**
- Given a Bybit linear coin, when each new entry is added from the Indicators dialog on 1m, 1h and 1D, then it draws on every loaded bar from stored columns, and the legend prints at the instrument precision.
- Given the Technicals tab, when any listed new entry is picked, then `technicals-values` returns its latest-closed-bar value, and `AnchoredStoredVWAP` is not offered.
- Given an Anchored VWAP drawing set to `stored`, when it is reloaded, then its source persists and the line equals Σpv/ΣV from the anchor.
- Given `grep -rnE "buy_v|sell_v|liq_" platform/frontend/src --include=*.ts --include=*.tsx`, when the sites are inspected, then only the volume colour mapping does arithmetic on them.
- Given the backend suites (`views`, `kernel`, `candles`, `data_api`) and the frontend `npm test`/`lint`/`build`, when they run, then all pass, and the regenerated `openapi.json`/`schema.ts` match the export.

## Spec Change Log

## Review Triage Log

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 10: (high 1, medium 3, low 6)
- defer: 0
- reject: 6: (high 0, medium 1, low 5)
- addressed_findings:
  - `[high]` `[patch]` A stored-source Anchored VWAP began one bar after its handle and left out the anchor bar's flow. The server kept `t >= anchor_t` while the client snaps the handle down to the bar holding it. `_anchored_vwap_replay` now floors the anchor with `bucket_start_ms` at the chart's width, for both the bars and the store prefix. Test added.
  - `[medium]` `[patch]` A later page's success wiped an earlier page's replay error, so a partial stored line read as whole (D-187). `useStoredAnchoredVwap` now keeps errors per request (instrument, width, anchors) and merges them across pages, with no reset effect. An errored stored drawing draws no line. Tests added.
  - `[medium]` `[patch]` `AverageTradeSize` printed at the size step, so an average of 0.0004 on a 0.001 grid read `0.000`. Added a `size_mean` unit (server `INDICATOR_UNITS`, frontend `formatIndicatorValue`) that prints 3 decimals finer, with tests; DATA_DICTIONARY §2.7 updated.
  - `[medium]` `[patch]` `DepthWithinBps` as a Technicals column reads the whole capped window per coin. Documented as a cost `Known limit:` in the replay, in audit D-189 and DATA_DICTIONARY §2.7, plus a DEPLOY_CHECKLIST watch step.
  - `[low]` `[patch]` The anchored error showed raw epoch ms, and the page test pinned a message the backend never sent. It now names ISO times and the reason (no tiling width / no store / the store's first bar). The tests now match the real text, and a 30 s untiled-width case was added.
  - `[low]` `[patch]` A known-flow bar with a null `pv` or precision raised a bare `TypeError`. It is now a named `ValueError` (DATA-07), with a test.
  - `[low]` `[patch]` An explicit JSON `null` for `volume_color_by` was accepted. It is now refused with a 422 naming the key, as the strict validator promises; `None` was added to the refusal test.
  - `[low]` `[patch]` The stored-VWAP values hook fetched in Lines mode, where nothing draws. It now takes `enabled` (Candles mode only), with a test.
  - `[low]` `[patch]` The new hook's reset-in-effect raised a `react(set-state-in-effect)` lint warning (TEST-04). It is replaced by request-keyed state.

### 2026-10-06 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 0, medium 1, low 7)
- defer: 0
- reject: 10: (high 0, medium 2, low 8)
- addressed_findings:
  - `[medium]` `[patch]` `_store_prefix` called a prefix "covered" from where the store starts only, so a capture outage inside `[start, first bar)` was summed as observed while D-187 promised never a partial sum. Added a `Known limit:` (continuity unchecked, same as an outage inside the page) with the upgrade path (the capture's gap markers), and amended audit D-187.
  - `[low]` `[patch]` A known-flow bar with a null `buy_n`/`sell_n`/`sell_v`/precision, or a partial liquidation group (`liq_short_v`/`liq_n` null), raised a bare `TypeError` in the per-bar replays. `_flow_values` and `_liquidations_known` now check the fold's own groups (`FLOW_KEYS`, `LIQUIDATION_KEYS`, `PRECISION_KEYS`) and raise a named `ValueError` (DATA-07). Parametrized test added; the `views` boundary allowlist gains the three key tuples.
  - `[low]` `[patch]` In the delta colour mode an exactly balanced bar was full-opacity neutral, the boldest bar on the pane. It now takes neutral at the `MIN_DELTA_ALPHA` floor; the module comment, the docs page and the tests are updated.
  - `[low]` `[patch]` `withAlpha` replaced an `rgba()` colour's own alpha instead of multiplying it. Fixed, tested, and the known limit now also names percentage/fractional `rgb()`.
  - `[low]` `[patch]` The `units` field comment (`data_api/routes/indicators.py`) and DATA_DICTIONARY §2.7 left out `size_mean`. Fixed.
  - `[low]` `[patch]` The DEPLOY_CHECKLIST 33-6 catalog curl described output it cannot print. It now shows the exact dict and the `False`.
  - `[low]` `[patch]` The docs page said Average Trade Size prints at the size precision. It now says 3 decimals finer.
  - `[low]` `[patch]` Python and TypeScript `INDICATOR_UNITS` had no mirror test. Added `test_indicator_units_mirror_the_frontend`, which also checks every catalog unit is in the set.

## Design Notes

- **Why a store prefix, not a wider candle read.** One mechanism serves CVD `all`, the sessions and the anchor. It is O(1) rows into Python (MEM-01).
  - The `Known limit:`: a page older than the 1m/5m retention, or than the store itself, has no prefix, so its session or anchored values are None, never partial.
  - Upgrade path: fold the raw seconds of `[start, t0)` for the prefix.
- **Why an unlisted entry.** The AC makes the anchored mode a drawing once 32.7 has landed. `listed=False` keeps it out of the picker, saved configs and the Technicals (`merged_catalog`). The values route still replays it, so the drawing reuses the paging values hook.
- **Exact VWAP example.** `pv` is in `10^-(pp+sp)`, ΣV in `10^-sp`:
  ```python
  Fraction(pv_units, volume_units * 10**price_precision)  # -> float at the edge
  ```
  For session sums, rescale every term to max pp and max sp first.

- Implementation decisions (dev, 2026-10-06):
  - No `first_bar_t`: `queries.oldest_t(db, iid, width, traded_only=False)` already returns the store's first observed bar, so it is reused (DESIGN-03).
  - `flow_totals` is one query grouped by UTC day as well as by the two precisions, so no SQLite `SUM` spans more than a day. A long anchored `pv` (about 2e17 a day on BTC spot) would otherwise overflow int64.
  - `AnchoredStoredVWAP` defaults `anchor_t` to `"0"`, a valid digit string. The listed-defaults test is kept, and `test_every_custom_default_passes_listed_or_not` covers every entry.
  - Book decoding for `query_books_at` lives in `kernel.second_snapshot.book_float_rows`, the one decoder of the gap-encoded book layout.
  - The stored drawings' values come through `hooks/useStoredAnchoredVwap.ts`, which wraps a second `usePickerIndicatorValues`. It has its own test seam, and its `Known limit:` is that it is not refetched on bar close, like the picker panes.
  - Units gained `size_mean` in review: a mean of sizes printed 3 decimals finer than the size step.

## Verification

**Commands:**
- `cd platform && python3 -m pytest kernel/tests candles/tests views/tests data_api/tests -q` -- expected: all pass, no warnings.
- `cd platform && ruff check . && ruff format --check . && mypy views kernel candles data_api` -- expected: clean.
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: all pass.
- `cd platform && python3 -m data_api.export_openapi` (check its docstring for the exact invocation/output path) then `cd frontend && npm run codegen`; `git status` shows no further change to `openapi.json`/`src/api/schema.ts` after re-running -- expected: no diff.

## Auto Run Result

**Summary:** Story 33.6 adds seven listed order-flow entries to the custom catalog: `VolumeDelta`, `OrganicDelta`, `ForcedShare`, `TradeCount` (`split`), `AverageTradeSize`, `StoredVWAP` (`bar|session`) and `DepthWithinBps`.
- It also adds an unlisted `AnchoredStoredVWAP`, behind a `stored` source on the 32.7 Anchored VWAP drawing.
- Catalog entries now carry a per-output `units` table, so legends print at the instrument precision.
- The Volume pane gains a `colour by: direction|delta` setting (`volume_color_by`).
- CVD and `StoredVWAP` `session` are seeded from an exact store prefix (`queries.flow_totals`), fixing D-186.
- The implementation run is commit 5d44a54895. This follow-up review pass applied 8 localized patches.

**Files changed in this follow-up pass:**
- `platform/views/indicator_picker.py`: named corrupt-row errors for partial flow and liquidation groups; `_store_prefix` continuity `Known limit:`.
- `platform/views/tests/test_indicator_picker_custom.py`: partial-group tests; the `INDICATOR_UNITS` mirror test.
- `platform/tests/test_boundaries.py`: views may import the fold's `FLOW_KEYS`/`LIQUIDATION_KEYS`/`PRECISION_KEYS`.
- `platform/frontend/src/lib/volumeColor.ts` (+ test): balanced bar at the floor; `withAlpha` multiplies an existing alpha.
- `platform/frontend/src/pages/docs/data.ts`: Average Trade Size precision; delta colour text.
- `platform/data_api/routes/indicators.py`, `platform/docs/DATA_DICTIONARY.md`: `size_mean` in the unit list.
- `platform/docs/DATA_INTEGRITY_AUDIT.md`: D-187 continuity note.
- `platform/docs/DEPLOY_CHECKLIST.md`: the exact catalog-curl output.

**Review (follow-up pass):** 8 patches (1 medium, 7 low), 0 deferred, 10 rejected. The rejected findings:
- Sub-minute/90 s session None and anchor older than the loaded bars: documented `Known limit:`s.
- CVD `all` from 0 past the store: pre-existing D-159.
- Sticky stored-VWAP errors: a failed page is never refetched, so its line is partial anyway.
- `DepthWithinBps` 1m Technicals cost: refuted. The window is the 120 bars `technicals_values` passes, not 7 days.
- Double `_bar_totals`, a missing boundary test, the anchor-gap snap mismatch, a per-row int64 overflow, the unmigrated-store prefix: negligible or unreachable.

**Verification:**
- `python3 -m pytest kernel/tests candles/tests views/tests data_api/tests tests/test_boundaries.py`: 1725 passed. The only failures were the 9 Redis-bound `data_api` tests (`test_archive`, `test_rankings`, `test_rankings_mode`): no Redis was running, and this pass does not touch them.
- ruff 0.16.8 check + format: clean on the touched Python files.
- mypy: no new errors. The 2 in `indicator_picker.py` (`_order_book_deltas`, from 2026-09) and those in `kernel/indicators.py` predate the story.
- Frontend `npm test`: 1169 passed. `npm run lint`: only the 3 pre-existing warnings. `npm run build`: OK.

**Residual risks:**
- Session and anchored values are None where the store does not cover the start (D-187), and a capture outage inside a covered prefix is summed as observed (now documented).
- `DepthWithinBps` at 1D in the Technicals reads up to 7 daily files per coin (D-189).
- The stored Anchored VWAP is not refetched on bar close.

**Follow-up review:** not recommended. This pass's patches are small, localized guards, docs and a colour-shading tweak, none high severity.

