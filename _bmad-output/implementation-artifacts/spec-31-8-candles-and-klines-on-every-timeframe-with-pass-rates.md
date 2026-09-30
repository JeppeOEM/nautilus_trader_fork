---
title: 'Story 31.8: Candles and klines on every timeframe, with pass rates recorded'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_revision: '04ef9b081e'
final_revision: '0c4bfa07ff'
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

**Problem:** No tool proves the candles against the market. Story 31.7 compared only the six stored widths with a fold of the rows a backtest receives. Nothing checks the following:
- the stored widths against the venue's own trades;
- the four read-time widths (10m, 30m, 45m, 1W). These exist only as `data_api` responses, folded from raw 1 s rows by `views.chart_series._parquet_page`;
- `seconds_observed` and `partial` against the coverage record;
- the difference between untraded buckets and missing data.

Planning also found four defects:
- `_parquet_page` folds whatever part of the oldest bucket its non-aligned query window reaches. A 1W bucket is *always* capped at `MAX_QUERY_SPAN_SECONDS` (7 d), so the chart's first 1W page shows the previous week truncated, with no marker.
- Read-time bars carry no `partial` flag at all.
- The nightly's step subprocesses never call `error_ledger.start()`, so every `reconcile.kline_mismatch` of D-51 reached stdout only. The verify stack's `archive.jsonl` holds 3 lines against 4,901 mismatches.
- D-51's pass rates were never recorded.

**Approach:** Add `python3 -m verification.candles --venue V --day D`. For every plan instrument and every width, it judges each bucket of D against three sources:
- the fold of the catalog's rebuilt seconds, which must be exact;
- the reference fold (31.3 `fold_candles`) of the recorder's trades over the seconds the catalog observed, with every difference explained by a per-second cause or failing;
- the coverage record (31.2), which must explain every unobserved second.

It checks both the stored bars (SQLite, read raw) and the bars `data_api` serves (HTTP, the chart's own path, all ten widths). Then fix the four defects and record the 2026-09-29 compare_klines pass rates in D-51 and the report, with every both-sided mismatch root-caused.

## Boundaries & Constraints

**Always:**
- **DATA-02 split:** the tool is oracle-side only. It never imports `candles`, `views`, `data_api`, `capture`, `ranking`, `kernel.fold`, `kernel.second_snapshot`, `nautilus_trader` or `verification.subject`/`verification.catalog`.
  - Production candles are read as data at rest: the SQLite store, read-only (`CandleStoreFile`).
  - Or read as the served HTTP response: `GET <data-api>/api/candles/{iid}?before_ns&limit&bar_seconds`.
  - The new HTTP module goes in `NON_VENUE_HTTP_CLIENTS` ("the local data_api HTTP API"), with stdlib `urllib` only.
  - `verification.candles` joins `VERIFICATION_ROOTS` and the runtime no-denied-module probe.
- **Reuse, and restate rather than import:**
  - Reuse `fold_candles`, `bucket_start`, `RefCandle`, `at_places`, `StoredBar`, `TradeRow`, `judge_bar`/`judge_width` and `CANDLE_CLASSES` (31.7), plus `STORE_BAR_SECONDS`, `CandleStoreFile`, `read_day(... SNAPSHOT_DIR, TradeRow.columns() ...)` and `CoverageFiles`.
  - Also reuse `collect_coverage`, `channel_trades`, `merge_reference`, `fold_second`, `recorder_gaps`, `trade_channels`, `wire_index`, `instrument_category`, and `plan_of`/`directory`/`Refused`/`is_closed`/`day_start_ns`/`day_hours`.
  - Private helpers the tool needs become public under their existing names without the underscore: `domain.conservation._reasons`, `application.trades._channel_gaps`, `verification.catalog.candle_store` (moved to `verification.conservation` next to `directory`). Update their callers.
  - Restate these, never import them:
    - `READ_TIME_BAR_SECONDS = (600, 1800, 2700, 604800)`;
    - `PARTIAL_OBSERVED_FRACTION = 0.9`, used as `partial = seconds_observed < 0.9 × width`;
    - `SERVED_PAGE_LIMIT = 500`.

    Cite `docs/DATA_DICTIONARY.md` §2.5 and the route.
- **Per instrument, per bucket of D:** the bucket grid is `86400 / w` buckets for each of the ten widths. 1W is handled separately below.
  - **Observation:** `rows` is the number of catalog snapshot rows with `ts_event` in the bucket, from the raw read.
    - Every second of the bucket without a row takes its reason from the coverage `SecondsRun`s.
    - A second with no row and no reason counts toward `unexplained_seconds`, which fails.
    - The bucket kind is `untraded` when it has rows but no trade, and `no_data` when it has no rows. Both are reported apart from `traded`; neither fails by itself.
  - **Catalog fold:** `judge_width` against the stored bars for the stored widths, which is 31.7's classes with `seconds_observed` int-equal. Its FAILING classes fail the run.
  - **Served:** every served item with `o` set, paged back from `before_ns = D+1` until `t < D` or `has_more` is false. Gap rows (`o` null) are ignored.
    - The o/h/l/c/v comparison with the catalog fold uses `at_places` at the bucket's places. `served_differs` fails; `float_noise` is reported apart and does not fail.
    - `partial` must equal the restated rule over `rows`. `partial_mismatch` fails, and so does a missing flag.
    - A traded bucket that is not served is `served_missing`, and a served bucket that is not traded is `served_extra`. Both fail.
  - **Reference:** trades with venue time in the bucket, merged by id, and folded per second with `fold_second` at that second's row precisions, over the seconds that have a row only.
    - When the bar (stored and served, via `at_places`) is not `exact`, `float_noise` or `both_undefined`, compare each second's `TradeColumns` with the row's.
    - Every differing second must lie in one of three places, or the bucket is `ref_different` (fails):
      - a recorder gap of one of the instrument's WS channels, making the bucket `ref_recorder_gap`;
      - a coverage `TradeWindow`;
      - an archive-gap marker span (live values kept), making the bucket `ref_explained`.
    - Reference trades in seconds without a row are counted per instrument, by that second's coverage reason, as `trades_unobserved`. This is informational, since the bar understates them by design.
- **1W:** the bucket containing D is judged only once its week is closed, i.e. `bucket end + DAY_SETTLE_NS ≤ now`. Otherwise it is reported `week_open` and does not fail.
  - The reference is the catalog fold of the week's seven days, read day by day with `read_day` (MEM-01).
  - The served bar is fetched twice:
    - aligned, with `before_ns` = the week's end;
    - chart-like, with `before_ns` = the week's end + 1 day, when that is ≤ now. It must either omit the week or serve it whole. A truncated bar is `served_differs`.
  - Known limit: 1W is proven against the reference only transitively, through each day's 1d verdict. The upgrade path is a `--week` reference fold.
- **Refusals** are ledgered at `verification.candles.refused` (`sites.py`, `test_sites.py` `_PREFIXES`):
  - an open day;
  - a missing catalog, candle store, raw directory or coverage file;
  - `data_api` unreachable, an HTTP non-200, or a malformed page;
  - a file vanishing mid-run.

  Crashes are recorded at the same site and re-raised. Exit 0 when every failing count is 0, else 1; exit 2 on a usage error.
- **CLI:** `--venue --day [--json] [--catalog] [--raw-dir] [--candles] [--data-api URL]`, and `main(argv, clock)` with an injectable `fetch` for tests. `--data-api` defaults to env `VERIFY_DATA_API_URL`, else `http://127.0.0.1:29100` (the verify stack, SEC-01).
  - `--no-served` skips the served checks.
  - The report then says `served: not checked` and the run is `provisional`, never a full pass. 31.11 runs it with served checks on.
- **Fixes in production code**, each with a test that fails before the fix:
  - `views/chart_series.py` `_parquet_page`: every query window (the first, and every gap-jump window in `catalog_reads.fetch_page`) spans whole buckets of `bar_seconds`, so no served bar is folded from a partial bucket.
    - Align the window's end up to a bucket boundary and its span down to whole buckets, never below one bucket and never above `MAX_QUERY_SPAN_SECONDS`.
    - The page filter `t < before_ms` is unchanged.
    - Known limit: a 1W page from Parquet holds at most one week per request. The upgrade path is read-time widths composed from stored bars.
  - `candles/application/queries.py` `candle_dicts_for_window`: each raw_1s bar also carries `partial` (`is_partial(seconds_observed, bar_seconds)`) from the fold's `seconds_observed`. The `/ws/live` forming-bar payload (`bars_from_rows`/`forming_bar`) is unchanged.
    - Update the `CandleItem.partial` comment, the frontend `api/schema.ts` doc if it states "absent on raw-1s", and `DATA_DICTIONARY.md` §2.5.
  - The nightly step entrypoints `archive/rebuild_seconds.py`, `archive/consolidate_catalog.py`, `candles/rebuild.py`, `archive/compare_klines.py`, `archive/prune_catalog.py` and `archive/nightly.py`, as `main()`: call `error_ledger.start(service=f"{os.environ.get('ERROR_LEDGER_SERVICE') or 'archive'}.<step>")`.
    - Each step gets its own file, so a child's `process_start` never resets the scheduler's since-restart window.
    - Test: each main opens a durable sink under a tmp `ERROR_LEDGER_DIR`.
- **AC2, klines:** read the verify stack's nightly result for 2026-09-29 from `verified_days` and `docker logs verify-archive`.
  - Record in `docs/VERIFICATION_REPORT.md` and D-51:
    - per venue and instrument: minutes compared and matched, the pass rate, mismatches split into `ours missing` / `theirs missing` / both-sided;
    - the soak window.
  - Root-cause each of the ~20 both-sided mismatches with evidence: the reference recorder's trades for that minute, the tool's per-second view, and a re-fetched venue kline.
  - Missing-ours minutes outside the soak window (before 12:59:19Z, and the operator's host-off hole from about 20:42Z) are explained by it.
  - D-51 closes locally only if every compared minute is explained. Otherwise it stays OPEN, naming each unexplained class and a follow-up story.
- **AC3:** 31.3 already made 1W Monday-anchored in every layer and removed the 1W→1D indicator clamp. Verify that the pinning tests listed in the Code Map still pass. Record it in audit D-118+ and cite the tests. The tool's 1W check (the reference `bucket_start`, Monday) pins it end to end.
- **Smoke:** run the tool on the live verify dirs (read-only) for 2026-09-29, for both venues, with served checks against `127.0.0.1:29100`.
  - Record per instrument and width: the classes, the unexplained seconds and their reasons, `trades_unobserved`, runtime and peak RSS.
  - Root-cause every failing count, then fix it or register it OPEN with a follow-up.
- Code rules: LGPL header, full typing, functions of about 30 lines or fewer, complexity ≤ 10, no module-level mutable state, and a `Known limit:` with the measured memory and runtime. No new dependency. No value tolerance.

**Block If:**
- The served check would need the oracle to import `views`, `candles` or `data_api`, i.e. HTTP cannot reach the served bars.
- A both-sided kline mismatch proves a capture defect whose fix needs a decision on the venue's data semantics. In that case register it OPEN instead, and block only if even that is impossible.

**Never:**
- Never modify `nautilus_trader/`, `crates/` or `sprint-status.yaml`, and never write the live catalog or candle store.
- Never change the frozen store schema (`test_schema_is_frozen.py`) or the `/ws/live` payload.
- Never set `awaiting-operator`. The real-day verdict belongs to 31.11.
- Do not build 31.9-31.11, and do not add `verify_day`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| Clean day | catalog + store from the real `candles.rebuild`, served by the real route, reference matching | all failing 0; untraded / no_data reported; exit 0 |
| Altered stored bar | one 1m `c` changed | catalog-fold `different` 1 and served `served_differs` 1; exit 1 |
| Truncated served 1W | stub page folding half the week | `served_differs` 1; exit 1 |
| Missing partial | served stored bar with `partial` absent or wrong | `partial_mismatch`; exit 1 |
| Reference differs | a reference trade the catalog lacks, not in any gap | `ref_different` 1; exit 1 |
| Recorder gap | the same, inside a connection close→open | `ref_recorder_gap`; pass |
| Unobserved second | no row, coverage `crossed` | counted under `crossed`; pass |
| Unexplained second | no row, no coverage | `unexplained_seconds` 1; exit 1 |
| Week open | D mid-week, week not closed | 1W `week_open`; pass |
| data_api down | connection refused | refused, ledgered; exit 1 |
| `--no-served` | any | served not checked; `provisional`; no full pass |

</intent-contract>

## Code Map

- `platform/verification/catalog.py:84`, `application/catalog.py`, `domain/catalog_check.py:72,111-120,525-700` -- 31.7's candle judging to reuse, plus `candle_store()` to move.
- `platform/verification/infrastructure/catalog_scan.py:256` (`CandleStoreFile`), `infrastructure/catalog_reader.py:107-142,353` (`read_window`, `read_day`, `CoverageFiles`).
- `platform/verification/domain/reference_signals.py:138,453-507` -- `RefBook.from_stored`, `bucket_start`, `RefCandle`, `fold_candles`; `domain/signal_compare.py:66-135` -- `at_places`.
- `platform/verification/domain/trade_check.py:200-360` -- `TradeColumns`, `fold_second`, `recorder_gaps`, `merge_reference`; `application/trades.py:174-260` -- `_channel_gaps` and the hour-reference pattern to mirror.
- `platform/verification/domain/conservation.py:75-133,361,653` -- `SecondsRun`, `TradeWindow`, `ReferenceTrade`, `_reasons`; `application/conservation.py:153,187,217,293,311` -- `day_start_ns`, `collect_coverage`, `channel_trades`, `day_hours`, `is_closed`.
- `platform/verification/derivs.py`, `trades.py:124` -- the CLI root pattern.
- `platform/views/chart_series.py:735-849` -- the `_parquet_page` window, `candle_page`; `views/catalog_reads.py:68-92` -- `fetch_page`.
- `platform/candles/application/queries.py:145`, `application/forming.py`, `domain/fold.py:79-181`, `domain/candle.py:228-260` -- read-time fold, `is_partial`.
- `platform/data_api/routes/candles.py` -- the served contract (`CandleItem`, limit ≤ 500).
- `platform/archive/compare_klines.py`, `archive/nightly.py:193`, `archive/application/reconcile_day.py:209,256-272`, `archive/scheduler.py:199` -- the nightly steps and the ledger gap.
- `platform/tests/test_boundaries.py:1152,2000-2170` -- `NON_VENUE_HTTP_CLIENTS`, verification roots and probe.
- 1W pinning tests (AC3): `candles/tests/test_candles.py:306,321`, `data_api/tests/test_indicator_series.py:320`, `data_api/tests/test_indicators_config.py:382`, `data_api/tests/test_candles.py:282`, `verification/tests/test_reference_signals.py:519`.
- `platform/verification/tests/test_catalog.py:119,235-330`, `test_conservation.py:78-180`, `test_trades.py:145-260` -- catalog, store, raw and env builders.
- `platform/docs/VERIFICATION_REPORT.md:66` (candles row), `docs/DATA_INTEGRITY_AUDIT.md:163` (D-51; next id D-118), `docs/DATA_DICTIONARY.md` (§1.20 last; §2.5 candles).

## Tasks & Acceptance

**Execution:**
- [x] `platform/verification/domain/candle_check.py` (new, pure) -- the restated constants, observation accounting per bucket, served judging, reference-difference explanation per second, the 1W window, the report dataclasses with `passed`/`provisional`.
- [x] `platform/verification/infrastructure/served_candles.py` (new) -- the urllib pager for `/api/candles`, which maps transport errors to `Refused`.
- [x] `platform/verification/application/candles.py` (new) -- the ports (`Protocol`s), `check_day`, `render_text`, `report_json`.
- [x] `platform/verification/candles.py` (new root) -- the CLI, wiring and refusals; `verification/conservation.py` gains `candle_store`, and `verification/catalog.py` uses it.
- [x] `platform/verification/domain/conservation.py`, `application/trades.py`, `application/sites.py`, `tests/test_sites.py` -- the public renames, `CANDLES_REFUSED`, the prefix.
- [x] `platform/tests/test_boundaries.py` -- the root, `NON_VENUE_HTTP_CLIENTS`, the probe, and a `COMPOSITION_ROOTS` entry for the new test module (`{CANDLES, DATA_API, VIEWS}`) if it drives the real route.
- [x] `platform/verification/tests/test_candles.py` (new) -- one end-to-end clean day (a real `write_data` catalog, the store built by the real `candles.application.rebuild`, the real route through `fastapi.testclient` as `fetch`), every I/O row, unit tests of each class, and the pager against a stdlib `http.server` stub.
- [x] `platform/views/chart_series.py`, `views/catalog_reads.py`, `views/tests/` -- whole-bucket windows, with tests: a 1W first page at a mid-week `before_ns`, a capped 30m/45m page, and a gap-jump window.
- [x] `platform/candles/application/queries.py`, `candles/tests/`, `data_api/routes/candles.py` comment, `data_api/tests/test_candles.py` -- `partial` on raw_1s bars.
- [x] The six nightly entrypoints and their tests -- `error_ledger.start(service=...)`.
- [x] Smoke and kline investigation (see Always), then the docs:
  - `DATA_DICTIONARY.md`: §1.21 (classes, sources, the 1W rule, the served paging, repro) and the §2.5/§6 updates;
  - `VERIFICATION_REPORT.md`: the candles row(s), the kline pass-rate table, and how each was run;
  - `DATA_INTEGRITY_AUDIT.md`: D-51 updated, plus D-118 onwards for every finding (the truncated 1W, the missing partial, the ledger gap, AC3, every smoke and kline finding);
  - `platform/CLAUDE.md`: DATA-02's tool list.

**Acceptance Criteria:**
- Given a closed day and a running data_api, when `python3 -m verification.candles --venue V --day D` runs, then it prints per instrument and width: the traded/untraded/no_data counts, the catalog-fold and served classes, the reference classes, the partial checks, and the unexplained seconds by reason. It exits 1 exactly when a failing count is non-zero or the inputs are refused.
- Given the verify stack's 2026-09-29 nightly, when the story ships, then `VERIFICATION_REPORT.md` and D-51 carry the per-instrument compare_klines pass rates, and every both-sided mismatch minute has a root cause with evidence or an OPEN audit row.
- Given `make test`'s suites for verification, views, candles, data_api, archive and boundaries, when run, then all pass, and the 1W Monday-anchor and 1W-indicator tests still pin AC3.

## Spec Change Log

## Review Triage Log

### 2026-09-30 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 13: (high 1, medium 4, low 8)
- defer: 0
- reject: 5: (high 0, medium 1, low 4)
- addressed_findings:
  - `[high]` `[patch]` A historical mid-bucket `before_ns` read rows after the cursor once windows were aligned up (look-ahead for API and research callers). Each fetch now stops at `min(end, before_ns) - 1`, so the cursor bucket folds only earlier seconds and is flagged `partial`. Tested; §2.5 and D-118 corrected.
  - `[medium]` `[patch]` A gap jump dropped the row lying exactly on a bucket boundary, because range ends are inclusive and the fetch reads to `end - 1`. With an align hook, the jump now targets `align_end(min(last + 1, start))`. Tested.
  - `[medium]` `[patch]` Duplicate rows of one second each carried the whole reference fold, doubling the reference. Now only the first row carries it, so a duplicated second fails the reference check. Unit and end-to-end tests.
  - `[medium]` `[patch]` The six one-shot verification tools opened the ledger under an inherited `ERROR_LEDGER_SERVICE` (e.g. `archive`), resetting that service's since-restart window. They now use `job_service("verify_<tool>", "verification")`. Tested.
  - `[medium]` `[patch]` `research.application.inspection` counted each nightly job's `process_start` as a restart. Job ledgers now go to a `runs` table. Tested.
  - `[low]` `[patch]`:
    - A dotted `ERROR_LEDGER_SERVICE` from the environment is now clamped to `_` with a warning, so a dot always means a job.
    - The archive and candles missing-catalog site constants are pinned equal.
    - `http.client.HTTPException` and an empty page claiming `has_more` are now refused.
    - The chart-like 1W page is documented as the D-118 regression probe, with next-week fixtures.
    - A `Known limit:` covers the forked workers' inherited sink.
    - The `/api/errors` docstring states the job semantics; `openapi.json` was regenerated.
    - §1.21 wording now matches the code.
  - Rejected:
    - one 1W bar per Parquet request: a documented Known limit, and `useCandles` pages back until `has_more` is false;
    - `ValueError`/`ArithmeticError` mapped to a refusal: the house pattern, which is ledgered and exits 1;
    - the week fold re-reading seven days: only on closed weeks, snapshot trade columns only;
    - `/api/errors` "forgetting" last night's job errors: they are the latest run by design, and the file keeps the history (documented).

### 2026-09-30 — Review pass (follow-up review)
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 1, low 5)
- defer: 1: (high 0, medium 0, low 1)
- reject: 13: (high 0, medium 3, low 10)
- addressed_findings:
  - `[medium]` `[patch]` Every venue's run of a nightly step shared one `archive.<step>.jsonl`. The scheduler runs the venues' sagas back to back, so Hyperliquid's `process_start` hid Bybit's `reconcile.kline_mismatch` lines from `/api/errors`' `since_start`. `job_service` now takes the run's venue, so each run writes `archive.<step>_<venue>.jsonl`. This covers all six steps and all six verification tools; a catalog-wide `consolidate_catalog` stays `archive.consolidate_catalog`. Tested (`test_each_venues_run_of_a_step_keeps_its_own_ledger`, `test_each_venues_run_of_a_job_has_its_own_ledger`). §6, D-120, the `/api/errors` docstring (with `openapi.json` regenerated) and the crosscheck docstring were updated to match.
  - `[low]` `[patch]` `job_service` did not clamp a dotted inherited parent the way `start()` does, so a job could name `bybit.collector.*` next to a `bybit_collector.jsonl` parent. It now clamps the same way. Tested.
  - `[low]` `[patch]` The five existing verification tools' move to `<parent>.verify_<tool>_<venue>.jsonl` is now documented in §6.
  - `[low]` `[patch]` The catalog-inspection notebook never printed `LedgerWindow.runs`, so it did not show nightly job runs. It now prints them and counts them in its summary. The `.ipynb` was re-synced.
  - `[low]` `[patch]` A non-HTTP `--data-api` (e.g. `file://`) crashed with `int(None)` instead of being refused. `urllib_fetch` now refuses it. Tested.
  - `[low]` `[patch]` A capped coverage window explained any per-second difference, including a catalog surplus. This is now a documented `Known limit:` (`Causes`, §1.21): the per-trade proof is `verification.trades`' `Explanations`. The upgrade path is named.
  - Deferred: an explicit empty plan passes over nothing in every verification tool. This is a cross-tool pattern from 31.2 onwards.
  - Rejected:
    - The candle reference ignores the buy/sell split. By spec, that split is `verification.trades`' per-second check.
    - An off-grid second can be explained by a cause. §1.21 says only that such a second always *differs*.
    - A refactor could break the off-grid guard. This is hypothetical.
    - 1W is judged only after close + 2 h, and the probe only after close + 1 day. Both are by spec.
    - One 1W candle per Parquet page. This is a spec Known limit, and the frontend pages back.
    - Job `process_start` lines are exempt from `--fail-on`. A crashing step is still recorded in the scheduler's own ledger as `nightly.<step>`.
    - Forked workers inherit the sink. There is already a Known limit, and the nightly uses `--workers 1`.
    - A page of gap rows only. Impossible, because `with_gap_markers` inserts gaps strictly between real rows.
    - Strict item keys. A malformed page is refused by spec, and a verifier should fail on schema drift.
    - `align_end is _unaligned` identity. This is an internal default and has no caller that fits the scenario.
    - Scratch evidence was not kept. The tool reproduces the per-second view, and the docs record the evidence.
    - Duplicate rows counting toward `partial`. Duplicates fail the catalog and reference checks.
    - A clamped name colliding with an existing service. This is speculative.

## Design Notes

**Why HTTP for served bars:** the read-time widths never exist at rest. The only production artifact is the response the chart receives. Importing `views` into verification would widen the DATA-02 subject exemption, which 31.7 made an operator decision. An HTTP GET keeps the tool oracle-only and checks exactly what the chart draws.

**Why the reference is masked to observed seconds:** a bar over a second with no row can never contain that second's trades. Comparing it with an unmasked fold would fail every rejected second. Instead, those trades are counted by coverage reason, and the unobserved second itself must be explained. The equality then asserted is exact: stored or served bar = fold of the reference trades over the seconds the archive holds.

**Aligned window (illustrative):**
```python
end = align_up(before_ns, w)                          # whole buckets only
span = max(w, min(limit * w * 3, MAX_SPAN) // w * w)
# fetch rows in [end - span, end); keep bars with t < before_ms
```

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. verification/tests views/tests candles/tests data_api/tests archive/tests tests/test_boundaries.py -q` -- expected: all pass (apart from the known-failing tests in the memory note, which must be unchanged).
- `cd platform && ruff format --check verification views candles data_api archive tests && ruff check verification views candles data_api archive tests && mypy verification` -- expected: clean.
- `cd platform && CATALOG_PATH=data/catalog VERIFY_DATA_DIR=data/verification CANDLES_DIR=data/candles python3 -m verification.candles --venue BYBIT --day 2026-09-29` -- expected: a report, with every failing count root-caused in the audit.


## Auto Run Result

Status: done

- **Change (story, committed at `5a101c9ebd`):**
  - New `python3 -m verification.candles --venue V --day D`, which is oracle-side only. It proves every width's buckets (six stored, three read-time, and the closed 1W) against three things:
    - the catalog fold;
    - the masked reference trades;
    - the coverage record.

    It covers the stored bars read raw from SQLite and the bars `data_api` serves over HTTP.
  - Production fixes:
    - whole-bucket Parquet candle windows (D-118);
    - `partial` on raw_1s bars (D-119);
    - per-job durable ledgers (D-120);
    - `candles.rebuild` refuses a missing catalog (D-123).
  - The 2026-09-29 kline pass rates are recorded in D-51 and `VERIFICATION_REPORT.md`.
- **Follow-up review pass (this run):** 6 patches, 1 deferred, 13 rejected (see the Review Triage Log).
  - `observability/error_ledger.py`: `job_service(job, parent, venue)` writes one ledger per venue-run and clamps a dotted inherited parent.
  - `archive/{compare_klines,consolidate_catalog,nightly,prune_catalog,rebuild_seconds}.py`, `candles/rebuild.py`, `verification/{book,candles,catalog,conservation,derivs,trades}.py`: these pass `args.venue`.
  - `verification/infrastructure/served_candles.py`: refuses a non-HTTP `--data-api`.
  - `verification/domain/candle_check.py` `Causes`: a `Known limit:` for capped and surplus coverage windows.
  - `research/notebooks/01_catalog_inspection.{py,ipynb}`: prints job `runs`.
  - `data_api/app.py`, `archive/application/crosscheck.py`, `frontend/openapi.json`: docstrings updated and the schema regenerated.
  - `docs/DATA_DICTIONARY.md` §1.21 and §6, and `docs/DATA_INTEGRITY_AUDIT.md` D-120.
  - Tests: `archive/tests/test_step_ledgers.py`, `observability/tests/test_error_ledger.py`, `verification/tests/{test_tool_ledgers,test_candles}.py`.
- **Deferred:** every verification tool passes over an explicit empty plan (a cross-tool pattern, appended to `deferred-work.md`).
- **Verification:**
  - `python3 -m pytest -o addopts="" --rootdir=. verification/tests views/tests candles/tests data_api/tests archive/tests observability/tests research/tests tests/test_boundaries.py -q` gave 2,239 passed. The failures are 9 redis-dependent `data_api` tests (no local redis) and 5 `research/test_backtest_runner.py` errors (an already-deferred fixture). Both sets are identical on the pre-patch HEAD. My one new test failure (the ledger sink not reset between runs) was fixed and the file re-run: 36 passed.
  - `mypy verification observability/error_ledger.py` is clean. `ruff format --check` is clean. The 9 `ruff check` findings were already there, in lines this pass did not touch.
- **Residual risks:**
  - Ledger file names change on the next deploy: `archive.compare_klines.jsonl` becomes `archive.compare_klines_bybit.jsonl` and so on. The old files stay on disk as history.
  - The cap/surplus gap in the candle reference relies on `verification.trades` running for the same day. 31.11's gate must run both.
