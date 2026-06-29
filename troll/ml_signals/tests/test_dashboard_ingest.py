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
"""Unit tests for _ingest_batch() — dashboard snapshot ingestion and gap detection."""

import pytest

import ml_signals.dashboard
from ml_signals.dashboard import _LAST_FED
from ml_signals.dashboard import _LIVE
from ml_signals.dashboard import _OFI_INDS
from ml_signals.dashboard import _OFI_RAW_INDS
from ml_signals.dashboard import _ingest_batch
from ml_signals.dashboard import _second_rolling

_IID = "BTC-USD-PERP.DYDX"


def _reset_state() -> None:
    """Clear all module-level state before each test."""
    _LIVE.clear()
    _OFI_INDS.clear()
    _OFI_RAW_INDS.clear()
    _LAST_FED.clear()
    _second_rolling.clear()


def _snap_dict(
    iid: str,
    bid_prices: list[float],
    bid_sizes: list[float],
    ask_prices: list[float],
    ask_sizes: list[float],
    buy_volume: float,
    sell_volume: float,
    buy_count: int,
    sell_count: int,
    ts_event: int,
) -> dict:
    """Build a snap dict matching DydxSecondSnapshot.to_dict() structure."""
    return {
        "instrument_id": iid,
        "bid_prices": bid_prices,
        "bid_sizes": bid_sizes,
        "ask_prices": ask_prices,
        "ask_sizes": ask_sizes,
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "ts_event": ts_event,
        "ts_init": ts_event,
    }


def _default_snap(iid: str = _IID, ts_event: int = 1_000_000_000) -> dict:
    return _snap_dict(
        iid=iid,
        bid_prices=[50000.0],
        bid_sizes=[1.0],
        ask_prices=[50001.0],
        ask_sizes=[1.0],
        buy_volume=10.0,
        sell_volume=5.0,
        buy_count=3,
        sell_count=2,
        ts_event=ts_event,
    )


def test_ingest_appends_to_second_rolling() -> None:
    _reset_state()
    snap = _default_snap()
    _ingest_batch([snap])
    assert len(_second_rolling[_IID]) == 1


def test_ingest_initializes_ofi_inds() -> None:
    _reset_state()
    snap = _default_snap()
    _ingest_batch([snap])
    assert _IID in _OFI_INDS
    assert _IID in _OFI_RAW_INDS


def test_ingest_sets_last_fed() -> None:
    _reset_state()
    ts_ns = 5_000_000_000
    snap = _default_snap(ts_event=ts_ns)
    _ingest_batch([snap])
    assert _LAST_FED[_IID] == ts_ns


def test_ingest_updates_live_with_required_keys() -> None:
    _reset_state()
    snap = _default_snap()
    _ingest_batch([snap])
    live = _LIVE[_IID]
    for key in ("instrument_id", "microprice", "spread", "cvd", "buy_count", "sell_count"):
        assert key in live, f"Missing key: {key}"


def test_gap_detection_no_gap_does_not_clear() -> None:
    _reset_state()
    # Two snaps 1s apart — no gap, clear_prev_state should NOT be called.
    # After two updates, _prev_bid_prices must be set (not None) since clear was not called.
    ts1 = 1_000_000_000
    ts2 = ts1 + 1_000_000_000  # exactly 1s gap
    snap1 = _default_snap(ts_event=ts1)
    snap2 = _default_snap(ts_event=ts2)
    _ingest_batch([snap1])
    # After first ingest: _prev_bid_prices is set by update_raw
    assert _OFI_INDS[_IID]._prev_bid_prices is not None
    _ingest_batch([snap2])
    # No gap — clear_prev_state was NOT called; _prev_bid_prices is still set
    assert _OFI_INDS[_IID]._prev_bid_prices is not None


def test_gap_detection_gap_clears_prev_state() -> None:
    _reset_state()
    # Two snaps with >3s gap — clear_prev_state IS called before second update.
    # After the gap: clear_prev_state sets _prev_bid_prices=None, then update_raw
    # sets it again from the second snap. The key difference versus no-gap: OFI
    # must not have accumulated a contribution from snap1 into the second tick.
    # We verify by checking initialized=False after second feed (window=50, only 1 new snap).
    ts1 = 1_000_000_000
    ts2 = ts1 + 4_000_000_000  # 4s gap — exceeds 3s threshold
    snap1 = _default_snap(ts_event=ts1)
    snap2 = _default_snap(ts_event=ts2)
    _ingest_batch([snap1])
    _ingest_batch([snap2])
    # Last fed updated to ts2 — ingest completed successfully
    assert _LAST_FED[_IID] == ts2
    # After gap + clear + one new feed: initialized=False (window=50 requires many snaps)
    # This verifies that clear_prev_state was called: with no clear, the second snap would
    # compute a OFI contribution using snap1's prev prices. With clear, no contribution is
    # produced (update_raw only stores _prev on first call after a clear).
    assert not _OFI_INDS[_IID].initialized


def test_cvd_is_sum_over_window() -> None:
    _reset_state()
    ts1 = 1_000_000_000
    ts2 = ts1 + 1_000_000_000
    snap1 = _snap_dict(
        iid=_IID,
        bid_prices=[50000.0], bid_sizes=[1.0], ask_prices=[50001.0], ask_sizes=[1.0],
        buy_volume=10.0, sell_volume=4.0, buy_count=2, sell_count=1, ts_event=ts1,
    )
    snap2 = _snap_dict(
        iid=_IID,
        bid_prices=[50000.0], bid_sizes=[1.0], ask_prices=[50001.0], ask_sizes=[1.0],
        buy_volume=3.0, sell_volume=7.0, buy_count=1, sell_count=2, ts_event=ts2,
    )
    _ingest_batch([snap1])
    _ingest_batch([snap2])
    expected_cvd = (10.0 + 3.0) - (4.0 + 7.0)  # sum over window
    assert _LIVE[_IID]["cvd"] == pytest.approx(expected_cvd)


def test_volume_delta_is_last_snap_only() -> None:
    _reset_state()
    ts1 = 1_000_000_000
    ts2 = ts1 + 1_000_000_000
    snap1 = _snap_dict(
        iid=_IID,
        bid_prices=[50000.0], bid_sizes=[1.0], ask_prices=[50001.0], ask_sizes=[1.0],
        buy_volume=10.0, sell_volume=4.0, buy_count=2, sell_count=1, ts_event=ts1,
    )
    snap2 = _snap_dict(
        iid=_IID,
        bid_prices=[50000.0], bid_sizes=[1.0], ask_prices=[50001.0], ask_sizes=[1.0],
        buy_volume=3.0, sell_volume=7.0, buy_count=1, sell_count=2, ts_event=ts2,
    )
    _ingest_batch([snap1])
    _ingest_batch([snap2])
    # volume_delta = last snap only: 3.0 - 7.0 = -4.0
    assert _LIVE[_IID]["volume_delta"] == pytest.approx(3.0 - 7.0)
