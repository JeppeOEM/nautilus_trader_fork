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
from nautilus_trader.adapters.dydx.config import DydxExecClientConfig
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.live.node import TradingNode

from live_paper.config import PaperConfig
from live_paper.config import RealMoneyConfig
from live_paper.node import build_node


def _paper_config() -> PaperConfig:
    return PaperConfig(
        network=DydxNetwork.MAINNET,
        starting_balances=("10_000 USDC",),
        account_type="MARGIN",
        log_level="ERROR",
    )


def test_paper_config_builds_node_with_sandbox_exec_client_and_zero_strategies() -> None:
    node = build_node(_paper_config())
    try:
        assert isinstance(node, TradingNode)
        # `_config` is TradingNode's own stored constructor argument (no public accessor
        # exists) -- reading it back here is state inspection, not mocking (troll/CLAUDE.md
        # TEST-03 bars mocking Nautilus internals, not reading real, already-built state).
        exec_client_config = node._config.exec_clients["DYDX"]
        assert isinstance(exec_client_config, SandboxExecutionClientConfig)
        assert not isinstance(exec_client_config, DydxExecClientConfig)
        # Story 3.2: the DummyStrategy is now attached by default (Story 3.1 built with
        # zero strategies since it predates the strategy's existence).
        assert len(node.trader.strategy_states()) == 1
    finally:
        node.dispose()


def test_real_money_config_builds_node_with_dydx_exec_client() -> None:
    config = RealMoneyConfig(
        mode="real_money", network=DydxNetwork.MAINNET, subaccount=0, log_level="ERROR"
    )
    node = build_node(config)
    try:
        exec_client_config = node._config.exec_clients["DYDX"]
        assert isinstance(exec_client_config, DydxExecClientConfig)
    finally:
        node.dispose()
