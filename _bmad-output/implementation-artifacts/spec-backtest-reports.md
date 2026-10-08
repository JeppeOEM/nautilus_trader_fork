---
title: 'Backtest reports: one saved folder per backtest run'
type: 'feature'
created: '2026-10-08'
status: 'done'
baseline_commit: 'dee9ea2aad'
review_loop_iteration: 0
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/platform/research/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A backtest run in the research notebooks leaves nothing behind once the kernel stops: no metrics, no record of the exact strategy code, parameters and data window, so runs cannot be revisited or compared.

**Approach:** One importable module saves every run as a folder `platform/data/backtest_reports/<StrategyClass>_<YYYY-MM-DDTHH-MM-SSZ>/` holding Nautilus's own tearsheet (`tearsheet.html`, built inside `NodeRunner` before the engine is disposed), a verbatim copy of the strategy module (`strategy.py`) and `record.json`, plus one `index.jsonl` line per run and readers for cross-run comparison (user-approved brief, 2026-10-07).

## Boundaries & Constraints

**Always:** Nautilus built-ins first (`create_tearsheet` with the engine, `create_tearsheet_from_stats` without); never modify `nautilus_trader/` or `crates/`; no new dependency; every write temp-then-rename; a save that fails raises and removes its half-written folder (DATA-07); `record.json` written with `allow_nan=False`; reports live beside the archive, never in it, and are git-ignored; `BACKTEST_REPORTS_DIR` overrides the root through `_params.py`; notebook tests point it at a tmp dir.

**Ask First:** none (unattended; the design is approved).

**Never:** a Markdown report; a second implementation of a statistic Nautilus or `MetricReport` already computes; saving into the catalog; silently skipping a tearsheet panel.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Engine run | `NodeRunner(report_root=R).run(spec)` | folder with tearsheet.html, strategy.py, record.json; index line; `result.report_dir` set | N/A |
| After the fact | `save_backtest_report(result, spec, R)` (engine gone) | same folder, tearsheet from stats + `result.returns` | N/A |
| Same strategy, same second | two saves | second folder suffixed `-2` | N/A |
| Nautilus undefined stat | `NaN` in `stats_*` | stored as JSON `null` (undefined) | N/A |
| Any other non-finite / non-JSON value | NaN/inf in params, wall time, stats inf | `ValueError` naming the key; folder removed | raise |
| Bars run, cache full | bars == cache `bar_capacity` | panel titled as the last N bars; benchmark omitted with the reason in record + table | N/A |
| No strategy source | `inspect.getsourcefile` None | `ValueError` naming the class | raise |

</frozen-after-approval>

## Code Map

- `platform/research/application/backtest_runner.py` -- `NodeRunner`, `_result` reads the engine before `node.dispose()`
- `platform/research/application/ports.py` -- `RunSpec`, `RunResult` (gains `run_id`, `instance_id`, `returns`, `report_dir`)
- `platform/research/application/evaluation.py` -- `pnl_by_hour_frame`/`pnl_by_weekday_frame` feed the custom panels
- `platform/research/domain/returns.py` -- `ReturnSeries.from_prices` for the buy-and-hold benchmark
- `platform/research/application/gallery.py`, `notebooks/08_strategy_gallery.py`, `notebooks/04_backtest_evaluation.py`, `notebooks/_params.py` -- wiring
- `platform/research/strategies/backtest_{ofi,liquidation_cascade,candle_pattern}.py` -- script wiring
- `platform/research/tests/fixture_catalog.py`, `test_notebooks.py` -- fixture env

## Tasks & Acceptance

**Execution:**
- [x] `platform/research/application/backtest_report.py` -- new: `save_backtest_report`, `load_backtest_record`, `list_backtest_reports`, the tearsheet config (default charts + "Run configuration" table + pnl-by-hour/weekday + `bars_with_fills` per bar type), benchmark, atomic writes, git revision -- the feature
- [x] `platform/research/application/ports.py` -- `RunResult` new optional fields -- carry engine identity and Nautilus's returns past dispose
- [x] `platform/research/application/backtest_runner.py` -- `NodeRunner(report_root=None)`; save before dispose -- engine-driven tearsheet
- [x] `platform/research/application/gallery.py` -- `report_frame(outcomes)` (label -> folder) -- find a row's report
- [x] notebooks 04/08 + `_params.py` (+ paired `.ipynb`) -- save the single run and every gallery row (not the execution-axis repeats)
- [x] `backtest_ofi.py`, `backtest_liquidation_cascade.py`, `backtest_candle_pattern.py` -- `report_root` keyword
- [x] `.gitignore`, `research/README.md`, `docs/DATA_DICTIONARY.md` §2.12, `docs/NAUTILUS_INDICATOR_BACKTEST_CATALOG.md` -- ignore + document
- [x] `platform/research/tests/test_backtest_report.py` -- round trip, sections, strategy copy, dirty flag, NaN refusal, index + listing, end-to-end through `NodeRunner` on `fixture_archive`

**Acceptance Criteria:**
- Given a gallery or notebook-04 run on the fixture, when the notebook finishes, then each saved folder's tearsheet holds every configured panel title and `record.json` loads back equal to the `RunResult`.
- Given `test_ad8_boundary.py`, `test_boundaries.py` and `test_notebooks.py`, when the platform suite runs, then all pass.

## Design Notes

- `register_chart` + `TearsheetCustomChart` alone renders nothing: `_create_tearsheet_figure` looks a custom chart up in `_TEARSHEET_CHART_SPECS` and silently skips an unknown name. The extra panels therefore register through `_register_tearsheet_chart`, the route Nautilus's own `docs/concepts/visualization.md` documents for tearsheet integration (marked internal); a `Known limit:` names it, the module checks every configured name is registered before building (no silent empty panel), and a test parses the HTML for every panel title so an upstream rename fails loudly.
- `create_tearsheet` builds its `run_info` internally with no hook, so the run's spec, models, revision and versions go in a separate "Run configuration" table panel (data passed through `TearsheetCustomChart.args`), keeping Nautilus's own Run Information / Account Summary untouched rather than re-deriving them.
- Every chart needs explicit `GridLayout` rows: the auto layout caps at 8 charts and drops the rest.
- Nautilus reports an undefined statistic as NaN; that one documented conversion stores it as `null`. Everything else non-finite raises.
- The OFI default path (`snapshot_backtest`, engine disposed by Nautilus) cannot be reported; `backtest_ofi.run(report_root=...)` refuses it naming the NodeRunner path.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. research/tests tests/test_boundaries.py -q` -- expected: all pass
- `ruff format --check` / `ruff check` / `mypy` on touched files -- expected: clean

## Suggested Review Order

**Saving a run**

- Entry point: one folder per run, written last-to-commit, removed on any failure.
  [`backtest_report.py:924`](../../platform/research/application/backtest_report.py#L924)

- Preflight before the run: root, JSON-able spec, strategy source read once.
  [`backtest_report.py:905`](../../platform/research/application/backtest_report.py#L905)

- NodeRunner checks every grid point's report before `node.build()`.
  [`backtest_runner.py:510`](../../platform/research/application/backtest_runner.py#L510)

- Save from the live engine, with the point's own params, before dispose.
  [`backtest_runner.py:552`](../../platform/research/application/backtest_runner.py#L552)

**The tearsheet (Nautilus's own)**

- Extra panels via the internal spec registry (Known limit); unknown panels refused.
  [`backtest_report.py:241`](../../platform/research/application/backtest_report.py#L241)

- Chart list and explicit GridLayout (auto layout drops panels past eight).
  [`backtest_report.py:631`](../../platform/research/application/backtest_report.py#L631)

- Engine-driven `create_tearsheet`, else `create_tearsheet_from_stats`.
  [`backtest_report.py:682`](../../platform/research/application/backtest_report.py#L682)

- Buy-and-hold benchmark only when the cached bars are complete.
  [`backtest_report.py:341`](../../platform/research/application/backtest_report.py#L341)

**The record and index**

- NaN is Nautilus's "undefined" -> null; everything else non-finite raises.
  [`backtest_report.py:399`](../../platform/research/application/backtest_report.py#L399)

- Every number of the run, one JSON object, `allow_nan=False`.
  [`backtest_report.py:738`](../../platform/research/application/backtest_report.py#L738)

- One O_APPEND write; failure truncates the index back.
  [`backtest_report.py:863`](../../platform/research/application/backtest_report.py#L863)

- Revision with `-dirty`, `unknown` without git, `-unknown-status` when unverifiable.
  [`backtest_report.py:501`](../../platform/research/application/backtest_report.py#L501)

- Cross-run listing, numeric metric columns, stable empty shape.
  [`backtest_report.py:1004`](../../platform/research/application/backtest_report.py#L1004)

**Wiring**

- RunResult carries run/instance ids, Nautilus returns and the report folder.
  [`ports.py:362`](../../platform/research/application/ports.py#L362)

- Notebook 08 saves every gallery row; axis repeats use a plain runner.
  [`08_strategy_gallery.py:89`](../../platform/research/notebooks/08_strategy_gallery.py#L89)

- Notebook 04 saves its single run only.
  [`04_backtest_evaluation.py:106`](../../platform/research/notebooks/04_backtest_evaluation.py#L106)

- Default OFI path refuses a report root loudly.
  [`backtest_ofi.py:89`](../../platform/research/strategies/backtest_ofi.py#L89)

- `BACKTEST_REPORTS_DIR` parameter.
  [`_params.py:104`](../../platform/research/notebooks/_params.py#L104)

**Peripherals**

- Reads guard: `engine.cache.bars` is the engine cache, not the catalog.
  [`test_research_reads.py:99`](../../platform/research/tests/test_research_reads.py#L99)

- End-to-end tests on the fixture archive.
  [`test_backtest_report.py:1`](../../platform/research/tests/test_backtest_report.py#L1)
