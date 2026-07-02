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
"""Unit tests for the rankings default volume-sort (_rankings_json) and volume24H parsing."""

import json

from ml_signals.dashboard import _LIVE_FAST
from ml_signals.dashboard import _VOLUME_24H
from ml_signals.dashboard import _rankings_json
from ml_signals.dashboard import parse_volume_24h


def _reset_state() -> None:
    _LIVE_FAST.clear()
    _VOLUME_24H.clear()


def _live_row(iid: str) -> dict:
    return {
        "ts": 1_000_000_000,
        "instrument_id": iid,
        "_err": None,
        "ofi_10_z": None,
        "obi_10": None,
        "obi_5": None,
        "obi_3": None,
        "cvd": None,
        "spread": None,
        "microprice_lean": None,
        "volume_delta": None,
        "buy_count": 0,
        "sell_count": 0,
        "price": None,
        "pct_1h": None,
        "pct_24h": None,
        "volatility": None,
    }


def test_parse_volume_24h_extracts_usd_volume_per_market() -> None:
    markets_json = {"markets": {
        "BTC": {"ticker": "BTC-USD", "volume24H": "50000000"},
        "ETH": {"ticker": "ETH-USD", "volume24H": "10000000"},
    }}
    result = parse_volume_24h(markets_json)
    assert result == {"BTC-USD-PERP.DYDX": 50000000.0, "ETH-USD-PERP.DYDX": 10000000.0}


def test_parse_volume_24h_missing_field_defaults_to_zero() -> None:
    markets_json = {"markets": {"X": {"ticker": "X-USD"}}}
    result = parse_volume_24h(markets_json)
    assert result == {"X-USD-PERP.DYDX": 0.0}


def test_parse_volume_24h_skips_market_missing_ticker() -> None:
    markets_json = {"markets": {"X": {"volume24H": "100"}}}
    assert parse_volume_24h(markets_json) == {}


def test_rankings_json_sorted_by_descending_volume24h() -> None:
    _reset_state()
    _LIVE_FAST["SHIB-USD-PERP.DYDX"] = _live_row("SHIB-USD-PERP.DYDX")
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = _live_row("BTC-USD-PERP.DYDX")
    _LIVE_FAST["ETH-USD-PERP.DYDX"] = _live_row("ETH-USD-PERP.DYDX")
    _VOLUME_24H["SHIB-USD-PERP.DYDX"] = 100.0
    _VOLUME_24H["BTC-USD-PERP.DYDX"] = 50_000_000.0
    _VOLUME_24H["ETH-USD-PERP.DYDX"] = 10_000_000.0

    payload = json.loads(_rankings_json())
    ordered_iids = [row["instrument_id"] for row in payload["rows"]]

    assert ordered_iids == ["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX", "SHIB-USD-PERP.DYDX"]


def test_rankings_json_missing_volume_sorts_as_zero() -> None:
    _reset_state()
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = _live_row("BTC-USD-PERP.DYDX")
    _LIVE_FAST["NEW-USD-PERP.DYDX"] = _live_row("NEW-USD-PERP.DYDX")  # no _VOLUME_24H entry
    _VOLUME_24H["BTC-USD-PERP.DYDX"] = 1.0

    payload = json.loads(_rankings_json())
    ordered_iids = [row["instrument_id"] for row in payload["rows"]]

    assert ordered_iids == ["BTC-USD-PERP.DYDX", "NEW-USD-PERP.DYDX"]


def test_rankings_json_cells_include_raw_value() -> None:
    _reset_state()
    row = _live_row("BTC-USD-PERP.DYDX")
    row["price"] = 50000.1234
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = row

    payload = json.loads(_rankings_json())
    cell = payload["rows"][0]["cells"]["price"]

    assert cell["raw"] == 50000.1234
