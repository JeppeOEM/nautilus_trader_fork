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
dYdX's crossed-book handling (DATA-04): per-level message-id uncrossing + the
escalation ladder, isolated from the venue-neutral write gate in `collector_core`.

`DydxCollector` overrides exactly two core hooks (`_apply_deltas`, `_handle_crossed_book`)
and delegates the latter here. These are plain functions over the state they touch
(`book`, `level_msg_id`, `crossed_since_ns`, `crossed_prices`, per-side delta timestamps)
so the algorithm is testable without a collector.
"""

import json
import logging
import time
from collections.abc import Awaitable
from collections.abc import Callable

from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


logger = logging.getLogger(__name__)
# Distinct logger name (not a new file/volume) so a steady-state desync escalation is
# separable from routine collector WARNING lines, while staying stdout/Dozzle-based.
critical_logger = logging.getLogger("dydx_collector.critical")

LevelMsgIds = dict[tuple[OrderSide, float], int]

# Story 5.2 / DATA-04: defensive bound on how many stale levels `_uncross_step` will
# drop in one _handle_crossed_book call before giving up and falling back to the
# existing resync path. A genuine crossed-book episode is normally one or two levels
# deep -- this is a safety cap against a pathological/many-levels-deep cross, not a
# value expected to be hit in practice.
_UNCROSS_MAX_STEPS: int = 5


def _resolve_stale_level(
    book: OrderBook,
    bid: Price,
    ask: Price,
    bid_seq: int,
    ask_seq: int,
) -> tuple[OrderSide, Price, int, int]:
    """
    Which side of a crossed book is stale, per dYdX's own tie-break rule (DATA-04):
    the level with the strictly older (smaller) message-id, or on a tie, the side
    with the smaller resting size (dYdX's own documented tie-break).

    Returns (stale_side, stale_price, stale_seq, other_seq) -- extracted from
    Collector._uncross_step to keep that method under this project's ~30-line
    guideline (READ-01).
    """
    if bid_seq == ask_seq:
        bid_stale = book.best_bid_size().as_double() <= book.best_ask_size().as_double()
    else:
        bid_stale = bid_seq < ask_seq
    if bid_stale:
        return OrderSide.BUY, bid, bid_seq, ask_seq
    return OrderSide.SELL, ask, ask_seq, bid_seq


def _apply_stale_delete(
    book: OrderBook, stale_side: OrderSide, stale_price: Price, sequence: int
) -> None:
    """
    Synthetic BookAction.DELETE for the stale level (DATA-04) -- applied identically
    to how a real dYdX-sent deletion is applied, never a full book wipe.
    """
    delete_order = BookOrder(
        side=stale_side,
        price=stale_price,
        size=Quantity(0.0, stale_price.precision),
        order_id=0,
    )
    now_ns = time.time_ns()
    book.apply_delta(
        OrderBookDelta(
            instrument_id=book.instrument_id,
            action=BookAction.DELETE,
            order=delete_order,
            flags=0,
            sequence=sequence,
            ts_event=now_ns,
            ts_init=now_ns,
        )
    )


def _log_uncross_result(
    iid: str,
    stale_side: OrderSide,
    stale_price: Price,
    stale_seq: int,
    other_seq: int,
    side_now_empty: bool,
) -> None:
    """
    DATA-02: a one-sided book after uncrossing is a real data-loss event, not routine
    self-healing -- must not be logged identically to the benign case, or this failure
    class stays invisible.
    """
    if side_now_empty:
        logger.warning(
            "Crossed book for %s uncrossed by dropping the LAST remaining %s level "
            "@ %.6f (msg_id %d, surviving side msg_id %d) -- book is now one-sided",
            iid,
            stale_side.name,
            stale_price.as_double(),
            stale_seq,
            other_seq,
        )
    else:
        logger.info(
            "Crossed book for %s actively uncrossed: dropped stale %s @ %.6f "
            "(msg_id %d, surviving side msg_id %d)",
            iid,
            stale_side.name,
            stale_price.as_double(),
            stale_seq,
            other_seq,
        )


def uncross_step(iid: str, book: OrderBook, level_msg_id: LevelMsgIds) -> bool:
    """
    One active-uncrossing correction step (DATA-04, Story 5.2).

    Ports dYdX's own Indexer remediation (Roundtable's `uncross-orderbook.ts`)
    instead of forcing a full resync: drop only the stale side of a crossed book,
    using each level's tagged message-id (`level_msg_id`, set in
    `DydxCollector._apply_deltas`) to decide which side is stale -- the level with the
    strictly older (smaller) message-id, or on a tie, the side with the smaller resting
    size (dYdX's own documented tie-break). Applies a synthetic `BookAction.DELETE` via
    `book.apply_delta()`, identical to how a real dYdX-sent deletion is applied.

    Returns True if a level was dropped (caller should re-check crossed state and
    may loop). Returns False if the book isn't crossed, or either level lacks a
    tag (can't arbitrate -- caller falls back to the existing resync path).
    """
    bid, ask = book.best_bid_price(), book.best_ask_price()
    if bid is None or ask is None or bid.as_double() < ask.as_double():
        return False
    bid_seq = level_msg_id.get((OrderSide.BUY, bid.as_double()))
    ask_seq = level_msg_id.get((OrderSide.SELL, ask.as_double()))
    if bid_seq is None or ask_seq is None:
        return False

    stale_side, stale_price, stale_seq, other_seq = _resolve_stale_level(
        book, bid, ask, bid_seq, ask_seq
    )
    _apply_stale_delete(book, stale_side, stale_price, max(bid_seq, ask_seq))
    level_msg_id.pop((stale_side, stale_price.as_double()), None)

    side_now_empty = (
        book.best_bid_price() is None
        if stale_side == OrderSide.BUY
        else book.best_ask_price() is None
    )
    _log_uncross_result(iid, stale_side, stale_price, stale_seq, other_seq, side_now_empty)
    return True


def handle_uncrossed_book(
    iid: str,
    book: OrderBook,
    now_ns: int,
    crossed_since_ns: dict[str, int],
    crossed_prices: dict[str, tuple[float, float]],
) -> None:
    """
    Book isn't (or is no longer) crossed -- clear tracking state and, if this
    instrument had an open crossed-book episode, log its resolution.

    Proves this was a real book update (the price actually moved), not a no-op
    or a silently-forced resync -- distinguishes a genuine, harmless sub-second
    touch (self-heals via normal delta activity, matches the resync grace window's
    documented tolerance) from a stuck desync that only recovers via
    _resync_book's forced resubscribe (logged separately, as a CRITICAL).
    """
    crossed_since = crossed_since_ns.pop(iid, None)
    prices = crossed_prices.pop(iid, None)
    if crossed_since is None or prices is None:
        return
    logger.info(
        "Crossed book for %s resolved after %.2fs (was bid=%.6f/ask=%.6f, now bid=%.6f/ask=%.6f)",
        iid,
        (now_ns - crossed_since) / 1e9,
        prices[0],
        prices[1],
        book.best_bid_price().as_double(),
        book.best_ask_price().as_double(),
    )


def try_active_uncross(
    iid: str,
    book: OrderBook,
    level_msg_id: LevelMsgIds,
    crossed_since_ns: dict[str, int],
    crossed_prices: dict[str, tuple[float, float]],
) -> bool:
    """
    DATA-04: try dYdX's own non-destructive fix first -- drop only the stale
    level(s), never the whole book -- before falling back to the existing
    WARNING/timer/CRITICAL/_resync_book machinery. The cap bounds a
    pathological/many-levels-deep cross; a genuine crossed-book episode is
    normally one or two levels. Returns True once the book is no longer crossed.
    """
    for _ in range(_UNCROSS_MAX_STEPS):
        if not uncross_step(iid, book, level_msg_id):
            return False
        new_bid, new_ask = book.best_bid_price(), book.best_ask_price()
        if new_bid is None or new_ask is None or new_bid.as_double() < new_ask.as_double():
            crossed_since_ns.pop(iid, None)
            crossed_prices.pop(iid, None)
            return True
    return False


async def escalate_persistent_crossed_book(
    iid: str,
    book: OrderBook,
    now_ns: int,
    *,
    crossed_since_ns: dict[str, int],
    crossed_prices: dict[str, tuple[float, float]],
    last_bid_delta_ns: dict[str, int],
    last_ask_delta_ns: dict[str, int],
    resync_after_ns: int,
    resync: Callable[[str], Awaitable[None]],
) -> None:
    """
    Logs a WARNING for a crossed book active uncrossing couldn't resolve, and
    escalates to CRITICAL + a forced resync once it's persisted past
    `resync_after_ns` (`CoreConfig.crossed_resync_seconds`; DATA-02/DATA-03: resync is
    a last resort, not a first response).

    Diagnostic: the collector's `_last_book_update_ns` refreshes on a delta for EITHER
    side, so it can't tell "bid deltas stopped arriving" apart from "both sides keep
    updating and are genuinely crossed". These per-side timestamps can.
    """
    bid_stale_s = (now_ns - last_bid_delta_ns.get(iid, 0)) / 1e9
    ask_stale_s = (now_ns - last_ask_delta_ns.get(iid, 0)) / 1e9
    logger.warning(
        "Crossed book for %s (bid=%.6f >= ask=%.6f) — skipping snapshot "
        "[last bid delta %.1fs ago, last ask delta %.1fs ago]",
        iid,
        book.best_bid_price().as_double(),
        book.best_ask_price().as_double(),
        bid_stale_s,
        ask_stale_s,
    )
    crossed_since = crossed_since_ns.setdefault(iid, now_ns)
    crossed_prices.setdefault(
        iid, (book.best_bid_price().as_double(), book.best_ask_price().as_double())
    )
    if now_ns - crossed_since <= resync_after_ns:
        return
    # Persisted past the grace window this system already uses as its tolerance
    # for a genuine sub-second touch (see the CONFIRMED-root-cause comment in
    # collector.py) -- confirmed local desync (a lost delta our reconstruction never
    # recovers from on its own), not bad data from dYdX. Not gated to "once ever":
    # _resync_book (below) resets crossed_since on every call, so for a book that keeps
    # failing to recover this naturally repeats roughly every grace window, not every
    # _second_loop tick -- an unresolved CRITICAL incident should keep alerting, not go
    # silent after a single log line.
    critical_logger.critical(
        json.dumps(
            {
                "instrument_id": iid,
                "reason": "steady_state_crossed_book",
                "best_bid": book.best_bid_price().as_double(),
                "best_ask": book.best_ask_price().as_double(),
                "crossed_duration_ns": now_ns - crossed_since,
                "bid_delta_stale_s": bid_stale_s,
                "ask_delta_stale_s": ask_stale_s,
                "ts_event_ns": now_ns,
            }
        )
    )
    await resync(iid)


async def handle_crossed_book(
    iid: str,
    book: OrderBook,
    now_ns: int,
    *,
    level_msg_id: LevelMsgIds,
    crossed_since_ns: dict[str, int],
    crossed_prices: dict[str, tuple[float, float]],
    last_bid_delta_ns: dict[str, int],
    last_ask_delta_ns: dict[str, int],
    resync_after_ns: int,
    resync: Callable[[str], Awaitable[None]],
) -> bool:
    """
    Detect/escalate/resolve a crossed book for one instrument. Returns True if
    currently crossed (caller should skip this tick's snapshot for it).

    A self-contained state machine (crossed / resolved / stuck-past-grace), split into
    handle_uncrossed_book / try_active_uncross / escalate_persistent_crossed_book to
    keep each stage under this project's ~30-line guideline (READ-01).
    """
    if book.best_bid_price().as_double() < book.best_ask_price().as_double():
        handle_uncrossed_book(iid, book, now_ns, crossed_since_ns, crossed_prices)
        return False
    if try_active_uncross(iid, book, level_msg_id, crossed_since_ns, crossed_prices):
        return False
    await escalate_persistent_crossed_book(
        iid,
        book,
        now_ns,
        crossed_since_ns=crossed_since_ns,
        crossed_prices=crossed_prices,
        last_bid_delta_ns=last_bid_delta_ns,
        last_ask_delta_ns=last_ask_delta_ns,
        resync_after_ns=resync_after_ns,
        resync=resync,
    )
    return True
