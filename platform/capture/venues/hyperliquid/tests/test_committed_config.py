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
the same file), read through the one loader `build_capture_from_file` uses. Hyperliquid collects
exactly `SOL-USD-PERP.HYPERLIQUID` (Story 29.3, decision 2026-09-26: SOL on Hyperliquid, BTC/ETH on
Bybit).
"""

from pathlib import Path

import capture.venues.hyperliquid
from capture.infrastructure.config import load_venue_config
from capture.venues.hyperliquid.__main__ import VENUE


_COMMITTED = Path(capture.venues.hyperliquid.__file__).parent / "config.toml"


def test_the_committed_plan_collects_exactly_the_decided_ids() -> None:
    _, plan = load_venue_config(_COMMITTED, VENUE)

    assert plan.venue == "HYPERLIQUID"
    assert plan.collected == ("SOL-USD-PERP.HYPERLIQUID",)
