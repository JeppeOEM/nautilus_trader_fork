# Research notebooks

The notebook index for the research context (`platform/research/`). Story 27.2 created this page
as a stub; Story 27.9 completes it (and folds in `BACKTESTING.md`, the backtest how-to).

Every notebook holds no analysis logic of its own: each number comes from `research.domain`'s
values or `research.application`'s ports and services, every market-data read is bounded by
`START`/`END` (MEM-01), and a gap or defect is shown, never filled.

## Index

| Notebook | What it shows |
|----------|---------------|
| `notebooks/01_catalog_inspection` | What the archive holds per venue and instrument over a window: instrument inventory, snapshot-file coverage and gaps (likely outage vs book gap vs quiet market, or no mark coverage to tell), candle-store day status (verified / provisional / failed), raw-trades-vs-folded-seconds agreement per day, snapshot sanity (crossed, spread, `ts_init - ts_event`), per-file price-precision labels and the error ledger's rejections by site. Every number from `research.application.inspection`. |
| `notebooks/02_microstructure` | Per instrument over a window: spread in ticks and bps (series and by UTC hour), depth by level and by distance from mid (one snapshot a minute), order-book imbalance at 1/5/10/20 levels and the OFI strategy's OFI replayed with both z-scores against its threshold, the microprice edge on the next-second mid (binned scatter, hit rate per bin), trade flow and CVD with a Kyle-lambda impact fit per volume bucket, funding/basis/open interest on price, return autocorrelation at 1 s-1 h, the volatility signature and rolling realised volatility. Every number from `kernel.indicators`, `research.domain.microstructure` and `research.application.microstructure`. |
| `notebooks/03_correlation` | Across the collected universe over a window: return correlation at 1 m, 5 m, 1 h and 1 d (the candle store's bars, read span by span so an outage is a gap) as clustered diverging heatmaps plus per-venue matrices, rolling correlation against an anchor instrument, single-linkage clusters and the merge list with distances, funding-level (per hour, `rate * 60 / interval`) and OI-change correlation, and a cluster built into a `RunSpec` (printed, not run). Per asset across dYdX, Bybit and Hyperliquid: the mid basis in bps, the 1 s lead-lag with its peak in words ("BYBIT leads DYDX by 2 s"), the funding differential and each venue's volume share; a venue with no data prints "not collected in this window". Every number from `research.domain.correlation` and `research.application.aligned`; same-asset matching from `kernel.venues.asset_key`. |

`backtest.ipynb` and `candlestick_pattern_scanner.ipynb` are legacy notebooks, replaced by Stories
27.5 and 27.7.

## Format

Each notebook is a pair (`notebooks/jupytext.toml`):

- `<nn>_<name>.py` -- jupytext percent format, **the source of truth**: reviewed like any module,
  ruff- and mypy-clean, LGPL header in its first (raw) cell;
- `<nn>_<name>.ipynb` -- its twin for Jupyter, stored with **no outputs** and no metadata.

Edit either one, then sync:

```bash
make notebooks        # from platform/: uv run jupytext --sync on every numbered pair
```

There is no pre-commit hook for this: `research/tests/test_notebooks.py` (part of `make test`) is
the guard. It fails an `.ipynb` with stored outputs or execution counts, an `.ipynb` without its
`.py` twin, a numbered `.py` without its `.ipynb`, and a pair whose cells differ; and it runs every
numbered `.py` with `runpy` against a small fixture archive (`research/tests/fixture_catalog.py`:
3 venues x 2 instruments with a planted outage, quiet market, crossed second, duplicate trade,
provisional day, ledger lines and, since Story 27.4, a Bybit BTC leg leading its other venues by 2 s) under `warnings.simplefilter("error")`, in under 60 s each.

## Parameters

The first code cell of every notebook reads its inputs through `notebooks/_params.py`
(`Params.from_env()`, the only place defaults live), so the same file runs against the fixture in
tests and against the real archive on your machine:

| Variable | Default |
|----------|---------|
| `CATALOG_PATH` | `platform/data/catalog` |
| `CANDLES_DIR` | `platform/data/candles` |
| `METRICS_DB_PATH` | `platform/data/metrics/metrics.db` |
| `ERRORS_DIR` | `platform/data/errors` (the Story 23.3 error ledger) |
| `INSTRUMENTS` | `BTC-USD-PERP.DYDX,ETH-USD-PERP.DYDX` (comma-separated) |
| `START` / `END` | yesterday 00:00 UTC / today 00:00 UTC (ISO; naive = UTC) |
| `NOTEBOOK_HEADLESS` | unset; `1` builds and serialises every figure but shows none (tests) |

## Running Jupyter

Jupyter is a personal tool: it runs locally, never as a compose service.

```bash
cd platform && uv run jupyter lab research/notebooks
```

The kernel starts in `research/notebooks/`; `_params.py` puts `platform/` on `sys.path`, so the
platform packages import as they do in the tests.
