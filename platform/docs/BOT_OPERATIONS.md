# Bot Operations: Starting/Stopping Bots and Writing Strategies

Two separate runtimes exist in `platform/`; since Story 27.8 a strategy written for the first
can run in the second:

| | Backtest / research | Live paper bot |
|---|---|---|
| Where | `research/strategies/*_strategy.py` + `research/strategies/backtest_*.py` | `bots/infrastructure/nautilus_host.py` (was `live_paper/`, Story 25.3), running `bots/strategies/dummy.py` or a research strategy |
| Runtime | `BacktestNode` (deterministic replay of the collector's Parquet catalog) | `TradingNode` (real venue WS data, sandbox execution) — the one place `platform/CLAUDE.md`'s TradingNode ban is lifted (AD-8) |
| Wiring | `ImportableStrategyConfig(strategy_path=..., config_path=...)` — string path, per-symbol config | Per bot, the `strategy` key in `bots/config.toml` → `nautilus_host.STRATEGIES`: `dummy` is `DummyStrategy`, built directly (mirroring `examples/sandbox/dydx_sandbox.py`); `candle_pattern` is `research.strategies.candle_pattern_strategy:CandlePatternStrategy`, built by the same string path via `StrategyFactory.create(ImportableStrategyConfig(...))`, its `[bots.params]` as the config |
| Start/stop | One-shot Python process call, exits when done | Long-running Docker container, controlled via Redis pub/sub or `bot_tui` |

If you just want to try an idea against history, use the backtest path — it's a function
call, not a deployment. Only touch `bots/` once a strategy already has a backtest
worth putting in front of real market data.

---

## 1. Starting/stopping the live paper bot

Full walkthrough (prerequisites, `config.toml`, troubleshooting table) lives in
[`bots/README.md`](../bots/README.md) — this is the quick-reference summary.

**Start:**
```bash
cd platform
make up-live-paper        # builds if needed, runs in background under the live-paper profile
```

**Stop:**
```bash
docker compose -f platform/docker-compose.yml --profile live-paper down live-paper
```
This kills the container. Fill history (`data/live_paper/fills.db`) and Cache state
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

**Via `bot_tui`** (the interactive terminal control surface — status, start/stop, trades
blotter, PnL sparkline):
```bash
cd platform
make tui
```
`bot_tui` has two panes: Bots, which it opens on, and Collector (`:data`); `:bots` returns
to Bots and `:help` lists every key. Rankings and the ranking-mode switch are web-only
(the web UI's home page, `/`, Story 25.1a). Highlight a bot, press `s` to start/stop it.
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
docker compose -f platform/docker-compose.yml logs -f live-paper   # or make web -> Dozzle
docker exec dydx-redis redis-cli SUBSCRIBE bots:status          # heartbeat every 5s
```

---

## 2. Creating a backtest strategy (`research/`)

This is the research path — a `Strategy` subclass run by `BacktestNode` against the
collector's own Parquet catalog, referenced by string path so parameter sweeps and
symbol/date changes never require touching the strategy file.

**Minimal shape** (see `research/strategies/example_strategy.py` for the full working
version):

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

Reuse an existing indicator from `kernel/indicators.py` (SIGNAL-01: derive from
stored snapshot fields, don't reinvent) rather than rolling your own math inline.

**Wire it into a backtest run** by pointing `ImportableStrategyConfig` at the new class
(`research/strategies/backtest_dydx.py:_build_run_config`, ~line 85):
```python
ImportableStrategyConfig(
    strategy_path="research.strategies.my_strategy:MyStrategy",
    config_path="research.strategies.my_strategy:MyStrategyConfig",
    config={
        "instrument_id": f"{symbol}.DYDX",
        "bar_type": f"{symbol}.DYDX-{bar_interval}-LAST-INTERNAL",
        "trade_size": "0.001",
    },
),
```

**Run it:**
```bash
cd platform
python3 -c "from research.strategies.backtest_dydx import run; print(run(symbols=['BTC-USD-PERP']))"
```
or directly: `python3 -m research.strategies.backtest_dydx` (runs against the full live Watchlist
by default — pass `symbols=[...]` explicitly for a quick single-coin check). `run()`
also takes `catalog_path`, `bar_interval` (a plain bar-spec string, e.g. `"5-MINUTE"` —
no code change needed), and per-strategy threshold kwargs; returns a
`dict[str, BacktestResult]` keyed by symbol. No persistent process, no start/stop —
each call is a complete, self-contained run.

Test any function with real arithmetic (OFI, imbalance, thresholds) per `platform/CLAUDE.md`
TEST-01 — use real `Price`/`Quantity`/`Bar` objects, never mocked Nautilus internals
(TEST-03).

---

## 3. Running a strategy as a live paper bot (`bots/`)

Each `[[bots]]` entry in `bots/config.toml` picks its strategy with the optional `strategy` key
and passes that strategy's parameters in an optional `[bots.params]` table (Story 27.8; full
rules in `bots/README.md`, "Choosing a bot's strategy"). Both default — `strategy = "dummy"`, no
params — so existing files parse unchanged. For example, the candlestick strategy a scanner hit
and a `04_backtest_evaluation` run pointed at:

```toml
[[bots]]
bot_id = "candle-01"
instrument_id = "BTC-USD-PERP.DYDX"
trade_size = "0.001"
strategy = "candle_pattern"

[bots.params]
long_patterns = ["HAMMER", "ENGULFING"]
trend_condition = "above"
exit_bars = 10
stop_atr_multiple = 2.0
```

The bot owns `instrument_id`, `trade_size` and its identity (`bot_id` is pinned as the strategy's
`order_id_tag`, AD-11); `[bots.params]` may not set those or any other `StrategyConfig` base
field, an unknown strategy or params key fails loudly (never a silent `dummy`), and the
`trend_*`/`ofi_confirm_threshold` keys belong to `dummy` alone.

To make another backtested strategy runnable as a paper bot:

1. Keep it a `StrategyConfig` + `Strategy` pair in `research/strategies/` that imports only
   `kernel` and `nautilus_trader` (never bots code), give its config
   `forbid_unknown_fields=True`, and have it keep a public `last_data_ns` set from a market-data
   callback (e.g. `on_quote_tick`): `bots.infrastructure.cache_reader` reads it for the
   feed-staleness incident log. Live, subscribe quote ticks too — they drive the Sandbox fill
   engine. `CandlePatternStrategy` is the reference.
2. Add one row to `STRATEGIES` in `bots/infrastructure/nautilus_host.py`
   (`"<name>": ("research.strategies.<module>:<Class>", "research.strategies.<module>:<Config>")`),
   the same path to `_STRING_PATH_IMPORTS` in `platform/tests/test_images.py` (its `ast` walk
   cannot follow a string; a test fails until it is there), and a read-only source mount
   `/app/strategy_source/<Class>.py` to the `bot_tui` service in `docker-compose.yml` for the
   `v` key. Never import the strategy from bots: `platform/tests/test_boundaries.py` fails a
   bots → research import.
3. Rebuild and restart — code is baked into the image, not bind-mounted:
   ```bash
   docker compose -f platform/docker-compose.yml --profile live-paper build live-paper
   make up-live-paper
   ```

A strategy that belongs to the bots context alone (like `DummyStrategy`, whose tunables are
`BotConfig` keys) lives in `bots/strategies/` and is built directly in `_strategy_for`; review
`bots/strategies/dummy.py`'s module docstring first: it documents which live data sources back
which indicator (no `DydxSecondSnapshot` exists live; OBI/OFI are sampled from a 1s clock timer,
not raw deltas, to match the backtest calibration cadence). Never add a `mode` key to the paper
config — `load_paper_config()` hard-errors on it by design (the paper/real-money split is
structural: `PaperFleet` and `ExecBot` are distinct types).

Real-money execution is a completely separate, explicitly-gated config file/loader
(`ExecConfig`/`ExecBot`/`load_real_money_config`) — never reachable from the default
`config.toml` path, and it always runs `DummyStrategy` (`ExecConfig` has no `strategy` key; a
`Known limit:` in `nautilus_host.py`). See `bots/README.md` and
`bots/infrastructure/config.py`'s module docstring before touching that path.

---

## 4. Spotting downtime/stale feeds without digging through logs

Two independent, additive signals — neither changes any existing ranking/sort/trading
behavior, both are read-only additions:

- **A single coin's feed going stale, on the ranking page.** `ranking_engine` already
  drops an instrument from the ranked list once its book has been silent for 30s+
  (OBS-01) — it now *also* publishes that instrument's id separately, as
  `stale_instrument_ids` on the same `rankings:live` message, instead of the coin just
  vanishing from the table with no trace. Shows in the web dashboard's rankings page,
  read verbatim from that field (the `bot_tui` Coins-pane banner that also read it was
  deleted with the pane in Story 25.1a: rankings are web-only).
- **A bot's own WS/data feed going stale, or the bot process restarting.** See
  section 1's "Incidents log (`i` key)" above — `bots/application/supervise.py` watches the
  running strategy's own last-quote timestamp and logs start/end spans plus restart
  markers to `bots:incidents:{bot_id}`, viewable from `bot_tui`'s Bot-detail (`i`).

Neither of these parses log files — both are computed from data these processes
already track, so nothing new needs to be logged more verbosely to get this visibility.
