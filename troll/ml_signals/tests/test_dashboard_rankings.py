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
import time

from ml_signals.dashboard import _LIVE_FAST
from ml_signals.dashboard import _VOLUME_24H
from ml_signals.dashboard import _WATCHLIST_STALE_NS
from ml_signals.dashboard import _is_fresh
from ml_signals.dashboard import _rankings_json
from ml_signals.dashboard import _watchlist_ids
from ml_signals.dashboard import make_app
from ml_signals.dashboard import parse_volume_24h
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork


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


def test_rankings_json_sort_updates_live_not_cached() -> None:
    """AC2: sort order must reflect _VOLUME_24H mutated between two calls, not a frozen order."""
    _reset_state()
    _LIVE_FAST["AAA-USD-PERP.DYDX"] = _live_row("AAA-USD-PERP.DYDX")
    _LIVE_FAST["BBB-USD-PERP.DYDX"] = _live_row("BBB-USD-PERP.DYDX")
    _VOLUME_24H["AAA-USD-PERP.DYDX"] = 100.0
    _VOLUME_24H["BBB-USD-PERP.DYDX"] = 1.0

    first = [r["instrument_id"] for r in json.loads(_rankings_json())["rows"]]
    assert first == ["AAA-USD-PERP.DYDX", "BBB-USD-PERP.DYDX"]

    _VOLUME_24H["BBB-USD-PERP.DYDX"] = 1_000.0  # now BBB outranks AAA

    second = [r["instrument_id"] for r in json.loads(_rankings_json())["rows"]]
    assert second == ["BBB-USD-PERP.DYDX", "AAA-USD-PERP.DYDX"]


def test_rankings_json_cells_null_out_non_finite_raw_value() -> None:
    """NaN/Infinity are valid Python floats but invalid JSON tokens -- must be nulled, not emitted."""
    _reset_state()
    row = _live_row("BTC-USD-PERP.DYDX")
    row["pct_24h"] = float("nan")
    row["volatility"] = float("inf")
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = row

    raw_text = _rankings_json()
    assert "NaN" not in raw_text
    assert "Infinity" not in raw_text

    payload = json.loads(raw_text)  # would raise if a literal NaN/Infinity leaked through
    cells = payload["rows"][0]["cells"]
    assert cells["pct_24h"]["raw"] is None
    assert cells["volatility"]["raw"] is None


def test_make_app_wires_configured_network_for_volume_poll() -> None:
    """The volume-24h poll must use the collector's configured network, not always MAINNET."""
    app = make_app("redis://127.0.0.1:6379", "/nonexistent/catalog", DydxNetwork.TESTNET)
    assert app["dydx_network"] == DydxNetwork.TESTNET


def test_make_app_defaults_network_to_mainnet() -> None:
    app = make_app("redis://127.0.0.1:6379", "/nonexistent/catalog")
    assert app["dydx_network"] == DydxNetwork.MAINNET


def test_is_fresh_true_within_window() -> None:
    _reset_state()
    now_ns = time.time_ns()
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = {**_live_row("BTC-USD-PERP.DYDX"), "ts": now_ns}
    assert _is_fresh("BTC-USD-PERP.DYDX", now_ns) is True


def test_is_fresh_false_past_stale_window() -> None:
    _reset_state()
    now_ns = time.time_ns()
    stale_ts = now_ns - _WATCHLIST_STALE_NS - 1
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = {**_live_row("BTC-USD-PERP.DYDX"), "ts": stale_ts}
    assert _is_fresh("BTC-USD-PERP.DYDX", now_ns) is False


def test_is_fresh_false_for_unknown_instrument() -> None:
    _reset_state()
    assert _is_fresh("NOPE-USD-PERP.DYDX", time.time_ns()) is False


def test_watchlist_ids_excludes_stale_instrument_ac2() -> None:
    """AC2: a coin that stops receiving snapshots must drop out of the Watchlist."""
    _reset_state()
    now_ns = time.time_ns()
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = {**_live_row("BTC-USD-PERP.DYDX"), "ts": now_ns}
    _LIVE_FAST["DEAD-USD-PERP.DYDX"] = {
        **_live_row("DEAD-USD-PERP.DYDX"), "ts": now_ns - _WATCHLIST_STALE_NS - 1,
    }
    _VOLUME_24H["BTC-USD-PERP.DYDX"] = 1.0
    _VOLUME_24H["DEAD-USD-PERP.DYDX"] = 1_000_000.0  # would sort first if not excluded

    assert _watchlist_ids() == ["BTC-USD-PERP.DYDX"]


def test_watchlist_ids_sorted_by_descending_volume() -> None:
    _reset_state()
    now_ns = time.time_ns()
    for iid, vol in (("BTC-USD-PERP.DYDX", 50_000_000.0), ("SHIB-USD-PERP.DYDX", 100.0)):
        _LIVE_FAST[iid] = {**_live_row(iid), "ts": now_ns}
        _VOLUME_24H[iid] = vol

    assert _watchlist_ids() == ["BTC-USD-PERP.DYDX", "SHIB-USD-PERP.DYDX"]
