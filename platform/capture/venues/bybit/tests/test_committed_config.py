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
The committed `config.toml` is the plan the container runs (the image bakes it in and compose mounts
the same file), read through the one loader `build_capture_from_file` uses. Bybit collects BTC and
ETH on both markets (Story 29.3): linear for mark, funding and open interest, spot for the spot
book.
"""

from pathlib import Path

import capture.venues.bybit
from capture.infrastructure.config import load_venue_config
from capture.venues.bybit.__main__ import VENUE


_COMMITTED = Path(capture.venues.bybit.__file__).parent / "config.toml"


def test_the_committed_plan_collects_exactly_the_decided_ids() -> None:
    _, plan = load_venue_config(_COMMITTED, VENUE)

    assert plan.venue == "BYBIT"
    assert plan.collected == (
        "BTCUSDT-LINEAR.BYBIT",
        "ETHUSDT-LINEAR.BYBIT",
        "BTCUSDT-SPOT.BYBIT",
        "ETHUSDT-SPOT.BYBIT",
    )


def test_the_committed_plan_is_uncapped_with_no_exclusions() -> None:
    """Story 29.4: no coin cap on this venue, and `exclude` appears only once a coin is unpinned."""
    _, plan = load_venue_config(_COMMITTED, VENUE)

    assert (plan.cap, plan.excluded, plan.min_liquidity_usd) == (None, frozenset(), None)
