# Backtesting & Strategy Development

How to build a strategy and run it against the dYdX catalog in `platform/research/` (the research
context since Story 24.4: strategies, runners, the watchlist client and the notebooks).

Governing rules: `platform/CLAUDE.md` NAUT-03 (BacktestNode + BacktestDataConfig only, no
custom engine) and DESIGN-02 (strategies never import collector internals — depend on
shared data types only).

---

## Run from a notebook

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

## Run an existing backtest

```bash
# from the repo root (default catalog path is platform/data/catalog)
PYTHONPATH=platform python -m research.strategies.backtest_dydx       # LogisticTrendStrategy on internally-aggregated Bars
PYTHONPATH=platform python -m research.strategies.backtest_snapshot   # SnapshotStrategy on raw 1s DydxSecondSnapshot
PYTHONPATH=platform python -m research.strategies.backtest_ofi        # OFIStrategy on 1s DydxSecondSnapshot, quotes from level 0
PYTHONPATH=platform python -m research.run_backtest --start 2026-09-05 --end 2026-09-06   # OFIStrategy, CLI window
```

`research/notebooks/backtest.ipynb` runs the same OFI backtest interactively.

Each file's `run()` returns results programmatically (from a notebook/script) and its
`__main__` block prints a report when run directly. See each file's docstring for
tuning knobs — e.g. `backtest_dydx.run(symbols=["BTC-USD-PERP.DYDX"], bar_interval="5-MINUTE")`.

`research/strategies/backtest_dydx.py` defaults to backtesting every coin in the live Watchlist (needs
`data_api` running); pass `symbols=[...]` to skip that dependency.

---

## Which existing backtest to copy

| Data granularity | Feed | Copy |
|---|---|---|
| Bars (aggregated from trades) | `TradeTick` → internal `Bar` | `research/strategies/backtest_dydx.py` |
| Raw 1s book snapshots | `DydxSecondSnapshot` | `research/strategies/backtest_snapshot.py` |
| Raw 1s book snapshots, fills at the snapshot's best bid/ask | `DydxSecondSnapshot` + `QuoteTick` derived by `kernel.catalog_files.query_top_of_book` | `research/strategies/backtest_ofi.py` (via `research/strategies/snapshot_backtest.py`) |

Don't write a new backtest runner from scratch — copy the closest match above and swap
the `strategy_path`/`config_path`/`data=[...]` list.

---

## Build a strategy

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
the candles query services (`open_store`, `window`, `oldest_t`, `newest_t`, `bucket_starts` and the `BAR_SECONDS` sizes, Story 27.1): never `views/`, `data_api/` or `ranking/`
(`platform/tests/test_boundaries.py`). Rolling metrics such as pct-change and volatility are
ranking's (`metrics.db`, the rankings API), never recomputed here.

**Read the catalog through a bounded path** — outside a `BacktestDataConfig`, read market-data
rows through `research.application.frames.CatalogFrames` or `kernel.catalog_files`
(`query_top_of_book`, `query_second_ohlc`, `query_index_prices`: column-projected and
time-bounded, MEM-01). A catalog `query`/`trade_ticks`/`bars` call must name both `start=` and
`end=`, and `read_parquet`/`read_table`/`ParquetFile` are never allowed
(`research/tests/test_research_reads.py`). `catalog.instruments(...)` (metadata) is fine.

---

## Wire it into a backtest

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

## Test it

Per `platform/CLAUDE.md` TEST-01: strategies with real branching/arithmetic need a test. See
`research/tests/test_ofi_strategy.py` and `research/tests/test_snapshot_strategy.py` for the
pattern — feed the strategy real `Bar`/`TradeTick`/`DydxSecondSnapshot` objects directly (never mock
Nautilus internals, TEST-03), assert on `portfolio.is_flat`/order submissions.

To backtest-test end-to-end, mirror `research/tests/test_watchlist_multi_coin_backtest.py` or
`research/tests/test_snapshot_backtest_node.py` — always pass an explicit `symbols=[...]`/`symbol=`
rather than relying on the live Watchlist.
