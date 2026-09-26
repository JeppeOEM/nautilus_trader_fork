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
Deprecated module (Story 26.1): the REST trade backfill was split in two. The fetch and parse half
is per venue -- `dydx_collector.trade_history`, `bybit_collector.trade_history`,
`hyperliquid_collector.trade_history` (each a `collector_core.ports.VenueTradeHistory`) -- over the
shared exact conversions in `collector_core.domain.trade_history`; the scheduling half and the
report are `collector_core.application.trade_backfill` and the `Collector`. The venue dispatcher
`fetch_trades` is gone (the venue's composition root injects its own history). The surviving names
are re-exported here.
"""

import warnings

from bybit_collector.trade_history import parse_bybit_trades
from dydx_collector.trade_history import parse_dydx_trades
from hyperliquid_collector.trade_history import parse_hyperliquid_trades

from collector_core.domain.trade_history import BackfillError
from collector_core.domain.trade_history import Fetched
from collector_core.domain.trade_history import exact_text
from collector_core.domain.trade_history import iso_to_ns
from collector_core.domain.trade_history import ms_to_ns


__all__ = [
    "BackfillError",
    "Fetched",
    "exact_text",
    "iso_to_ns",
    "ms_to_ns",
    "parse_bybit_trades",
    "parse_dydx_trades",
    "parse_hyperliquid_trades",
]

REMOVE_AFTER = "26-3-closeout-shims-gone-spines-reconciled"

warnings.warn(
    "collector_core.trade_backfill is deprecated (Story 26.1): import from "
    "collector_core.domain.trade_history or <venue>_collector.trade_history; "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
