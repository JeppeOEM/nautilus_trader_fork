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
dYdX's capture policies (DATA-04, DDD spine AD-D6): per-level message-id tagging and the uncross
ladder, as pure values the core's `LiveBook` runs. Was `uncross.py` plus two `DydxCollector` hook
overrides (`_apply_deltas`, `_handle_crossed_book`) until Story 26.1.

A crossed dYdX v4 book is expected and architectural (no central book; each validator matches
against its own mempool), so a cross is not corruption and is not ledgered per episode. The
correct resolution is dYdX's own Indexer's (Roundtable's `uncross-orderbook.ts`): tag every price
level with the message id (`OrderBookDelta.sequence`, the WS envelope's connection-global
`message_id`) of the update that last touched it, and when crossed delete only the level with the
older tag -- never wipe the book. A forced resync (DATA-03) is the fallback for a level that
cannot be tagged (right after a resubscribe) or a book still crossed past the grace window.

Evidence for the grace window (`CoreConfig.crossed_resync_seconds`, default 10 s, provisional):
2026-09-04, cross-checked live against dYdX's REST book over ~25 episodes, one side of our book
could hold a stale level no later delta ever touched again (category 2: a lost update, never
self-healing, so waiting it out is pure downside); 2026-09-06, a reference client with zero shared
code showed a genuine venue cross (category 1) byte-identical to ours for a full 3 s, which the old
3 s timer force-resynced for nothing. Duration alone cannot tell the two apart, so the window was
widened 3 s -> 10 s while Story 5.1 gathers duration data (`platform/.planning/debug/
crossed-book-root-cause.md`).
"""

from dataclasses import dataclass
from typing import ClassVar

from capture.domain.policies import LevelTags
from capture.domain.verdicts import CrossVerdict
from capture.domain.verdicts import DroppedLevel
from capture.domain.verdicts import ResyncRequested
from capture.domain.verdicts import StillCrossed
from capture.domain.verdicts import Uncrossed
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


# Defensive bound on how many stale levels one step deletes before falling back to the ladder: a
# genuine episode is one or two levels deep; this caps a pathological, many-levels-deep cross.
UNCROSS_MAX_STEPS: int = 5


class DydxLevelTagger:
    """
    Invariant: `tags` holds, per (side, price) level present in the book, the message id of the
    update that last touched it -- a Clear drops every tag, a delete drops that level's. This is a
    *per level* use of dYdX's connection-global `sequence`, not per-instrument gap detection (which
    false-positives on every interleaved message and was removed in commit 944891bbba, DATA-08).
    """

    def tag(self, tags: LevelTags, delta: OrderBookDelta) -> OrderSide:
        if delta.is_clear:
            tags.clear()
            return OrderSide.NO_ORDER_SIDE
        order = delta.order
        side = order.side
        key = (side, order.price.as_double())
        if delta.is_delete:
            tags.pop(key, None)
        else:
            tags[key] = delta.sequence
        return side


def _stale_side(
    book: OrderBook, bid: Price, ask: Price, bid_seq: int, ask_seq: int
) -> tuple[OrderSide, Price, int, int]:
    """
    Pick the stale side per dYdX's rule: the strictly older (smaller) message id, or on a tie the
    side with the smaller resting size. Returns (side, price, its id, the other side's id).
    """
    if bid_seq == ask_seq:
        bid_stale = book.best_bid_size().as_double() <= book.best_ask_size().as_double()
    else:
        bid_stale = bid_seq < ask_seq
    if bid_stale:
        return OrderSide.BUY, bid, bid_seq, ask_seq
    return OrderSide.SELL, ask, ask_seq, bid_seq


def _delete(book: OrderBook, side: OrderSide, price: Price, sequence: int, now_ns: int) -> None:
    """Apply a synthetic DELETE exactly as a dYdX-sent deletion is -- never a book wipe."""
    order = BookOrder(side=side, price=price, size=Quantity(0.0, price.precision), order_id=0)
    book.apply_delta(
        OrderBookDelta(
            instrument_id=book.instrument_id,
            action=BookAction.DELETE,
            order=order,
            flags=0,
            sequence=sequence,
            ts_event=now_ns,
            ts_init=now_ns,
        )
    )


def uncross_step(book: OrderBook, tags: LevelTags, now_ns: int) -> DroppedLevel | None:
    """
    One active-uncrossing step: delete the stale side's best level. None when the book is not
    crossed or either best level is untagged (cannot arbitrate: the caller falls back to the
    ladder, leaving book and tags untouched).
    """
    bid, ask = book.best_bid_price(), book.best_ask_price()
    if bid is None or ask is None or bid.as_double() < ask.as_double():
        return None
    bid_seq = tags.get((OrderSide.BUY, bid.as_double()))
    ask_seq = tags.get((OrderSide.SELL, ask.as_double()))
    if bid_seq is None or ask_seq is None:
        return None
    side, price, stale_seq, other_seq = _stale_side(book, bid, ask, bid_seq, ask_seq)
    _delete(book, side, price, max(bid_seq, ask_seq), now_ns)
    tags.pop((side, price.as_double()), None)
    now_empty = (book.best_bid_price() if side == OrderSide.BUY else book.best_ask_price()) is None
    return DroppedLevel(side, price.as_double(), stale_seq, other_seq, now_empty)


def _uncrossed(book: OrderBook) -> bool:
    bid, ask = book.best_bid_price(), book.best_ask_price()
    return bid is None or ask is None or bid.as_double() < ask.as_double()


@dataclass(frozen=True)
class DydxUncrossPolicy:
    """
    DATA-04's ladder: active uncross first (up to `UNCROSS_MAX_STEPS` stale-level deletes), then
    skip the sample while the episode lasts, then -- past `resync_after_ns` -- the DATA-03
    fallback. Invariant: a book this policy calls `Uncrossed` is uncrossed (or one-sided), and a
    resync is never asked for inside the grace window.
    """

    resync_after_ns: int
    crossing_is_corruption: ClassVar[bool] = False

    def step(
        self, book: OrderBook, tags: LevelTags, since_ns: int | None, now_ns: int
    ) -> CrossVerdict:
        dropped: list[DroppedLevel] = []
        for _ in range(UNCROSS_MAX_STEPS):
            level = uncross_step(book, tags, now_ns)
            if level is None:
                break
            dropped.append(level)
            if _uncrossed(book):
                return Uncrossed(tuple(dropped))
        since = now_ns if since_ns is None else since_ns
        if now_ns - since > self.resync_after_ns:
            return ResyncRequested(since, tuple(dropped))
        return StillCrossed(since, tuple(dropped))
