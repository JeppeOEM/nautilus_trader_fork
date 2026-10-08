---
title: 'Story 33.7: Rankings sorts by every column, gains derivatives, flow and range columns, and saved filter presets'
type: 'feature'
created: '2026-10-06'
status: 'done'
baseline_revision: '672faca7e8c98a839c5159c07e4386451047fa50'
final_revision: '6368568c1cdec2a10390577df75cd3c182053ead'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Story 33.4 publishes funding, open interest, basis, liquidations, forced share, relative volume and the 24 h range on every `rankings:live` row, but the Rankings page shows none of them. It sorts only by Symbol and Exchange. Its filters vanish on reload. The History page tiles only OI, funding and liquidation notional.

**Approach:**
- Add ten columns to `views/ranking_columns.py` `RANKING_COLS` and its TS mirror. Each new column is also a filter field.
- Make every Performance column sortable (asc → desc → rank order), with missing values always last and the sort key kept in `localStorage`.
- Publish OI Δ % from `ranking` (SSOT-02), because 33.4's `oi_change_*` are absolute venue units.
- Store named filter presets server-side in the one preferences directory behind `GET/PUT /api/rankings/filter-presets`.
- Tile forced share and relative volume on the History page.

## Boundaries & Constraints

**Always:**
- **One computer (SSOT-02).** Add `oi_change_1h_pct` and `oi_change_24h_pct` in `ranking/domain/derivs.py`:
  - computed exactly in `Decimal` as `(latest − base) / base × 100` and floated only at the end;
  - None when `base` is None, `latest` is None or `base == 0`.
  - The browser computes no metric. It only scales a value for display (`× 100` for a fraction shown as a percent).
- **Added keys only (AD-D12).**
  - The two keys go at the end of `DERIVS_FIELDS` and at the end of `metrics_store.COLS`, as nullable columns added by the existing `_migrate`, and also in `MetricHistoryItem`.
  - `test_replay.py`'s recorded bytes still pass with the new keys stripped.
- **Spot (epic rule).** On a row whose `market` is `"spot"`, every derivatives column renders `—`, never 0, whatever the value. Those columns are OI, both OI Δ %, Funding, Basis, Liq 1h, Liq L/S and Forced %.
  - Sort and filter read such a cell as missing.
  - Rel vol and 24h range are not derivatives, so spot shows them.
- **Missing.** A missing value is `null`, `undefined`, a non-number or `NaN`.
  - It sorts last in both directions, and ties break by rank.
  - It never matches a filter, as today.
- **Staleness.** The `⏱`/`⚠` markers, the 15 s rule and their placement stay unchanged.
- **Mirror tests stay green.**
  - The TS `RANKING_COLS` stays a flat array of `{ key: "...", label: "...", ... }` literals for `data_api/tests/test_ranking_columns_mirror.py`. Tooltip builders live outside the array, keyed by column key.
  - A lowerCamelCase helper named in a `ranking_columns.py` comment must exist in the frontend (`test_frontend_named_helpers.py`).
- **Filter units follow the existing convention.** A filter value is typed in the row's raw units, and `=` matches what the cell shows (`DisplayPrecision.scale`). The field label names the typed unit, as `Vol24h (raw USD)` does today.
- **Presets file.**
  - It is `screener_filter_presets.toml` under `CHART_PREFERENCES_DIR` (SSOT-06). Its path is derived in `data_api/settings.py`; no new mount.
  - It is written only by the PUT route through `views/preferences.py`, with `_write_atomic`, a full rewrite, under `PREFERENCES_LOCK`.
  - A malformed body gets a 422 naming the field (`presets[i].conditions[j].value`). Nothing is ever dropped (DATA-07).
  - A missing file reads as empty. A corrupt or unwritable file gives a 500.
- **No new dependency. Never touch `nautilus_trader/` or `crates/`.**
- Simplifications carry a `Known limit:` comment with the upgrade path.

**Block If:**
- `rankings:live` rows lack `market` or the 33.4 `DERIVS_FIELDS` keys.
- The mirror test can't hold the new columns without changing what it checks.

**Never:**
- No sorting or column changes on the Technicals tab. Its columns stay unsortable, though the shared row order applies there too.
- No client-side computation of OI Δ %, basis, ratios or the annualised rate.
- No `localStorage` for presets. No per-browser preset copy.
- No change to the existing filter semantics in `filters.ts` `compare`/`applyFilters`.
- No table framework or dropdown library (§B5: hand-rendered).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| OI Δ % | OI 1 h ago 200, now 210 | `oi_change_1h` 10.0, `oi_change_1h_pct` 5.0 | — |
| OI base zero / short history | base 0, or series under 1 h | `oi_change_1h_pct` None | — |
| Sort numeric | `pct_1h` [2, null, −1, 5], click once | first click ascending −1, 2, 5, null; second click 5, 2, −1, null; third click rank order | — |
| Sort spot derivative | spot row with `funding_rate` 0.0001 (stray) sorted by Funding | renders `—`, sorts last | — |
| Sort persisted | sort Funding desc, reload | Funding desc restored; stored key of a removed column → rank order | bad JSON / throwing storage → rank order, no crash |
| Funding cell | rate 0.0001, annualised 0.1095, `next_funding_ns` now+1 h | `0.0100%`; tooltip `annualised 10.95% · next payment in 01:00:00` | next null → `next payment unknown` |
| Liq cell | long 1.5, short 0.5, notional 120000 | `120.0K`; tooltip with long/short base sizes; Liq L/S `75.0%` | no feed → `—` |
| Range cell | position 0.25, high 110, low 100 | bar with marker at 25 %, text `25%`, tooltip `low 100 · high 110` | flat range (null) → `—` |
| Preset save | name "lev", conditions [Funding > 0.0003] | PUT writes the file; dropdown lists "lev"; chip `lev` | PUT fails → inline error, `console.error`, list reloaded |
| Preset recall | pick "lev" | conditions replaced; chip shows `lev`; any later filter edit clears the chip | field unknown → inline notice naming it; condition not applied |
| Preset PUT invalid | duplicate name, empty name, >64 chars, op `!=`, `value` NaN/bool, text value with `>`, >100 presets, >50 conditions | 422 naming the field | file untouched |
| History tiles | rows with `forced_share_1h` / `relative_volume` all null | no tile for that metric | — |

</intent-contract>

## Code Map

All paths are under `platform/`.

- `views/ranking_columns.py`: `RANKING_COLS` (:73-89), `(key, label, format_fn)` tuples; the comment block above it (units contract, upgrade path).
- `frontend/src/pages/RankingsPage.tsx`:
  - the `RANKING_COLS` mirror (:92-108), the formatters `fmtSigned`/`fmtPercent`/`fmtMillions`, and `formatCell` (:110);
  - sort: `SortKey`, `SORT_FIELDS`, `nextSort`, `compareText`, `sortRows`, `SortHeader` (:140-218), sort state (:317);
  - venue-chip storage `DESELECTED_VENUES_STORAGE_KEY`, `loadDeselectedVenues`/`saveDeselectedVenues` (:34-56), the pattern for the sort key;
  - `filterFields` (:361-382), `readField` (:384), filter state (:330), header (:543-545), cells (:654-655), staleness markers (:635-636), `ExchangeCell` (`market` field);
  - `saveEntries` (:408-422): the save-error pattern.
- `frontend/src/pages/FilterPanel.tsx`: `FilterField`, the builder and its chips. `pages/filters.ts`: `DisplayPrecision`, `FilterCondition`, `FILTER_OPERATORS`, `compare`, `applyFilters`.
- `frontend/src/lib/units.ts`: `formatPercent` (:166), `formatCountdown` (:172).
- `frontend/src/api/client.ts`: `fetchTechnicalsColumns`/`saveTechnicalsColumns` (:309-326), the pattern for the preset client.
- `frontend/src/pages/HistoryPage.tsx`: `METRIC_COLUMNS` (:15-32) and the skip rule (:58-65).
- Tests:
  - `pages/RankingsPage.test.tsx`: the `vi.mock("../api/client")` factory, `useLiveChannelMock`/`liveMessage`, `PERFORMANCE_COL_LABELS`, the "Symbol and Exchange columns (Story 29.1)" sort tests, and the storage-throwing tests (~:757).
  - `pages/HistoryPage.test.tsx`; `pages/filters.test.ts`.
- `ranking/domain/derivs.py`: `DERIVS_FIELDS` (:102), `OpenInterestSeries.fields` (:181). `ranking/domain/board.py` `_slow_row` (:524), `_rank_row` (:570).
- `ranking/infrastructure/metrics_store.py`: `COLS` (:37-65), `_migrate`. `data_api/routes/metrics.py`: `MetricHistoryItem`.
- Tests for the two appended keys:
  - `ranking/tests/test_replay.py` strips them;
  - `research/application/ranking_history.py` `METRIC_COLUMNS` mirrors `COLS`;
  - `verification/tests/test_ssot_trace.py` accounts for the new keys.
- `views/preferences.py`:
  - the module docstring's file list;
  - `_write_atomic` (:270), `load_/save_screener_columns` (:238-268);
  - `DrawingError` with `.field` (:298) and `validate_drawings` (:473), the validator precedent.
- `data_api/settings.py:49-70`: the derived preference paths. `data_api/routes/rankings.py`: the technicals-columns routes (:186/:214). `data_api/routes/drawings.py`: raw `request.json()`, 400/422/500 mapping. `data_api/routes/indicators.py:76`: `PREFERENCES_LOCK`.
- Docs:
  - `docs/DATA_DICTIONARY.md` §2.10 (:2979; its key list is stale: it lists `microprice_lean` and lacks `pct_1w`/`pct_1m`), §3.3 (:3752, 33.4 fields at :3795-3830), §3.4, §3.5 (:3879);
  - `docs/DATA_INTEGRITY_AUDIT.md`: the last row is D-191;
  - `docs/DEPLOY_CHECKLIST.md`: 33-6 section (:1591), the format to copy;
  - `_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md` §B3 (:952), §B4 (:973), §B5 (:990);
  - `frontend/src/pages/docs/kbData.ts`: KB docs.

## Tasks & Acceptance

**Execution:**

*Ranking: OI Δ %*
- [x] `ranking/domain/derivs.py`:
  - `OpenInterestSeries.fields` adds `oi_change_1h_pct`/`oi_change_24h_pct` per Always.
  - Append both to `DERIVS_FIELDS`.
  - Hand-computed tests in `ranking/tests` for the value, base 0 and short history.
- [x] `ranking/infrastructure/metrics_store.py`, `data_api/routes/metrics.py`, `research/application/ranking_history.py`:
  - append both to `COLS`, `MetricHistoryItem` and `METRIC_COLUMNS`;
  - extend `test_replay.py`'s stripped-key tuple and `test_ssot_trace.py`;
  - add a migration test for the two columns.

*Columns, sort, filters*
- [x] `views/ranking_columns.py`: append to `RANKING_COLS`, in this order:

  | key | label |
  |---|---|
  | `open_interest` | `OI` |
  | `oi_change_1h_pct` | `OI Δ1h %` |
  | `oi_change_24h_pct` | `OI Δ24h %` |
  | `funding_rate` | `Funding` |
  | `basis_mi_bps` | `Basis (bps)` |
  | `liq_notional_1h` | `Liq 1h` |
  | `liq_ratio_1h` | `Liq L/S` |
  | `forced_share_1h` | `Forced %` |
  | `relative_volume` | `Rel vol` |
  | `range_position_24h` | `24h range` |

  - Each gets a `format_fn` that matches the TS display.
  - Update the units-contract comment with each column's unit and the spot-dash rule.
  - Add `DERIVATIVE_COLUMN_KEYS`, the eight derivatives keys.
  - Tests in `views/tests` cover the new definitions and the derivative key set.
- [x] `frontend/src/pages/RankingsPage.tsx`, the mirror columns. Precisions and formats:

  | Column | Precision | Display |
  |---|---|---|
  | OI | `{decimals: 2}` | as is |
  | OI Δ % | `{decimals: 2}` | `fmtPercent` |
  | Funding | `{scale: 0.01, decimals: 4}` | shown `%` |
  | Basis | `{decimals: 2}` | signed |
  | Liq 1h | `{scale: 1e3, decimals: 1}` | `K` suffix |
  | Liq L/S, Forced % | `{scale: 0.01, decimals: 1}` | `%` |
  | Rel vol | `{decimals: 2}` | `×` suffix |
  | 24h range | `{scale: 0.01, decimals: 0}` | `%`, rendered as a small inline bar (marker at `position × 100 %`) plus the text |

  - Add a `DERIVATIVE_COLUMNS` set mirroring `DERIVATIVE_COLUMN_KEYS`, with a mirror assertion added to `test_ranking_columns_mirror.py`.
  - Cell tooltips (a `title` attribute) come from a key → builder map:
    - Funding: `annualised {formatPercent(funding_annualised, 2)}% · next payment in {formatCountdown(next_funding_ns/1e6 − now)}`, or `next payment unknown`.
    - Liq 1h: long and short base sizes.
    - Liq L/S: "long share of liquidated size".
    - 24h range: `low … · high …`.
  - Spot rows render `—` in the derivatives columns.
- [x] `RankingsPage.tsx`, sort:
  - generalise to every `RANKING_COLS` key plus `symbol`/`venue`;
  - a numeric comparator per Always (missing last, ties by rank);
  - a `SortHeader` on every Performance metric header;
  - `readSortValue` treats a spot derivative as missing;
  - persist `{key, direction}` under `localStorage` `rankings-sort`, try/catch both ways, ignore an unknown key or bad shape.
- [x] `RankingsPage.tsx`, filters:
  - the new columns join `filterFields` with unit-naming labels: `Funding (fraction/interval)`, `Liq 1h (raw quote)`, `Liq L/S (fraction)`, `Forced % (fraction)`, `24h range (fraction)`, `OI (venue units)`;
  - `readField` returns undefined for a spot derivative.

*Presets*
- [x] `views/preferences.py`:
  - add `FilterPresetError(ValueError)` with `.field`;
  - add `validate_filter_presets(body) -> list[FilterPreset]` (rules from the matrix: name stripped, non-empty, ≤ 64, unique; 1–50 conditions; `field` a 1–512 char string; `op` in `FILTER_OPERATORS`; a value a finite non-bool number, or a ≤ 512 char string only with `=`; ≤ 100 presets);
  - add `load_filter_presets`/`save_filter_presets` (`v = 1`, `[[presets]]` with `[[presets.conditions]]`);
  - add the file to the module docstring;
  - tests in `views/tests/test_filter_presets.py` cover the round trip, the pinned written text, every refusal naming its field, and a mirror of `FILTER_OPERATORS` against `pages/filters.ts`.
- [x] `data_api/settings.py` + `data_api/routes/rankings.py`:
  - `SCREENER_FILTER_PRESETS_PATH`;
  - `GET /api/rankings/filter-presets` → `FilterPresetsResponse{presets: [{name, conditions: [{field, op, value: float|str}]}]}`;
  - `PUT` with a raw JSON body `{presets: [...]}` → 200 with the stored list; 400 for bad JSON, 422 for an invalid body, 500 for a corrupt or unwritable file;
  - tests in `data_api/tests/test_filter_presets.py`;
  - regenerate `frontend/openapi.json` and `src/api/schema.ts`.
- [x] `frontend/src/api/client.ts`: `fetchFilterPresets`, `saveFilterPresets`, following the technicals-columns pattern.
- [x] `frontend/src/pages/FilterPanel.tsx` (+ `RankingsPage.tsx` wiring):
  - a preset `<select>` (recall replaces the conditions), a name input with Save (reads "Overwrite" when the name exists, disabled with no conditions or an empty name), and Delete for the selected preset;
  - an active-preset chip, cleared by any filter edit;
  - a recalled condition gets its precision from the current field list, `tech:` fields from `TECHNICALS_PRECISION`;
  - an unknown field shows an inline notice naming it and is not applied;
  - a save failure follows the `saveEntries` pattern.

*Tests, History, docs*
- [x] `frontend/src/pages/RankingsPage.test.tsx`:
  - extend the client mock;
  - update `PERFORMANCE_COL_LABELS`;
  - cover the sort cycle with nulls in both directions, ties by rank, sort persistence with bad storage, the spot dashes (and spot sorting last), the tooltips (funding countdown), the range bar, and the preset save → recall → chip → edit clears chip → delete round trip, plus the unknown-field notice and a save failure.
- [x] `frontend/src/pages/HistoryPage.tsx` + test: add `forced_share_1h` "Forced share 1h (fraction)" and `relative_volume` "Relative volume (×)" after the 33.5 tiles. The skip rule is tested.
- [x] Docs:
  - `docs/DATA_DICTIONARY.md`:
    - §2.10: rewrite the key list to match `RANKING_COLS`, and add the units, spot dash, sort rule and presets;
    - §3.3: the two OI Δ % keys;
    - §3.4: `COLS` appended;
    - §3.5: presets and History tiles.
  - `spec-multi-exchange-screener-chart.md` §B3/§B4 (and §B5's sort note).
  - `docs/DATA_INTEGRITY_AUDIT.md`, from D-192:
    - the OI Δ % base rule;
    - fraction filters are typed raw;
    - preset last-write-wins across tabs and processes (`Known limit:`);
    - the countdown uses the browser clock against the venue's `next_funding_ns`;
    - a stored preset field can outlive its column.
  - `docs/DEPLOY_CHECKLIST.md`: a `33-7-…` deferred action to rebuild `ranking` and `data_api` (the migration runs itself), with curl and browser verify steps.
  - `kbData.ts`: a Rankings screener KB entry (columns and units, sort, presets file).

**Acceptance Criteria:**
- Given live rankings with Bybit linear, Bybit spot and Hyperliquid rows, when the Performance tab renders, then all ten new columns show:
  - the spot row reads `—` in every derivatives column;
  - the Hyperliquid row reads `—` in the liquidation columns (no feed);
  - the staleness markers are unchanged.
- Given any Performance header, when it is clicked three times, then the rows go ascending, descending, then back to rank order, and a reload restores the last non-null sort.
- Given a preset saved in one browser, when the page opens in another browser, then the preset is listed and recalling it applies the same conditions.
- Given the backend suites (`views`, `data_api`, `ranking`, `research`, `verification`, `tests`) and frontend `npm test`/`lint`/`build`, when they run, then all pass, and the regenerated `openapi.json`/`schema.ts` match the export.

## Spec Change Log

## Review Triage Log

### 2026-10-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 12: (high 2, medium 3, low 7)
- defer: 0
- reject: 4: (high 0, medium 0, low 4)
- addressed_findings:
  - `[high]` `[patch]` `=` on the scale-0.01 columns (Funding, Liq L/S, Forced %, 24h range) never matched some values because of float noise in `value / scale`. Fixed with `typedInDisplayUnits` (15 significant digits) and tested.
  - `[high]` `[patch]` A failed or pending preset GET followed by Save sent a PUT of one preset, wiping the stored list. Save and Delete are now disabled until a GET succeeds; a load error shows inline.
  - `[medium]` `[patch]` A stale mount GET resolving after a PUT overwrote the list. Fixed with a request sequence guard.
  - `[medium]` `[patch]` Save dropped hidden `tech:` conditions and the recalled-but-unapplied ones. It now saves `allFilters` plus the unapplied raws, and the notice says "kept on save".
  - `[medium]` `[patch]` A metric-column sort also reordered the Technicals tab, where no header shows it. It now applies only on Performance.
  - `[low]` `[patch]` The same preset couldn't be recalled again after an edit. The select now resets after each recall.
  - `[low]` `[patch]` The notice now names the real reason: unknown field, operator does not fit, or value does not fit.
  - `[low]` `[patch]` The range-bar marker is clamped visually; the text keeps the real value.
  - `[low]` `[patch]` A funding payment in the past reads "next payment due", not `00:00:00`.
  - `[low]` `[patch]` The chip is set after a save only if the filters are unchanged.
  - `[low]` `[patch]` Integer preset values beyond ±2^53 now get a 422.
  - `[low]` `[patch]` The `ranking_columns.py` comment no longer claims Python applies the spot dash.

### 2026-10-07 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 12: (high 0, medium 4, low 8)
- defer: 0
- reject: 12: (high 0, medium 2, low 10)
- addressed_findings:
  - `[medium]` `[patch]` A failed preset GET at mount left Save and Delete disabled until a page reload, with nothing retrying it. A Retry button now shows beside the load error (`useFilterPresets().retry`).
  - `[medium]` `[patch]` A `tech:` condition recalled before the Technicals columns had loaded was reported as "unknown field". It now reads "Technicals columns not loaded" and is kept on save.
  - `[medium]` `[patch]` After a recall of "lev" and a save as "lev2", Delete still removed "lev". Delete now targets the preset last recalled or saved; its title names it.
  - `[medium]` `[patch]` A PUT with an integer too large for a double (`10**400`) raised `OverflowError` from `math.isfinite` (a 500, not a 422). The integer check now runs first and refuses it naming the field.
  - `[low]` `[patch]` A recalled `tech:` output the loaded values do not have (a misspelt or removed attr) was applied and emptied the table. It is now "unknown field".
  - `[low]` `[patch]` Save was disabled when the only conditions were hidden Technicals ones or recalled-but-unapplied ones, though Save stores them. It is now enabled by what a Save would store (`PresetControls.savable`).
  - `[low]` `[patch]` The ±2^53 integer bound refused exact doubles such as `10**16`. Replaced by an exactness check (`_exact_in_a_double`).
  - `[low]` `[patch]` A presets file with `v = true` loaded as v1 (`True == 1`). The version must now be an int.
  - `[low]` `[patch]` The API model's `op` Literal is a third copy of the operators. A test now pins it to `preferences.FILTER_OPERATORS`.
  - `[low]` `[patch]` The settings subprocess test hid its own failure. It now asserts the return code with stderr and runs from `platform/`.
  - `[low]` `[patch]` The `typedInDisplayUnits` comment said typed digits are never rounded. It now states the 15-digit bound.
  - `[low]` `[patch]` OI sorts and filters across per-coin venue units with no `Known limit:`. Added in `views/ranking_columns.py`, with the OI-notional upgrade path.

## Design Notes

**Why OI Δ % is published, not computed client-side.** 33.4's `oi_change_*` are absolute venue units. A percent needs the base value, which only `ranking` holds exactly. SSOT-02 makes `ranking` the sole computer of rolling metrics. The keys are appended, so recorded replays still pass with them stripped.

**Preset storage shape** (TOML, written by `tomli_w`):
```toml
v = 1
[[presets]]
name = "levered longs"
[[presets.conditions]]
field = "funding_rate"
op = ">"
value = 0.0003
```
Precision is not stored. It is display configuration, re-derived from the field list on recall, so a column's precision change never makes stored presets stale.

## Verification

**Commands:**
- `cd platform && python3 -m pytest views/tests data_api/tests ranking/tests research/tests verification/tests tests -q -p no:cacheprovider`: all pass, no warnings. Redis-backed `data_api` tests need a throwaway redis on 6379.
- `cd platform && PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json && cd frontend && npm run codegen && git diff --exit-code src/api/schema.ts openapi.json` after commit: stable.
- `cd platform/frontend && npm test && npm run lint && npm run build`: green.
- `ruff format --check` / `ruff check` on touched Python: clean.


## Auto Run Result

Status: done

- **Change.** A follow-up review of Story 33.7, which shipped in 41b9829ad6. Twelve review patches landed in 6368568c1c:
  - Presets: a Retry after a failed load. A `tech:` condition recalled before the Technicals columns load reads "Technicals columns not loaded". An unknown Technicals output is now reported. Delete targets the preset last recalled or saved. Save is enabled by what it stores.
  - Validator: an exact-double integer check, which also replaces an `OverflowError` 500 with a 422. The file version must be an int.
  - Tests and docs: a test pins the operator Literal to `FILTER_OPERATORS`, the subprocess test is fixed, the filters comment is corrected, and OI's cross-unit sort is recorded as a `Known limit:`.
- **Files.**
  - `frontend/src/pages/{filterPresets.ts,FilterPanel.tsx,RankingsPage.tsx}`: the retry, Technicals recall reasons, the Delete target and `savable`.
  - `frontend/src/pages/RankingsPage.test.tsx`: 6 new tests.
  - `frontend/src/pages/filters.ts`: comment fix.
  - `views/preferences.py`: `_exact_in_a_double` and the int-only version. `views/tests/test_filter_presets.py`: cases for both.
  - `data_api/tests/test_filter_presets.py`: the Literal mirror and the subprocess return code.
  - `views/ranking_columns.py`: the OI `Known limit:`.
  - `docs/DATA_DICTIONARY.md` §2.10 and `kbData.ts`: the preset behaviour text.
- **Review.** 12 patches (4 medium, 8 low), 0 deferred, 12 rejected:
  - by spec: the spot dash, Liq 1h formatting, Overwrite without confirmation, the `format_fn` the spec mandates, and the Decimal docstring wording;
  - precedent: the PUT body size and the 500 detail naming the path follow the drawings route;
  - noise: Save's condition order, carry-over into a save-as (the notice says "kept on save"), `SortKey` typing, the `str.strip`/`trim` control-character mismatch, and the raw 422 above 50 conditions.
- **Verification.**
  - Backend `views`/`data_api`/`ranking`/`research`/`verification`/`tests` with a throwaway redis: all pass except `tests/test_legacy_names.py::test_only_published_language_keeps_a_legacy_name`, which also fails at baseline.
  - Frontend: vitest 1203 passed; lint shows only the 3 warnings in untouched files; build green.
  - The OpenAPI export is unchanged; ruff is clean except the C901 on `validate_drawing`, which predates this story.
- **Residual risk.** Presets stay last-write-wins across tabs (D-194). Follow-up review is not recommended: the patches are localized and each has a test.
