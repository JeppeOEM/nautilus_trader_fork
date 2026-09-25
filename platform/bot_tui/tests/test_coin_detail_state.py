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
Tests for bot_tui.coin_detail_state -- Story 4.3, AC1.

Pure-logic tests only (state transitions + per-instrument snapshot filtering). The
actual _redis_listener coroutine's connection code is covered by the manual smoke
check, matching ranking_state.py's own established test-boundary precedent.

Fixture rows are real `snapshots:raw` entries (full Nautilus ids): since Story 24.2 each batch
is decoded through `DydxSecondSnapshot.from_dict` (`views.coin_detail.snapshot_for`), which the
collector's `to_dict()` output always satisfies and a venue-less id does not.
"""

from bot_tui import coin_detail_state


def _reset() -> None:
    coin_detail_state.close_coin()


def _snapshot(instrument_id: str, ts_event: int = 1, bid_price: float = 100.0) -> dict:
    return {
        "instrument_id": instrument_id,
        "bid_prices": [bid_price],
        "bid_sizes": [1.0],
        "ask_prices": [bid_price + 0.5],
        "ask_sizes": [1.0],
        "buy_volume": 1.0,
        "sell_volume": 1.0,
        "buy_count": 1,
        "sell_count": 1,
        "ts_event": ts_event,
        "ts_init": ts_event,
    }


def test_no_coin_open_leaves_state_untouched() -> None:
    _reset()
    coin_detail_state._handle_snapshot_batch([_snapshot("BTC-USD-PERP.DYDX")])
    assert coin_detail_state._LATEST_SNAPSHOT is None


def test_open_coin_then_batch_only_matching_row_is_stored() -> None:
    _reset()
    coin_detail_state.open_coin("BTC-USD-PERP.DYDX")
    coin_detail_state._handle_snapshot_batch(
        [_snapshot("ETH-USD-PERP.DYDX"), _snapshot("BTC-USD-PERP.DYDX")]
    )
    assert coin_detail_state._LATEST_SNAPSHOT is not None
    assert coin_detail_state._LATEST_SNAPSHOT.instrument_id.value == "BTC-USD-PERP.DYDX"


def test_open_coin_then_batch_with_no_matching_row_leaves_snapshot_none() -> None:
    _reset()
    coin_detail_state.open_coin("BTC-USD-PERP.DYDX")
    coin_detail_state._handle_snapshot_batch([_snapshot("ETH-USD-PERP.DYDX")])
    assert coin_detail_state._LATEST_SNAPSHOT is None


def test_malformed_batch_not_list_shaped_is_ignored() -> None:
    _reset()
    coin_detail_state.open_coin("BTC-USD-PERP.DYDX")
    coin_detail_state._handle_snapshot_batch({"not": "a list"})
    assert coin_detail_state._LATEST_SNAPSHOT is None


def test_close_coin_resets_every_piece_of_state() -> None:
    _reset()
    coin_detail_state.open_coin("BTC-USD-PERP.DYDX")
    coin_detail_state._handle_snapshot_batch([_snapshot("BTC-USD-PERP.DYDX")])
    coin_detail_state.close_coin()
    assert coin_detail_state._CURRENT_INSTRUMENT_ID is None
    assert coin_detail_state._LATEST_SNAPSHOT is None
    assert coin_detail_state._LATEST_SNAPSHOT_RECEIVED_AT == 0.0
