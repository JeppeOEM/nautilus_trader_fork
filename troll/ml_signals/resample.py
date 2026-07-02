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
"""Resample DydxSecondSnapshot collections into wider timeframe aggregates."""

from dataclasses import dataclass

from nautilus_trader.model.identifiers import InstrumentId

from dydx_collector.second_snapshot import DydxSecondSnapshot


@dataclass
class ResampledSnapshot:
    """Aggregated view of N consecutive DydxSecondSnapshots over a fixed time bucket."""

    instrument_id: InstrumentId
    ts_open: int          # bucket start (ns)
    ts_close: int         # ts_event of last snapshot in bucket
    bid_prices: list[float]
    bid_sizes: list[float]
    ask_prices: list[float]
    ask_sizes: list[float]
    buy_volume: float
    sell_volume: float
    buy_count: int
    sell_count: int
    snapshot_count: int
    # Derived signals (spread, microprice, OFI) are NOT stored here per SIGNAL-01.
    # Compute them from bid_prices/bid_sizes/ask_prices/ask_sizes via indicators.py.


def resample_snapshots(
    snapshots: list[DydxSecondSnapshot],
    period_seconds: float,
) -> list[ResampledSnapshot]:
    """
    Aggregate consecutive DydxSecondSnapshots into `period_seconds`-wide buckets.

    All snapshots must share the same instrument_id — raises ValueError on mismatch.
    Book state (bid/ask levels) comes from the last snapshot in each bucket.
    Volume fields are summed.
    """
    if not snapshots:
        return []
    instrument_id = snapshots[0].instrument_id
    for snap in snapshots:
        if snap.instrument_id != instrument_id:
            raise ValueError(
                f"resample_snapshots expects a single instrument; "
                f"got {instrument_id} and {snap.instrument_id}"
            )

    period_ns = int(period_seconds * 1_000_000_000)
    buckets: dict[int, list[DydxSecondSnapshot]] = {}
    for snap in sorted(snapshots, key=lambda s: s.ts_event):
        key = (snap.ts_event // period_ns) * period_ns
        buckets.setdefault(key, []).append(snap)

    return [
        _aggregate(bucket_ts, snaps)
        for bucket_ts, snaps in sorted(buckets.items())
    ]


def _aggregate(bucket_ts: int, snaps: list[DydxSecondSnapshot]) -> ResampledSnapshot:
    last = snaps[-1]
    return ResampledSnapshot(
        instrument_id=last.instrument_id,
        ts_open=bucket_ts,
        ts_close=last.ts_event,
        bid_prices=last.bid_prices,
        bid_sizes=last.bid_sizes,
        ask_prices=last.ask_prices,
        ask_sizes=last.ask_sizes,
        buy_volume=sum(s.buy_volume for s in snaps),
        sell_volume=sum(s.sell_volume for s in snaps),
        buy_count=sum(s.buy_count for s in snaps),
        sell_count=sum(s.sell_count for s in snaps),
        snapshot_count=len(snaps),
    )
