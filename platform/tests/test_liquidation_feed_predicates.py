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
The two "has a liquidation feed" predicates agree (Story 33.3 review loop 1).

`kernel.liquidation.has_liquidation_feed` decides the candle store's null-vs-0 rule; the
verification oracle restates it independently (`verification.domain.liquidation_check.
has_liquidation_feed`, DATA-02: the oracle never imports the code it checks). Neither may import
the other, so this cross-cutting test is where the two are held equal, on every collected market
type of every venue.
"""

import pytest
from kernel.liquidation import has_liquidation_feed as production
from kernel.venues import venue_of
from verification.domain.liquidation_check import has_liquidation_feed as oracle


_IDS = {
    "BTCUSDT-LINEAR.BYBIT": True,
    "ETHUSDT-LINEAR.BYBIT": True,
    "BTCUSDT-SPOT.BYBIT": False,
    "ETHUSDT-SPOT.BYBIT": False,
    "SOL-USD-PERP.HYPERLIQUID": False,
    "BTC-USD-PERP.HYPERLIQUID": False,
    "BTC-USD-PERP.DYDX": False,
    "ETH-USD-PERP.DYDX": False,
}


@pytest.mark.parametrize(("instrument_id", "expected"), sorted(_IDS.items()))
def test_production_and_oracle_predicates_agree(instrument_id: str, expected: bool) -> None:
    assert production(instrument_id) is expected
    assert oracle(venue_of(instrument_id), instrument_id) is expected
