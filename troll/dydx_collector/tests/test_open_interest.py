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
"""Unit tests for open_interest.parse_open_interest."""

from decimal import Decimal

from dydx_collector.open_interest import parse_open_interest


_TS = 1_000_000_000


def _markets(**markets: dict) -> dict:
    return {"markets": markets}


def test_parses_single_market() -> None:
    result = parse_open_interest(_markets(BTC={"ticker": "BTC-USD", "openInterest": "1234.56"}), ts=_TS)
    assert len(result) == 1
    assert result[0].instrument_id.value == "BTC-USD-PERP.DYDX"
    assert result[0].open_interest == Decimal("1234.56")
    assert result[0].ts_event == _TS


def test_parses_multiple_markets() -> None:
    result = parse_open_interest(
        _markets(
            BTC={"ticker": "BTC-USD", "openInterest": "100.0"},
            ETH={"ticker": "ETH-USD", "openInterest": "200.0"},
        ),
        ts=_TS,
    )
    assert len(result) == 2
    iids = {r.instrument_id.value for r in result}
    assert iids == {"BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"}


def test_skips_market_missing_ticker() -> None:
    result = parse_open_interest(_markets(X={"openInterest": "100.0"}), ts=_TS)
    assert result == []


def test_skips_market_missing_open_interest() -> None:
    result = parse_open_interest(_markets(X={"ticker": "BTC-USD"}), ts=_TS)
    assert result == []


def test_open_interest_preserved_as_decimal() -> None:
    # Decimal("999999999.123456789") must not be mangled by float conversion
    result = parse_open_interest(_markets(X={"ticker": "ETH-USD", "openInterest": "999999999.123456789"}), ts=_TS)
    assert result[0].open_interest == Decimal("999999999.123456789")


def test_empty_markets_returns_empty_list() -> None:
    assert parse_open_interest({"markets": {}}, ts=_TS) == []


if __name__ == "__main__":
    test_parses_single_market()
    test_parses_multiple_markets()
    test_skips_market_missing_ticker()
    test_skips_market_missing_open_interest()
    test_open_interest_preserved_as_decimal()
    test_empty_markets_returns_empty_list()
    print("ok")
