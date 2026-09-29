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
The dYdX indexer's REST URLs (moved out of `kernel.venue_http` in Story 31.1): the network map and
the URL builder, both taken from the `nautilus_pyo3` bindings (`DydxNetwork`,
`get_dydx_http_url`), so every dYdX request goes to the host the Rust adapter uses.

Invariant: the one place a dYdX indexer URL is built. Kept apart from `kernel.venue_http` so that
module stays standard-library only -- the verification context must reach venue URLs without
reaching `nautilus_pyo3` (`platform/tests/test_boundaries.py`).
"""

from types import MappingProxyType

from kernel.venue_http import rooted_path
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.core.nautilus_pyo3 import get_dydx_http_url  # type: ignore[attr-defined]


DYDX_NETWORKS = MappingProxyType({"mainnet": DydxNetwork.MAINNET, "testnet": DydxNetwork.TESTNET})


def dydx_indexer_url(network: DydxNetwork, path_and_query: str) -> str:
    """Return the dYdX indexer's base for `network` + `path_and_query` (`/v4/...`)."""
    return f"{get_dydx_http_url(network)}{rooted_path(path_and_query)}"
