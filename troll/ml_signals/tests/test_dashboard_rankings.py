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
Unit tests for dashboard as a pure reader of rankings:live (Story 1.8, Task 8).

Story 1.2/1.4's inline volume-sort/watchlist/rank logic has been extracted into
ranking_engine -- dashboard._rankings_json() now renders whatever order
ranking_engine's rankings:live message dictates, never re-sorting locally. See
ranking_engine/tests/test_engine.py for the relocated ranking-computation tests.
"""

import asyncio
import json
import time

import ml_signals.dashboard as dashboard_module
from ml_signals.dashboard import _LIVE_FAST
from ml_signals.dashboard import _rankings_json
from ml_signals.dashboard import watchlist_json_handler


def _reset_state() -> None:
    _LIVE_FAST.clear()
    dashboard_module._LATEST_RANKING = None
    dashboard_module._LATEST_RANKING_RECEIVED_AT = 0.0


def _live_row(iid: str) -> dict:
    return {
        "ts": time.time_ns(),
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


def _ranking_message(ranks: list[dict], mode: str = "volume") -> dict:
    return {"mode": mode, "updated_at": time.time_ns(), "ranks": ranks}


def _rank_row(iid: str, rank: int, volume24h: float = 1.0) -> dict:
    return {"instrument_id": iid, "rank": rank, "volume24h": volume24h, "volatility_score": None}


def test_rankings_json_renders_ranks_in_engines_exact_order() -> None:
    """Rows follow rankings:live's own order -- never re-sorted client- or server-side."""
    _reset_state()
    _LIVE_FAST["SHIB-USD-PERP.DYDX"] = _live_row("SHIB-USD-PERP.DYDX")
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = _live_row("BTC-USD-PERP.DYDX")
    dashboard_module._LATEST_RANKING = _ranking_message([
        _rank_row("BTC-USD-PERP.DYDX", 1, 50_000_000.0),
        _rank_row("SHIB-USD-PERP.DYDX", 2, 100.0),
    ])

    payload = json.loads(_rankings_json())
    ordered_iids = [row["instrument_id"] for row in payload["rows"]]

    assert ordered_iids == ["BTC-USD-PERP.DYDX", "SHIB-USD-PERP.DYDX"]


def test_rankings_json_ranked_row_volume24h_cell_comes_from_ranks_entry() -> None:
    _reset_state()
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = _live_row("BTC-USD-PERP.DYDX")
    dashboard_module._LATEST_RANKING = _ranking_message([
        _rank_row("BTC-USD-PERP.DYDX", 1, 50_000_000.0),
    ])

    payload = json.loads(_rankings_json())

    assert payload["rows"][0]["cells"]["volume24h"]["raw"] == 50_000_000.0


def test_rankings_json_unranked_instrument_appears_after_ranked_rows_with_null_cells() -> None:
    """An instrument in _LIVE_FAST but absent from ranks[] is appended after all ranked
    rows, with no rank/volume24h cell values (never dropped, never an error).
    """
    _reset_state()
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = _live_row("BTC-USD-PERP.DYDX")
    _LIVE_FAST["NEW-USD-PERP.DYDX"] = _live_row("NEW-USD-PERP.DYDX")  # not yet ranked
    dashboard_module._LATEST_RANKING = _ranking_message([
        _rank_row("BTC-USD-PERP.DYDX", 1, 50_000_000.0),
    ])

    payload = json.loads(_rankings_json())
    ordered_iids = [row["instrument_id"] for row in payload["rows"]]

    assert ordered_iids == ["BTC-USD-PERP.DYDX", "NEW-USD-PERP.DYDX"]
    assert payload["rows"][1]["cells"]["volume24h"]["raw"] is None


def test_rankings_json_cold_start_with_no_ranking_message_does_not_raise() -> None:
    """_LATEST_RANKING is None (no message received yet) must render cleanly."""
    _reset_state()
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = _live_row("BTC-USD-PERP.DYDX")

    payload = json.loads(_rankings_json())  # must not raise

    assert [row["instrument_id"] for row in payload["rows"]] == ["BTC-USD-PERP.DYDX"]
    assert payload["rows"][0]["cells"]["volume24h"]["raw"] is None


def test_rankings_json_cells_include_raw_value() -> None:
    _reset_state()
    row = _live_row("BTC-USD-PERP.DYDX")
    row["price"] = 50000.1234
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = row
    dashboard_module._LATEST_RANKING = _ranking_message([_rank_row("BTC-USD-PERP.DYDX", 1)])

    payload = json.loads(_rankings_json())
    cell = payload["rows"][0]["cells"]["price"]

    assert cell["raw"] == 50000.1234


def test_rankings_json_cells_null_out_non_finite_raw_value() -> None:
    """NaN/Infinity are valid Python floats but invalid JSON tokens -- must be nulled, not emitted."""
    _reset_state()
    row = _live_row("BTC-USD-PERP.DYDX")
    row["pct_24h"] = float("nan")
    row["volatility"] = float("inf")
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = row
    dashboard_module._LATEST_RANKING = _ranking_message([_rank_row("BTC-USD-PERP.DYDX", 1)])

    raw_text = _rankings_json()
    assert "NaN" not in raw_text
    assert "Infinity" not in raw_text

    payload = json.loads(raw_text)  # would raise if a literal NaN/Infinity leaked through
    cells = payload["rows"][0]["cells"]
    assert cells["pct_24h"]["raw"] is None
    assert cells["volatility"]["raw"] is None


def test_rankings_json_ranking_stale_false_under_threshold() -> None:
    _reset_state()
    dashboard_module._LATEST_RANKING = _ranking_message([])
    dashboard_module._LATEST_RANKING_RECEIVED_AT = time.time()

    payload = json.loads(_rankings_json())

    assert payload["ranking_stale"] is False


def test_rankings_json_ranking_stale_true_past_threshold() -> None:
    _reset_state()
    dashboard_module._LATEST_RANKING = _ranking_message([])
    dashboard_module._LATEST_RANKING_RECEIVED_AT = (
        time.time() - dashboard_module._RANKING_STALE_SECONDS - 1
    )

    payload = json.loads(_rankings_json())

    assert payload["ranking_stale"] is True


def test_rankings_json_ranking_stale_true_when_no_message_ever_received() -> None:
    _reset_state()

    payload = json.loads(_rankings_json())

    assert payload["ranking_stale"] is True
    assert payload["ranking_age_s"] is None


def test_watchlist_json_handler_reads_instrument_ids_from_latest_ranking() -> None:
    """FR-7 proxy: /api/watchlist is now a thin read of rankings:live's own ranked
    instrument-id list -- no new Redis call, no local re-derivation.
    """
    _reset_state()
    dashboard_module._LATEST_RANKING = _ranking_message([
        _rank_row("BTC-USD-PERP.DYDX", 1),
        _rank_row("ETH-USD-PERP.DYDX", 2, 0.5),
    ])

    response = asyncio.run(watchlist_json_handler(None))
    body = json.loads(response.text)

    assert body["instrument_ids"] == ["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"]


def test_watchlist_json_handler_empty_before_first_ranking_message() -> None:
    _reset_state()

    response = asyncio.run(watchlist_json_handler(None))
    body = json.loads(response.text)

    assert body["instrument_ids"] == []


def test_watchlist_json_handler_skips_malformed_rank_row_missing_instrument_id() -> None:
    _reset_state()
    dashboard_module._LATEST_RANKING = _ranking_message([
        {"rank": 1, "volume24h": 1.0, "volatility_score": None},  # missing instrument_id
        _rank_row("BTC-USD-PERP.DYDX", 2),
    ])

    response = asyncio.run(watchlist_json_handler(None))  # must not raise
    body = json.loads(response.text)

    assert body["instrument_ids"] == ["BTC-USD-PERP.DYDX"]


def test_handle_rankings_message_sets_latest_ranking_and_received_at() -> None:
    _reset_state()
    message = _ranking_message([_rank_row("BTC-USD-PERP.DYDX", 1)])

    before = time.time()
    dashboard_module._handle_rankings_message(message)
    after = time.time()

    assert dashboard_module._LATEST_RANKING == message
    assert before <= dashboard_module._LATEST_RANKING_RECEIVED_AT <= after


def test_handle_rankings_message_missing_ranks_key_is_ignored() -> None:
    _reset_state()

    dashboard_module._handle_rankings_message({"mode": "volume", "updated_at": 1})

    assert dashboard_module._LATEST_RANKING is None


def test_handle_rankings_message_non_list_ranks_is_ignored() -> None:
    _reset_state()

    dashboard_module._handle_rankings_message({"mode": "volume", "ranks": "not-a-list"})

    assert dashboard_module._LATEST_RANKING is None


def test_handle_rankings_message_malformed_preserves_previous_valid_state() -> None:
    """A malformed message must not corrupt _LATEST_RANKING -- the last good ranking is
    kept, not overwritten with garbage.
    """
    _reset_state()
    good_message = _ranking_message([_rank_row("BTC-USD-PERP.DYDX", 1)])
    dashboard_module._handle_rankings_message(good_message)

    dashboard_module._handle_rankings_message({"mode": "volume"})  # missing ranks

    assert dashboard_module._LATEST_RANKING == good_message


def test_rankings_json_ranked_instrument_absent_from_live_fast_uses_fallback_row() -> None:
    """Reverse of the already-tested 'live but unranked' case: an instrument
    ranking_engine has ranked but dashboard's own _LIVE_FAST hasn't seen yet (redis
    ingest race, cold start) still renders via live_by_iid.get(iid, {"instrument_id":
    iid})'s fallback, rather than being dropped or raising a KeyError.
    """
    _reset_state()
    dashboard_module._LATEST_RANKING = _ranking_message([
        _rank_row("BRANDNEW-USD-PERP.DYDX", 1, 12_345.0),
    ])

    payload = json.loads(_rankings_json())

    assert [row["instrument_id"] for row in payload["rows"]] == ["BRANDNEW-USD-PERP.DYDX"]
    assert payload["rows"][0]["cells"]["volume24h"]["raw"] == 12_345.0


def test_rankings_json_malformed_rank_row_missing_instrument_id_is_skipped() -> None:
    """One malformed entry (missing instrument_id) in an otherwise-valid ranks list
    must not crash the render -- it is skipped, the rest of the list still renders.
    """
    _reset_state()
    _LIVE_FAST["BTC-USD-PERP.DYDX"] = _live_row("BTC-USD-PERP.DYDX")
    dashboard_module._LATEST_RANKING = _ranking_message([
        {"rank": 1, "volume24h": 1.0, "volatility_score": None},  # missing instrument_id
        _rank_row("BTC-USD-PERP.DYDX", 2),
    ])

    payload = json.loads(_rankings_json())  # must not raise

    assert [row["instrument_id"] for row in payload["rows"]] == ["BTC-USD-PERP.DYDX"]
