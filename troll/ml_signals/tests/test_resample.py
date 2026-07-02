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
"""Unit tests for resample_snapshots: bucketing, volume aggregation, instrument guard."""

import pytest
from nautilus_trader.model.identifiers import InstrumentId

from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals.resample import resample_snapshots

_IID = InstrumentId.from_str("BTC-USD-PERP.DYDX")
_S = 1_000_000_000  # 1 second in nanoseconds


def _snap(
    ts: int,
    bid_price: float = 100.0,
    bid_size: float = 5.0,
    ask_price: float = 101.0,
    ask_size: float = 5.0,
    buy_volume: float = 1.0,
    sell_volume: float = 1.0,
    buy_count: int = 1,
    sell_count: int = 1,
) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=_IID,
        bid_prices=[bid_price],
        bid_sizes=[bid_size],
        ask_prices=[ask_price],
        ask_sizes=[ask_size],
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        buy_count=buy_count,
        sell_count=sell_count,
        ts_event=ts,
        ts_init=ts,
    )


def test_single_bucket_three_snapshots() -> None:
    snaps = [_snap(ts=i * _S) for i in range(3)]
    result = resample_snapshots(snaps, period_seconds=10.0)
    assert len(result) == 1
    assert result[0].snapshot_count == 3
    assert result[0].ts_open == 0
    assert result[0].ts_close == 2 * _S


def test_volumes_are_summed() -> None:
    snaps = [_snap(ts=i * _S, buy_volume=2.0, sell_volume=3.0, buy_count=2, sell_count=1) for i in range(4)]
    result = resample_snapshots(snaps, period_seconds=60.0)
    assert len(result) == 1
    assert result[0].buy_volume == 8.0
    assert result[0].sell_volume == 12.0
    assert result[0].buy_count == 8
    assert result[0].sell_count == 4


def test_splits_into_two_buckets() -> None:
    snaps = (
        [_snap(ts=i * _S) for i in range(60)] +
        [_snap(ts=(60 + i) * _S) for i in range(60)]
    )
    result = resample_snapshots(snaps, period_seconds=60.0)
    assert len(result) == 2
    assert result[0].snapshot_count == 60
    assert result[1].snapshot_count == 60


def test_book_state_is_from_last_snapshot() -> None:
    s1 = _snap(ts=0, bid_price=100.0, ask_price=101.0)
    s2 = _snap(ts=_S, bid_price=102.0, ask_price=103.0)
    result = resample_snapshots([s1, s2], period_seconds=60.0)
    assert result[0].bid_prices[0] == 102.0
    assert result[0].ask_prices[0] == 103.0


def test_empty_snapshots_returns_empty() -> None:
    assert resample_snapshots([], period_seconds=60.0) == []


def test_mixed_instruments_raises() -> None:
    eth_iid = InstrumentId.from_str("ETH-USD-PERP.DYDX")
    s1 = _snap(ts=0)
    s2 = DydxSecondSnapshot(
        instrument_id=eth_iid,
        bid_prices=[2000.0],
        bid_sizes=[1.0],
        ask_prices=[2001.0],
        ask_sizes=[1.0],
        buy_volume=1.0,
        sell_volume=1.0,
        buy_count=1,
        sell_count=1,
        ts_event=_S,
        ts_init=_S,
    )
    with pytest.raises(ValueError, match="single instrument"):
        resample_snapshots([s1, s2], period_seconds=60.0)
