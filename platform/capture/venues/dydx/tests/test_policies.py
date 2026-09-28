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
dYdX's policy values (DATA-04, Story 26.1): the level tagger and the uncross ladder, pure --
real `OrderBook`s and deltas, no collector, time passed in.
"""

from capture.domain.policies import LevelTags
from capture.domain.verdicts import ResyncRequested
from capture.domain.verdicts import StillCrossed
from capture.domain.verdicts import Uncrossed
from capture.venues.dydx.policies import UNCROSS_MAX_STEPS
from capture.venues.dydx.policies import DydxLevelTagger
from capture.venues.dydx.policies import DydxUncrossPolicy
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_IID = InstrumentId.from_str("BTC-USD-PERP.DYDX")
_S = 1_000_000_000
_T = 1_790_000_000 * _S


def _delta(
    side: OrderSide, price: float, seq: int, action: BookAction = BookAction.ADD
) -> OrderBookDelta:
    order = BookOrder(side, Price(price, 1), Quantity(1.0, 1), 0)
    return OrderBookDelta(_IID, action, order, 0, seq, 1, 1)


def _tagged_book(levels: list[tuple[OrderSide, float, int]]) -> tuple[OrderBook, LevelTags]:
    book, tagger = OrderBook(_IID, BookType.L2_MBP), DydxLevelTagger()
    tags: LevelTags = {}
    for side, price, seq in levels:
        delta = _delta(side, price, seq)
        book.apply_delta(delta)
        tagger.tag(tags, delta)
    return book, tags


def test_the_tagger_reports_the_side_it_touched() -> None:
    tags: LevelTags = {}
    tagger = DydxLevelTagger()
    assert tagger.tag(tags, _delta(OrderSide.BUY, 100.0, 7)) == OrderSide.BUY
    assert tags == {(OrderSide.BUY, 100.0): 7}
    assert tagger.tag(tags, OrderBookDelta.clear(_IID, 8, 1, 1)) == OrderSide.NO_ORDER_SIDE
    assert tags == {}


def test_a_tagged_cross_is_uncrossed_by_deleting_the_older_level() -> None:
    book, tags = _tagged_book([(OrderSide.BUY, 101.0, 1), (OrderSide.SELL, 100.0, 2)])
    verdict = DydxUncrossPolicy(10 * _S).step(book, tags, None, _T)
    assert isinstance(verdict, Uncrossed)
    (dropped,) = verdict.dropped
    assert (dropped.side, dropped.price, dropped.side_now_empty) == (OrderSide.BUY, 101.0, True)
    assert (OrderSide.BUY, 101.0) not in tags


def test_an_untagged_cross_escalates_only_past_the_grace_window() -> None:
    book = OrderBook(_IID, BookType.L2_MBP)
    for delta in (_delta(OrderSide.BUY, 101.0, 1), _delta(OrderSide.SELL, 100.0, 2)):
        book.apply_delta(delta)
    policy = DydxUncrossPolicy(10 * _S)
    assert policy.step(book, {}, None, _T) == StillCrossed(_T)
    assert policy.step(book, {}, _T, _T + 10 * _S + 1) == ResyncRequested(_T)
    assert not policy.crossing_is_corruption  # a dYdX cross is architectural (DATA-04)


def test_a_cross_deeper_than_the_step_cap_falls_back_with_what_it_dropped() -> None:
    bids = [(OrderSide.BUY, 110.0 - n, 1) for n in range(UNCROSS_MAX_STEPS + 1)]
    book, tags = _tagged_book([*bids, (OrderSide.SELL, 100.0, 2)])
    verdict = DydxUncrossPolicy(10 * _S).step(book, tags, None, _T)
    assert isinstance(verdict, StillCrossed)
    assert len(verdict.dropped) == UNCROSS_MAX_STEPS
