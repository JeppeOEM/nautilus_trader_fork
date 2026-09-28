# Research

The one page for the research context (`platform/research/`): the notebook index, the recipes for
a new notebook, metric or candlestick pattern, how to launch Jupyter locally, and the backtest and
strategy how-to (moved in from `BACKTESTING.md` in Story 27.9; that file is now a redirect stub).

Every notebook holds no analysis logic of its own: each number comes from `research.domain`'s
values or `research.application`'s ports and services, every market-data read is bounded by
`START`/`END` (MEM-01), and a gap or defect is shown, never filled. The binding rules are
`platform/CLAUDE.md`'s "Research notebooks" section, NB-01..NB-04.

## Notebook index

Each notebook is `notebooks/<nn>_<name>.py` plus its `.ipynb` twin. *Inputs* are the stored types
it reads (field level: `docs/DATA_DICTIONARY.md` §2.12 "Research reads"); *calls* are the analysis
functions every number comes from; *fixture run time* is its test run against the fixture archive
(the call phase; building the archive takes about 0.8 s more, once per session).

| # | Purpose | Inputs (stored types) | Calls | Fixture run time |
|---|---------|-----------------------|-------|------------------|
| `01_catalog_inspection` | What the archive holds per venue and instrument, and where it is wrong | instrument definitions; `DydxSecondSnapshot`; `TradeTick`; `MarkPriceUpdate`; the Parquet price-precision labels of the trade, mark and index files; the candle store's `verified_days`; the error ledger (`data/errors/*.jsonl`) | `research.application.inspection` (`gap_report`, `fold_agreement`, `snapshot_sanity`, `receive_lag_ms`); `CatalogFrames.seconds`; `kernel.catalog_files` (`data_file_ranges`, `price_precision_labels`); `kernel.indicators.MultiLevelOBI`; `kernel.fold.fold_trades`; `candles.application.queries.verified_status`; `observability.error_ledger` | 2.9 s |
| `02_microstructure` | Spread, depth, imbalance, OFI, microprice edge, trade flow, basis and volatility of one instrument | `DydxSecondSnapshot`; instrument definitions (`price_increment`); `MarkPriceUpdate`; `IndexPriceUpdate`; `FundingRateUpdate`; `OpenInterest` | `research.application.microstructure` (`spread_frame`, `microprice_edge`, `trade_flow`, `basis_frame`); `research.domain.microstructure` (`hit_rate_by_bin`, `price_impact`, `autocorrelation`, `volatility_signature`, `realised_volatility`); `research.domain.returns.ReturnSeries`; `kernel.indicators` (`MultiLevelOBI`, `MultiLevelOFI`, `RollingZScore`, `volume_delta`, `snapshot_depth`, `cumulative_depth`, `depth_within_bps`) | 13.2 s |
| `03_correlation` | Correlation and clusters across the universe; cross-venue basis, lead-lag, funding and volume share per asset | the candle store's bars (1 m, 5 m, 1 h, 1 d); `DydxSecondSnapshot` (level 0 and trade volume); `FundingRateUpdate`; `OpenInterest` | `research.domain.correlation` (`correlation_matrix`, `cluster`, `merge_order`, `rolling_correlation`, `correlation_of`, `basis_bps`, `lead_lag`, `peak_lag`); `research.domain.returns.ReturnSeries`; `research.application.aligned` (`funding_per_hour`, `oi_changes`, `venue_volume_share`); `kernel.venues.asset_key` | 1.8 s |
| `04_backtest_evaluation` | One strategy's backtest: equity, drawdowns, metrics, trade breakdowns, a sweep and a walk-forward | through `NodeRunner` (`data="seconds"`): instrument definitions and `DydxSecondSnapshot` (quotes derived from its top of book); `TradeTick` for `data="trades"`/`"bars:..."` runs | `research.application.backtest_runner.NodeRunner`; `research.application.evaluation` (`metric_grid`, `top_runs`); `research.application.walk_forward` (`walk_forward`, `concat_equity`); `research.domain` (`MetricReport` = `kernel.performance_metrics.all_metrics`, `EquityCurve`, `ReturnSeries.rolling_sharpe`, `TradeLedger`) | 2.9 s |
| `05_monte_carlo` | How robust one run is: bootstraps, risk of ruin, Sharpe interval, deflated Sharpe | as `04` | `research.domain.monte_carlo` (`bootstrap_trades`, `block_bootstrap_returns`, `risk_of_ruin`, `sharpe_confidence_interval`, `deflated_sharpe`); `research.application.robustness.deflated_check`; `NodeRunner`; `MetricReport` | 1.6 s |
| `06_candlestick_scanner` | The 22 candlestick patterns over the collected instruments, with forward returns and hit rates | the candle store's bars only (`t`, `o`, `h`, `l`, `c`, `seconds_observed`) | `kernel.candle_patterns.CandlePatternSet`; `research.application.patterns` (`scan_grids`, `ema_values` over Nautilus's `ExponentialMovingAverage`); `research.domain.events` (`forward_returns`, `hit_rate`) | 0.1 s |

Re-measure the run times (from `platform/`, on the host):

```bash
python3 -m pytest -o addopts="" --rootdir=. research/tests/test_notebooks.py \
    -k runs_against_the_fixture --durations=0 -q
```

### What each notebook shows

| Notebook | What it shows |
|----------|---------------|
| `notebooks/01_catalog_inspection` | What the archive holds per venue and instrument over a window: instrument inventory, snapshot-file coverage and gaps (likely outage vs book gap vs quiet market, or no mark coverage to tell), candle-store day status (verified / provisional / failed), raw-trades-vs-folded-seconds agreement per day, snapshot sanity (crossed, spread, `ts_init - ts_event`), per-file price-precision labels and the error ledger's rejections by site. Every number from `research.application.inspection`. |
| `notebooks/02_microstructure` | Per instrument over a window: spread in ticks and bps (series and by UTC hour), depth by level and by distance from mid (one snapshot a minute), order-book imbalance at 1/5/10/20 levels and the OFI strategy's OFI replayed with both z-scores against its threshold, the microprice edge on the next-second mid (binned scatter, hit rate per bin), trade flow and CVD with a Kyle-lambda impact fit per volume bucket, funding/basis/open interest on price, return autocorrelation at 1 s-1 h, the volatility signature and rolling realised volatility. Every number from `kernel.indicators`, `research.domain.microstructure` and `research.application.microstructure`. |
| `notebooks/03_correlation` | Across the collected universe over a window: return correlation at 1 m, 5 m, 1 h and 1 d (the candle store's bars, read span by span so an outage is a gap) as clustered diverging heatmaps plus per-venue matrices, rolling correlation against an anchor instrument, single-linkage clusters and the merge list with distances, funding-level (per hour, `rate * 60 / interval`) and OI-change correlation, and a cluster built into a `RunSpec` (printed, not run). Per asset across dYdX, Bybit and Hyperliquid: the mid basis in bps, the 1 s lead-lag with its peak in words ("BYBIT leads DYDX by 2 s"), the funding differential and each venue's volume share; a venue with no data prints "not collected in this window". Every number from `research.domain.correlation` and `research.application.aligned`; same-asset matching from `kernel.venues.asset_key`. |
| `notebooks/04_backtest_evaluation` | One strategy (by string path, default `OFIStrategy`) on one instrument over a window, through `BacktestRunner` (`NodeRunner`): the equity with its underwater series and every drawdown episode, the `MetricReport` table (exactly `kernel.performance_metrics.all_metrics`), a rolling Sharpe (`ReturnSeries.rolling_sharpe`, or a sentence when the curve is shorter than the window), the realized-PnL and holding-time distributions, PnL by UTC hour and weekday of exit, the trade list; a two-parameter sweep as a heatmap with the top runs, grid size and runtime; and a walk-forward: `N` consecutive in-sample/out-of-sample folds, each fold's pick on the in-sample metric, the joined out-of-sample equity (a stated convention, not one continuous run) against the single run and the out-of-sample metrics beside the in-sample ones. Every number from `research.application.evaluation`, `research.application.walk_forward` and `research.domain`; no cell sums or averages. The one way to evaluate a strategy interactively ("Backtesting & Strategy Development" below). |
| `notebooks/05_monte_carlo` | How robust one strategy run is (04's strategy and parameters, through `BacktestRunner`): the trade-order bootstrap (the closed trades resampled with replacement) as an equity fan with the observed path, terminal-wealth and path max-drawdown histograms with the observed values marked; the same for a stationary block bootstrap of the equity's returns (autocorrelation kept) and how the two differ; risk of ruin at three equity floors; the Sharpe ratio's bootstrap confidence interval; and, with `SWEEP`, the deflated Sharpe of the grid's best point with a verdict sentence. Every figure's title states its seed and path count; an empty ledger, an undefined Sharpe or `SWEEP=False` prints a sentence instead of a figure. Every number from `research.domain.monte_carlo` and `research.application.robustness`. |
| `notebooks/06_candlestick_scanner` | The 22 candlestick patterns of `kernel.candle_patterns` (the same detector the chart picker, the screener's Technicals columns and a strategy use; no TA-Lib) over `INSTRUMENTS` at every `TIMEFRAMES` size the candle store keeps (another size is skipped with a line, never resampled): each timeframe's bars on the complete bucket grid with its holes counted, one hits table tagged by instrument, timeframe, pattern and direction, narrowed by an EMA condition (`above`/`below`/`any` against Nautilus's `ExponentialMovingAverage`) and a pattern filter, a plotly candlestick chart centred on hit `HIT_INDEX` with the EMA and the hit marked, and the research output: forward returns at `HORIZONS` bars and the hit rate, mean and count per timeframe, pattern and direction. The pattern set and the EMA restart after every gap, and a forward return across a gap or past `END` is blank. Every number from `research.application.patterns` and `research.domain.events`; an empty filter prints a sentence instead of a chart. |

No legacy notebook remains: `candlestick_pattern_scanner.ipynb` was deleted in Story 27.7
(`backtest.ipynb` in Story 27.5).

## Recipes

### Write a notebook

1. **Pair it.** Create `notebooks/<nn>_<name>.py` in jupytext percent format (copy the closest
   numbered notebook: the raw LGPL-header cell, then a title markdown cell), then run
   `make notebooks` from `platform/` to write its output-free `.ipynb` twin. Add its row to the
   index above.
2. **Parameters cell first.** The first code cell reads `Params.from_env()` (`notebooks/_params.py`)
   and every other constant through `_params.setting(name, default)`; a constant the fixture cannot
   satisfy gets a shrunk value in `research/tests/test_notebooks.py`'s `NOTEBOOK_ENV`. A notebook
   never reads the environment itself.
3. **One section per question.** A markdown cell states the question and how to read the answer; the
   code cell under it only calls functions and shows what they return.
4. **Analysis logic goes in `research/domain`** (pure: stdlib, numpy, `kernel`, Nautilus value
   types) or in the notebook's `research/application` service (pandas frames), with unit tests in
   `research/tests/`. A formula in a cell fails `platform/tests/test_notebook_rules.py` (NB-01).
5. **Reads go through `MarketFrames`** (`research.application.frames.CatalogFrames`: `seconds`,
   `trades`, `bars`, `funding`, `open_interest`, `mark_index`, each bounded by `start=`/`end=`), a
   backtest through `BacktestRunner` (`NodeRunner`). `research/tests/test_research_reads.py` fails
   an unbounded catalog read (NB-04).
6. **Sync and test.** `make notebooks`, then `make test` (or, on the host, the `research/tests`
   and `tests` suites: `research/tests/test_notebooks.py` runs the notebook against the fixture
   archive under `warnings.simplefilter("error")`, NB-02). A notebook that needs a library outside
   `uv.lock` files a dependency decision first; it never installs one from a cell (NB-03).

### Add a metric

Add it where it belongs first, with its test, then call it from the notebook, never the reverse:

- a portfolio statistic (Sharpe, drawdown, win rate...) goes into `kernel/performance_metrics.py`
  (`all_metrics`), which `MetricReport` wraps without adding anything, so a backtest report and a
  notebook read the same number;
- any other analysis value goes into `research/domain` (a value object or a pure function, its
  invariant in the docstring, DESIGN-01), or into `kernel/indicators.py` when it is a per-snapshot
  microstructure value the views or a strategy also need (SSOT-01);
- a rolling ranking metric (pct-change, volatility) is `ranking`'s, read over HTTP
  (`research.application.ranking_history`), never recomputed here (SSOT-02).

### Add a pattern

A candlestick pattern is one member of `kernel.candle_patterns.PatternName`, one detector function
and one `_PATTERNS` entry (its bar count and whether it needs a prior trend) in
`kernel/candle_patterns.py`, with its definition in the module docstring's pattern list. Add a
firing case and a near-miss case to `kernel/tests/test_candle_patterns.py`'s hand-drawn table
(`test_every_pattern_has_a_firing_and_a_near_miss_case` fails without both, and a directional
pattern needs both directions). Nothing else changes: the chart picker's `CandlePattern` catalog
entry (`views.indicator_picker`) offers every `PatternName`, and the screener, the scanner notebook
and `CandlePatternStrategy` take the pattern by name.

## Running Jupyter locally

Jupyter is a personal tool: it runs locally, never as a compose service. JupyterLab and
`ipykernel` are not in `uv.lock` (NFR12), so launch the `jupyter lab` installed on your machine.
Its kernel must import `nautilus_trader`, numpy, pandas, pyarrow and plotly, as the `python3` that
runs the host test suites does. (`uv run jupyter lab` adds nothing, since the locked environment
holds no Jupyter. In a fresh git worktree it would first build the Rust extension from scratch,
as the root `CLAUDE.md` warns.)

```bash
cd platform && jupyter lab research/notebooks
```

The kernel starts in `research/notebooks/`; `_params.py` puts `platform/` on `sys.path`, so the
platform packages import as they do in the tests. Point a notebook at another archive or window
with the environment variables of "Parameters" below, e.g.
`START=2026-09-20 END=2026-09-21 INSTRUMENTS=BTCUSDT-LINEAR.BYBIT jupyter lab
research/notebooks`. After editing either file of a pair in Jupyter, run `make notebooks` before
committing.

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
A constant sized for the real archive that the fixture cannot satisfy (04's instrument, warm-up,
grid, fold count and selection metric; 05's path count, return period and block length; 06's timeframes, EMA length, horizons and hit window) is read through `_params.setting(name, default)` -- JSON in
`NOTEBOOK_<NAME>`, else the default, a type mismatch raising -- and `test_notebooks.py`'s
`NOTEBOOK_ENV` shrinks it per notebook (Story 27.5); a notebook never reads the environment itself.
`platform/tests/test_notebook_rules.py` (Story 27.9) adds the static rules: no array math in a
numbered notebook's code cell (NB-01), no install line in a cell and no legacy TA-library token
anywhere under `platform/` outside `docs/` and `.planning/` (NB-03), and no `.ipynb` outside
`research/notebooks/`.

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

## Backtesting & Strategy Development

How to build a strategy and run it against the dYdX catalog in `platform/research/` (the research
context since Story 24.4: strategies, runners, the watchlist client and the notebooks).

Governing rules: `platform/CLAUDE.md` NAUT-03 (BacktestNode + BacktestDataConfig only, no
custom engine) and DESIGN-02 (strategies never import collector internals — depend on
shared data types only).

---

### Run from a notebook

**`research/notebooks/04_backtest_evaluation` is the one way to evaluate a strategy
interactively** (Story 27.5). Set `STRATEGY`, `STRATEGY_CONFIG`, `PARAMS` (and `GRID`, `N_FOLDS`,
`SELECT_BY`) in its Parameters cell and it shows, for one instrument over `START`-`END`: the
equity with its underwater series and every drawdown episode, the `MetricReport` table (exactly
`kernel.performance_metrics.all_metrics`), a rolling Sharpe (`ReturnSeries.rolling_sharpe`), the
realized-PnL and holding-time distributions, PnL by UTC hour and weekday, the trade list, a
two-parameter sweep as a heatmap with its top runs, grid size and runtime, and a walk-forward
(`research.application.walk_forward`: `N` consecutive folds, each fold's point picked on the
in-sample metric and run out of sample, the joined out-of-sample equity and metrics beside the
in-sample ones). Every backtest in it goes through `BacktestRunner`; every table comes from
`research.application.evaluation`. Launch it locally: `cd platform && jupyter lab
research/notebooks` ("Running Jupyter locally" above).

The walk-forward's joined out-of-sample equity is a convention, not one continuous run: each fold
runs from the full starting balance and is shifted by the earlier folds' PnL
(`walk_forward.concat_equity`), and each fold's run spends its strategy's warm-up inside its own
out-of-sample window (the `Known limit:` in `walk_forward.py`).

**How robust is the result?** `research/notebooks/05_monte_carlo` (Story 27.6) runs the same
strategy and grid and resamples the run: a trade-order bootstrap and a stationary block bootstrap
of the equity's returns (fans, terminal-wealth and drawdown histograms), risk of ruin, a Sharpe
confidence interval, and the deflated Sharpe ratio of the sweep's best point, which says whether
that point is distinguishable from the luck of trying `len(grid)` points. The functions are
`research.domain.monte_carlo` (seeded; each result records its seed and path count).

`research.application.backtest_runner.NodeRunner` (the `BacktestRunner` port, Story 27.1) is the
one way a notebook runs a backtest: it wraps `BacktestNode` + `BacktestDataConfig` +
`ImportableStrategyConfig`, so a notebook never builds a node, a run config or a report by hand.

```python
from research.application.backtest_runner import NodeRunner
from research.application.ports import RunSpec

spec = RunSpec(
    catalog_path="platform/data/catalog",
    instrument_ids=("BTC-USD-PERP.DYDX",),        # one venue per run (Known limit)
    start="2026-09-05",                            # required: every read is bounded (MEM-01)
    end="2026-09-07",
    strategy_path="research.strategies.ofi_strategy:OFIStrategy",
    config_path="research.strategies.ofi_strategy:OFIStrategyConfig",
    params={"ofi_threshold": 2.0, "warmup_seconds": 600, "trade_size": "0.01"},
    starting_balance=10_000,
    data="seconds",   # DydxSecondSnapshot + quotes derived from its top of book
                      # "trades" = TradeTick; "bars:1-MINUTE" = TradeTick aggregated by Nautilus
                      # into <iid>-1-MINUTE-LAST-INTERNAL, injected as the strategy's `bar_type`
)
runner = NodeRunner()
result = runner.run(spec)
result.metrics.as_table()      # MetricReport: kernel.performance_metrics.all_metrics, typed
result.equity.drawdowns()      # EquityCurve: underwater series + drawdown episodes
result.trades.by_hour_of_day() # TradeLedger: closed round trips (net realized PnL)

# A sweep: one BacktestNode, one BacktestRunConfig per grid point (identical points raise), each
# point's params merged over spec.params; results come back in grid order, each attributed by its
# BacktestRunConfig.id.
results = runner.sweep(spec, [{"ofi_threshold": t} for t in (1.0, 1.5, 2.0)])
{r.params["ofi_threshold"]: r.metrics.sharpe_ratio for r in results}
```

What `RunResult` carries: `config_id`, the merged `params`, `equity` (the account report's
balance `total` after each of the account's own events, in event order, including a position
still open at the end -- so it can differ from `metrics`, which cover closed trades only), `trades` (every closed position of the positions report, NETTING
snapshots included; a position still open at the end is not a trade), `metrics`, `pnl_by_day`,
Nautilus's own `nautilus_stats` (`stats_pnls`/`stats_returns`, for cross-checking),
`iterations` and `wall_seconds`. The runner reads each engine's reports before disposing the node
(`dispose_on_completion=False`), which is why they are not empty the way `backtest_dydx.py`'s
docstring found them after `run()`. A config Nautilus returns no result for raises -- never a
silent gap. The runner's `Known limit:`s (one venue and one settlement currency per run; a sweep
holds every grid point's engine until its reports are read; one-shot data loading per grid point
because the pinned Nautilus cannot stream a custom data type) are in
`research/application/backtest_runner.py` and `research/application/ports.py`.

Market data for the same window comes from `research.application.frames.CatalogFrames`
(`frames.seconds(iid, start=..., end=...)`, `trades`, `bars(iid, 60, ...)` from the candle store,
`funding`, `open_interest`, `mark_index`), and ranking history from
`research.application.ranking_history.HttpRankingHistory` (data_api over HTTP). Returns,
correlation and clustering live in `research.domain` (`ReturnSeries`, `align`,
`correlation_matrix`, `lead_lag`, `cluster`): a notebook cell calls them, it never re-derives a
statistic.

---

### Run an existing backtest

```bash
# from the repo root (default catalog path is platform/data/catalog)
PYTHONPATH=platform python -m research.strategies.backtest_dydx       # LogisticTrendStrategy on internally-aggregated Bars
PYTHONPATH=platform python -m research.strategies.backtest_snapshot   # SnapshotStrategy on raw 1s DydxSecondSnapshot
PYTHONPATH=platform python -m research.strategies.backtest_ofi        # OFIStrategy on 1s DydxSecondSnapshot, quotes from level 0
PYTHONPATH=platform python -m research.strategies.backtest_candle_pattern   # CandlePatternStrategy on 1-minute bars from trade ticks
PYTHONPATH=platform python -m research.run_backtest --start 2026-09-05 --end 2026-09-06   # OFIStrategy, CLI window
```

To evaluate the same OFI backtest interactively, run `research/notebooks/04_backtest_evaluation`
(see "Run from a notebook"; the legacy `backtest.ipynb` was deleted in Story 27.5).

Each file's `run()` returns results programmatically (from a notebook/script) and its
`__main__` block prints a report when run directly. See each file's docstring for
tuning knobs — e.g. `backtest_dydx.run(symbols=["BTC-USD-PERP.DYDX"], bar_interval="5-MINUTE")`.

`research/strategies/backtest_dydx.py` defaults to backtesting every coin in the live Watchlist (needs
`data_api` running); pass `symbols=[...]` to skip that dependency.

---

### Which existing backtest to copy

| Data granularity | Feed | Copy |
|---|---|---|
| Bars (aggregated from trades) | `TradeTick` → internal `Bar` | `research/strategies/backtest_dydx.py` |
| Raw 1s book snapshots | `DydxSecondSnapshot` | `research/strategies/backtest_snapshot.py` |
| Raw 1s book snapshots, fills at the snapshot's best bid/ask | `DydxSecondSnapshot` + `QuoteTick` derived by `kernel.catalog_files.query_top_of_book` | `research/strategies/backtest_ofi.py` (via `research/strategies/snapshot_backtest.py`) |
| Bars from trades, pattern entries | `TradeTick` → internal `Bar`, through `NodeRunner` (`data="trades"`) | `research/strategies/backtest_candle_pattern.py` (`CandlePatternStrategy`, Story 27.8; also the second worked example in `04_backtest_evaluation`, and a paper bot with `strategy = "candle_pattern"`, `bots/README.md`) |

Don't write a new backtest runner from scratch — copy the closest match above and swap
the `strategy_path`/`config_path`/`data=[...]` list.

---

### Build a strategy

A strategy is a `StrategyConfig` + `Strategy` pair, referenced by string path
(`ImportableStrategyConfig`) — never imported directly into the backtest runner. This is
what lets `run()` sweep parameters and swap strategies with no code change.

```python
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.trading.strategy import Strategy


class MyStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    trade_size: Decimal
    threshold: float = 1.0


class MyStrategy(Strategy):
    def __init__(self, config: MyStrategyConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument not found: {self.config.instrument_id}")
            self.stop()
            return
        self.subscribe_bars(
            ...
        )  # or subscribe_trade_ticks / subscribe_order_book_deltas / subscribe_data

    def on_bar(self, bar: Bar) -> None:
        if (
            self.portfolio.is_flat(self.config.instrument_id)
            and bar.close.as_double() > self.config.threshold
        ):
            self._submit(OrderSide.BUY)

    def _submit(self, side: OrderSide) -> None:
        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)
```

**Conventions used by every strategy here** (`research/strategies/example_strategy.py`,
`research/strategies/ofi_strategy.py`, `research/strategies/snapshot_strategy.py`):
- `frozen=True` on the config class.
- `on_start`: look up `self.instrument` via `self.cache.instrument(...)`, `self.stop()` and
  log an error if missing — never assume it's there.
- Subscribe to exactly the feeds you need: `subscribe_bars`, `subscribe_trade_ticks`,
  `subscribe_order_book_deltas`, or `subscribe_data(DataType(CustomType), instrument_id=...)`
  for custom types like `DydxSecondSnapshot`.
- Position checks via `self.portfolio.is_flat/is_net_long/is_net_short(instrument_id)`,
  never hand-rolled position tracking.
- Orders via `self.order_factory.market(...)` + `self.submit_order(...)`.
- Signals for dashboard/UI consumption: `self.publish_signal(name, value, ts_event)`.

**Which feed to subscribe to** — pick based on the signal, not habit:
- Need an indicator over price bars → `subscribe_bars(bar_type)`, feed via
  `self.register_indicator_for_bars(bar_type, indicator)` or manually in `on_bar`.
- Need trade flow (buy/sell volume, cumulative delta) → `subscribe_trade_ticks`, read
  `tick.aggressor_side` in `on_trade_tick`.
- Need book-level microstructure (OFI, depth, imbalance) → `subscribe_order_book_deltas`,
  maintain your own `OrderBook(instrument_id, BookType.L2_MBP)` and `apply_delta` in
  `on_order_book_deltas`. Raw deltas exist only for the dYdX instruments opted in via
  `store_order_book_deltas`; no strategy here uses them (`ofi_strategy.py` moved to the 1s
  snapshot below), so prefer the snapshot unless you need tick-by-tick resolution.
- Need the pre-computed 1s snapshot (top-20 levels + per-second buy/sell volume) instead of
  raw deltas → `subscribe_data(DataType(DydxSecondSnapshot), instrument_id=...)`, handle in
  `on_data` (see `research/strategies/snapshot_strategy.py` and `research/strategies/ofi_strategy.py`). Cheaper than rebuilding an `OrderBook` if you don't
  need tick-by-tick delta resolution.

**Reuse existing signal math** — don't reimplement OFI/OBI/microprice/spread. They're in
`kernel/indicators.py` (`OrderFlowImbalance`, `MultiLevelOFI`, `MultiLevelOBI`,
`Microprice`, `OnlineLogisticTrend`) per SIGNAL-01 in `platform/CLAUDE.md` — raw data is
stored, signals are computed on read. In-repo, research imports only `kernel`, `observability` and
the candles query services (`open_store`, `window`, `oldest_t`, `newest_t`, `bucket_starts`, `verified_status` and the `BAR_SECONDS` sizes, Stories 27.1/27.2) and the archive's pure gap heuristic (`find_gaps`, Story 27.2): never `views/`, `data_api/` or `ranking/`
(`platform/tests/test_boundaries.py`). Rolling metrics such as pct-change and volatility are
ranking's (`metrics.db`, the rankings API), never recomputed here.

**Read the catalog through a bounded path** — outside a `BacktestDataConfig`, read market-data
rows through `research.application.frames.CatalogFrames` or `kernel.catalog_files`
(`query_top_of_book`, `query_second_ohlc`, `query_index_prices`: column-projected and
time-bounded, MEM-01). A catalog `query`/`trade_ticks`/`bars` call must name both `start=` and
`end=`, and `read_parquet`/`read_table`/`ParquetFile` are never allowed
(`research/tests/test_research_reads.py`). `catalog.instruments(...)` (metadata) is fine.

---

### Wire it into a backtest

Copy the closest existing `backtest_*.py`, then in `_build_run_config`/`run()`:

1. Point `strategy_path`/`config_path` at your new files
   (`"research.strategies.my_strategy:MyStrategy"`).
2. Set `config={...}` to your `MyStrategyConfig` fields (as plain dict values, not the
   Pydantic model itself).
3. List every `BacktestDataConfig` your strategy subscribes to. Missing one means the
   subscription silently gets no data — no error.
   - Custom types (anything not a Nautilus built-in, e.g. `DydxSecondSnapshot`) need an
     explicit `client_id=str(venue)` and, if the strategy also needs `cache.instrument()` to
     resolve, a parallel `TradeTick` config purely for instrument auto-registration
     (see `research/strategies/backtest_snapshot.py`'s comment on this).
   - For a `Bar` feed, aggregate internally from `TradeTick` (`research/strategies/
     backtest_dydx.py`'s `-LAST-INTERNAL` pattern): the catalog holds no `Bar` at all since
     minute bars were retired on 2026-09-20 (D-35), so an `EXTERNAL` bar query streams
     nothing. The `TradeTick` archive is the Story 22.13 raw trade archive, kept for
     `--trade-retention-days` (`docs/DATA_DICTIONARY.md` §1.1), so bound the window to it.
4. Trust `BacktestResult.stats_pnls`/`stats_returns` for whether trades happened —
   `total_orders`/`total_positions` were found unreliable (sometimes 0 despite real fills)
   in this pinned nautilus_trader version.

---

### Test it

Per `platform/CLAUDE.md` TEST-01: strategies with real branching/arithmetic need a test. See
`research/tests/test_ofi_strategy.py` and `research/tests/test_snapshot_strategy.py` for the
pattern — feed the strategy real `Bar`/`TradeTick`/`DydxSecondSnapshot` objects directly (never mock
Nautilus internals, TEST-03), assert on `portfolio.is_flat`/order submissions.

To backtest-test end-to-end, mirror `research/tests/test_watchlist_multi_coin_backtest.py` or
`research/tests/test_snapshot_backtest_node.py` — always pass an explicit `symbols=[...]`/`symbol=`
rather than relying on the live Watchlist.
