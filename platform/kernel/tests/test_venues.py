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
`kernel.venues`, the only `InstrumentId` parser (the tests of the three former venue-id parsers
and `venue_http.bybit_category` -- whose re-export shims Story 24.2 deleted -- plus the id-shape
table).
"""

import pytest

from kernel.venues import USD_QUOTES
from kernel.venues import AssetKey
from kernel.venues import MalformedInstrumentId
from kernel.venues import asset_key
from kernel.venues import base_symbol
from kernel.venues import bybit_category
from kernel.venues import has_venue
from kernel.venues import market_kind
from kernel.venues import market_suffix
from kernel.venues import same_asset
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
    """The pre-kernel `market_kind` contract; `market_suffix`/`bybit_category` stay strict."""
    assert market_kind("PERP.X") == "perp"
    assert market_kind("SPOT.BYBIT") == "spot"
    assert market_suffix("SPOT.BYBIT") is None
    with pytest.raises(MalformedInstrumentId):
        bybit_category("LINEAR.BYBIT")


_BTC_PERP = AssetKey("BTC", "USD", "perp")

# Real id shapes (Story 27.4) -> the asset they trade, or None where the tables cannot read them.
_ASSET_KEYS = [
    ("BTC-USD-PERP.DYDX", _BTC_PERP),
    ("BTCUSDT-LINEAR.BYBIT", _BTC_PERP),
    ("BTCPERP-LINEAR.BYBIT", _BTC_PERP),  # Bybit's USDC perpetual
    ("BTC-USD-PERP.HYPERLIQUID", _BTC_PERP),
    ("ETH-USD-PERP.DYDX", AssetKey("ETH", "USD", "perp")),
    ("1000PEPEUSDT-LINEAR.BYBIT", AssetKey("1000PEPE", "USD", "perp")),
    ("BTCUSDT-SPOT.BYBIT", AssetKey("BTC", "USD", "spot")),
    ("BTCUSDC-SPOT.BYBIT", AssetKey("BTC", "USD", "spot")),
    ("BTCUSDT-25SEP26-LINEAR.BYBIT", None),  # a dated future
    ("BTCUSD-INVERSE.BYBIT", None),
    ("ETHBTC-SPOT.BYBIT", None),
    ("BTCUSD1-SPOT.BYBIT", None),
    ("BBSOLSOL-SPOT.BYBIT", None),
    ("BTCPERP-SPOT.BYBIT", None),  # PERP names a linear contract only
    ("USDT-LINEAR.BYBIT", None),  # no base
    ("HYPE-USDC-SPOT.HYPERLIQUID", None),
    ("km:US500-USD-PERP.HYPERLIQUID", None),
    ("BTC-USD-PERP-X.DYDX", None),
    ("BTC-USD-PERP.NEWVENUE", None),
    ("garbage", None),
    ("", None),
]


@pytest.mark.parametrize(("iid", "key"), _ASSET_KEYS)
def test_asset_key_reads_every_real_shape_and_refuses_the_rest(
    iid: str, key: AssetKey | None
) -> None:
    assert asset_key(iid) == key


def test_the_usd_quote_class_is_the_dollar_and_its_stablecoins() -> None:
    assert frozenset({"USD", "USDC", "USDT"}) == USD_QUOTES


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("BTC-USD-PERP.DYDX", "BTCUSDT-LINEAR.BYBIT", True),
        ("BTC-USD-PERP.DYDX", "BTCPERP-LINEAR.BYBIT", True),
        ("BTC-USD-PERP.DYDX", "BTC-USD-PERP.HYPERLIQUID", True),
        ("BTC-USD-PERP.DYDX", "BTCUSDT-SPOT.BYBIT", False),  # a different kind
        ("BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX", False),
        ("garbage", "garbage", False),  # no key never matches, not even itself
        ("BTCUSDT-25SEP26-LINEAR.BYBIT", "BTCUSDT-25SEP26-LINEAR.BYBIT", False),
    ],
)
def test_same_asset(a: str, b: str, same: bool) -> None:
    assert same_asset(a, b) is same
    assert same_asset(b, a) is same


# Story 29.1's I/O matrix: every real id shape -> the base coin the web rankings line up.
_BASE_SYMBOLS = [
    ("BTC-USD-PERP.DYDX", "BTC"),
    ("BTCUSDT-LINEAR.BYBIT", "BTC"),
    ("BTCUSDT-SPOT.BYBIT", "BTC"),
    ("ETHUSDC-SPOT.BYBIT", "ETH"),
    ("BTCUSD-INVERSE.BYBIT", "BTC"),
    ("BTCPERP-LINEAR.BYBIT", "BTC"),  # Bybit's USDC perpetual
    ("1000PEPEUSDT-LINEAR.BYBIT", "1000PEPE"),
    ("BTCUSDT-25SEP26-LINEAR.BYBIT", "BTC"),  # a dated future
    ("ETHBTC-SPOT.BYBIT", "ETHBTC"),  # an unlisted quote: the whole head, no guess
    ("USDT-LINEAR.BYBIT", "USDT"),  # stripping would leave no base: the whole head
    ("SOL-USD-PERP.HYPERLIQUID", "SOL"),
    ("HYPE-USDC-SPOT.HYPERLIQUID", "HYPE"),
    ("km:US500-USD-PERP.HYPERLIQUID", "km:US500"),
    ("BTC-USD-PERP.NEWVENUE", "BTC"),  # an unknown venue: the first `-` segment
    ("BTCUSDT.NEWVENUE", "BTCUSDT"),
    ("-LINEAR.BYBIT", "-LINEAR"),  # an empty first segment: the symbol whole, never ""
    # Known limit, pinned so a change is deliberate: an unlisted quote that ends in a listed one
    # is cut in the wrong place (`BUSD` -> `USD` stripped).
    ("ETHBUSD-SPOT.BYBIT", "ETHB"),
]


@pytest.mark.parametrize(("iid", "base"), _BASE_SYMBOLS)
def test_base_symbol_reads_every_real_shape(iid: str, base: str) -> None:
    assert base_symbol(iid) == base


@pytest.mark.parametrize("bad", ["BTCUSDT", "", ".BYBIT", "BTCUSDT-LINEAR."])
def test_base_symbol_malformed_fails_loudly(bad: str) -> None:
    with pytest.raises(MalformedInstrumentId):
        base_symbol(bad)


@pytest.mark.parametrize(
    "iid", ["ETHUSDT-SPOT.BYBIT", "ETHUSDC-SPOT.BYBIT", "ETHUSD-INVERSE.BYBIT"]
)
def test_bybit_stablecoin_quotes_are_stripped_whole_not_as_their_usd_prefix(iid: str) -> None:
    assert base_symbol(iid) == "ETH"
