# Backtesting & Strategy Development

How to build a strategy and run it against the dYdX catalog in `troll/ml_signals/`.

Governing rules: `troll/CLAUDE.md` NAUT-03 (BacktestNode + BacktestDataConfig only, no
custom engine) and DESIGN-02 (strategies never import collector internals — depend on
shared data types only).

---

## Run an existing backtest

```bash
cd troll/ml_signals
python backtest_dydx.py       # LogisticTrendStrategy on internally-aggregated Bars
python backtest_snapshot.py   # SnapshotStrategy on raw 1s DydxSecondSnapshot
python backtest_ofi.py        # OFIStrategy on OrderBookDelta + TradeTick + 1-min Bars
```

Each file's `run()` returns results programmatically (from a notebook/script) and its
`__main__` block prints a report when run directly. See each file's docstring for
tuning knobs — e.g. `backtest_dydx.run(symbols=["BTC-USD-PERP.DYDX"], bar_interval="5-MINUTE")`.

`backtest_dydx.py` defaults to backtesting every coin in the live Watchlist (needs
`make dashboard` running); pass `symbols=[...]` to skip that dependency.

---

## Which existing backtest to copy

| Data granularity | Feed | Copy |
|---|---|---|
| Bars (aggregated from trades) | `TradeTick` → internal `Bar` | `backtest_dydx.py` |
| Raw 1s book snapshots | `DydxSecondSnapshot` | `backtest_snapshot.py` |
| Raw order book deltas + trades + bars | `OrderBookDelta`, `TradeTick`, `Bar` | `backtest_ofi.py` |

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
        self.subscribe_bars(...)  # or subscribe_trade_ticks / subscribe_order_book_deltas / subscribe_data

    def on_bar(self, bar: Bar) -> None:
        if self.portfolio.is_flat(self.config.instrument_id) and bar.close.as_double() > self.config.threshold:
            self._submit(OrderSide.BUY)

    def _submit(self, side: OrderSide) -> None:
        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)
```

**Conventions used by every strategy here** (`example_strategy.py`, `ofi_strategy.py`,
`snapshot_strategy.py`):
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
  `on_order_book_deltas` (see `ofi_strategy.py`).
- Need the pre-computed 1s snapshot (top-20 levels + per-second buy/sell volume) instead of
  raw deltas → `subscribe_data(DataType(DydxSecondSnapshot), instrument_id=...)`, handle in
  `on_data` (see `snapshot_strategy.py`). Cheaper than rebuilding an `OrderBook` if you don't
  need tick-by-tick delta resolution.

**Reuse existing signal math** — don't reimplement OFI/OBI/microprice/spread. They're in
`ml_signals/indicators.py` (`OrderFlowImbalance`, `MultiLevelOFI`, `MultiLevelOBI`,
`Microprice`, `OnlineLogisticTrend`) and `ml_signals/book_features.py`
(`compute_features`, `CancellationTracker`) per SIGNAL-01 in `troll/CLAUDE.md` — raw data is
stored, signals are computed on read.

---

## Wire it into a backtest

Copy the closest existing `backtest_*.py`, then in `_build_run_config`/`run()`:

1. Point `strategy_path`/`config_path` at your new files (`"ml_signals.my_strategy:MyStrategy"`).
2. Set `config={...}` to your `MyStrategyConfig` fields (as plain dict values, not the
   Pydantic model itself).
3. List every `BacktestDataConfig` your strategy subscribes to. Missing one means the
   subscription silently gets no data — no error.
   - Custom types (anything not a Nautilus built-in, e.g. `DydxSecondSnapshot`) need an
     explicit `client_id=str(venue)` and, if the strategy also needs `cache.instrument()` to
     resolve, a parallel `TradeTick` config purely for instrument auto-registration
     (see `backtest_snapshot.py`'s comment on this).
   - For a `Bar` feed, match `bar_spec` to what's actually in the catalog — only
     `1-MINUTE` bars are currently collected (`troll/dydx_collector/config.toml`'s
     `bar_intervals`); anything else needs internal aggregation from `TradeTick`
     (`backtest_dydx.py`'s pattern) instead of an `EXTERNAL` bar query.
4. Trust `BacktestResult.stats_pnls`/`stats_returns` for whether trades happened —
   `total_orders`/`total_positions` were found unreliable (sometimes 0 despite real fills)
   in this pinned nautilus_trader version.

---

## Test it

Per `troll/CLAUDE.md` TEST-01: strategies with real branching/arithmetic need a test. See
`tests/test_ofi_strategy.py` and `tests/test_snapshot_strategy.py` for the pattern — feed
the strategy real `Bar`/`OrderBookDelta`/`DydxSecondSnapshot` objects directly (never mock
Nautilus internals, TEST-03), assert on `portfolio.is_flat`/order submissions.

To backtest-test end-to-end, mirror `tests/test_watchlist_multi_coin_backtest.py` or
`tests/test_snapshot_backtest_node.py` — always pass an explicit `symbols=[...]`/`symbol=`
rather than relying on the live Watchlist.
