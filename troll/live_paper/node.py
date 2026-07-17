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

No strategy is attached to the node here (Story 3.2's job) -- this module only proves the
node builds and disposes cleanly with live data + simulated execution wired in.
"""

import logging
import os
from pathlib import Path

from nautilus_trader.adapters.dydx.config import DydxDataClientConfig
from nautilus_trader.adapters.dydx.config import DydxExecClientConfig
from nautilus_trader.adapters.dydx.constants import DYDX
from nautilus_trader.adapters.dydx.factories import DydxLiveDataClientFactory
from nautilus_trader.adapters.dydx.factories import DydxLiveExecClientFactory
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId

from live_paper.config import PaperConfig
from live_paper.config import RealMoneyConfig
from live_paper.config import resolve_config


logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).parent
_DEFAULT_PAPER_CONFIG_PATH = _MODULE_DIR / "config.toml"
_REAL_MONEY_ENV_VAR = "LIVE_PAPER_REAL_MONEY_CONFIG"


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

    node_config = TradingNodeConfig(
        trader_id=TraderId("LIVE-PAPER-001"),
        logging=LoggingConfig(log_level=config.log_level, use_pyo3=True),
        data_clients=data_clients,
        exec_clients=exec_clients,
    )

    node = TradingNode(config=node_config)
    node.add_data_client_factory(DYDX, DydxLiveDataClientFactory)
    node.add_exec_client_factory(DYDX, exec_factory)
    node.build()
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
