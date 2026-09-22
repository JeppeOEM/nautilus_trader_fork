# Story 27.2: Executable notebooks and the catalog inspection notebook

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> Research epic (Epic 27). Depends on Story 27.1 (`research/domain`, `research/application`) and Story 23.3 (durable error ledger, read here). Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 27.2".

## Story

As a strategy researcher,
I want every notebook stored as reviewable source that the test suite executes against a fixture catalog, and a first notebook that shows me exactly what my archive contains,
so that a notebook can never rot silently after a refactor again, and I can see coverage, gaps, verified days and data quality per venue before I trust a research result.

## Acceptance Criteria

1. **Given** `research/notebooks/` and the `jupytext` dev dependency already pinned in `pyproject.toml`
**When** the story ships
**Then** every notebook under `research/notebooks/` is a jupytext-paired `<nn>_<name>.py` (percent format, the source of truth, `ruff`- and `mypy`-clean like any other module) plus a `<nn>_<name>.ipynb` with outputs stripped (a `research/tests/test_notebooks.py` check fails on any stored output cell, on any `.ipynb` without its `.py` twin, and on a pair whose cells differ); each notebook's first code cell is a **Parameters** cell reading `CATALOG_PATH`, `CANDLES_DIR`, `METRICS_DB_PATH`, `INSTRUMENTS`, `START`, `END` from environment variables with the `platform/data/` defaults, so the same file runs against the fixture catalog in tests and the real archive on the user's machine; a `make notebooks` target syncs every pair (`jupytext --sync`) and is documented in `research/README.md` together with the one-line local launch (`uv run jupyter lab research/notebooks`, Jupyter itself stays a personal tool and is never containerised)

2. **Given** the notebook smoke test
**When** `make test` runs
**Then** `research/tests/test_notebooks.py` builds one small fixture catalog per session with `ParquetDataCatalog.write_data()` (two instruments on each of dYdX, Bybit and Hyperliquid, ~10 minutes of `DydxSecondSnapshot`, `TradeTick`, `MarkPriceUpdate`, `IndexPriceUpdate`, `FundingRateUpdate`, `OpenInterest`, plus a candle store built by `candles.application.rebuild_day` and a `metrics.db` with two rows), executes every `.py` notebook with `runpy` under `warnings.simplefilter("error")` (TEST-04) with plotly's renderer forced off-screen, asserts each finishes in under 60 s, and lists the fixture's deliberate defects (one gap, one provisional day, one crossed second) so a notebook that claims to show them can be checked against them

3. **Given** the archive, the candle stores and the durable error ledger from Story 23.3
**When** `01_catalog_inspection` runs
**Then** it shows, per venue and instrument: the instrument inventory (`MarketFrames` over `catalog.instruments()`, market kind from `kernel.venues`), the coverage timeline and gaps (`kernel.catalog_files.data_file_ranges` and `catalog_stats.find_gaps` via `views`/`kernel`, never re-derived), likely outages versus quiet market, verified/provisional day status from the candle store's `verified_days`, raw-trade-archive-versus-folded-seconds volume agreement per day (the Story 22.13 fold, `kernel.fold.fold_trades`, re-run over the raw `trade_tick/` archive for the window), a snapshot sanity table (spread `>= 0`, best bid `<` best ask, `Price.precision` uniform per instrument, `ts_init - ts_event` distribution) and the ledger's rejection counts by site over the same window; every read is bounded by `START`/`END`; the parent spine's Deferred item "Rejection-rate observability for research use" is struck with an amendment naming this notebook and the ledger reader it uses (MR14); `dydx_collector/notebooks/dydx_catalog_pandas.ipynb` (moved by 24.4) is deleted, with its one still-useful cell (the `to_dict` catalog-to-pandas idiom) folded into this notebook's first section

## Tasks / Subtasks

- [ ] Task 1 — notebook format and harness (AC: #1)
  - [ ] `research/notebooks/jupytext.toml` (`formats = "ipynb,py:percent"`, `notebook_metadata_filter = "-all"`, `cell_metadata_filter = "-all"`) so the `.py` carries no kernel metadata and diffs are clean.
  - [ ] `research/notebooks/_params.py`: `Params.from_env()` (`CATALOG_PATH` default `platform/data/catalog`, `CANDLES_DIR` default `platform/data/candles`, `METRICS_DB_PATH` default `platform/data/metrics/metrics.db`, `INSTRUMENTS` comma-separated default `BTC-USD-PERP.DYDX,ETH-USD-PERP.DYDX`, `START`/`END` ISO dates default "yesterday 00:00 UTC" to "today 00:00 UTC"); `NOTEBOOK_HEADLESS=1` sets `plotly.io.renderers.default = "json"` so figures are built but not shown. The helper is imported by every notebook's Parameters cell; it is the only place defaults live.
  - [ ] Makefile: `notebooks:` target = `uv run jupytext --sync research/notebooks/*.py`; `research/README.md` (created here as the notebook index stub; 27.9 completes it) documents `make notebooks`, the env vars, and `uv run jupyter lab research/notebooks` as the local launch (memory: never containerise personal tools).
  - [ ] Pre-commit: add the `jupytext --sync --pre-commit-mode` hook only if it needs no new package (jupytext is already a dev dependency); otherwise the test in Task 2 is the guard and the README says so.
- [ ] Task 2 — fixture catalog and smoke test (AC: #2)
  - [ ] `research/tests/fixture_catalog.py`: `build(tmp_path) -> FixturePaths` writing, through `ParquetDataCatalog.write_data()` only, one `CryptoPerpetual` per instrument (`BTC-USD-PERP.DYDX`, `ETH-USD-PERP.DYDX`, `BTCUSDT-LINEAR.BYBIT`, `ETHUSDT-LINEAR.BYBIT`, `BTC-USD-PERP.HYPERLIQUID`, `ETH-USD-PERP.HYPERLIQUID`), ~600 `DydxSecondSnapshot` rows each with 20 levels (precision from the instrument, `Price.from_str`, never floats before `Price`), `TradeTick`s whose fold reproduces the snapshots' `buy_volume`/`sell_volume` (use `kernel.fold.fold_trades` to generate the snapshots from the ticks so the agreement check is exact by construction), `MarkPriceUpdate`/`IndexPriceUpdate`/`FundingRateUpdate` rows, `OpenInterest` rows, a candle store per venue via `candles.application.rebuild_day`, one `verified` and one `provisional` day, a `metrics.db` via `ranking_engine.metrics_store.write` (two rows). Deliberate defects: a 90 s hole in one instrument, one second with `best_bid >= best_ask` (written straight into the fixture, since the gate would never write it live), documented in the module docstring and exposed as `FixturePaths.defects`.
  - [ ] `research/tests/test_notebooks.py`: session-scoped fixture building the catalog once; `@pytest.mark.parametrize` over `sorted(glob("research/notebooks/[0-9]*_*.py"))`; each run sets the env vars, `NOTEBOOK_HEADLESS=1`, `warnings.simplefilter("error")`, `runpy.run_path(...)`, asserts wall time `< 60 s`. A second test walks every `.ipynb`: no `outputs` non-empty, no `execution_count`, and a `.py` twin whose `jupytext` round-trip equals the notebook's cells.
  - [ ] Makefile `test` list already contains `research/tests` (24.4); confirm, do not duplicate.
- [ ] Task 3 — `01_catalog_inspection` (AC: #3)
  - [ ] Sections, each a markdown heading + one code cell + prose: (1) Parameters; (2) Instrument inventory (`catalog.instruments()` → `to_dict` table, `kernel.venues.market_kind`, per venue); (3) Coverage and gaps (`data_file_ranges`, `find_gaps`, `likely_outages`, `coverage` from their post-23.2 homes; plotly timeline per instrument); (4) Day status (`candles.application` `VerifiedDays.verified_status` per day in the window); (5) Trades-vs-seconds agreement (`MarketFrames.trades` → `fold_trades` → compare `buy_volume`/`sell_volume` sums per day with `MarketFrames.seconds`; a mismatch is printed as a red row, never hidden); (6) Snapshot sanity (`MarketFrames.seconds`: spread `< 0` count, crossed count, `Price.precision` set per instrument, `ts_init - ts_event` histogram); (7) Ledger rejections by site in the window (the 23.3 ledger reader; state clearly when the ledger is empty versus absent); (8) Summary table.
  - [ ] Delete `research/notebooks/dydx_catalog_pandas.ipynb` (post-24.4 path) after folding its `CryptoPerpetual.to_dict` idiom into section 2.
  - [ ] Parent spine `architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md` Deferred "Rejection-rate observability for research use": strike with `[amended <date>: Story 27.2 — 01_catalog_inspection §7 over the 23.3 ledger]`.

## Dev Notes

- **Why `runpy` and not a notebook runner:** NFR12 forbids `nbclient`/`papermill`; the jupytext `.py` twin is plain Python, so `runpy.run_path` executes exactly the cells in order. A cell that only works inside Jupyter (magics, `display` side effects) is a defect: notebooks use `print`/`fig.show()` and plotly's renderer switch.
- **Headless plotly:** set `plotly.io.renderers.default = "json"` under `NOTEBOOK_HEADLESS`; `fig.show()` then serialises without a browser. Do not import `kaleido` (not needed, not guaranteed installed).
- **TEST-04:** `warnings.simplefilter("error")` will surface pandas `FutureWarning`s (pandas 3 in `requirements.txt`, pandas 2.x pin in `pyproject.toml` — note both; the collector image is what `make test` runs). Fix the code, never filter the warning.
- **The fixture is the only place synthetic data is written**, and it writes through `ParquetDataCatalog.write_data()` (NAUT-02). Reuse the helpers 24.4's repaired tests already have (`ml_signals/tests/test_snapshot_backtest_node.py` builds snapshots); do not create a second snapshot factory.
- **Crossed-second defect:** the gate never writes one live, so the fixture writes it deliberately to prove the notebook shows it rather than hiding it (DATA-01/DATA-07). Every series plot must render that second as `None`.
- **Ledger reader:** Story 23.3's ledger is file-based (`observability/`); read it with its own query function, never by parsing log lines. If 23.3 is not merged when this story starts, section 7 states "ledger unavailable" and the story records the dependency; it does not fake a count.
- **Project rules:** DATA-01, DATA-07, MEM-01 (every read bounded by `START`/`END`), NAUT-01/02, TEST-01..04, READ-03, SIGNAL-01 (derived columns computed on read via `kernel.indicators`), memory "Don't containerize personal tools".
- **Working directory:** `platform/`; `python3 -m pytest -o addopts="" --rootdir=. research/tests -q`.

### Project Structure Notes

- `research/notebooks/01_catalog_inspection.py` + `.ipynb`, `research/notebooks/_params.py`, `research/notebooks/jupytext.toml`, `research/tests/{fixture_catalog,test_notebooks}.py`, `research/README.md` (stub), Makefile `notebooks` target.
- No new dependency. Jupyter is launched locally by the user.

### References

- Epic text: "Story 27.2"; FR71, FR72, NFR12, NFR3
- Code: `ml_signals/catalog_stats.py` (`find_gaps`, `likely_outages`, `coverage`, `data_file_ranges`), `collector_core/fold.py` (`fold_trades`, 22.13), `ml_signals/candle_store.py` (`verified_status`), `ranking_engine/metrics_store.py` (`write`), 23.3's ledger module
- Memory: `feedback_dont_containerize_personal_tools`, `feedback_warnings_are_not_noise`
- Parent spine Deferred: "Rejection-rate observability for research use"

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
