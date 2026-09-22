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
The venues `live_paper` can trade on -- a plain table, not a class hierarchy.

One `VenueSpec` per Nautilus venue string: how to build that venue's public data client
and its non-Sandbox exec client, plus its Sandbox quote currency / allowed environments.
Paper execution is always `SandboxExecutionClientConfig` (built in node.py) and ignores
the exec-side columns; the explicit `real_money`/`exchange_demo` path (config.py) drives
itself entirely off them, so adding a venue there is one more row here rather than
another `if venue == ...` branch in node.py.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from kernel.venues import market_suffix

from nautilus_trader.adapters.bybit.config import BybitDataClientConfig
from nautilus_trader.adapters.bybit.config import BybitExecClientConfig
from nautilus_trader.adapters.bybit.constants import BYBIT
from nautilus_trader.adapters.bybit.factories import BybitLiveDataClientFactory
from nautilus_trader.adapters.bybit.factories import BybitLiveExecClientFactory
from nautilus_trader.adapters.dydx.config import DydxDataClientConfig
from nautilus_trader.adapters.dydx.config import DydxExecClientConfig
from nautilus_trader.adapters.dydx.constants import DYDX
from nautilus_trader.adapters.dydx.factories import DydxLiveDataClientFactory
from nautilus_trader.adapters.dydx.factories import DydxLiveExecClientFactory
from nautilus_trader.adapters.hyperliquid.config import HyperliquidDataClientConfig
from nautilus_trader.adapters.hyperliquid.config import HyperliquidExecClientConfig
from nautilus_trader.adapters.hyperliquid.constants import HYPERLIQUID
from nautilus_trader.adapters.hyperliquid.factories import HyperliquidLiveDataClientFactory
from nautilus_trader.adapters.hyperliquid.factories import HyperliquidLiveExecClientFactory
from nautilus_trader.core.nautilus_pyo3 import BybitEnvironment
from nautilus_trader.core.nautilus_pyo3 import BybitProductType
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.core.nautilus_pyo3 import HyperliquidEnvironment


if TYPE_CHECKING:  # config.py imports this module, so the annotation can't be a real import
    from live_paper.config import ExecConfig


@dataclass(frozen=True)
class VenueSpec:
    make_data_config: Callable[..., object]
    data_factory: type
    allowed_environments: tuple[str, ...]
    environment_cls: type
    paper_quote_currency: str
    exec_config_cls: type
    exec_factory: type
    exec_kwargs: "Callable[[ExecConfig], dict]" = lambda config: {}

    def parse_environment(self, name: str) -> object:
        # PyO3 enums have no string constructor that agrees across venues; the
        # upper-cased member name does (config spells them lower-case).
        return getattr(self.environment_cls, name.upper())


def _bybit_exec_kwargs(config: "ExecConfig") -> dict:
    """
    Bybit's exec client is scoped to product types, and the instrument id's own suffix is
    that product type (`BTCUSDT-LINEAR.BYBIT` -> LINEAR) -- the same suffix
    `kernel.venues.market_kind()` reads. So the bot's one instrument decides it.
    """
    suffix = market_suffix(config.instrument_id)
    product_type = getattr(BybitProductType, suffix.upper(), None) if suffix else None
    if product_type is None:
        raise ValueError(
            f"Bybit instrument id {config.instrument_id!r} must carry its product type as the "
            "symbol suffix (LINEAR, INVERSE, SPOT or OPTION), e.g. BTCUSDT-LINEAR.BYBIT"
        )
    return {"product_types": (product_type,)}


VENUES: dict[str, VenueSpec] = {
    DYDX: VenueSpec(
        make_data_config=DydxDataClientConfig,
        data_factory=DydxLiveDataClientFactory,
        allowed_environments=("mainnet", "testnet"),
        environment_cls=DydxNetwork,
        paper_quote_currency="USDC",
        exec_config_cls=DydxExecClientConfig,
        exec_factory=DydxLiveExecClientFactory,
        exec_kwargs=lambda config: {"subaccount": config.subaccount},
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
        exec_config_cls=BybitExecClientConfig,
        exec_factory=BybitLiveExecClientFactory,
        exec_kwargs=_bybit_exec_kwargs,
    ),
    HYPERLIQUID: VenueSpec(
        make_data_config=HyperliquidDataClientConfig,
        data_factory=HyperliquidLiveDataClientFactory,
        allowed_environments=("mainnet", "testnet"),
        environment_cls=HyperliquidEnvironment,
        paper_quote_currency="USDC",
        exec_config_cls=HyperliquidExecClientConfig,
        exec_factory=HyperliquidLiveExecClientFactory,
    ),
}
