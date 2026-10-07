---
title: 'Story 33.13: Liquidation and forced-flow research: cascade episodes, implied leverage, the forced/organic split proven on the trade archive, and the OFI strategy filter'
type: 'feature'
created: '2026-10-07'
status: 'in-review'
baseline_revision: '163c9b116e08d35740b7f0e8778847498ae0f33b'
final_revision: '76a7008739c4ad2196b56a96acd5f57aba0fef54'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** Bybit LINEAR liquidations have been archived since 33.1, and 33.14 trades cascades. Research still cannot answer four questions about them: what follows a cascade, how levered the liquidated crowd was, how much volume was forced, and whether taking forced flow out of OFI's cumulative delta helps the strategy.

**Approach:**
- `MarketFrames` gains a liquidations frame.
- `research/application/liquidations.py` and `aligned.py` gain pure, tested frame functions, plus one day-sliced study service.
- Notebook 09 presents the study.
- `OFIStrategy` gains `forced_flow_filter` and `liquidation_cascade_mode`. It uses 33.14's `LiquidationCascade` and the kernel's 33.6 organic-delta formula, so there is no second definition of either (SSOT-02).
- The strategy's backtest uses a new `seconds_liquidations` data kind, and notebook 08 runs the three variants beside a baseline.

## Boundaries & Constraints

**Always:**
- **Liquidation inputs (epic context).** Rows exist for Bybit LINEAR only, and `price_units` is always the **bankruptcy** price.
  - The frame carries a `price_kind` column that is always `"bankruptcy"`, with a `Known limit:` comment: there is no stored kind column, and Hyperliquid has no feed.
  - An id without `has_liquidation_feed` returns an empty frame with the full column set. It never invents rows.
- **Cascades.** Every cascade is `replay_cascade`/`LiquidationCascade` (33.14). `cascade_episodes` and the strategy never redefine a cascade.
- **Organic delta.** Every organic delta is `kernel.indicators.organic_delta_units` over exact integer units.
- **Forced share.** It is `kernel.indicators.units_ratio`.
- **Exact units (DATA-04).**
  - A liquidation's size is rescaled to the snapshot's/instrument's `size_precision` with exact integer arithmetic.
  - A row that cannot be rescaled exactly is ledgered at the existing `UNSCALABLE_ROW_SITE`. Its bucket/second is NaN (DATA-07), never silently dropped from a sum.
  - Floats appear only at a function's output.
- **Gaps (DATA-01).** A missing second, mark or OI bucket is NaN, never filled or carried forward.
  - A second that has a snapshot and no liquidation has liquidation volume 0.
  - An id without a feed has liquidation columns NaN.
- **Bounded reads (MEM-01, NB-04).** The study service reads seconds one UTC day at a time and keeps only reduced columns. Liquidations and trades are read through day-sliced reads. Every read takes `start`/`end`.
- **Notebook rules (NB-01/NB-02).** Notebook 09 holds no formula, is jupytext-paired, is driven by `_params.py`, and runs on the fixture under `make test` with warnings as errors.
- **Pure decisions.** OFI's cascade gate and phase transition are pure functions in `research/strategies/cascade_rules.py` (stdlib plus `OrderSide`).
- **OFI defaults are byte-identical.** With `forced_flow_filter=False` and mode `"off"`, `OFIStrategy` subscribes to nothing new and behaves exactly as before. `test_ofi_strategy*.py`, the 31.9 OFI parity test and notebook 04 pass unchanged.
- Never touch `nautilus_trader/` or `crates/`. No new dependency.

**Block If:**
- `BacktestNode` cannot deliver both `DydxSecondSnapshot` and `Liquidation` custom data to one strategy without touching `nautilus_trader/`. HALT with the evidence.

**Never:**
- No Hyperliquid, dYdX or spot liquidation rows.
- No `price_kind` stored column.
- No change to `LiquidationCascadeStrategy`'s behaviour, records or parity.
- No live or bots wiring for `OFIStrategy`'s new modes.
- No chart, screener, alert or `data_api` change.
- No change to `verification.domain.liquidation_check`. It is the independent oracle and deliberately never shares code with research.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Leverage | Long liq, bankruptcy 95, latest mark 100 at or before ts within `max_mark_age_s` | 20.0 | — |
| Leverage gaps | No mark within age; mark == bankruptcy; `price_kind` ≠ bankruptcy | NaN, and the per-day count still includes the row | — |
| Organic delta | Second: buy 10, sell 4 units, liq_long 3, liq_short 1 | (10−1)−(4−3) = 8 units, decoded once | — |
| Liq between snapshots | Liquidation in a second with no snapshot row | That second stays absent (NaN); the liquidation is reported as `unattributed` | — |
| Forced share | Minute: liq 2 units, traded 8 units | 0.25; a minute with traded 0 is NaN | — |
| Match | Long liq, size 5 units; seller trade, size 5, +0.4 s; tol 2 s | matched, offset +0.4 s; each trade used once, earliest-unused rule | — |
| Episode returns | Episode ends at second e; mid known at e and e+60 | 1-minute fwd = mid[e+60]/mid[e]−1; open episode or missing mid → NaN | — |
| Cross venue | Hyperliquid side empty | Empty pairs, `reason` names the empty side | — |
| Follow gate | `building` phase, direction −1, rising, not spent; OFI wants SELL / BUY | SELL passes / BUY suppressed | — |
| Fade gate | Phase `building`/`unwinding`; or `fade_window` with direction −1 | Any side suppressed; in `fade_window` BUY passes and SELL is suppressed | — |
| Bad config | mode `"x"`; or filter/mode on for a non-feed id | — | `ValueError` at strategy init |

</intent-contract>

## Code Map

- `platform/research/application/ports.py`: `MarketFrames` (:360), `DATA_KINDS` (:45), `RunSpec._check_data` (:216).
- `platform/research/application/frames.py`: `CatalogFrames`, `SECONDS_COLUMNS` (:73), `_frame`, `seconds` (:157), `objects` (:381). `DydxSecondSnapshot.buy_volume_units`/`sell_volume_units` exist (`kernel/second_snapshot.py:470`).
- `platform/research/application/liquidations.py`: `CascadeEpisode` (:66), `CascadeEpisodes`, `read_liquidations` (:98, day-sliced over `kernel.catalog_files.query_liquidations`), `replay_cascade` (:132), `UNSCALABLE_ROW_SITE`.
- `platform/research/application/aligned.py`: `bucket_last` (:198), `oi_changes` (:269), `cross_venue` (:513).
- `platform/research/application/microstructure.py`: `grid` (:129), `read_instrument` (:587), the one-read-per-instrument pattern.
- `platform/research/domain/events.py:60`: `forward_returns(closes, hit_indices, horizons)`, NaN past the end.
- `platform/kernel/indicators.py`: `LiquidationCascade` (:508; outputs listed at :509-567), `organic_delta_units` (:813), `units_ratio` (:827).
- `platform/kernel/liquidation.py`: `Liquidation` (:123), `notional_units_at` (:205), `.size` (exact `Quantity`), `has_liquidation_feed` (:82), `LIQUIDATION_CLIENT_ID`.
- `platform/research/strategies/ofi_strategy.py`: config (:49-91), `on_start` subscribe (:126), `on_data` (:128-155), `_track_cum_delta` (:165), `_filters_pass` (:184), `_enter` (:225).
- `platform/research/strategies/cascade_rules.py`: pure rules; `_side_of` (:166).
- `platform/research/strategies/liquidation_cascade_strategy.py`: the subscription pattern (:430) and `units_of` (:546). Lift its body to a module-level function that both strategies call.
- `platform/research/application/backtest_runner.py`: `_QUOTED_KINDS` (:166), `_data_configs` (:169), `_liquidation_config` (:205).
- `platform/research/strategies/backtest_ofi.py`: the OFI runner (via `snapshot_backtest`).
- `platform/research/application/gallery.py`: `_spec` (:131), `cascade_specs` (:280), `cascade_sample` (:375), `run_specs` (:415).
- `platform/research/notebooks/08_strategy_gallery.py`: count text (:19-27), settings (:73-81), §3 cascades (:156-167), reading guide (:211). Also `notebooks/_params.py` `setting`.
- `platform/research/tests/test_notebooks.py`: `_FIXTURE_OFI` (:75), `NOTEBOOK_ENV` (:89), the 08 entry (:188), `_FIXTURE_CASCADE` (:163). A `09_*.py` is auto-discovered (:201).
- `platform/research/tests/fixture_catalog.py`: `CASCADE_INSTRUMENT` (:119), LONG background plus a 30 s burst (:340).
- `platform/verification/domain/liquidation_check.py`: the matching rule to mirror (same size, forced aggressor, earliest unused within window). Mirror it; never import it.
- Docs:
  - `platform/docs/DATA_DICTIONARY.md` §2.12 (:3261; heading says "six notebooks"; 08 entry at :3359).
  - `docs/NAUTILUS_INDICATOR_BACKTEST_CATALOG.md` :24.
  - `docs/DATA_INTEGRITY_AUDIT.md` (last row D-219).
  - `docs/DEPLOY_CHECKLIST.md` (33-14 entry at :1839).
  - `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md` :157 (AD-D1 research row).
  - `research/README.md` (index :12-28, "What each notebook shows" :37).
  - `frontend/src/pages/docs/kbData.ts:332` (the "Six executable notebooks" text is stale).
  - `platform/CLAUDE.md:167` ("six notebooks", also stale).

## Tasks & Acceptance

**Execution:**

*Frames*
- [x] `research/application/frames.py`, `ports.py` -- Frame changes:
  - Add `LIQUIDATIONS_COLUMNS`: `ts_event, side ("long"|"short"), size_units, price_units, size_precision, price_precision, size, price, notional, price_kind, venue_event_id, ts_init`.
    - `size`, `price` and `notional` are decoded once from the units (`notional` is in quote currency at the bankruptcy price).
    - The window is half-open on `ts_event`. Read day by day through `query_liquidations`.
  - Add `MarketFrames.liquidations(iid, *, start, end)` and implement it on `CatalogFrames`.
  - `SECONDS_COLUMNS` gains `buy_volume_units`, `sell_volume_units` (int, added columns only). Update `test_frames.py`'s expectations.
- [x] `research/tests/test_frames.py` -- Cover the liquidations frame: decoded values, half-open bounds, the constant `price_kind`, and the empty frame (all columns) for a non-feed id.

*Pure frame functions*
- [x] `research/application/liquidations.py` -- Add:
  - **`cascade_episodes(liqs, mids, *, window_s, baseline_s, intensity_threshold, decay_ratio, precisions, start_ns, end_ns) -> pd.DataFrame`.**
    - Rebuild `Liquidation` rows exactly from the frame's integer columns and call `replay_cascade`.
    - Output one row per episode: `start_ns, end_ns, direction, side, duration_s, peak_rate, notional`, plus `fwd_1m, fwd_5m, fwd_15m, fwd_60m` via `research.domain.events.forward_returns` on the 1 s mid grid at the episode's end second. An open episode has NaN returns.
  - **`implied_leverage(liqs, mark_index, max_mark_age_s=5) -> ImpliedLeverage`**:
    - `per_liquidation`: `leverage = mark / |mark − price|`.
    - `per_day`: `count, finite, p10, p25, p50, p75, p90`.
    - Add a `Known limit:` covering Hyperliquid, mark-priced rows, and the fact that the bankruptcy price yields an implied rather than a margin leverage.
  - **`forced_share(liqs, seconds) -> ForcedShare`**: `per_minute` and `per_hour` with `forced_units, traded_units, size_precision, seconds_observed, share`.
    - The share is `units_ratio`; a bucket with traded 0 is NaN.
    - Mixed precisions in a bucket are rescaled exactly to the finest.
  - **`match_to_trades(liqs, trades: Sequence[TradeTick], tol_s=2) -> TradeMatch`**:
    - `per_liquidation`: `matched`, `offset_s`, `trade_size`.
    - Summary: `total`, `matched`, `share` (None when empty).
    - Sizes are compared exactly as raws (liq units → `Quantity`, against `trade.size.raw`), on the forced aggressor. Each trade is used once, and each liquidation takes the earliest unused trade.
  - **`organic_delta(seconds, liqs) -> pd.DataFrame`**, one row per seconds row: `delta_units, liq_long_units, liq_short_units, organic_units, organic` (decoded), plus `.attrs["unattributed"]`.
    - Each liquidation is bucketed to the snapshot second whose trade window holds its `ts_event`, by the same assignment rule the capture service uses for trades. Find that rule and cite it in the docstring.
    - `liqs=None` (no feed) gives NaN liquidation and organic columns.
  - **`liquidation_study(frames, iid, start_ns, end_ns, config) -> LiquidationStudy`**: one day-sliced read (MEM-01) returning every result above, plus `liquidations_vs_oi` and `cross_venue_liquidations` against the first `frames.same_symbol` id on another venue (Hyperliquid).
  - Extend the module docstring.
- [x] `research/application/aligned.py` -- Add:
  - **`liquidations_vs_oi(liqs, oi, bucket_s, start_ns, end_ns)`**: per bucket, `liquidation_size, liquidation_notional, oi_change` (`bucket_last` diff, NaN on a gap), `deleveraging` (`oi_change < 0` and liquidation size > 0), and `share_of_oi_drop`.
  - **`cross_venue_liquidations(a, b, max_lag_s) -> CascadeLeadLag`**: given two episode frames, pair each `a` episode with the nearest same-direction `b` episode by start within `max_lag_s` (lag = b − a). Also return the counts and `reason` (set when either side is empty). Add a `Known limit:` that Hyperliquid has no feed, so the result is empty until one exists.
- [x] `research/tests/test_liquidations.py`, `test_aligned.py` -- Hand-built frames (real `Liquidation`/`TradeTick`) for every matrix row: leverage, organic delta, unattributed, forced share incl. traded 0 and mixed precision, the match (offset, once-only, wrong aggressor, size mismatch), forward returns incl. an open episode, `liquidations_vs_oi`, the empty cross venue, and `liquidation_study` on the fixture (≥ 1 episode).

*Strategy*
- [x] `research/strategies/cascade_rules.py` -- Add the frozen `CascadePhase(kind: "quiet"|"building"|"unwinding"|"fade_window", direction, rising, spent, since_ns)` and two functions:
  - `next_phase(previous, cascade_view, now_ns, window_s) -> CascadePhase`:
    - An episode start moves to `building`, which lasts while the episode is open.
    - After the episode ends, the phase is `unwinding` while total rate ≥ baseline.
    - At the first update with total rate < baseline it becomes `fade_window` for `window_s` seconds, then `quiet`.
    - A new episode start always moves to `building`.
  - `cascade_allows(mode, side, phase) -> bool`:
    - `off`: always True.
    - `quiet`: True.
    - `follow`: in `building`, only the forced-flow side (`_side_of(direction)`) while rising and not spent. Every other non-quiet case is False.
    - `fade`: `building`/`unwinding` give False. `fade_window` allows only `_side_of(−direction)`.
- [x] `research/strategies/ofi_strategy.py`, `liquidation_cascade_strategy.py` (lift `units_of` only) -- Strategy changes:
  - **Config:** add `forced_flow_filter: bool = False` and `liquidation_cascade_mode: str = "off"`, plus `cascade_window_s=30`, `cascade_baseline_s=3600`, `cascade_intensity_threshold=3.0` and `cascade_decay_ratio=0.5` (the cascade strategy's defaults).
  - **Validation in `__init__`:** the mode is in `MODES ∪ {"off"}`. Enabling either option requires `has_liquidation_feed`.
  - **Subscription:** when either option is on, subscribe to `DataType(Liquidation)` with `ClientId(LIQUIDATION_CLIENT_ID)`.
  - **Filter:**
    - Accumulate per-side liquidation size units at the snapshot's `size_precision` between snapshots.
    - At each snapshot, push `organic_delta_units(buy_volume_units, sell_volume_units, liq_long, liq_short)` decoded to the cum-delta deque instead of `buy_volume − sell_volume`, then reset the accumulators.
  - **Cascade:** feed `LiquidationCascade` with `update_liquidation(side, notional_units_at(pp, sp), ts_init)` and `advance(snapshot ts)`, then `next_phase`.
  - **Entries:** `_enter` submits only if `cascade_allows`.
  - Keep functions ≤ ~30 lines.
- [x] `research/application/ports.py`, `backtest_runner.py`, `strategies/backtest_ofi.py` -- Add the `"seconds_liquidations"` data kind: derived quotes, snapshots and the liquidation config. It needs a feed id. `backtest_ofi.run` picks it when either option is on.
- [x] `research/tests/test_cascade_rules.py`, `test_ofi_strategy.py` (new tests only), `test_backtest_runner.py`/`test_ports.py` -- Tests:
  - Every phase transition and every gate branch.
  - Config refusals.
  - The filter changing cum delta exactly (hand-computed).
  - A follow-mode backtest suppressing an opposite-side entry during a planted burst.
  - The defaults producing identical fills to a run without the new fields.
  - The data kind.

*Gallery and notebooks*
- [x] `research/application/gallery.py`, `notebooks/08_strategy_gallery.py` (+ `.ipynb`), `test_notebooks.py` -- Gallery changes:
  - Add `ofi_specs(...)`: baseline, `forced_flow_filter`, `fade` and `follow` on `CASCADE_INSTRUMENT` with `data="seconds_liquidations"`, `OFI_PARAMS` overrides, and the fill/fee/latency axes unchanged. It returns `[]` without a feed.
  - Show the result table beside the baseline, with the sample size (days, episodes) printed and no claim beyond the recorded window.
  - Update the count text.
  - Add `NOTEBOOK_OFI_PARAMS` to the 08 env.
- [x] `research/notebooks/09_liquidations.py` (+ `.ipynb` via `make notebooks`), `test_notebooks.py` -- Notebook 09:
  - Calls `liquidation_study` and presents episodes and returns, leverage per day, forced share, match share, organic vs raw delta, liquidations vs OI, and cross venue. Each section has a reading-guide paragraph.
  - Add a `NOTEBOOK_ENV` entry with fixture-sized cascade params and a namespace test asserting ≥ 1 episode.

*Docs (MR4, OPS-01)*
- [x] Docs updates:
  - `docs/DATA_DICTIONARY.md` §2.12: notebook 09, the liquidations frame, the new functions and the `seconds_liquidations` kind; fix the stale notebook count.
  - `docs/NAUTILUS_INDICATOR_BACKTEST_CATALOG.md`: the OFI row documents the two parameters and the cascade params.
  - The spine's AD-D1 research row: an `[amended 2026-10-07: Story 33.13 — …]` naming liquidations among the research inputs.
  - `docs/DATA_INTEGRITY_AUDIT.md` D-220 onward: the second-attribution boundary, implied leverage from the bankruptcy price, the self-match undercount on split fills, and the fade/follow phase approximation.
  - `docs/DEPLOY_CHECKLIST.md`: a 33-13 entry before 33-14 stating that research code has no VPS action beyond the next image rebuild.
  - `research/README.md`: index row 09, "What each notebook shows", and the OFI copy row.
  - `kbData.ts` notebooks text and `platform/CLAUDE.md:167`: make the notebook count truthful.

**Acceptance Criteria:**
- Given `make test`'s notebook run, when 08 and 09 execute on the fixture, then both finish under the existing time limit with no warning. 09 reports ≥ 1 episode, and 08 prints the four OFI rows with their sample size.
- Given `cd platform && python3 -m pytest research/tests tests/test_boundaries.py tests/test_notebook_rules.py kernel/tests -q`, when the story is complete, then everything passes, including the unchanged OFI and cascade tests.
- Given `cd platform/frontend && npm test && npm run lint && npm run build`, when `kbData.ts` changes, then all pass.

## Spec Change Log

## Review Triage Log

### 2026-10-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 16: (high 1, medium 7, low 8)
- defer: 0
- reject: 0
- addressed_findings:
  - `[high]` `[patch]` OFI forced-flow filter subtracted a whole snapshot gap's liquidations from the first post-gap second; now discarded and counted (`unattributed_liquidations`, WARNING), same for one-sided snapshots; D-220.
  - `[medium]` `[patch]` Implied leverage reads ~1/(maintenance margin + fees) at the trigger, not chosen leverage; Known limit, notebook 09 and D-221 now say so, the "upper bound" claim is gone.
  - `[medium]` `[patch]` `_leverage` ignored the liquidated side; an impossible side now gives NaN and a `wrong_side` count.
  - `[medium]` `[patch]` Study episodes vs strategy episodes: the study now uses definition precisions (`MarketFrames.definition_precisions`); the notebook and gallery wording is corrected (seconds vs snapshot clock); D-223.
  - `[medium]` `[patch]` `next_phase` from `quiet` skipped an episode already ended after a gap; it now goes to `unwinding`.
  - `[medium]` `[patch]` The gallery filter row was a no-op without `cum_delta_threshold`; all four OFI rows now set it, and a BacktestNode test proves liquidations change decisions.
  - `[medium]` `[patch]` `cross_venue_liquidations` paired many-to-one; it is now one-to-one, nearest unused, ties to the later `b`.
  - `[low]` `[patch]` `liquidations_vs_oi` first bucket was NaN by construction; OI is now read one bucket early.
  - `[low]` `[patch]` Every read `ValueError` was ledgered as a duplicate; `LiquidationDuplicateError` now splits it from `LIQUIDATION_READ_SITE`.
  - `[low]` `[patch]` Duplicate indexes on the joined per-liquidation frames are now unique.
  - `[low]` `[patch]` `_other_leg` preferred feedless Hyperliquid and printed "None"; it now prefers a feed leg and the reason text is explicit.
  - `[low]` `[patch]` `backtest_ofi`: an explicit `TypeError` replaces the asserts, `forced_flow_filter` must be a strict bool, and the docstring states the NodeRunner path and the LINEAR requirement.
  - `[low]` `[patch]` The fade-window invariant is reworded (closes at the first snapshot after a gap; tested); direction-0 blocking is documented and tested; `days` became `days_touched` plus `hours`.

### 2026-10-07 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 10: (high 0, medium 4, low 6)
- defer: 0
- reject: 8
- addressed_findings:
  - `[medium]` `[patch]` `OFIStrategy`'s forced-flow filter netted a liquidation received before the previous second's snapshot into that earlier second. Liquidations are now held by their venue second (`ts_event // 1 s`, research's `organic_delta` rule) and netted only in that second's snapshot; D-220, the data dictionary and the module docstring are updated, and the old arrival-order Known limit is gone.
  - `[medium]` `[patch]` The first snapshot after a feed gap discarded its own second's liquidations, so its delta was raw. Gap-second rows are still discarded, but the snapshot's own rows are now netted (tested).
  - `[medium]` `[patch]` A liquidation received after its second's snapshot was netted into the next second. It is now counted `unattributed`, never moved (tested).
  - `[medium]` `[patch]` `match_to_trades` rebuilt every key's timestamp list for each liquidation, which is quadratic on a busy day. Each key's timestamps are now a sorted array built once.
  - `[low]` `[patch]` Liquidations received before the first snapshot were all netted into it. Only the first snapshot's own second is netted now (tested).
  - `[low]` `[patch]` `cross_venue_liquidations` paired greedily in `a`'s order, so an early episode could take a later one's only match. It now pairs nearest-first, with a Known limit (not a minimum-cost matching) (tested).
  - `[low]` `[patch]` Notebook 09's cross-venue cell printed a raw "Empty DataFrame" and a blank venue name. The new `CascadeLeadLag.lines()` says it in words (tested).
  - `[low]` `[patch]` The study's "unattributed" line said "no snapshot" but also counted seconds with two rows. The wording and notebook 09 now say "none, or two".
  - `[low]` `[patch]` Two limits were undocumented: the detector's cold start (no episode in the first `baseline_s`) and the read on `ts_event` vs the replay on `ts_init` at the window edges. Both are now Known limits in `liquidation_study`, and the cold start is in notebook 09's reading guide.
  - `[low]` `[patch]` `liquidation_study` accepted a backwards window and returned an empty study. It now raises `ValueError` (tested).

## Design Notes

**Why phases for the cascade gate.** With `intensity_threshold > 1`, an episode ends (spent and not active) before the total rate falls below the baseline. A gate built only on the episode would therefore never see the epics' "fade after the rate decays below the baseline". The strategy keeps a pure phase machine (`building → unwinding → fade_window → quiet`) so that both modes are literal:
- `follow` acts while the cascade builds.
- `fade` acts within one detector window after the rate is back under the baseline.

Both suppress every other entry while a cascade is detected. The `fade_window` length is the detector's own `window_s`, so there is no new parameter. Record this as a `Known limit:` with the upgrade path (a dedicated `fade_window_s`).

**Why the strategy is a filter, not a trigger.** OFI still decides the entries. The cascade mode only gates them, so the three variants differ from the baseline by exactly one input.

## Verification

**Commands:**
- `cd platform && python3 -m pytest research/tests kernel/tests tests/test_boundaries.py tests/test_notebook_rules.py -q -W error` -- expected: all pass.
- `cd platform && ruff check research && ruff format --check research && mypy research/application research/strategies` -- expected: clean.
- `cd platform/frontend && npm test && npm run lint && npm run build` -- expected: pass.

## Auto Run Result

Status: done

**Summary:** This was a follow-up review of Story 33.13 (two independent reviewers, then triage). It applied 10 patches, mainly how `OFIStrategy`'s forced-flow filter attributes liquidations.
- Each liquidation is now held by its venue second and netted only in that second's snapshot, the same rule as research's `organic_delta`.
- A liquidation whose second has no usable snapshot is counted as `unattributed`, never netted elsewhere.

**Files changed in this pass:**
- `platform/research/strategies/ofi_strategy.py`: per-second holding of liquidations, `_take_forced` settlement, and docstrings.
- `platform/research/application/liquidations.py`: linear-time trade match index, window check, two new Known limits, and the "unattributed" wording.
- `platform/research/application/aligned.py`: nearest-first cross-venue pairing and `CascadeLeadLag.lines()`.
- `platform/research/notebooks/09_liquidations.py` (+ `.ipynb`, synced with jupytext): the cross-venue text and reading-guide notes.
- `platform/research/tests/test_{ofi_strategy_forced_flow,aligned,liquidations}.py`: 8 new tests.
- `platform/docs/DATA_INTEGRITY_AUDIT.md` (D-220) and `platform/docs/DATA_DICTIONARY.md` (the OFI entry).

**Review:**
- 10 patches applied (medium 4, low 6), 0 deferred.
- 8 rejected:
  - unreachable `next_phase` case: an episode cannot start and end in one detector update;
  - seconds frame index: already a `DatetimeIndex`;
  - other leg's NaN returns: documented;
  - `backtest_ofi` routing: documented, and a bad mode still raises;
  - data-kind tuple drift: speculative;
  - test-helper import: the `.ipynb` exists;
  - gallery params override: documented order;
  - non-`ValueError` reads: they still raise.

**Verification:**
- `cd platform && python3 -m pytest research/tests kernel/tests tests/test_boundaries.py tests/test_notebook_rules.py verification/tests/test_ofi_parity.py -q -W error -W ignore::pytest.PytestConfigWarning`: 1637 passed, 4 skipped (all pre-existing).
  - The notebooks ran on the fixture.
  - The PytestConfigWarning ignore is for the root `pyproject.toml`'s unknown `asyncio_default_fixture_loop_scope` option under the system pytest.
- `ruff format --check research`: clean.
- `ruff check research`: only the SIM300 in `test_backtest_runner.py`, which was already there.
- mypy: no errors in the changed files. The 23 it reports are in other modules (e.g. `backtest_dydx.py`).
- Frontend: not touched in this pass, not re-run.

**Residual risks:**
- The attribution change alters which liquidations the OFI filter nets. Notebook 08's forced-flow numbers can differ from the first pass's.
- Same-second netting still depends on the liquidation's and its forced trade's stamps falling in one second (D-220 Known limit).
- `archive/tests` and `candles/tests` were not re-run; nothing they cover changed.
- Every result is a research reading of the recorded window.
