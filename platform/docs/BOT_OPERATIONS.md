# Bot Operations: Starting/Stopping Bots and Writing Strategies

Two separate strategy paths exist in `platform/`, and they are not interchangeable:

| | Backtest / research | Live paper bot |
|---|---|---|
| Where | `research/strategies/*_strategy.py` + `research/strategies/backtest_dydx.py` | `bots/strategies/dummy.py` + `bots/infrastructure/nautilus_host.py` (was `live_paper/`, Story 25.3) |
| Runtime | `BacktestNode` (deterministic replay of the collector's Parquet catalog) | `TradingNode` (real dYdX WS data, sandbox execution) — the one place `platform/CLAUDE.md`'s TradingNode ban is lifted (AD-8) |
| Wiring | `ImportableStrategyConfig(strategy_path=..., config_path=...)` — string path, per-symbol config | One strategy class hardcoded via `node.trader.add_strategy(...)` in `nautilus_host.py`'s `build_node()`, mirroring `examples/sandbox/dydx_sandbox.py` |
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

The Collector pane has one section per venue that publishes `collector:status` (dYdX, Bybit,
Hyperliquid; Story 29.2), sorted by venue. Each section is headed
`<VENUE>: N collected +P pending · cap C` (P = planned but not yet subscribed; Bybit and
Hyperliquid, which have no coin cap, read `· no cap`), then shows the plan's last apply (what the
collector last subscribed, unsubscribed or failed, with its time), the rows (`pending` marks a
planned id not yet subscribed; a liquidity label only on a plan that classifies liquidity, i.e.
dYdX) and the venue's `unpinned` line. `~` marks a stale row or a stale section. Every venue's
plan accepts commands (Bybit and Hyperliquid since Story 29.4): `p` (unpin: stop and exclude),
`x` (stop) and `:start <ID>` work on any venue's row or id, each sent on `collector:control`
addressed to the id's venue, so only that venue's collector acts. `:pintop` is dYdX's only (it
fills dYdX's cap by liquidity; Bybit's and Hyperliquid's collectors refuse a pin). A command is
refused before sending, with the reason, when the venue has published no plan since the TUI
started (`waiting for <VENUE> plan on collector:status`, dYdX included) or its status is stale
(`<VENUE>: no collector:status for over 60 min (collector down?)`); a collector stopped less
than an hour ago still receives commands, since plans republish only every 30 min. For the same
reason a freshly started TUI can wait up to 30 min for a venue's first plan and refuses its
commands until then (a restart of that collector publishes at once). Hyperliquid
counts its WebSocket channels against the venue's 1000 per IP: a `:start` past that budget stays
`pending`, with a `collector.subscribe_failed` ledger entry per retry naming the limit (this
collector's own channels only; `live-paper` on the same IP shrinks the real budget). On Bybit and Hyperliquid a command
rewrites the committed `platform/capture/venues/<venue>/config.toml` (its comments are lost; the
VPS checkout then shows it modified, see `docs/DEPLOY_CHECKLIST.md`), and an in-place hand edit
of that file is picked up within 30 s without a restart (a replaced file -- `git checkout`,
`sed -i` -- needs `docker compose restart` of the collector). An `unpin` adds the optional `exclude` list to
that file; a `:start` of the id removes it again. A venue's `last refusal` line (under its last
apply) is the latest command its collector refused and why (Story 29.5).

**Market browser** (Story 29.5): `/` on the Collector pane opens a searchable list of every market
each venue offers, from `markets:live` (the ranking engine publishes each venue's names once a
minute; names only, no volume or price). Before the first message it reads
`waiting for markets:live…` (up to a minute after the TUI starts). Type part of a symbol or id
(case-insensitive, trimmed; results refilter on every key, an empty search lists everything);
`Enter` moves to the results, `Esc` closes the search keeping the query (the breadcrumb shows it),
`/` reopens it, and `Esc` on the results goes back to the Collector pane. Results are grouped per
venue under that venue's Collector header plus `· M matches`; a venue whose list is over 3 min old
shows `~ `, and one silent for 15 min leaves the browser. `a` adds the highlighted market after you
type `add` + Enter: it sends `start` with the venue on `collector:control`, exactly like
`:start <ID>`. It is refused before anything is sent, with the reason in the footer, when the
venue's plan takes no commands (the same reasons as above), the id is `already collected` or
`already in the plan (pending)`, it is `excluded (unpinned): re-add with :start <ID>`, an add from
this TUI is still awaiting its answer, or dYdX's `cap reached (30)`; Bybit and Hyperliquid have no
cap. Each row carries one marker: `collected`; `pending` (in the plan, not yet subscribed -- with
`· failed: subscribe failed in last apply <UTC>, retrying` when the last apply failed it);
`failed: <reason>` (the collector refused your add, e.g. the cap filled or another TUI added it
first); `excluded`; `pending` (your add, waiting for `collector:status`); or
`no answer from <VENUE> collector` when nothing answered within 2 min (the collector may be down;
`a` may be pressed again). An add whose publish never reached Redis says so in the footer
(`failed to send start <ID>: Redis publish failed`) and marks nothing, so `a` may be pressed
again. Only `collector:status` ever makes a row `collected`.

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

## 3. Creating a live paper bot strategy (`bots/`)

The bots run exactly **one** strategy class, attached directly in code (not by string
path — that's a deliberate difference from the backtest path, see
`bots/infrastructure/nautilus_host.py`'s module docstring). To trade a different strategy live:

1. Write a new `Strategy` + `StrategyConfig` pair in `bots/strategies/` (e.g.
   `bots/strategies/my_strategy.py`), following `bots/strategies/dummy.py`'s `DummyStrategy`
   as the reference — same subscribe/`on_start`/`on_bar` shape as a backtest strategy, but
   review that file's module docstring first: it documents which live data sources back
   which indicator (no `DydxSecondSnapshot` exists live; OBI/OFI are sampled from a 1s
   clock timer, not raw deltas, to match the backtest calibration cadence). It must keep a
   public `last_data_ns` (the last quote's `ts_event`): `bots.infrastructure.cache_reader`
   reads it for the feed-staleness incident log.
2. Swap the import and instantiation in `bots/infrastructure/nautilus_host.py`'s
   `build_node()`:
   ```python
   from bots.strategies.my_strategy import MyStrategy, MyStrategyConfig
   ...
   strategy = MyStrategy(config=MyStrategyConfig(...))
   ```
3. Add any new tunable fields to `BotConfig`/`ExecConfig` in `bots/domain/config.py`, to both
   loaders in `bots/infrastructure/config.py`, and to `config.toml`, mirroring the existing
   `trend_buy_threshold`-style fields. Never add a `mode` key to the paper config —
   `load_paper_config()` hard-errors on it by design (the paper/real-money split is structural:
   `PaperFleet` and `ExecBot` are distinct types).
4. Rebuild and restart — code is baked into the image, not bind-mounted:
   ```bash
   docker compose -f platform/docker-compose.yml --profile live-paper build live-paper
   make up-live-paper
   ```

Real-money execution is a completely separate, explicitly-gated config file/loader
(`ExecConfig`/`ExecBot`/`load_real_money_config`) — never reachable from the default
`config.toml` path. See `bots/README.md` and `bots/infrastructure/config.py`'s module docstring
before touching that path.

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
