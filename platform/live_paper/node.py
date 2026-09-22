# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
TradingNode entrypoint: live dYdX market data + paper (sandbox) execution by default.

The first sanctioned TradingNode/Strategy usage in platform/ -- see ARCHITECTURE-SPINE.md's
AD-8 amendment. dydx_collector/ml_signals never instantiate TradingNode (platform/CLAUDE.md's
FORK-02); that rule's own header scopes it to those two modules only, and does not bind
this new, structurally separate module.

Paper mode (the only reachable default -- see config.py) wires each venue's live public
market data client (dYdX/Bybit/Hyperliquid, see venues.py) into a
SandboxExecutionClientConfig/SandboxLiveExecClientFactory simulated exchange, one per venue: no wallet address or private key anywhere in this path, so real funds are
structurally unreachable, not merely gated by a disabled flag. The explicit path
(`ExecConfig`, mode `real_money` or `exchange_demo`) uses the venue's own exec client
from venues.py instead, which really signs and submits orders -- only reachable via the
separate, explicit file in config.py.

The Dummy Strategy (Story 3.2, `live_paper.strategy.DummyStrategy`) is attached directly
via `node.trader.add_strategy(...)`, mirroring `examples/sandbox/dydx_sandbox.py`'s exact
pattern -- not `ImportableStrategyConfig`/string-path referencing, which is a BacktestNode
parameter-sweep convenience (AD-6) that this repo's live reference examples don't use.

`build_node` also schedules `bot_status.run(...)` on the node's own event loop (Story
4.4, architecture AD-10) -- this is bot_tui/the web dashboard's only path to this
process's PnL/position state and start/stop control, via the bots:status/bots:control
Redis channels; see bot_status.py's own module docstring for why this lives outside the
Strategy's component lifecycle rather than inside one of its clock timers.
"""

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from kernel.venues import venue_of

from live_paper import bot_status
from live_paper import trade_history
from live_paper.config import BotConfig
from live_paper.config import ExecConfig
from live_paper.config import PaperConfig
from live_paper.config import resolve_config
from live_paper.strategy import DummyStrategy
from live_paper.strategy import DummyStrategyConfig
from live_paper.venues import VENUES
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.config import CacheConfig
from nautilus_trader.config import DatabaseConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money


logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).parent
_DEFAULT_PAPER_CONFIG_PATH = _MODULE_DIR / "config.toml"
_REAL_MONEY_ENV_VAR = "LIVE_PAPER_REAL_MONEY_CONFIG"
_REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
# Shared across every bot (Story 4.6) -- mirrors ranking_engine's METRICS_DB_PATH
# env-override convention. Directory (not the bare file) must be volume-mounted for
# fills to survive a container restart -- see docker-compose.yml's live-paper service.
_FILLS_DB_PATH = os.environ.get("FILLS_DB_PATH", str(_MODULE_DIR / "data" / "fills.db"))
_EXEC_MODE_LABELS = {"real_money": "live", "exchange_demo": "demo"}


def _paper_venue_clients(
    config: PaperConfig, bots: tuple[BotConfig, ...], instrument_provider: InstrumentProviderConfig
) -> tuple[dict, dict, dict]:
    """
    One data client + one Sandbox exec client per venue in use (AD-11: Nautilus allows only
    one exec client per venue, so bots on the same venue share that venue's balance pool).
    """
    data_clients, exec_clients, data_factories = {}, {}, {}
    for venue in sorted({venue_of(bot.instrument_id) for bot in bots}):
        spec = VENUES[venue]
        venue_config = config.venue_config(venue)
        data_clients[venue] = spec.make_data_config(
            environment=spec.parse_environment(venue_config.environment),
            instrument_provider=instrument_provider,
        )
        data_factories[venue] = spec.data_factory
        exec_clients[venue] = SandboxExecutionClientConfig(
            venue=venue,
            starting_balances=list(venue_config.starting_balances),
            account_type=venue_config.account_type,
            instrument_provider=instrument_provider,
        )
    return data_clients, exec_clients, data_factories


def build_node(config: PaperConfig | ExecConfig) -> TradingNode:
    instrument_provider = InstrumentProviderConfig(load_all=True)

    if isinstance(config, ExecConfig):
        # The explicit path stays one-bot-per-file/account (AD-11) -- ExecConfig itself
        # already carries that one bot's instrument/sizing/thresholds/bot_id. Which venue
        # and which clients both come off the VENUES table, so real_money and
        # exchange_demo differ only by the environment enum they resolve to.
        venue = venue_of(config.instrument_id)
        spec = VENUES[venue]
        environment = spec.parse_environment(config.environment)
        data_clients = {
            venue: spec.make_data_config(
                environment=environment,
                instrument_provider=instrument_provider,
            ),
        }
        data_factories = {venue: spec.data_factory}
        # No credential is ever passed in: each venue's Rust client resolves the key pair
        # its environment selects (BYBIT_DEMO_API_KEY/..., HYPERLIQUID_TESTNET_PK, ...)
        # straight from the process environment, so no secret reaches a config file or a
        # log line. A missing key yields an unauthenticated client, not an error -- see
        # DEPLOY_CHECKLIST.md's pre-flight step.
        exec_clients = {
            venue: spec.exec_config_cls(
                environment=environment,
                instrument_provider=instrument_provider,
                **spec.exec_kwargs(config),
            ),
        }
        exec_factory = spec.exec_factory
        bots = (config,)
    else:
        bots = config.bots
        data_clients, exec_clients, data_factories = _paper_venue_clients(
            config, bots, instrument_provider
        )
        exec_factory = SandboxLiveExecClientFactory

    # Redis-backed Cache (Story 4.6, AD-10) -- orders/positions/fills persist beyond
    # this process's lifetime instead of defaulting to in-memory-only. Parsed from the
    # same _REDIS_URL bot_status.py already connects to, rather than a second hardcoded
    # host/port literal, so an overridden REDIS_URL env var can't silently split the
    # Cache and the bots:status/bots:control channels onto different Redis instances.
    redis_url = urlparse(_REDIS_URL)
    node_config = TradingNodeConfig(
        # One fixed id for the whole node (AD-11) -- every paper bot (or the one
        # real-money bot) shares this single node/connection; per-bot addressing lives
        # entirely in bot_id/StrategyId (order_id_tag below), never in trader_id.
        trader_id=TraderId("LIVE-PAPER-001"),
        logging=LoggingConfig(log_level=config.log_level, use_pyo3=True),
        cache=CacheConfig(
            database=DatabaseConfig(
                type="redis",
                host=redis_url.hostname,
                port=redis_url.port,
                username=redis_url.username,
                password=redis_url.password,
                ssl=redis_url.scheme == "rediss",
            ),
        ),
        data_clients=data_clients,
        exec_clients=exec_clients,
    )

    node = TradingNode(config=node_config)
    for venue, data_factory in data_factories.items():
        node.add_data_client_factory(venue, data_factory)
        node.add_exec_client_factory(venue, exec_factory)

    strategies = []
    for bot in bots:
        strategy = DummyStrategy(
            config=DummyStrategyConfig(
                instrument_id=InstrumentId.from_str(bot.instrument_id),
                trade_size=bot.trade_size,
                trend_buy_threshold=bot.trend_buy_threshold,
                trend_sell_threshold=bot.trend_sell_threshold,
                ofi_confirm_threshold=bot.ofi_confirm_threshold,
                # Pinned to bot_id, never left to Trader.add_strategy()'s
                # insertion-order auto-increment default (AD-11) -- an auto-assigned
                # tag would make a bot's Cache/Redis/fills.db identity depend on
                # config list order, silently reassigning history on a reorder.
                order_id_tag=bot.bot_id,
            ),
        )
        node.trader.add_strategy(strategy)
        strategies.append((strategy, bot))

    node.build()

    # Scheduled on the node's own loop, outside each strategy's component lifecycle
    # (Story 4.4; see bot_status.run's own docstring for why -- a Strategy-internal
    # timer would stop firing once Stopped, and could never later hear a "start").
    # One bot_status/trade_history task per configured bot (AD-11), all on this one
    # node's loop -- both already take (strategy, bot_id, ...), so looping here is a
    # call-site change, not a signature change.
    # Kept short: bot_tui's bots pane renders this in a fixed 5-char column.
    mode = "paper" if isinstance(config, PaperConfig) else _EXEC_MODE_LABELS[config.mode]
    loop = node.get_event_loop()
    assert loop is not None, "TradingNode's kernel loop must exist once constructed"
    for strategy, bot in strategies:
        loop.create_task(
            bot_status.run(
                strategy,
                bot_id=bot.bot_id,
                mode=mode,
                redis_url=_REDIS_URL,
                db_path=_FILLS_DB_PATH,
            )
        )
        # Anchors performance_metrics.equity_returns()'s equity curve (Sharpe/Sortino/
        # etc. in bots:history's "metrics" field) -- this bot's own config value, a
        # bookkeeping anchor only (AD-11: bots share one real balance pool, so this is
        # not that bot's actual simulated balance). Real-money mode's actual balance
        # lives on-chain, not in a config file, so those return-based stats are
        # skipped there (None) rather than computed against a fabricated number (see
        # all_metrics()'s own docstring). Money.from_str reuses the same
        # "10_000 USDC"-style parser SandboxExecutionClientConfig already trusts
        # above, rather than a second hand-rolled one.
        starting_balance = (
            Money.from_str(bot.starting_balance).as_double()
            if isinstance(config, PaperConfig)
            else None
        )
        loop.create_task(
            trade_history.run(
                strategy,
                bot_id=bot.bot_id,
                redis_url=_REDIS_URL,
                db_path=_FILLS_DB_PATH,
                starting_balance=starting_balance,
            )
        )

    return node


def main() -> None:
    real_money_path = os.environ.get(_REAL_MONEY_ENV_VAR)
    config, is_real_money = resolve_config(_DEFAULT_PAPER_CONFIG_PATH, real_money_path)

    if is_real_money:
        logger.warning(
            "Starting in %s mode via %s=%s",
            config.mode.upper(),  # type: ignore[union-attr]
            _REAL_MONEY_ENV_VAR,
            real_money_path,
        )

    node = build_node(config)
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
