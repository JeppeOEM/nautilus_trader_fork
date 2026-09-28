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
`SecondSampler`: the single write gate (parent spine AD-1, DDD spine AD-D6).

Invariant: a `DydxSecondSnapshot` exists only for an instrument whose `LiveBook` passed all four
checks at this sample -- present, two-sided, uncrossed (after the venue's `CrossedBookPolicy`)
and fresh -- and it holds exactly the trades of its own second. A rejected instrument's trades
are discarded from the live row (never carried onto a later one) and stay archived for the
nightly rebuild. This is the only place the four checks run; no venue can override it.

Pure: no I/O, no logging, no clock. It returns the accepted rows, every rejection and every
requested resync; the `CaptureService` writes, publishes, logs, ledgers and executes those. Two
clocks (story 22.12, `BookTimeSource`): `arrival` samples the book as received with the trades
that arrived since the last sample (`ts_event` = the sample time); `venue` closes exchange second
`second` (`ts_event` = its mid-second) from the trades stamped inside it, the caller having
applied the held deltas up to its end.
"""

from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field

from kernel.fold import fold_trades
from kernel.second_snapshot import DydxSecondSnapshot

from capture.domain.events import BookUncrossed
from capture.domain.live_book import S_NS
from capture.domain.live_book import LiveBook
from capture.domain.policies import BookTimeSource
from capture.domain.policies import CrossedBookPolicy
from capture.domain.trade_intake import TradeIntake
from capture.domain.verdicts import NO_BOOK
from capture.domain.verdicts import Accepted
from capture.domain.verdicts import Crossed
from capture.domain.verdicts import Rejected
from capture.domain.verdicts import SampleVerdict
from capture.domain.verdicts import Stale
from nautilus_trader.model.identifiers import InstrumentId


_HALF_S_NS = 500_000_000


@dataclass
class SampleResult:
    """One sample's outcome: rows to write and publish, rejections, episode ends, resyncs."""

    accepted: list[DydxSecondSnapshot] = field(default_factory=list)
    rejected: list[tuple[str, Rejected]] = field(default_factory=list)
    uncrossed: list[tuple[str, BookUncrossed]] = field(default_factory=list)
    resyncs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SecondSampler:
    """The gate's parameters: book depth, per-instrument staleness, crossed policy, clock."""

    depth: int
    stale_ns: float
    crossed: CrossedBookPolicy
    time_source: BookTimeSource = "arrival"

    def sample(
        self,
        iids: Iterable[str],
        books: Mapping[str, LiveBook],
        intakes: Mapping[str, TradeIntake],
        now_ns: int,
        second: int | None,
        feed_dead: Stale | None,
    ) -> SampleResult:
        """Gate every instrument in `iids` (the collected set) at `now_ns` / exchange `second`."""
        result = SampleResult()
        sampled: set[str] = set()
        for iid in iids:
            sampled.add(iid)
            book = books.get(iid)
            verdict: SampleVerdict
            if book is None:
                verdict, event = NO_BOOK, None
            else:
                verdict, event = book.snapshot_top(
                    self.depth, now_ns, second, self.stale_ns, feed_dead, self.crossed
                )
            if event is not None:
                result.uncrossed.append((iid, event))
            intake = intakes.get(iid)
            if isinstance(verdict, Accepted):
                trades = intake.take_second(second) if intake is not None else []
                result.accepted.append(self._row(iid, verdict, trades, now_ns, second))
                continue
            result.rejected.append((iid, verdict))
            if intake is not None:
                intake.discard(second)
            if (verdict is NO_BOOK and book is not None and book.resync_pending) or (
                isinstance(verdict, Crossed) and verdict.resync
            ):
                result.resyncs.append(iid)
        for iid, intake in intakes.items():
            if iid not in sampled:
                intake.drop_live()  # MEM-02: an unsampled instrument's trades never fold
        return result

    def close_second(
        self, intakes: Mapping[str, TradeIntake], second: int, first_close: bool
    ) -> None:
        """Venue mode: mark `second` closed in every intake (older buckets are late/pre-start)."""
        for intake in intakes.values():
            intake.close(second, first_close)

    @staticmethod
    def _row(
        iid: str, top: Accepted, trade_list: list, now_ns: int, second: int | None
    ) -> DydxSecondSnapshot:
        ts_event = now_ns if second is None else second * S_NS + _HALF_S_NS
        trades = fold_trades(trade_list).snapshot_values()
        return DydxSecondSnapshot(
            instrument_id=InstrumentId.from_str(iid),
            bid_prices=[lv.price.as_double() for lv in top.bids],
            bid_sizes=[lv.size() for lv in top.bids],
            ask_prices=[lv.price.as_double() for lv in top.asks],
            ask_sizes=[lv.size() for lv in top.asks],
            buy_volume=trades.buy_volume,
            sell_volume=trades.sell_volume,
            buy_count=trades.buy_count,
            sell_count=trades.sell_count,
            open_price=trades.open_price,
            high_price=trades.high_price,
            low_price=trades.low_price,
            close_price=trades.close_price,
            ts_event=ts_event,
            ts_init=now_ns,
        )
