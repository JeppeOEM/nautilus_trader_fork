---
title: 'Story 33.8: Alert conditions beyond a price cross, indicator and derivatives conditions, and an Alerts page that creates and edits'
type: 'feature'
created: '2026-10-07'
status: 'done'
baseline_revision: '2d70986235b74bd490d3ea25e2f097fc9b386536'
final_revision: 'f088a46b1fad8a6238481d3140cdfe0f59b78106'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** an alert today holds one condition: a static `level` crossed by the forming bar's close (`alerting/domain/alert.py`, `policy._crossed`). Funding, open interest, liquidations, indicators and trendlines cannot be watched. The Alerts page can only list and delete. The chart's dialog offers only a price cross.

**Approach:**
- An alert gains a `condition` table (an added key) with 14 kinds. Each kind is a pure `evaluate` in `alerting/domain/conditions.py`, and the existing firing policies apply to every kind.
- Price, trendline and indicator kinds are fed by the bar observer (`AlertEngine.on_bar`). Indicators are read through the chart's own replay, behind a port.
- Derivatives kinds are fed by a new observer hook on 33.4's `LiveDerivsBus` (`AlertEngine.on_deriv`, `on_liquidation`).
- `PUT /api/alerts/{id}` edits an alert. The Alerts page gains a create form and an edit dialog. The chart dialog offers every kind, prefilled from a clicked hline or trendline.

## Boundaries & Constraints

**Always:**
- **Back-compat (AD-D12).**
  - `Alert` gets `condition: dict | None = None` and `invalid_reason: str | None = None` appended after `last_fired_ns`. No field is renamed or reordered.
  - A stored alert without `condition` reads as `{"kind": "price_cross", "level": <level>}`, normalised in `Alert.__post_init__`.
  - `level` becomes `float | None`. It equals `condition["level"]` for the five level-bearing price kinds, and is None (omitted in TOML) for every other kind. A loaded file where they disagree raises, as a corrupt file does today.
  - Existing `/api/alerts` keys, `detail` strings and the toast's `{"channel": "alerts", "alert": {"id", "message"}}` stay. Additions only: response `condition`, `condition_text`, `invalid_reason`, status `"invalid"`, and toast `alert.condition`.
  - POST without `condition` behaves exactly as today. POST with both `level` and `condition` is a 422 unless `condition` is a level-bearing price kind with an equal `level`.
- **Condition kinds and fields** (validated in `conditions.validate_condition`, with `ConditionError(ValueError)` carrying `.field`, and mirrored by pydantic models in the route):

  | kind | fields |
  |---|---|
  | `price_cross` | `level` |
  | `price_cross_up` | `level` |
  | `price_cross_down` | `level` |
  | `price_above` | `level` |
  | `price_below` | `level` |
  | `pct_move` | `pct`: finite, ≠ 0, \|pct\| ≤ 1000; `bars`: int 1..500 |
  | `channel_exit` | `upper` > `lower` |
  | `indicator` | `name`, `params` (dict), `source` (default `close`), `output` (1..64 chars), `op` ∈ `> < crosses_up crosses_down`, `value` (finite) |
  | `trendline_cross` | `drawing_id` (1..128 chars) |
  | `funding_above` | `rate` (finite) |
  | `funding_below` | `rate` (finite) |
  | `oi_change` | `pct` (as `pct_move`); `window_s`: int 1..86400 |
  | `liquidation_notional` | `notional` > 0; `window_s`; optional `side` ∈ `long short` |
  | `forced_share` | `share` in (0, 10]; `window_s` |

  - Every level, price and value must be finite.
  - The route also checks an `indicator` with `indicator_picker.check_params` and `check_source`, and that a `trendline_cross`'s `drawing_id` is a `trendline` in the coin's `chart_drawings.toml`. Both are 422.
- **Semantics** (pure, in `conditions.py`; the I/O matrix pins them):
  - A cross is `prev < x <= cur` (up) or `prev > x >= cur` (down), the current `_crossed` rule. `price_cross` is either direction. The above/below kinds are strict comparisons.
  - `pct_move` is `kernel.indicators.pct_change(base, close)`: the base is the close N closed bars back. A positive `pct` fires when the change is ≥ `pct`; a negative `pct` fires when the change is ≤ `pct`.
  - `channel_exit` is a cross up through `upper` or a cross down through `lower`.
  - `trendline_cross` crosses `close − line(t)`, the sign rule above with the level at 0:
    - `t` is the bar's `t` in seconds;
    - `line(t) = a.price + (b.price − a.price)·(t − a.time)/(b.time − a.time)`, extrapolated beyond both anchors. This is `alerting/domain/geometry.py` and `lib/drawings.ts` `trendlinePriceAt`, which share one JSON fixture;
    - equal anchor times make the alert invalid.
  - `indicator` compares the output at the newest closed bar (`cur`) with the bar before it (`prev`, from the same replay), so its crosses survive a restart. It is evaluated once per closed bar under every frequency, and `only_once` still fires once.
  - `funding_above`/`funding_below` compare each `FUNDING` tick's exact `Decimal` value.
  - `oi_change` is `pct_change(base, latest)` over the instrument's `OI` ticks. The base is the newest tick at or before `t − window_s`, and the change is None until the series reaches back that far.
  - `liquidation_notional` sums `Liquidation.notional_units()` at each row's precisions as an exact `Decimal` over `(t − window_s, t]`, optionally one side, and fires when the sum is ≥ `notional`.
  - `forced_share` is `units_ratio(Σ liquidation size_units, Σ traded volume units)` over the window:
    - the volume is the per-tick increase of the forming bar's `buy_v + sell_v`, with a new bucket counting its whole value;
    - evaluated on each bar tick and each liquidation batch;
    - None with no volume;
    - it fires at ≥ `share`.
- **SSOT-02.** `pct_change(base: Decimal, latest: Decimal) -> Decimal | None` (None when base is 0) is added to `kernel/indicators.py`, and ranking's `oi_change_*_pct` is rerouted through it with unchanged results. Notional comes from `Liquidation.notional_units()` and ratios from `units_ratio`. Indicator values come only from `views.chart_series.indicator_values_page`, which goes through `values_by_time` and `replay_entry`, over the chart's own `candle_page` with `recent_rows`/`recent_liquidations`.
- **Firing policies.** The existing `once_per_bar_close`/`once_per_bar`/`only_once` state machine in `policy.py` is generalised to take a sample and a condition instead of a price and a level.
  - For `price_cross` it fires exactly as today, so the existing `test_policy.py`/`test_engine.py` scenarios pass, changed in call shape only.
  - A derivatives sample buckets by `alert.bar_seconds` on its event time (`tick.t`, the liquidation's `ts_event`, the bar tick's `ts_ns`).
- **Template.** `render` gains `{{value}}`, the triggering value (`str(float)`), and `{{condition}}`, from `conditions.describe(condition, bar_seconds)`, the one describer, e.g. `RSI(period=14) value > 70 on 3600s bars`.
  - `{{close}}` is the newest close the engine saw for the pair, or `n/a`.
  - `AlertResponse.condition_text` and the toast's `alert.condition` use `describe`.
- **Invalidation (DATA-07).** An alert is marked invalid when:
  - its indicator name is no longer in `merged_catalog()`;
  - its output is absent from the replay's outputs;
  - its drawing id is missing, not a `trendline`, or vertical.

  It then gets `invalid_reason` through `AlertRepository.mark_invalid`, which never raises and ledgers a failed persist like `record_fire`. It is ledgered once at `alerting.engine.invalid` naming the alert and reason. `status_of` returns `invalid`, checked first, and the alert is no longer evaluated or watched.

  A replay or read failure, or a corrupt drawings file, is ledgered at `alerting.engine.input` and only skips that sample. It never invalidates the alert.

  A PUT clears `invalid_reason` after re-validation.
- **Event loop.** `on_bar`, `on_deriv` and `on_liquidation` run on the loop.
  - The indicator read is blocking and goes through an injected `submit(job, done)` seam: production runs `job` in the default executor and `done` on the loop via `call_soon_threadsafe`; tests run it inline.
  - There is one read per `(instrument_id, bar_seconds, closed bar t)`, batching every distinct indicator of that pair, so N alerts on one indicator compute it once.
  - The drawings reader is cached by file `(mtime_ns, size)`.
- **MEM-01/02.**
  - Per instrument, the OI series, the liquidation window and the volume window exist only while an active alert of that kind names the instrument, and are trimmed to that kind's largest `window_s` on every append.
  - Each pair keeps a close history only up to its largest `pct_move.bars + 1`.
  - Run state is dropped by `forget` (delete and PUT).
- **Restart.** Run state and windows stay in memory only. After a restart a cross needs two samples, and the windowed kinds are understated until a full window has been observed: a `Known limit:` naming a catalog backfill as the upgrade path.
- `LiveDerivsBus` gains `attach(observer)` and `detach(observer)` for a `DerivsObserver` protocol (`on_deriv(tick: DerivsTick)`, `on_liquidation(rows: list[Liquidation])`).
  - Observers are called for every decoded tick and every batch, listener or not.
  - An observer that raises is ledgered at `live_derivs.observer` and never stops the relay.
  - The `app.py` lifespan attaches and detaches `alert_wiring.engine`.
- `alerting` still imports only stdlib, `tomli_w`, `kernel` and `observability` (`tests/test_boundaries.py`). The indicator and drawing adapters live in `data_api/alert_inputs.py`, are built in `alert_wiring`, and implement the ports `IndicatorReader` and `DrawingReader` in `alerting/application/ports.py`.
- No new dependency. `nautilus_trader/` and `crates/` stay untouched. Simplifications carry a `Known limit:` comment.

**Block If:**
- Honouring the back-compat read would require changing a stored key's name or type beyond `level`'s nullability.
- `LiveDerivsBus` cannot carry an observer without changing the frames its listeners receive.

**Never:**
- No `localStorage` for alerts.
- No browser-computed condition values or condition text: the page shows `condition_text`.
- No AND/OR multi-condition alerts and no alert templates library.
- No new transport.
- No change to delivery or to `NotifyDeliverer`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Legacy alert | fixture TOML with `level = 100.0`, no `condition` | loads as `price_cross` 100; ticks 99 → 101 fire as before; a save writes `condition` | — |
| Cross up/down | `price_cross_up` 100; closes 99 → 100 / 101 → 99 | the first fires; the second does not | — |
| Above, once_per_bar | `price_above` 100; ticks 101, 102 in one bucket, then 103 in the next | fires at 101 and at 103 | — |
| pct_move | closed closes 100, 105, 110; `pct` 10, `bars` 2; tick 110 | change 10 % ≥ 10: fires | fewer than N closed bars: no sample |
| channel_exit | upper 110, lower 90; 105 → 111 | fires | upper ≤ lower → 422 `condition.upper` |
| Trendline | anchors (1000 s, 100), (2000 s, 200); bar t = 3000 s; closes 290 → 310 | line(3000) = 300; fires | drawing deleted → invalid, ledgered once, never fires |
| Indicator cross | RSI outputs prev 69, cur 71; `crosses_up` 70 | fires once for that closed bar | unknown output → invalid; replay error → ledgered, no fire |
| Indicator batching | 3 alerts on RSI(14) for one pair | one read per closed bar | — |
| Funding | `funding_above` 0.0003; ticks 0.0002, 0.0004 | fires at 0.0004; `{{value}}` `0.0004` | — |
| OI change | ticks 200 at t0 and 210 at t0+3600 s; `pct` 5, `window_s` 3600 | fires | series younger than the window → no sample |
| Liq notional | rows of 60k and 50k long within 300 s; `notional` 100000, `side` long | fires on the second row | a short row is not summed under `side: long` |
| Forced share | volume 10 BTC, liquidations 2 BTC in the window; `share` 0.15 | 0.2 ≥ 0.15: fires | no volume → None, no fire |
| PUT edit | change the condition of a triggered `only_once` alert with `rearm: true` | 200; status `active`; run state reset | unknown id → 404; bad field → 422 naming it; no channel → 422 |
| Both level and condition | `level` 5 with `condition` `price_above` 6 | 422 | — |

</intent-contract>

## Code Map

All paths are under `platform/`.

- `alerting/domain/alert.py`: `Alert` (frozen key order), `status_of`, `new_alert`. `alerting/domain/policy.py`: `FiringPolicy`, `RunState`, `_crossed`, `evaluate`, `render`.
- `alerting/application/engine.py`: `AlertEngine` (`watched_bars`, `on_bar`, `_fire`, the toast). `ports.py`: `AlertRepository`, `Deliverer`. `service.py`: `AlertService`.
- `alerting/infrastructure/toml_store.py`: `AlertStore` `_load`/`_save`/`record_fire` (the ledger pattern).
- `alerting/tests/`: `test_policy.py`, `test_engine.py` (`_RecordingDeliverer`, `_alert`), `test_toml_store.py`.
- `data_api/routes/alerts.py`: `AlertCreate`/`AlertResponse`, `_to_response`, the frozen details. `data_api/alert_wiring.py`: the composition root. `data_api/app.py:115-130`: the lifespan attaches. `data_api/tests/test_alerts.py`: alerts through a real `LiveCandleBus`.
- `views/live_derivs.py`: `LiveDerivsBus.handle_derivs`/`_relay_tick`/`publish_liquidations`. `views/live_candles.py`: `BarObserver`, `_ask_watched`, the observer-ledger pattern.
- `views/chart_series.py`: `indicator_values_page` (:707). `views/indicator_picker.py`: `merged_catalog`, `check_params`, `check_source`, `IndicatorSpec.outputs`, `CustomIndicatorSpec` (:554), `native_catalog_json`, `custom_catalog_json`. `views/preferences.py`: `load_chart_drawings`. `data_api/settings.py`: `CHART_DRAWINGS_PATH`. `data_api/routes/candles.py`: `CATALOG_PATH`, `CANDLES_DB_DIR`.
- `kernel/indicators.py`: `units_ratio` (:826). `kernel/derivs_wire.py`: `DerivsTick`, `FUNDING`/`OI`. `kernel/liquidation.py`: `notional_units`. `ranking/domain/derivs.py`: `OpenInterestSeries.fields` (the `_pct` math).
- `tests/test_boundaries.py`: alerting's allowed edges.
- Frontend (`frontend/src`):
  - `pages/AlertsPage.tsx` + test: fetch stubbed, `describeAlert` hard-codes "price crosses";
  - `components/chart/AlertDialog.tsx` + test: raw `<dialog>`, the `priceLines` target select;
  - `components/chart/SettingsDialogShell.tsx`;
  - `hooks/useAlertToasts.ts` (`parseToast`) and `App.tsx` `AlertToasts`;
  - `api/client.ts` alert functions (:366-385) and `fetchIndicatorCatalog`/`fetchRankings`;
  - `lib/drawings.ts`: `TrendlineDrawing` `anchors` (time in UTC s);
  - `components/chart/LightweightChart.tsx`: the drawing menu (`menu` :768, `handleClick` ~:1971, render ~:2064, `menuSpec`);
  - `pages/ChartPage.tsx`: the Alert button (:1593), `AlertDialog` (:1800), drawing callbacks (:1649);
  - `timeframes.ts`: `TIMEFRAMES`;
  - `pages/docs/kbData.ts`: the chart group entries.
- Docs:
  - `docs/DATA_DICTIONARY.md` §2.11 (:3077);
  - `docs/DATA_INTEGRITY_AUDIT.md`, next row D-197;
  - `docs/DEPLOY_CHECKLIST.md`, the 33-7 section (:1634) as the format;
  - `_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md` §A6 (:374).

## Tasks & Acceptance

**Execution:**

*Domain*
- [x] `kernel/indicators.py` + `ranking/domain/derivs.py`:
  - add `pct_change`;
  - make `OpenInterestSeries` use it;
  - kernel tests for value, base 0 and negative change;
  - the ranking tests stay green, unchanged.
- [x] `alerting/domain/conditions.py` (new):
  - `CONDITION_KINDS`, `ConditionError`, `validate_condition(raw) -> dict` (normalised: ints for `bars`/`window_s`, floats elsewhere, `source` defaulted, `side` omitted when absent), `describe(condition, bar_seconds)`;
  - the per-kind pure `evaluate(condition, state, inputs) -> bool`, with sample/input dataclasses as needed;
  - `alerting/tests/test_conditions.py`: table-driven, one table per kind, covering the matrix and every `ConditionError.field`.
- [x] `alerting/domain/geometry.py` (new) `trendline_price_at(anchors, t) -> float | None` (None for vertical), plus `alerting/tests/fixtures/trendline_cases.json`, which the TS test reads too.
- [x] `alerting/domain/alert.py`: the appended fields, `__post_init__` back-compat and the level/condition agreement, and `status_of` → `invalid` first.
- [x] `alerting/domain/policy.py`: the generalised step, plus `render` with `{{value}}`/`{{condition}}`; `test_policy.py` adapted, with the old cases kept.

*Application and infrastructure*
- [x] `alerting/application/ports.py`:
  - `IndicatorReader.read(instrument_id, bar_seconds, closed_t_ms, refs) -> per-ref (prev, cur, outputs) | Missing(reason) | Failed(message)`;
  - `DrawingReader.trendline(instrument_id, drawing_id) -> anchors | Missing(reason)`;
  - repository `update` (persist-before-return, raises) and `mark_invalid`.
- [x] `alerting/application/engine.py`:
  - `on_bar` covers price/trendline/pct_move/forced_share samples, close history, the volume window and closed-bar indicator batching through `submit`;
  - `on_deriv` and `on_liquidation`, the per-instrument windows (MEM rules), invalidation and the `{{close}}` tracking;
  - `_fire(alert, value, ts_ns)` adds `alert.condition` to the toast;
  - the constructor takes `indicators`, `drawings` and `submit`.
- [x] `alerting/application/service.py`: `create` normalises level/condition; `update(alert_id, *, condition, frequency, expires_at_ns, template, webhook_url, rearm)` checks the channel, clears `invalid_reason`, re-arms and calls `engine.forget`.
- [x] `alerting/infrastructure/toml_store.py`:
  - `_load` validates every condition (raise);
  - `_save` drops None values recursively;
  - `update` and `mark_invalid`.
- [x] `alerting/tests/`:
  - `test_engine.py` covers every kind through the engine with fake readers and inline `submit`: batching, invalidation and ledger-once, restart, MEM trimming, `forget` on edit;
  - `test_toml_store.py` covers the committed `fixtures/alerts_pre_33_8.toml` (legacy, every frequency, one triggered, one with expiry) loading as `price_cross` and round-tripping, a condition round trip, and a bad stored condition raising.

*Interface*
- [x] `views/live_derivs.py`: `DerivsObserver`, `attach`/`detach`, calls and ledger. Tests in `views/tests`.
- [x] `views/indicator_picker.py`: `CustomIndicatorSpec.outputs` set on all 11 custom entries, and `outputs` in both catalog JSONs (native: `spec.outputs`). A test checks that every listed entry has outputs and that its `units` keys are a subset of them.
- [x] `data_api/alert_inputs.py` (new):
  - `ChartIndicatorReader` over `indicator_values_page(before_ns = closed bar end, limit = 300)`. The newest row must be the closed bar, else no reading. Missing is decided by `merged_catalog`/outputs.
  - `DrawingFileReader` over `load_chart_drawings`, with the mtime cache.
  - Wire both and the executor `submit` in `alert_wiring.py`.
  - `app.py` attaches the engine to `live_derivs_bus`.
- [x] `data_api/routes/alerts.py`:
  - a discriminated-union `Condition`; `AlertCreate` with optional `level`/`condition` plus the both-given rule;
  - `AlertResponse` gains `condition`, `condition_text`, `invalid_reason` and the `invalid` status;
  - `AlertUpdate` and `PUT /api/alerts/{id}`;
  - the indicator/drawing 422 checks.
- [x] `data_api/tests/test_alerts.py`: POST each kind, the legacy body unchanged, both-given 422, PUT (edit, re-arm, 404, 422, no channel), invalid status, a mirror test of the pydantic kinds against `CONDITION_KINDS`, a derivatives fire through a real `LiveDerivsBus`, and the catalog `outputs`.
- [x] Regenerate `frontend/openapi.json` and `src/api/schema.ts`.

*Frontend*
- [x] `lib/drawings.ts` `trendlinePriceAt` + a test over the shared fixture. `lib/alertConditions.ts`: `CONDITION_KINDS` with a field spec per kind (mirror-tested against Python), default condition and form↔condition conversion.
- [x] `components/alerts/ConditionFields.tsx` (new): the kind select plus the fields of that kind.
  - The indicator is picked from `fetchIndicatorCatalog`, with param inputs from its defaults/choices, a source select when `source_selectable`, and an output select from `outputs`.
  - The trendline is picked from the coin's drawings (`fetchCoinDrawings`).
- [x] `api/client.ts`: `updateAlert(id, body)`.
- [x] `pages/AlertsPage.tsx`:
  - a create form: instrument from `fetchRankings` items, timeframe from `TIMEFRAMES`, `ConditionFields`, frequency, expiry, template, webhook;
  - rows show `condition_text`, the status and the invalid reason;
  - Edit opens a `SettingsDialogShell` with the same fields prefilled, plus a Re-arm checkbox on a triggered alert;
  - errors inline.
- [x] `components/chart/AlertDialog.tsx`: `ConditionFields` with an `initialCondition` prop. The toolbar keeps the hline target select for price kinds.
- [x] `LightweightChart.tsx` + `ChartPage.tsx`:
  - the drawing menu gains "Add alert…" for `hline` (prefills `price_cross` at its price) and `trendline` (prefills `trendline_cross`);
  - a `contextmenu` on a hit drawing in Cursor mode opens the same menu (`preventDefault`).
- [x] `hooks/useAlertToasts.ts` + `App.tsx`: parse the optional `condition` and show it under the message. Add a hook test.
- [x] Tests:
  - `AlertsPage.test.tsx` covers create for a price kind and an indicator kind (posted body), edit/PUT, re-arm, the invalid row, and a failed PUT;
  - `AlertDialog.test.tsx` covers the prefill from `initialCondition` and the trendline kind;
  - a drawing-menu "Add alert…" test.

*Docs*
- [x] `docs/DATA_DICTIONARY.md` §2.11: rewrite it with the key set, the kinds table, the semantics, policies per family, the placeholders, invalidation, PUT and the toast key.
- [x] `kbData.ts`: a `chart-alerts` entry, plus a fix to the "right-click" wording if it is still inaccurate.
- [x] Amend `spec-multi-exchange-screener-chart.md` §A6 with `[amended 2026-10-07: Story 33.8]`.
- [x] `docs/DATA_INTEGRITY_AUDIT.md` rows from D-197:
  - restart windows understated;
  - trendline extrapolation;
  - live derivs/liquidations pub/sub at most once, so a missed row understates a window;
  - forced share across two streams;
  - an indicator read at rollover can miss seconds not yet in `recent_rows`/the store;
  - a stale `{{close}}`.
- [x] `docs/DEPLOY_CHECKLIST.md`: a `33-8-…` deferred action to rebuild `data_api`, verify the existing `alerts.toml` loads, and run a curl for PUT.

**Acceptance Criteria:**
- Given an `alerts.toml` written before this story, when `data_api` starts, then every alert lists with its old fields, `condition` is `price_cross` and it fires on the same ticks as before.
- Given a Bybit linear coin with a `liquidation_notional` alert, when liquidations arrive on `liquidations:raw`, then the alert fires through `LiveDerivsBus`'s observer, with no `/ws/live` listener open.
- Given the Alerts page, when the operator creates an `indicator` alert (RSI > 70 on 1H) without opening a chart and later edits it to `crosses_up`, then the list shows the new `condition_text` and the stored TOML holds the edited condition.
- Given a trendline alert whose drawing is then deleted on the chart, when the next bar arrives, then the alert shows status `invalid` with its reason, and one `alerting.engine.invalid` ledger row exists.
- Given `cd platform && python3 -m pytest alerting/tests data_api/tests views/tests kernel/tests ranking/tests tests -q` and `cd platform/frontend && npm test && npm run lint && npm run build`, when they run, then all pass with no new warnings, and the regenerated `openapi.json`/`schema.ts` match the export.

## Spec Change Log

## Review Triage Log

### 2026-10-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 17: (high 3, medium 8, low 6)
- defer: 0
- reject: 4: (high 0, medium 1, low 3)
- addressed_findings:
  - `[high]` `[patch]` `engine.forget` ran `_prune` from the threadpool routes while the loop mutated the same dicts. It is now marshalled onto the engine's loop, and run state is keyed to the alert object, so a queued forget cannot evaluate an edited alert against the old previous value.
  - `[high]` `[patch]` `{{close}}` regressed for `once_per_bar_close` alerts: it rendered the next bucket's first tick. `step` now returns a `Fire` carrying the firing sample's close; `_last_close` is used only for derivative and liquidation fires.
  - `[high]` `[patch]` A concurrent `record_fire` during `AlertService.update` could be lost, so an `only_once` alert fired again. The read-modify-write now runs under the store lock (`AlertRepository.update(alert_id, edit)`); `mark_invalid` likewise.
  - `[medium]` `[patch]` A sample older than the state's bucket (a lagging liquidation `ts_event`) rolled the bucket back, so `once_per_bar` fired twice and `once_per_bar_close` saw spurious rollovers. It now counts as the current bucket.
  - `[medium]` `[patch]` `once_per_bar_close` on sparse event-fed kinds waited for the next event of that kind (hours for funding). `policy.advance` closes a passed bucket from any derivatives tick or watched bar tick of the instrument; the residual is a `Known limit:` (D-204).
  - `[medium]` `[patch]` Liquidation and volume windows rescanned on every tick and row (O(N·M) in a cascade). New `alerting/application/windows.py` keeps exact integer prefix sums, a dedup-id set and bisect lookups (OI base too), checked against a recomputed sum in a randomized test.
  - `[medium]` `[patch]` Indicator reads in flight were unbounded. One read per pair in flight; a skipped closed bar is ledgered at `alerting.engine.input` (D-203); `executor_submit` hands a job's exception to `done`.
  - `[medium]` `[patch]` The first volume observation counted the whole bucket as one second (any mid-bucket create, not only restart) and two widths raced. The first observation now only seeds; volume comes from the narrowest active `forced_share` width.
  - `[medium]` `[patch]` Editing a triggered `only_once` alert to another frequency left it triggered forever. `triggered` is kept only for an un-rearmed `only_once`.
  - `[medium]` `[patch]` "Add alert…" on a just-drawn trendline raced the debounced drawings save. `useChartDrawings.saveNow()` is awaited before a `trendline_cross` create.
  - `[medium]` `[patch]` Editing an alert silently moved its expiry to 23:59:59 local. The stored `expires_at_ns` is kept unless the date input changed.
  - `[medium]` `[patch]` Indicator params were coerced to numbers before the catalog loaded. Save is refused until the catalog loads; params keep their stored JSON types.
  - `[low]` `[patch]` A redelivered liquidation row was still stepped. Duplicates are skipped.
  - `[low]` `[patch]` `DrawingFileReader`'s cache key gains `st_ino`.
  - `[low]` `[patch]` The chart dialog stored a stale hline price if the line was dragged, and kept delivery fields across openings. The level is read live; delivery resets on open.
  - `[low]` `[patch]` `useTrendlines` showed the previous coin's lines and kept its `drawing_id` after an instrument change. Fixed.
  - `[low]` `[patch]` Re-arm was hidden for an alert both triggered and invalid. Shown for an invalid `only_once` alert with `last_fired_ns`.

### 2026-10-07 — Review pass (follow-up review)
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 0, medium 1, low 8)
- defer: 1: (high 0, medium 1, low 0)
- reject: 12: (high 0, medium 3, low 9)
- addressed_findings:
  - `[medium]` `[patch]` A liquidation row whose `ts_event` lagged rows already in the window was sampled as the window total ending at its own time, leaving the newer rows out. That smaller total became the alert's newest value, so a `liquidation_notional`/`forced_share` fire could be missed and the next comparison started from a wrong previous value. A windowed sample is now taken at `AlertEngine._now`, the newest event either of the instrument's windows holds.
  - `[low]` `[patch]` `on_liquidation` never advanced the instrument's event clock, though the module docstring and D-204 named liquidations as an input that does. It now calls `_clock`.
  - `[low]` `[patch]` `_invalidate` ledgered `alerting.engine.invalid` for an alert object an edit had already replaced, while the stored alert stayed active. `AlertRepository.mark_invalid` now returns whether the alert was still the stored one, and the engine ledgers only then.
  - `[low]` `[patch]` `forget` ran inline on a request thread before the first observer call had bound the loop. The lifespan now calls `AlertEngine.bind_loop()` at attach.
  - `[low]` `[patch]` `ChartIndicatorReader._series_values` matched keys by prefix, so `X_bps=10.` also took `X_bps=10.5.bid` as an output `5.bid`. A key whose remainder after the prefix holds a dot is now another series.
  - `[low]` `[patch]` A stored indicator output or param value that the catalog entry no longer lists showed the select's first option while Save sent the stale value. It is now shown as the selected option.
  - `[low]` `[patch]` (same root cause as the previous item) applied to the param select.
  - `[low]` `[patch]` The create form's default instrument followed `ids[0]` on every rankings refetch. It is now fixed once the list first loads, and an instrument that drops out of rankings stays shown.
  - `[low]` `[patch]` A chart seed mid-bucket adds the bucket's earlier volume to the next `forced_share` volume tick. That volume is real but placed up to one bar width late. This is now a `Known limit:` on `_track_volume` and audit D-205, with an upgrade path.

## Design Notes

**Prior attempt (2026-10-07, host shutdown mid dev-1).** The first dev session was cut off by a power-off about 26 minutes in. Its tree (13 modified files + `conditions.py`, `geometry.py`, the fixtures and `test_conditions.py`, plus this spec) is pinned on branch `33-8-prior-attempt`, parent = baseline `2d70986235`. The resumed dev restores it with `git read-tree -m -u HEAD 33-8-prior-attempt && git reset -q`, then re-verifies every task and AC rather than trusting checkboxes (none were ticked).

**One sample stream, three policies.** The engine turns every input into a sample `(value, ts_ns, inputs)` for each alert. `policy.step` decides which samples reach `conditions.evaluate`:
- `once_per_bar_close` evaluates only the last sample of a bucket, at rollover;
- `once_per_bar` evaluates every sample and fires at most once per bucket;
- `only_once` evaluates every sample and fires once.

`price_cross` under this rule reproduces the current `_crossed` behaviour, which is why the old policy tests survive. Indicator samples exist only at a closed bar, which is why the three frequencies coincide for them.

**Stored shape** (TOML, None omitted):
```toml
[[alerts]]
id = "…"
instrument_id = "BTCUSDT-LINEAR.BYBIT"
frequency = "once_per_bar_close"
bar_seconds = 3600
# … existing keys …
[alerts.condition]
kind = "indicator"
name = "RelativeStrengthIndex"
source = "close"
output = "value"
op = ">"
value = 70.0
[alerts.condition.params]
period = 14
```

## Verification

**Commands:**
- `cd platform && python3 -m pytest alerting/tests data_api/tests views/tests kernel/tests ranking/tests tests -q -p no:cacheprovider`: expected all pass, no warnings. Redis-backed `data_api` tests need a throwaway redis on 6379.
- `cd platform && PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json && cd frontend && npm run codegen && git diff --stat`: expected the schema regenerated and stable on rerun.
- `cd platform/frontend && npm test && npm run lint && npm run build`: expected green.
- `ruff format --check` and `ruff check` on the touched Python: expected clean.


## Auto Run Result

Status: done

- **Change.** A follow-up review of Story 33.8 (the spec arrived `done` with `followup_review_recommended: true`). The story itself is described in the sections above and the first review pass: 14 condition kinds, a derivatives observer, `PUT /api/alerts/{id}`, and the Alerts page create and edit flows. This pass fixed how late liquidation rows are sampled, plus several small concurrency, ledger, read-model and form issues.
- **Files (this pass).**
  - `alerting/application/engine.py`: `_now` (windowed samples taken at the windows' newest event); `on_liquidation` advances the event clock; `_invalidate` ledgers only when the stored alert was marked; `bind_loop` is public; a `Known limit:` on `_track_volume` for the seed volume jump.
  - `alerting/application/ports.py`: `mark_invalid -> bool`; `update`'s docstring names the in-place-write `Known limit:`.
  - `alerting/infrastructure/toml_store.py`: `mark_invalid` returns whether it persisted.
  - `data_api/app.py`: binds the engine's loop at attach. `data_api/alert_inputs.py`: exact series key match.
  - `frontend/src/components/alerts/ConditionFields.tsx`: a stale output or param stays the selected option. `frontend/src/pages/AlertsPage.tsx`: the default instrument is fixed on first load.
  - Tests: `alerting/tests/test_engine.py` (+3, each failing on the pre-fix engine), `data_api/tests/test_alert_inputs.py` (+1), `frontend/src/pages/AlertsPage.test.tsx` (+1).
  - Docs: `docs/DATA_INTEGRITY_AUDIT.md` D-205 (new) and D-204 (liquidations clock too).
- **Review.** 9 patches (1 medium, 8 low), 1 deferred and 12 rejected.
  - Deferred: `alerts.toml`'s in-place rewrite can truncate on a full disk. It predates this story, and the fix needs a frozen mount moved. A temp-file rename was tried and reverted, because the file is a single-file bind mount (EBUSY).
  - Rejected, as spec-defined or decided by the first pass: re-arm on a frequency change, the trendline stat on the loop, an indicator `Failed` on a missing closed bar, `record_fire` disk I/O on the loop (pre-33.8), the volume source switch losing about one second, run state of triggered alerts, a liquidation row raising (`notional_units` cannot raise), the catalog-failure edit block, `withKind` field carry-over, a hung read, derivs/bar clock skew (D-204), and an absent drawings file.
- **Verification.**
  - `cd platform && python3 -m pytest alerting/tests data_api/tests views/tests kernel/tests ranking/tests tests -q` (throwaway redis on 6379): 2459 passed, 1 failed. The failure is `tests/test_legacy_names.py::test_only_published_language_keeps_a_legacy_name`, which also fails at baseline on files this story does not touch.
  - Frontend: `npm test` 1247 passed; `npm run lint` shows only the 3 earlier warnings; `npm run build` is green.
  - The OpenAPI export is unchanged against the committed `openapi.json`.
  - ruff format and check are clean on the touched Python. mypy reports no errors in the touched files; the 11 it reports are in imported, unchanged modules (`kernel/indicators.py`, `views/indicator_picker.py`, `candles/domain/fold.py`).
- **Residual risk.** Run state and windows stay in memory, so a restart understates windows (D-197). The live pub/sub delivers at most once (D-199). The volume seed timing is D-205. The `alerts.toml` truncation is deferred.

