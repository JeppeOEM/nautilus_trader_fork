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
Unit tests for dashboard as a pure reader of rankings:live (Story 1.8, Task 8; SSOT-02
migration).

Every rankings-table/coin-panel metric (rank, volume24h, volatility_score, OFI/OBI,
microprice, spread, cvd, price, pct_1h/pct_24h/volatility, ...) is now computed
exclusively by ranking_engine and arrives fully-formed on each rankings:live rank
entry -- dashboard._rankings_json() just formats whatever's already on that entry, it
never merges in a locally-computed value or re-sorts. See ranking_engine/tests/
test_engine.py for the relocated ranking/metric-computation tests.
"""

import asyncio
import json
import time

import ml_signals.dashboard as dashboard_module
from ml_signals.dashboard import _ind_rolling
from ml_signals.dashboard import _rankings_json
from ml_signals.dashboard import watchlist_json_handler


def _reset_state() -> None:
    dashboard_module._LATEST_RANKING = None
    dashboard_module._LATEST_RANKING_RECEIVED_AT = 0.0
    _ind_rolling.clear()


def _ranking_message(ranks: list[dict], mode: str = "volume", updated_at: int | None = None) -> dict:
    return {"mode": mode, "updated_at": updated_at if updated_at is not None else time.time_ns(), "ranks": ranks}


def _rank_row(iid: str, rank: int, **overrides: object) -> dict:
    """A fully-formed rankings:live rank entry -- every RANKING_COLS key present (as
    None unless overridden), matching what ranking_engine actually publishes now.
    """
    row = {
        "instrument_id": iid, "rank": rank, "volume24h": 1.0, "volatility_score": None,
        "ofi_10_z": None, "obi_10": None, "obi_5": None, "obi_3": None,
        "cvd": None, "spread": None, "microprice_lean": None, "volume_delta": None,
        "buy_count": 0, "sell_count": 0, "price": None,
        "pct_1h": None, "pct_24h": None, "volatility": None,
    }
    row.update(overrides)
    return row


def test_rankings_json_renders_ranks_in_engines_exact_order() -> None:
    """Rows follow rankings:live's own order -- never re-sorted client- or server-side."""
    _reset_state()
    dashboard_module._LATEST_RANKING = _ranking_message([
        _rank_row("BTC-USD-PERP.DYDX", 1, volume24h=50_000_000.0),
        _rank_row("SHIB-USD-PERP.DYDX", 2, volume24h=100.0),
    ])

    payload = json.loads(_rankings_json())
    ordered_iids = [row["instrument_id"] for row in payload["rows"]]

    assert ordered_iids == ["BTC-USD-PERP.DYDX", "SHIB-USD-PERP.DYDX"]


def test_rankings_json_volume24h_cell_comes_from_ranks_entry() -> None:
    _reset_state()
    dashboard_module._LATEST_RANKING = _ranking_message([
        _rank_row("BTC-USD-PERP.DYDX", 1, volume24h=50_000_000.0),
    ])

    payload = json.loads(_rankings_json())

    assert payload["rows"][0]["cells"]["volume24h"]["raw"] == 50_000_000.0


def test_rankings_json_cold_start_with_no_ranking_message_does_not_raise() -> None:
    """_LATEST_RANKING is None (no message received yet) must render cleanly."""
    _reset_state()

    payload = json.loads(_rankings_json())  # must not raise

    assert payload["rows"] == []
    assert payload["ranking_stale"] is True
    assert payload["stale_instrument_ids"] == []


def test_rankings_json_carries_stale_instrument_ids_from_ranking_engine() -> None:
    _reset_state()
    message = _ranking_message([_rank_row("BTC-USD-PERP.DYDX", 1)])
    message["stale_instrument_ids"] = ["SOL-USD-PERP.DYDX"]
    dashboard_module._LATEST_RANKING = message

    payload = json.loads(_rankings_json())

    assert payload["stale_instrument_ids"] == ["SOL-USD-PERP.DYDX"]


def test_rankings_json_cells_include_raw_value() -> None:
    _reset_state()
    dashboard_module._LATEST_RANKING = _ranking_message([
        _rank_row("BTC-USD-PERP.DYDX", 1, price=50000.1234),
    ])

    payload = json.loads(_rankings_json())
    cell = payload["rows"][0]["cells"]["price"]

    assert cell["raw"] == 50000.1234


def test_rankings_json_cells_null_out_non_finite_raw_value() -> None:
    """NaN/Infinity are valid Python floats but invalid JSON tokens -- must be nulled, not emitted."""
    _reset_state()
    dashboard_module._LATEST_RANKING = _ranking_message([
        _rank_row("BTC-USD-PERP.DYDX", 1, pct_24h=float("nan"), volatility=float("inf")),
    ])

    raw_text = _rankings_json()
    assert "NaN" not in raw_text
    assert "Infinity" not in raw_text

    payload = json.loads(raw_text)  # would raise if a literal NaN/Infinity leaked through
    cells = payload["rows"][0]["cells"]
    assert cells["pct_24h"]["raw"] is None
    assert cells["volatility"]["raw"] is None


def test_rankings_json_malformed_rank_row_missing_instrument_id_is_skipped() -> None:
    """One malformed entry (missing instrument_id) in an otherwise-valid ranks list
    must not crash the render -- it is skipped, the rest of the list still renders.
    """
    _reset_state()
    dashboard_module._LATEST_RANKING = _ranking_message([
        {"rank": 1, "volume24h": 1.0, "volatility_score": None},  # missing instrument_id
        _rank_row("BTC-USD-PERP.DYDX", 2),
    ])

    payload = json.loads(_rankings_json())  # must not raise

    assert [row["instrument_id"] for row in payload["rows"]] == ["BTC-USD-PERP.DYDX"]


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
        _rank_row("ETH-USD-PERP.DYDX", 2, volume24h=0.5),
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


def test_handle_rankings_message_feeds_ind_rolling_from_rank_entry_fields() -> None:
    """SSOT-02: the /coin/{id} signal chart's rolling window is now fed straight from
    each rank entry's own ofi_10_z/obi_10/microprice_lean -- not locally recomputed.
    """
    _reset_state()
    updated_at_ns = 1_700_000_000_000_000_000
    message = _ranking_message(
        [_rank_row("BTC-USD-PERP.DYDX", 1, ofi_10_z=0.5, obi_10=0.6, microprice_lean=0.01)],
        updated_at=updated_at_ns,
    )

    dashboard_module._handle_rankings_message(message)

    entry = _ind_rolling["BTC-USD-PERP.DYDX"][-1]
    assert entry["ts"] == updated_at_ns // 1_000_000
    assert entry["ofi_10_z"] == 0.5
    assert entry["obi_10"] == 0.6
    assert entry["lean"] == 0.01
