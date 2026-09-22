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
`kernel.venues`, the only `InstrumentId` parser (the former `common.venues`,
`ml_signals.venue` and `venue_http.bybit_category` tests, plus the id-shape table).
"""

import pytest

from kernel.venues import MalformedInstrumentId
from kernel.venues import bybit_category
from kernel.venues import has_venue
from kernel.venues import market_kind
from kernel.venues import market_suffix
from kernel.venues import venue_kind
from kernel.venues import venue_of


def test_known_venues() -> None:
    assert venue_kind("DYDX") == "dex"
    assert venue_kind("HYPERLIQUID") == "dex"
    assert venue_kind("BYBIT") == "cex"


def test_unknown_venue_does_not_raise() -> None:
    assert venue_kind("NEWVENUE") == "unknown"


def test_market_kind_real_id_shapes() -> None:
    assert market_kind("BTC-USD-PERP.DYDX") == "perp"
    assert market_kind("BTCUSDT-LINEAR.BYBIT") == "perp"
    assert market_kind("BTCUSDT-SPOT.BYBIT") == "spot"
    assert market_kind("BTCUSD-INVERSE.BYBIT") == "perp"
    assert market_kind("BTC-USD-PERP.HYPERLIQUID") == "perp"
    assert market_kind("HYPE-USDC-SPOT.HYPERLIQUID") == "spot"
    assert market_kind("km:US500-USD-PERP.HYPERLIQUID") == "perp"


def test_market_kind_unknown_never_raises() -> None:
    assert market_kind("BTC-30SEP26-100000-C.BYBIT") == "unknown"
    assert market_kind("BTCUSDT-SPOT") == "unknown"
    assert market_kind("") == "unknown"


def test_venue_of() -> None:
    assert venue_of("BTC-USD-PERP.DYDX") == "DYDX"
    assert venue_of("ETH-USD.PERP.BYBIT") == "BYBIT"


@pytest.mark.parametrize("bad", ["", "BTC", ".DYDX", "BTC."])
def test_venue_of_malformed_fails_loudly(bad: str) -> None:
    with pytest.raises(MalformedInstrumentId):
        venue_of(bad)
    assert not has_venue(bad, "DYDX")


# Every id shape the three venues produce: (id, venue, venue kind, market kind, Bybit category).
_ID_SHAPES = [
    ("BTC-USD-PERP.DYDX", "DYDX", "dex", "perp", None),
    ("BTCUSDT-LINEAR.BYBIT", "BYBIT", "cex", "perp", "linear"),
    ("BTCUSD-INVERSE.BYBIT", "BYBIT", "cex", "perp", "inverse"),
    ("BTCUSDT-SPOT.BYBIT", "BYBIT", "cex", "spot", "spot"),
    ("BTC-USD-PERP.HYPERLIQUID", "HYPERLIQUID", "dex", "perp", None),
]


@pytest.mark.parametrize(("iid", "venue", "kind", "market", "category"), _ID_SHAPES)
def test_every_venue_id_shape(
    iid: str, venue: str, kind: str, market: str, category: str | None
) -> None:
    assert venue_of(iid) == venue
    assert has_venue(iid, venue)
    assert venue_kind(venue_of(iid)) == kind
    assert market_kind(iid) == market
    if category is None:
        with pytest.raises(MalformedInstrumentId):
            bybit_category(iid)
    else:
        assert bybit_category(iid) == category


@pytest.mark.parametrize(
    "bad", ["BTC-30SEP26-100000-C.BYBIT", "BTCUSDT-PERP.BYBIT", "BTCUSDT.BYBIT", "BTC", ""]
)
def test_bybit_category_refuses_every_other_shape(bad: str) -> None:
    with pytest.raises(MalformedInstrumentId):
        bybit_category(bad)
    with pytest.raises(ValueError):  # the pre-kernel contract callers still catch
        bybit_category(bad)


def test_market_suffix() -> None:
    assert market_suffix("BTCUSDT-LINEAR.BYBIT") == "LINEAR"
    assert market_suffix("BTC-USD-PERP.DYDX") == "PERP"
    assert market_suffix("BTCUSDT.BYBIT") is None
    assert market_suffix("BTCUSDT-LINEAR") is None
    assert market_suffix("-LINEAR.BYBIT") is None


def test_market_kind_reads_a_dashless_symbol_whole_as_before() -> None:
    """`common.venues.market_kind`'s contract; `market_suffix`/`bybit_category` stay strict."""
    assert market_kind("PERP.X") == "perp"
    assert market_kind("SPOT.BYBIT") == "spot"
    assert market_suffix("SPOT.BYBIT") is None
    with pytest.raises(MalformedInstrumentId):
        bybit_category("LINEAR.BYBIT")
