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
Shared builders for the ranking tests: a board, a decoded snapshot, a fake clock. Real kernel and
Nautilus objects throughout (TEST-03); nothing here is a mock.
"""

from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot

from nautilus_trader.model.identifiers import InstrumentId
from ranking.application.engine import RankingConfig
from ranking.domain.board import RankingBoard
from ranking.domain.board import RankingsPublisher


NOW_NS = 1_800_000_000_000_000_000
SEC_NS = 1_000_000_000


class FakeClock:
    """An injectable wall clock: `time_ns` for the engine, `time` for the publisher."""

    def __init__(self, ns: int = NOW_NS) -> None:
        self.ns = ns

    def time_ns(self) -> int:
        return self.ns

    def time(self) -> float:
        return self.ns / 1e9


def board(clock: FakeClock | None = None, heartbeat_seconds: float = 5) -> RankingBoard:
    clock = clock or FakeClock()
    return RankingBoard(
        RankingsPublisher(heartbeat_seconds, now_fn=clock.time),
        volatility_lookback_seconds=3600,
        volume_max_age_ns=RankingConfig().volume_max_age_ns,
    )


def snap(
    iid: str,
    bid: float | None = 99.0,
    ask: float | None = 101.0,
    ts_event: int = 0,
    bid_size: float = 1.0,
    ask_size: float = 1.0,
    buy_volume: float = 0.0,
    sell_volume: float = 0.0,
    buy_count: int = 0,
    sell_count: int = 0,
    close_price: float | None = None,
) -> DydxSecondSnapshot:
    """Build a one-level snapshot; `bid=None`/`ask=None` makes that side empty."""
    return make_snapshot(
        instrument_id=InstrumentId.from_str(iid),
        bid_prices=[] if bid is None else [bid],
        bid_sizes=[] if bid is None else [bid_size],
        ask_prices=[] if ask is None else [ask],
        ask_sizes=[] if ask is None else [ask_size],
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        buy_count=buy_count,
        sell_count=sell_count,
        close_price=close_price,
        ts_event=ts_event,
        ts_init=ts_event,
    )


def snap_dict(iid: str, bid: float = 99.0, ask: float = 101.0, ts_event: int = 0) -> dict:
    """Return the `snapshots:raw` wire form of `snap(...)`."""
    return DydxSecondSnapshot.to_dict(snap(iid, bid, ask, ts_event=ts_event))


def mark_fresh(target: RankingBoard, iid: str, received_ns: int) -> None:
    """Stamp freshness only: an empty book feeds no tracker."""
    target.ingest(snap(iid, bid=None, ask=None), received_ns)


def feed_prices(target: RankingBoard, iid: str, prices: list[float], received_ns: int) -> None:
    """Feed mids one second apart (bid == ask == price)."""
    for i, price in enumerate(prices):
        target.ingest(snap(iid, price, price, ts_event=i * SEC_NS), received_ns)


def set_volumes(target: RankingBoard, volumes: dict[str, float], now_ns: int) -> None:
    target.record_volume_poll("test", volumes, now_ns)
    target.refresh_volumes(now_ns)
