# Bot Operations: Starting/Stopping Bots and Writing Strategies

Two separate strategy paths exist in `troll/`, and they are not interchangeable:

| | Backtest / research | Live paper bot |
|---|---|---|
| Where | `ml_signals/*_strategy.py` + `ml_signals/backtest_dydx.py` | `live_paper/strategy.py` + `live_paper/node.py` |
| Runtime | `BacktestNode` (deterministic replay of the collector's Parquet catalog) | `TradingNode` (real dYdX WS data, sandbox execution) — the one place `troll/CLAUDE.md`'s TradingNode ban is lifted (AD-8) |
| Wiring | `ImportableStrategyConfig(strategy_path=..., config_path=...)` — string path, per-symbol config | One strategy class hardcoded via `node.trader.add_strategy(...)` in `node.py`, mirroring `examples/sandbox/dydx_sandbox.py` |
| Start/stop | One-shot Python process call, exits when done | Long-running Docker container, controlled via Redis pub/sub or `bot_tui` |

If you just want to try an idea against history, use the backtest path — it's a function
call, not a deployment. Only touch `live_paper/` once a strategy already has a backtest
worth putting in front of real market data.

---

## 1. Starting/stopping the live paper bot

Full walkthrough (prerequisites, `config.toml`, troubleshooting table) lives in
[`live_paper/README.md`](../live_paper/README.md) — this is the quick-reference summary.

**Start:**
```bash
cd troll
make up-live-paper        # builds if needed, runs in background under the live-paper profile
```

**Stop:**
```bash
docker compose -f troll/docker-compose.yml --profile live-paper down live-paper
```
This kills the container. Fill history (`live_paper/data/fills.db`) and Cache state
(Redis) both persist across restarts.

**Pause/resume without killing the container** — the strategy stops trading but the
process, WS connection, and Redis heartbeat stay up:
```bash
docker exec dydx-redis redis-cli PUBLISH bots:control '{"bot_id":"bot-01","action":"stop"}'
docker exec dydx-redis redis-cli PUBLISH bots:control '{"bot_id":"bot-01","action":"start"}'
```
Stopping does **not** flatten an open position — no auto-flatten logic exists. A message
with the wrong `bot_id` is silently ignored, so this is safe to run against a shared
Redis instance with multiple bots.

**Via `bot_tui`** (the interactive terminal dashboard — status, start/stop, trades
blotter, PnL sparkline):
```bash
cd troll
make tui
```
Navigate to the Bots pane (`:bots`), highlight a bot, press `s` to start/stop it.
Stopping a *running* bot opens a type-to-confirm prompt (type `stop` + Enter); starting
has no such guard. `bot_tui` needs `redis` up (`make up` or `make up-live-paper` bring it
up) but not `live-paper` itself — an offline bot just shows as stale.

Open a bot's detail view (`Enter` on its row) and press `v` to read its strategy source
inline, read-only and scrollable (`esc` back). It's bind-mounted read-only into the
`bot_tui` container (`docker-compose.yml`) — this only displays the text; it can't spawn
an editor or a new terminal window from inside a headless container.

Press `i` in that same detail view to see the bot's **incidents log**: restarts and
WS/data-feed interruptions, without digging through logs. `bot_status.py`'s heartbeat
loop treats 30s+ of silence on the bot's own quote feed as a problem (OBS-01's own
"a liquid instrument going quiet is a pipeline failure" doctrine, applied here since no
typed WS-reconnect event exists to hook) and logs a start immediately, filling in the
end once it recovers — a still-open one reads `ongoing (Nm..)`. A `restarted` marker is
also logged once per container start. Persisted in Redis (`bots:incidents:{bot_id}`,
last 50 kept), so it survives a `bot_tui` restart.

**Watch it:**
```bash
docker compose -f troll/docker-compose.yml logs -f live-paper   # or make web -> Dozzle
docker exec dydx-redis redis-cli SUBSCRIBE bots:status          # heartbeat every 5s
```

---

## 2. Creating a backtest strategy (`ml_signals/`)

This is the research path — a `Strategy` subclass run by `BacktestNode` against the
collector's own Parquet catalog, referenced by string path so parameter sweeps and
symbol/date changes never require touching the strategy file.

**Minimal shape** (see `ml_signals/example_strategy.py` for the full working version):

```python
from decimal import Decimal
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.trading.strategy import Strategy

class MyStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal

class MyStrategy(Strategy):
    def __init__(self, config: MyStrategyConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        ...  # your signal + self.submit_order(...) logic
```

Reuse an existing indicator from `ml_signals/indicators.py` (SIGNAL-01: derive from
stored snapshot fields, don't reinvent) rather than rolling your own math inline.

**Wire it into a backtest run** by pointing `ImportableStrategyConfig` at the new class
(`ml_signals/backtest_dydx.py:_build_run_config`, ~line 99):
```python
ImportableStrategyConfig(
    strategy_path="ml_signals.my_strategy:MyStrategy",
    config_path="ml_signals.my_strategy:MyStrategyConfig",
    config={
        "instrument_id": f"{symbol}.DYDX",
        "bar_type": f"{symbol}.DYDX-{bar_interval}-LAST-INTERNAL",
        "trade_size": "0.001",
    },
),
```

**Run it:**
```bash
cd troll
python3 -c "from ml_signals.backtest_dydx import run; print(run(symbols=['BTC-USD-PERP']))"
```
or directly: `python3 -m ml_signals.backtest_dydx` (runs against the full live Watchlist
by default — pass `symbols=[...]` explicitly for a quick single-coin check). `run()`
also takes `catalog_path`, `bar_interval` (a plain bar-spec string, e.g. `"5-MINUTE"` —
no code change needed), and per-strategy threshold kwargs; returns a
`dict[str, BacktestResult]` keyed by symbol. No persistent process, no start/stop —
each call is a complete, self-contained run.

Test any function with real arithmetic (OFI, imbalance, thresholds) per `troll/CLAUDE.md`
TEST-01 — use real `Price`/`Quantity`/`Bar` objects, never mocked Nautilus internals
(TEST-03).

---

## 3. Creating a live paper bot strategy (`live_paper/`)

`live_paper` runs exactly **one** strategy, attached directly in code (not by string
path — that's a deliberate difference from the backtest path, see `node.py`'s module
docstring). To trade a different strategy live:

1. Write a new `Strategy` + `StrategyConfig` pair in `live_paper/` (e.g.
   `live_paper/my_strategy.py`), following `live_paper/strategy.py`'s `DummyStrategy` as
   the reference — same subscribe/`on_start`/`on_bar` shape as a backtest strategy, but
   review that file's module docstring first: it documents which live data sources back
   which indicator (no `DydxSecondSnapshot` exists live; OBI/OFI are sampled from a 1s
   clock timer, not raw deltas, to match the backtest calibration cadence).
2. Swap the import and instantiation in `live_paper/node.py`'s `build_node()`:
   ```python
   from live_paper.my_strategy import MyStrategy, MyStrategyConfig
   ...
   strategy = MyStrategy(config=MyStrategyConfig(...))
   ```
3. Add any new tunable fields to `PaperConfig`/`RealMoneyConfig` in `live_paper/config.py`
   (both loaders) and to `config.toml`, mirroring the existing `trend_buy_threshold`-style
   fields. Never add a `mode` key to the paper config — `load_paper_config()` hard-errors
   on it by design (the paper/real-money split is structural, not a flag).
4. Rebuild and restart — code is baked into the image, not bind-mounted:
   ```bash
   docker compose -f troll/docker-compose.yml --profile live-paper build live-paper
   make up-live-paper
   ```

Real-money execution is a completely separate, explicitly-gated config file/loader
(`RealMoneyConfig`/`load_real_money_config`) — never reachable from the default
`config.toml` path. See `live_paper/README.md` and `config.py`'s module docstring before
touching that path.

---

## 4. Spotting downtime/stale feeds without digging through logs

Two independent, additive signals — neither changes any existing ranking/sort/trading
behavior, both are read-only additions:

- **A single coin's feed going stale, on the ranking page.** `ranking_engine` already
  drops an instrument from the ranked list once its book has been silent for 30s+
  (OBS-01) — it now *also* publishes that instrument's id separately, as
  `stale_instrument_ids` on the same `rankings:live` message, instead of the coin just
  vanishing from the table with no trace. Shows as a `~ stale feed: SOL-USD-PERP.DYDX`
  banner in `bot_tui`'s Coins-pane breadcrumb and in the web dashboard's status line,
  both reading the same field (SSOT-04).
- **A bot's own WS/data feed going stale, or the bot process restarting.** See
  section 1's "Incidents log (`i` key)" above — `live_paper/bot_status.py` watches the
  running strategy's own last-quote timestamp and logs start/end spans plus restart
  markers to `bots:incidents:{bot_id}`, viewable from `bot_tui`'s Bot-detail (`i`).

Neither of these parses log files — both are computed from data these processes
already track, so nothing new needs to be logged more verbosely to get this visibility.
