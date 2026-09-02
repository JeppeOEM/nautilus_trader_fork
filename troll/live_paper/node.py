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

The first sanctioned TradingNode/Strategy usage in troll/ -- see ARCHITECTURE-SPINE.md's
AD-8 amendment. dydx_collector/ml_signals never instantiate TradingNode (troll/CLAUDE.md's
FORK-02); that rule's own header scopes it to those two modules only, and does not bind
this new, structurally separate module.

Paper mode (the only reachable default -- see config.py) wires live DydxDataClientConfig
market data into a SandboxExecutionClientConfig/SandboxLiveExecClientFactory simulated
exchange: no wallet address or private key anywhere in this path, so real funds are
structurally unreachable, not merely gated by a disabled flag. Real money uses
DydxExecClientConfig/DydxLiveExecClientFactory instead, which does sign and submit real
on-chain transactions -- only reachable via the separate, explicit path in config.py.

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

from live_paper import bot_status
from live_paper import trade_history
from live_paper.config import PaperConfig
from live_paper.config import RealMoneyConfig
from live_paper.config import resolve_config
from live_paper.strategy import DummyStrategy
from live_paper.strategy import DummyStrategyConfig
from nautilus_trader.adapters.dydx.config import DydxDataClientConfig
from nautilus_trader.adapters.dydx.config import DydxExecClientConfig
from nautilus_trader.adapters.dydx.constants import DYDX
from nautilus_trader.adapters.dydx.factories import DydxLiveDataClientFactory
from nautilus_trader.adapters.dydx.factories import DydxLiveExecClientFactory
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


logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).parent
_DEFAULT_PAPER_CONFIG_PATH = _MODULE_DIR / "config.toml"
_REAL_MONEY_ENV_VAR = "LIVE_PAPER_REAL_MONEY_CONFIG"
_REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
# Shared across every bot (Story 4.6) -- mirrors ranking_engine's METRICS_DB_PATH
# env-override convention. Directory (not the bare file) must be volume-mounted for
# fills to survive a container restart -- see docker-compose.yml's live-paper service.
_FILLS_DB_PATH = os.environ.get("FILLS_DB_PATH", str(_MODULE_DIR / "data" / "fills.db"))


def build_node(config: PaperConfig | RealMoneyConfig) -> TradingNode:
    instrument_provider = InstrumentProviderConfig(load_all=True)

    data_clients = {
        DYDX: DydxDataClientConfig(
            environment=config.network,
            instrument_provider=instrument_provider,
        ),
    }

    if isinstance(config, RealMoneyConfig):
        exec_clients = {
            DYDX: DydxExecClientConfig(
                environment=config.network,
                subaccount=config.subaccount,
                instrument_provider=instrument_provider,
            ),
        }
        exec_factory = DydxLiveExecClientFactory
    else:
        exec_clients = {
            DYDX: SandboxExecutionClientConfig(
                venue=DYDX,
                starting_balances=list(config.starting_balances),
                account_type=config.account_type,
                instrument_provider=instrument_provider,
            ),
        }
        exec_factory = SandboxLiveExecClientFactory

    # Redis-backed Cache (Story 4.6, AD-10) -- orders/positions/fills persist beyond
    # this process's lifetime instead of defaulting to in-memory-only. Parsed from the
    # same _REDIS_URL bot_status.py already connects to, rather than a second hardcoded
    # host/port literal, so an overridden REDIS_URL env var can't silently split the
    # Cache and the bots:status/bots:control channels onto different Redis instances.
    redis_url = urlparse(_REDIS_URL)
    node_config = TradingNodeConfig(
        trader_id=TraderId("LIVE-PAPER-001"),
        logging=LoggingConfig(log_level=config.log_level, use_pyo3=True),
        cache=CacheConfig(
            database=DatabaseConfig(type="redis", host=redis_url.hostname, port=redis_url.port),
        ),
        data_clients=data_clients,
        exec_clients=exec_clients,
    )

    strategy = DummyStrategy(
        config=DummyStrategyConfig(
            instrument_id=InstrumentId.from_str(config.instrument_id),
            trade_size=config.trade_size,
            trend_buy_threshold=config.trend_buy_threshold,
            trend_sell_threshold=config.trend_sell_threshold,
            ofi_confirm_threshold=config.ofi_confirm_threshold,
        ),
    )

    node = TradingNode(config=node_config)
    node.trader.add_strategy(strategy)
    node.add_data_client_factory(DYDX, DydxLiveDataClientFactory)
    node.add_exec_client_factory(DYDX, exec_factory)
    node.build()

    # Scheduled on the node's own loop, outside the strategy's component lifecycle
    # (Story 4.4; see bot_status.run's own docstring for why -- a Strategy-internal
    # timer would stop firing once Stopped, and could never later hear a "start").
    mode = "live" if isinstance(config, RealMoneyConfig) else "paper"
    loop = node.get_event_loop()
    assert loop is not None, "TradingNode's kernel loop must exist once constructed"
    loop.create_task(
        bot_status.run(strategy, bot_id=config.bot_id, mode=mode, redis_url=_REDIS_URL)
    )
    # Same event-loop-lifecycle reasoning as bot_status.run() above (Story 4.6).
    loop.create_task(
        trade_history.run(
            strategy, bot_id=config.bot_id, redis_url=_REDIS_URL, db_path=_FILLS_DB_PATH
        )
    )

    return node


def main() -> None:
    real_money_path = os.environ.get(_REAL_MONEY_ENV_VAR)
    config, is_real_money = resolve_config(_DEFAULT_PAPER_CONFIG_PATH, real_money_path)

    if is_real_money:
        logger.warning(
            "Starting in REAL-MONEY mode via %s=%s", _REAL_MONEY_ENV_VAR, real_money_path
        )

    node = build_node(config)
    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
