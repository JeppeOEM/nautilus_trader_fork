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
dYdX's capture config: `DydxConfig` (the core thresholds plus dYdX's own cadences), the
collection cap its WebSocket server forces, and where the plan file lives. Read by the one
loader, `capture.infrastructure.config` (`VENUE_SCHEMAS["DYDX"]`).
"""

import os
from dataclasses import dataclass
from pathlib import Path

from capture.application.config import CoreConfig
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork


# The dYdX plan file: compose bind-mounts the host's `./data/dydx_config.toml` at this frozen
# container path (published language, AD-D12, shared with the `archive` service's
# `DYDX_PLAN_PATH` default and `make nightly`). Unlike Bybit's and Hyperliquid's, no `config.toml`
# is committed beside this module (Story 26.3): the plan is operator data, and a run with nothing
# mounted there fails loudly on the missing file instead of silently collecting an empty plan.
# `DYDX_PLAN_PATH` points a host run at another file (e.g. `platform/data/dydx_config.toml`); set
# but empty, it counts as unset rather than naming the working directory.
CONFIG_PATH = Path(os.environ.get("DYDX_PLAN_PATH") or "/app/dydx_collector/config.toml")

# dYdX's WS server hard-caps subscriptions per channel per connection at 32 (confirmed live via its
# own error: "Per-connection subscription limit reached for v4_trades (limit=32)"). Every collected
# instrument subscribes both v4_trades and v4_orderbook, so the cap bounds the whole collected set --
# going over it does not just drop the overflow, the repeated rejections get the whole connection
# detected as dead and endlessly reconnected (permanent "Stale book" on every instrument). 30 is
# this collector's own operating cap (Story 6.1): a deliberate 2-slot margin under the venue's 32.
DYDX_MAX_WS_SUBSCRIPTIONS = 32
DYDX_MAX_COLLECTED_INSTRUMENTS = 30


@dataclass(frozen=True, kw_only=True)
class DydxConfig(CoreConfig):
    """
    The core thresholds plus dYdX's own cadences. Keyword-only, so `network` can be required;
    `environment` is always derived from `network`, so `CoreConfig` stays satisfied. The plan keys
    (`instruments`, `exclude`, `liquidity_min_oi_usd`, `non_config_retain_hours`) are the
    `CollectionPlan`'s, not the config's.
    """

    environment: str = ""
    network: DydxNetwork
    open_interest_poll_seconds: int = 300
    config_reload_seconds: int = 30
    liquidity_check_seconds: int = 1800

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", str(self.network))
