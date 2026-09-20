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
The venues `live_paper` can paper-trade on -- a plain table, not a class hierarchy.

One `VenueSpec` per Nautilus venue string: how to build that venue's public data client
and what its Sandbox quote currency / environments are. Paper execution is always
`SandboxExecutionClientConfig` (built in node.py), so no exec-side entries are needed
here until story 22.7 adds real-money for Bybit/Hyperliquid.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from nautilus_trader.adapters.bybit.config import BybitDataClientConfig
from nautilus_trader.adapters.bybit.constants import BYBIT
from nautilus_trader.adapters.bybit.factories import BybitLiveDataClientFactory
from nautilus_trader.adapters.dydx.config import DydxDataClientConfig
from nautilus_trader.adapters.dydx.constants import DYDX
from nautilus_trader.adapters.dydx.factories import DydxLiveDataClientFactory
from nautilus_trader.adapters.hyperliquid.config import HyperliquidDataClientConfig
from nautilus_trader.adapters.hyperliquid.constants import HYPERLIQUID
from nautilus_trader.adapters.hyperliquid.factories import HyperliquidLiveDataClientFactory
from nautilus_trader.core.nautilus_pyo3 import BybitEnvironment
from nautilus_trader.core.nautilus_pyo3 import BybitProductType
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.core.nautilus_pyo3 import HyperliquidEnvironment


@dataclass(frozen=True)
class VenueSpec:
    make_data_config: Callable[..., object]
    data_factory: type
    allowed_environments: tuple[str, ...]
    environment_cls: type
    paper_quote_currency: str

    def parse_environment(self, name: str) -> object:
        # PyO3 enums have no string constructor that agrees across venues; the
        # upper-cased member name does (config spells them lower-case).
        return getattr(self.environment_cls, name.upper())


VENUES: dict[str, VenueSpec] = {
    DYDX: VenueSpec(
        make_data_config=DydxDataClientConfig,
        data_factory=DydxLiveDataClientFactory,
        allowed_environments=("mainnet", "testnet"),
        environment_cls=DydxNetwork,
        paper_quote_currency="USDC",
    ),
    BYBIT: VenueSpec(
        # Both product types: spot bots and linear bots share the one BYBIT data client.
        make_data_config=partial(
            BybitDataClientConfig,
            product_types=(BybitProductType.LINEAR, BybitProductType.SPOT),
        ),
        data_factory=BybitLiveDataClientFactory,
        allowed_environments=("mainnet", "demo", "testnet"),
        environment_cls=BybitEnvironment,
        paper_quote_currency="USDT",
    ),
    HYPERLIQUID: VenueSpec(
        make_data_config=HyperliquidDataClientConfig,
        data_factory=HyperliquidLiveDataClientFactory,
        allowed_environments=("mainnet", "testnet"),
        environment_cls=HyperliquidEnvironment,
        paper_quote_currency="USDC",
    ),
}
