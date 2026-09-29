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
Invariant tests for the gate's aggregates and core policies (Story 26.1, DDD spine AD-D6): real
Nautilus `OrderBook`/`OrderBookDeltas`/`TradeTick` objects, no collector, no I/O, time passed in.
"""

import time

from kernel.second_snapshot import DydxSecondSnapshot

from capture.domain.events import BookUncrossed
from capture.domain.feed_group import Feed
from capture.domain.feed_group import FeedGroup
from capture.domain.flush_batch import TRADE_CARRY_NS
from capture.domain.flush_batch import FlushBatch
from capture.domain.live_book import S_NS
from capture.domain.live_book import LiveBook
from capture.domain.policies import CentralBookCrossPolicy
from capture.domain.sampler import SecondSampler
from capture.domain.trade_intake import ACCEPTED
from capture.domain.trade_intake import ARCHIVE_FEED_NAME
from capture.domain.trade_intake import DEDUP_HORIZON_NS
from capture.domain.trade_intake import DUPLICATE_FEED
from capture.domain.trade_intake import DUPLICATE_FEED_FOLD
from capture.domain.trade_intake import REPLAY
from capture.domain.trade_intake import REST_FEED_NAME
from capture.domain.trade_intake import STALE
from capture.domain.trade_intake import TradeIntake
from capture.domain.verdicts import EMPTY_TOP
from capture.domain.verdicts import NO_BOOK
from capture.domain.verdicts import Accepted
from capture.domain.verdicts import Crossed
from capture.domain.verdicts import ResyncRequested
from capture.domain.verdicts import SampleVerdict
from capture.domain.verdicts import Stale
from capture.domain.verdicts import StillCrossed
from capture.domain.verdicts import Uncrossed
from capture.domain.verdicts import Unencodable
from capture.tests.test_collector import _BYBIT
from capture.tests.test_collector import _adds
from capture.tests.test_collector import _deltas
from capture.tests.test_collector import _trade
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import OrderSide


_T = 1_790_000_000 * S_NS
_NEVER = 10**18  # a staleness bound nothing reaches
_CORE = CentralBookCrossPolicy(resync_after_ns=10 * S_NS, can_resync=True)


def _snapshotted(bids: list, asks: list, now: int = _T) -> LiveBook:
    book = LiveBook()
    book.apply(_deltas(bids, asks, ts=now), now)
    return book


def _top(
    book: LiveBook, now: int = _T, stale_ns: float = _NEVER, feed_dead: Stale | None = None
) -> tuple[SampleVerdict, BookUncrossed | None]:
    return book.snapshot_top(20, now, None, stale_ns, feed_dead, _CORE)


# -- LiveBook ------------------------------------------------------------------------------------


def test_a_delta_before_any_snapshot_builds_no_book_and_is_counted() -> None:
    book = LiveBook()
    book.apply(_adds([(100.0, 1.0)], [(101.0, 1.0)]), _T)
    assert (book.book, book.before_snapshot, book.last_update_ns) == (None, 1, None)
    assert _top(book) == (NO_BOOK, None)


def test_a_snapshot_then_deltas_build_the_book_by_reference() -> None:
    book = _snapshotted([(100.0, 1.0)], [(101.0, 1.0)])
    nautilus_book = book.book
    assert nautilus_book is not None
    book.apply(_adds([(100.5, 2.0)], []), _T + 1)
    assert nautilus_book.best_bid_price().as_double() == 100.5
    assert book.last_update_ns == _T + 1
    assert book.book is nautilus_book  # mutated in place, never copied


def test_the_four_gate_checks() -> None:
    assert _top(LiveBook())[0] is NO_BOOK
    assert _top(_snapshotted([(100.0, 1.0)], []))[0] is EMPTY_TOP
    crossed, _ = _top(_snapshotted([(101.0, 1.0)], [(100.0, 1.0)]))
    assert isinstance(crossed, Crossed)
    assert (crossed.first_seen, crossed.resync) == (True, False)
    stale, _ = _top(_snapshotted([(100.0, 1.0)], [(101.0, 1.0)]), now=_T + 10 * S_NS, stale_ns=S_NS)
    assert isinstance(stale, Stale)
    assert stale.kind == "instrument_silent"
    accepted, _ = _top(_snapshotted([(100.0, 1.0), (99.0, 1.0)], [(101.0, 1.0)]))
    assert isinstance(accepted, Accepted)
    assert [lv.price.as_double() for lv in accepted.bids] == [100.0, 99.0]


def test_a_dead_feed_outranks_the_instruments_own_silence() -> None:
    dead = Stale("feed_dead", "feed dead: no WS message of any kind for 60.0s")
    book = _snapshotted([(100.0, 1.0)], [(101.0, 1.0)])
    assert _top(book, now=_T + 60 * S_NS, stale_ns=S_NS, feed_dead=dead)[0] is dead


def test_a_crossed_episode_keeps_its_start_and_ends_with_an_event() -> None:
    book = _snapshotted([(101.0, 1.0)], [(100.0, 1.0)])
    first, _ = _top(book, now=_T)
    later, _ = _top(book, now=_T + 3 * S_NS)
    assert isinstance(later, Crossed)
    assert (later.since_ns, later.first_seen) == (_T, False)
    book.apply(_deltas([(99.0, 1.0)], [(100.0, 1.0)], ts=_T + 5 * S_NS), _T + 5 * S_NS)
    verdict, event = _top(book, now=_T + 5 * S_NS)
    assert isinstance(verdict, Accepted)
    assert event is not None
    assert (event.since_ns, event.was) == (_T, (101.0, 100.0))
    assert book.crossed_since_ns is None


def test_the_core_policy_resyncs_only_past_the_window_and_only_when_it_can() -> None:
    book = _snapshotted([(101.0, 1.0)], [(100.0, 1.0)]).book
    assert _CORE.step(book, {}, _T, _T + 10 * S_NS) == StillCrossed(_T)
    assert _CORE.step(book, {}, _T, _T + 10 * S_NS + 1) == ResyncRequested(_T)
    no_resync = CentralBookCrossPolicy(resync_after_ns=0, can_resync=False)
    assert no_resync.step(book, {}, _T, _T + 3600 * S_NS) == StillCrossed(_T)


def test_resync_drops_every_trace_of_the_stale_book() -> None:
    book = _snapshotted([(101.0, 1.0)], [(100.0, 1.0)])
    _top(book)
    book.tags[(OrderSide.BUY, 101.0)] = 1
    book.last_u = 7
    book.resync()
    assert (book.book, book.crossed_since_ns, book.crossed_prices, book.tags, book.last_u) == (
        None,
        None,
        None,
        {},
        None,
    )
    assert book.last_update_ns == _T  # liveness is not book state: the watchdog keeps it


class _ClaimsUncrossed:
    """A broken policy: says `Uncrossed` without touching the book."""

    crossing_is_corruption = True

    def step(self, book: object, tags: object, since_ns: int | None, now_ns: int) -> Uncrossed:
        return Uncrossed()


def test_the_gate_never_trusts_a_policys_uncrossed_verdict_alone() -> None:
    book = _snapshotted([(101.0, 1.0)], [(100.0, 1.0)])
    verdict, event = book.snapshot_top(20, _T, None, _NEVER, None, _ClaimsUncrossed())
    assert isinstance(verdict, Crossed)  # still crossed on the book: never an Accepted row
    assert event is None


def test_a_fresh_snapshot_settles_a_queued_resync() -> None:
    book = _snapshotted([(100.0, 1.0)], [(101.0, 1.0)])
    book.resync()
    book.resync_pending = True
    book.apply(_deltas([(100.0, 1.0)], [(101.0, 1.0)], ts=_T + 1), _T + 1)
    assert book.book is not None
    assert book.resync_pending is False  # the baseline the resync asked for has arrived


def test_forget_leaves_nothing_of_a_removed_instrument() -> None:
    book = _snapshotted([(100.0, 1.0)], [(101.0, 1.0)])
    book.resync_pending = True
    book.last_bid_delta_ns = book.last_ask_delta_ns = _T
    book.forget()
    assert (book.book, book.last_update_ns, book.resync_pending) == (None, None, False)
    assert (book.last_bid_delta_ns, book.last_ask_delta_ns) == (None, None)


def test_held_deltas_apply_in_ts_event_order_once_and_only_up_to_the_boundary() -> None:
    book = LiveBook()
    later = _adds([(100.5, 1.0)], [])
    later = type(later)(later.instrument_id, [_with_ts(d, _T + 700) for d in later.deltas])
    book.hold(later, _T + 900, None)
    book.hold(_deltas([(100.0, 1.0)], [(101.0, 1.0)], ts=_T + 100), _T + 901, None)
    assert book.drain(_T + 100, _T + 1000) is None  # nothing strictly before the boundary
    assert book.drain(_T + 800, _T + 1000) is None
    assert book.book is not None
    assert book.book.best_bid_price().as_double() == 100.5  # the snapshot first, then the update
    assert (book.pending_count, book.book_event_ns) == (0, _T + 700)
    assert book.drain(_T + 800, _T + 1000) is None  # nothing is applied twice


def _with_ts(delta: OrderBookDelta, ts: int, ts_init: int | None = None) -> OrderBookDelta:
    ts_init = ts if ts_init is None else ts_init
    return type(delta)(delta.instrument_id, delta.action, delta.order, 0, 0, ts, ts_init)


def test_a_message_held_past_the_bound_drops_the_book_counted() -> None:
    book = LiveBook()
    ahead = _deltas([(100.0, 1.0)], [(101.0, 1.0)])
    ahead = type(ahead)(ahead.instrument_id, [_with_ts(d, _T + 9 * S_NS, _T) for d in ahead.deltas])
    book.hold(ahead, _T, None)  # stamped 9 s ahead of its arrival
    assert book.check_overflow(_T + 5 * S_NS, 5 * S_NS) is None
    overflow = book.check_overflow(_T + 5 * S_NS + 1, 5 * S_NS)
    assert overflow is not None
    assert (overflow.held, overflow.held_ns) == (1, 5 * S_NS + 1)
    assert (book.has_pending, book.take_counts()) == (False, (1, 0))


def test_a_message_held_after_its_second_closed_is_counted_late() -> None:
    book = LiveBook()
    book.hold(_deltas([(100.0, 1.0)], [(101.0, 1.0)], ts=_T), _T + 2 * S_NS, _T // S_NS)
    assert book.late_deltas == 1


# -- TradeIntake ---------------------------------------------------------------------------------


def _accept(intake: TradeIntake, trade: TradeTick, feed: str, now: int | None = None) -> int:
    now = trade.ts_event if now is None else now
    return intake.accept(trade, str(trade.trade_id), feed, now, 10 * S_NS)


def test_arbitration_first_copy_replay_and_other_feed() -> None:
    intake = TradeIntake(10)
    trade = _trade(100.0, 1.0, AggressorSide.BUYER, 1)
    assert _accept(intake, trade, "main") == ACCEPTED
    assert _accept(intake, trade, "main") == REPLAY
    assert _accept(intake, trade, "trades") == DUPLICATE_FEED
    assert _accept(intake, trade, "trades") == REPLAY  # that feed has now delivered it too
    assert (intake.duplicate, intake.duplicate_feed) == (2, 1)


def test_a_live_copy_of_a_rest_first_copy_is_folded_not_archived() -> None:
    intake = TradeIntake(10)
    trade = _trade(100.0, 1.0, AggressorSide.BUYER, 1)
    intake.register("1", REST_FEED_NAME, trade.ts_init)
    assert _accept(intake, trade, "main") == DUPLICATE_FEED_FOLD
    # A second live socket's copy (`trade_feeds = 2`) is folded no more: once per trade.
    assert _accept(intake, trade, "trades") == DUPLICATE_FEED


def _arrived(n: int, ts_event: int, ts_init: int) -> TradeTick:
    trade = _trade(100.0, 1.0, AggressorSide.BUYER, n, ts=ts_event)
    return TradeTick(
        trade.instrument_id,
        trade.price,
        trade.size,
        trade.aggressor_side,
        trade.trade_id,
        ts_event,
        ts_init,
    )


def test_subscribe_time_history_is_stale_but_a_replay_is_checked_first() -> None:
    intake = TradeIntake(10)
    old = _arrived(1, _T, _T + 11 * S_NS)  # 11 s old when it arrived: venue replay
    fresh = _arrived(1, _T, _T)
    assert _accept(intake, old, "main", now=_T + 11 * S_NS) == STALE
    assert _accept(intake, fresh, "main", now=_T) == ACCEPTED
    assert _accept(intake, old, "main", now=_T + 11 * S_NS) == REPLAY


def test_age_is_judged_on_arrival_not_on_a_late_processing_time() -> None:
    """Story 31.2: a trade 0.2 s old on arrival, processed 15 s later (a backlog), is live."""
    intake = TradeIntake(10)
    trade = _arrived(1, _T, _T + S_NS // 5)
    assert _accept(intake, trade, "main", now=_T + 15 * S_NS) == ACCEPTED
    assert intake.take_counts().stale == 0


def test_a_trade_without_a_receipt_stamp_keeps_the_processing_time_age() -> None:
    intake = TradeIntake(10)
    trade = _arrived(1, _T, 0)
    assert _accept(intake, trade, "main", now=_T + 11 * S_NS) == STALE


def test_the_stale_report_carries_the_ts_event_span_and_the_arrival_ages() -> None:
    intake = TradeIntake(10)
    _accept(intake, _arrived(1, _T, _T + 30 * S_NS), "main")
    _accept(intake, _arrived(2, _T + 5 * S_NS, _T + 17 * S_NS), "main")
    counts = intake.take_counts()
    assert counts.stale == 2
    assert counts.stale_span == (_T, _T + 5 * S_NS, 30 * S_NS, 12 * S_NS)
    assert intake.take_counts().stale_span is None


def test_the_window_keeps_an_id_inside_the_horizon_past_its_size() -> None:
    intake = TradeIntake(2)
    for n in (1, 2, 3):
        intake.register(str(n), "main", _T + n * S_NS)
    assert [intake.first_feed(str(n)) for n in (1, 2, 3)] == ["main", "main", "main"]


def test_the_window_evicts_past_the_horizon_and_over_its_size() -> None:
    intake = TradeIntake(2)
    for n in (1, 2, 3):
        intake.register(str(n), "main", _T + n * S_NS)
    intake.register("4", "main", _T + 2 * S_NS + DEDUP_HORIZON_NS)
    # 1 is past the horizon: evicted; 2 is not (strictly older is required), 3 neither.
    assert [intake.first_feed(str(n)) for n in (1, 2, 3, 4)] == [None, "main", "main", "main"]
    intake.register("5", "main", _T + 4 * S_NS + DEDUP_HORIZON_NS)
    assert [intake.first_feed(str(n)) for n in (2, 3)] == [None, None]  # back to the size
    assert list(intake._first) == ["4", "5"]


def test_a_seeded_id_is_a_duplicate_feed_never_folded_or_archived() -> None:
    intake = TradeIntake(10)
    assert intake.seed([("7", _T - 10 * S_NS), ("8", _T - 5 * S_NS)]) == 2
    assert intake.first_feed("7") == ARCHIVE_FEED_NAME
    assert _accept(intake, _arrived(7, _T - 10 * S_NS, _T - 10 * S_NS), "main") == DUPLICATE_FEED
    assert intake.seed([("8", _T)]) == 0  # already known: kept as it was


def test_venue_fold_buckets_late_and_ahead() -> None:
    intake = TradeIntake(10)
    second = _T // S_NS
    in_time = _trade(100.0, 1.0, AggressorSide.BUYER, 1, ts=_T)
    intake.fold(in_time, second - 1, 5 * S_NS)
    intake.fold(in_time, second, 5 * S_NS)  # its second already closed
    ahead = TradeTick(
        in_time.instrument_id, in_time.price, in_time.size, in_time.aggressor_side,
        in_time.trade_id, _T + 10 * S_NS, _T,
    )  # fmt: skip
    intake.fold(ahead, None, 5 * S_NS)
    assert ([len(b) for b in intake.buckets.values()], intake.late, intake.ahead) == ([1], 1, 1)
    intake.close(second, first_close=True)
    assert (intake.buckets, intake.take_counts().pre_start) == ({}, 1)
    assert intake.take_counts() == (0, 0, 0, 0, 0, 0, None)


# -- FeedGroup -----------------------------------------------------------------------------------


def test_silence_then_speech_on_a_book_feed_is_a_reconnect() -> None:
    feeds = FeedGroup(5 * S_NS, 3 * S_NS)
    main, trades = Feed("main", "g"), Feed("trades", "g", trades_only=True)
    assert feeds.note_message(main, _T, _T) is None
    assert feeds.note_message(trades, _T + 9 * S_NS, _T + 9 * S_NS) is None
    assert feeds.last_book_message_ns == _T  # a trades-only socket says nothing of the book
    assert feeds.note_message(main, _T + 9 * S_NS, _T + 9 * S_NS) == "feed silent 9.0s"


def test_one_request_per_feed_keeps_its_due_time_and_lowers_its_baselines() -> None:
    feeds = FeedGroup(5 * S_NS, 3 * S_NS)
    assert feeds.schedule("main", _T, "a", {"X": 100})
    assert not feeds.schedule("main", _T + S_NS, "b", {"X": 50}, {"X": 40})
    request = feeds.requests["main"]
    assert (request.due_ns, request.reasons, request.since) == (
        _T + 3 * S_NS,
        ["a", "b"],
        {"X": 40},
    )
    assert feeds.due(_T + 3 * S_NS - 1) == []
    assert [name for name, _ in feeds.due(_T + 3 * S_NS)] == ["main"]
    feeds.schedule("main", _T, "c", {})
    assert [name for name, _ in feeds.abandon()] == ["main"]
    assert feeds.requests == {}


def test_an_inactive_then_active_feed_reconnects_with_its_pre_gap_baselines() -> None:
    feeds = FeedGroup(5 * S_NS, 3 * S_NS)
    main = Feed("main", "g")
    baselines = {"main": {"X": 1}}
    assert feeds.observe_states({main: True}, lambda n: dict(baselines[n])) == []
    feeds.observe_states({main: False}, lambda n: dict(baselines[n]))
    baselines["main"]["X"] = 99  # live trades after the resume must not move the baseline
    assert feeds.observe_states({main: True}, lambda n: dict(baselines[n])) == [("main", {"X": 1})]


# -- SecondSampler and FlushBatch ------------------------------------------------------------------


def test_a_row_is_encoded_exactly_at_the_definition_precisions() -> None:
    sampler = SecondSampler(20, _NEVER, _CORE)
    book = _snapshotted([(100.25, 1.5), (100.0, 0.125)], [(100.5, 3.0)])
    intakes = {_BYBIT: TradeIntake(10)}
    intakes[_BYBIT].fold(_trade(100.25, 0.5, AggressorSide.BUYER, 1), None, None)
    result = sampler.sample([_BYBIT], {_BYBIT: book}, intakes, _T, None, None, {_BYBIT: (2, 3)})
    (row,) = result.accepted
    wire = DydxSecondSnapshot.to_dict(row)
    assert (wire["price_precision"], wire["size_precision"]) == (2, 3)
    assert (wire["bid_prices"], wire["bid_sizes"]) == ([10025, 25], [1500, 125])
    assert (wire["ask_prices"], wire["close_price"], wire["buy_volume"]) == ([10050], 10025, 500)
    level = book.book.bids()[0]  # the book's own exact values, not `BookLevel.size()`'s float
    assert row.exact.bid_prices[0] == level.price
    assert row.exact.bid_sizes[0] == level.orders()[0].size


def test_a_book_without_a_definition_is_rejected_unencodable_never_guessed() -> None:
    sampler = SecondSampler(20, _NEVER, _CORE)
    book = _snapshotted([(100.0, 1.0)], [(101.0, 1.0)])
    intakes = {_BYBIT: TradeIntake(10)}
    intakes[_BYBIT].fold(_trade(100.5, 1.0, AggressorSide.BUYER, 1), None, None)
    result = sampler.sample([_BYBIT], {_BYBIT: book}, intakes, _T, None, None, {})
    assert result.accepted == []
    assert result.rejected == [(_BYBIT, Unencodable("no instrument definition"))]
    assert intakes[_BYBIT].live == []  # taken with the second: never carried onto a later row


def test_a_value_finer_than_the_definition_is_rejected_unencodable_never_rounded() -> None:
    sampler = SecondSampler(20, _NEVER, _CORE)
    book = _snapshotted([(100.25, 1.0)], [(101.0, 1.0)])
    result = sampler.sample([_BYBIT], {_BYBIT: book}, {}, _T, None, None, {_BYBIT: (1, 3)})
    ((iid, verdict),) = result.rejected
    assert iid == _BYBIT
    assert isinstance(verdict, Unencodable)
    assert "not exact at precision 1" in verdict.reason


def test_the_sampler_writes_accepted_books_and_drops_everyone_elses_trades() -> None:
    sampler = SecondSampler(20, _NEVER, _CORE)
    good = _snapshotted([(100.0, 1.0)], [(101.0, 1.0)])
    pending = LiveBook()
    pending.resync_pending = True
    intakes = {iid: TradeIntake(10) for iid in (_BYBIT, "B", "C")}
    for intake in intakes.values():
        intake.fold(_trade(100.5, 1.0, AggressorSide.BUYER, 1), None, None)
    precisions = {_BYBIT: (2, 3), "B": (2, 3)}
    books = {_BYBIT: good, "B": pending}
    result = sampler.sample([_BYBIT, "B"], books, intakes, _T, None, None, precisions)
    (row,) = result.accepted
    assert isinstance(row, DydxSecondSnapshot)
    assert row.buy_count == 1
    assert result.rejected == [("B", NO_BOOK)]
    assert result.resyncs == ["B"]
    assert [i.live for i in intakes.values()] == [[], [], []]  # taken, discarded, not sampled


def test_the_flush_carries_the_open_ts_init_group_and_the_rows_after_it() -> None:
    batch = FlushBatch()
    now = time.time_ns()
    old, fresh = (
        _trade(1.0, 1.0, AggressorSide.BUYER, 1, ts=now - TRADE_CARRY_NS),
        _trade(1.0, 1.0, AggressorSide.BUYER, 2, ts=now),
    )
    batch[(TradeTick, _BYBIT)].extend([fresh, old])
    ((key, written),) = batch.take(now, final=False, queue_pending=False)
    assert (key, written, batch[(TradeTick, _BYBIT)]) == ((TradeTick, _BYBIT), [old], [fresh])
    assert batch.take(now, final=True, queue_pending=False) == [((TradeTick, _BYBIT), [fresh])]
