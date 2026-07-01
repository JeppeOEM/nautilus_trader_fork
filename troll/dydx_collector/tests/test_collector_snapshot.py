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
Integration tests for the collector's snapshot-building logic.

Tests here exercise the real Nautilus OrderBook (L2_MBP) to verify that:
  - bids() returns levels in descending order (best bid first)
  - asks() returns levels in ascending order (best ask first)
  - bid[0] < ask[0] after normal delta application
  - a crossed book (bid >= ask) is correctly detected by the guard added to _second_loop
  - stale book emits identical snapshots (the root cause of flat lines)

The _second_loop logic is extracted into a helper that mirrors its exact snapshot
path so we can unit-test it without running the full asyncio collector.
"""

from pathlib import Path

from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity

from dydx_collector.collector import _prune_delta_retention
from dydx_collector.collector import _prune_interval_seconds
from dydx_collector.second_snapshot import BOOK_DEPTH
from dydx_collector.second_snapshot import DydxSecondSnapshot

_IID = InstrumentId.from_str("BTC-USD-PERP.DYDX")
_TS = 1_000_000_000


def _delta(
    action: BookAction,
    side: OrderSide,
    price: float,
    size: float,
    ts: int = _TS,
) -> OrderBookDelta:
    order = BookOrder(
        side=side,
        price=Price(price, 1),
        size=Quantity(size, 1),
        order_id=0,
    )
    return OrderBookDelta(
        instrument_id=_IID,
        action=action,
        order=order,
        flags=0,
        sequence=0,
        ts_event=ts,
        ts_init=ts,
    )


def _book(*bid_levels: tuple[float, float], ask_levels: list[tuple[float, float]] | None = None) -> OrderBook:
    """Build an L2_MBP OrderBook from bid/ask (price, size) tuples."""
    asks = ask_levels or [(bid_levels[0][0] + 1.0, bid_levels[0][1])]
    book = OrderBook(_IID, BookType.L2_MBP)
    for price, size in bid_levels:
        book.apply_delta(_delta(BookAction.ADD, OrderSide.BUY, price, size))
    for price, size in asks:
        book.apply_delta(_delta(BookAction.ADD, OrderSide.SELL, price, size))
    return book


def _snapshot_from_book(book: OrderBook, ts: int = _TS) -> DydxSecondSnapshot | None:
    """Mirror the _second_loop snapshot path exactly, including the crossed-book guard."""
    if book.best_bid_price() is None or book.best_ask_price() is None:
        return None
    if book.best_bid_price().as_double() >= book.best_ask_price().as_double():
        return None  # crossed book guard — matches collector._second_loop
    bid_levels = book.bids()[:BOOK_DEPTH]
    ask_levels = book.asks()[:BOOK_DEPTH]
    return DydxSecondSnapshot(
        instrument_id=_IID,
        bid_prices=[lv.price.as_double() for lv in bid_levels],
        bid_sizes=[lv.size() for lv in bid_levels],
        ask_prices=[lv.price.as_double() for lv in ask_levels],
        ask_sizes=[lv.size() for lv in ask_levels],
        buy_volume=0.0,
        sell_volume=0.0,
        buy_count=0,
        sell_count=0,
        ts_event=ts,
        ts_init=ts,
    )


# ---------------------------------------------------------------------------
# OrderBook level ordering
# ---------------------------------------------------------------------------

def test_bids_returned_descending_best_first() -> None:
    book = _book((100.0, 5.0), (99.0, 3.0), (98.0, 2.0))
    bid_prices = [lv.price.as_double() for lv in book.bids()]
    assert bid_prices == [100.0, 99.0, 98.0], f"bids not descending: {bid_prices}"


def test_asks_returned_ascending_best_first() -> None:
    book = _book((100.0, 5.0), ask_levels=[(101.0, 4.0), (102.0, 6.0), (103.0, 2.0)])
    ask_prices = [lv.price.as_double() for lv in book.asks()]
    assert ask_prices == [101.0, 102.0, 103.0], f"asks not ascending: {ask_prices}"


def test_best_bid_price_is_highest_bid() -> None:
    book = _book((100.0, 5.0), (99.0, 3.0))
    assert book.best_bid_price().as_double() == 100.0


def test_best_ask_price_is_lowest_ask() -> None:
    book = _book((100.0, 5.0), ask_levels=[(101.0, 4.0), (102.0, 6.0)])
    assert book.best_ask_price().as_double() == 101.0


# ---------------------------------------------------------------------------
# Normal snapshot: bid < ask
# ---------------------------------------------------------------------------

def test_snapshot_bid_below_ask_after_normal_deltas() -> None:
    book = _book((50000.0, 1.0), ask_levels=[(50001.0, 1.0)])
    snap = _snapshot_from_book(book)
    assert snap is not None
    assert snap.bid_prices[0] < snap.ask_prices[0], (
        f"bid={snap.bid_prices[0]} not below ask={snap.ask_prices[0]}"
    )


def test_snapshot_level_count_matches_book_depth() -> None:
    bids = [(100.0 - i, float(i + 1)) for i in range(25)]  # 25 levels, more than BOOK_DEPTH
    asks = [(101.0 + i, float(i + 1)) for i in range(25)]
    book = _book(*bids, ask_levels=asks)
    snap = _snapshot_from_book(book)
    assert snap is not None
    assert len(snap.bid_prices) == BOOK_DEPTH
    assert len(snap.ask_prices) == BOOK_DEPTH


def test_snapshot_bid_prices_descending() -> None:
    book = _book((100.0, 5.0), (99.0, 3.0), (98.0, 2.0))
    snap = _snapshot_from_book(book)
    assert snap is not None
    assert snap.bid_prices == sorted(snap.bid_prices, reverse=True), (
        f"bid_prices not descending: {snap.bid_prices}"
    )


def test_snapshot_ask_prices_ascending() -> None:
    book = _book((100.0, 5.0), ask_levels=[(101.0, 4.0), (102.0, 6.0), (103.0, 2.0)])
    snap = _snapshot_from_book(book)
    assert snap is not None
    assert snap.ask_prices == sorted(snap.ask_prices), (
        f"ask_prices not ascending: {snap.ask_prices}"
    )


def test_snapshot_is_none_when_book_empty() -> None:
    book = OrderBook(_IID, BookType.L2_MBP)
    assert _snapshot_from_book(book) is None


# ---------------------------------------------------------------------------
# Crossed-book guard
# ---------------------------------------------------------------------------

def test_crossed_book_guard_returns_none() -> None:
    """
    Simulate reconnect mid-replay: ask side was cleared and bid is now above where
    the new ask will land. _snapshot_from_book must return None (mirrors the guard
    in collector._second_loop that was added to fix the visual bid>ask anomaly).
    """
    book = OrderBook(_IID, BookType.L2_MBP)
    # Only bid side populated (ask side not yet rebuilt after CLEAR + replay)
    book.apply_delta(_delta(BookAction.ADD, OrderSide.BUY, 102.0, 1.0))
    book.apply_delta(_delta(BookAction.ADD, OrderSide.SELL, 101.0, 1.0))  # ask < bid
    snap = _snapshot_from_book(book)
    assert snap is None, "crossed book must be skipped, not emitted"


def test_touched_book_guard_returns_none() -> None:
    # bid == ask (zero spread) is also invalid
    book = OrderBook(_IID, BookType.L2_MBP)
    book.apply_delta(_delta(BookAction.ADD, OrderSide.BUY, 100.0, 1.0))
    book.apply_delta(_delta(BookAction.ADD, OrderSide.SELL, 100.0, 1.0))
    snap = _snapshot_from_book(book)
    assert snap is None, "zero-spread book must be skipped"


def test_normal_book_after_fix_emits_snapshot() -> None:
    # After reconnect completes the book should have ask > bid → snap emitted
    book = _book((100.0, 1.0), ask_levels=[(100.5, 1.0)])
    snap = _snapshot_from_book(book)
    assert snap is not None
    assert snap.bid_prices[0] < snap.ask_prices[0]


# ---------------------------------------------------------------------------
# Stale feed (flat line) — documented behaviour
# ---------------------------------------------------------------------------

def test_stale_book_emits_identical_snapshots_each_second() -> None:
    """
    If no new OrderBookDeltas arrive, _live_books[iid] stays unchanged and
    _snapshot_from_book() returns identical snapshots each call.

    In production this path is now blocked by the staleness guard added to
    collector._second_loop: if _last_book_update_ns[iid] is more than
    _STALE_BOOK_NS (5s) in the past, _second_loop skips snapshot emission
    entirely.  This helper (_snapshot_from_book) does not include that guard,
    so the identical-snapshot behaviour remains testable in isolation —
    confirming that the fix must live in _second_loop, not in the snapshot
    helper itself.
    """
    book = _book((50000.0, 1.5), ask_levels=[(50001.0, 2.0)])

    snaps = [_snapshot_from_book(book, ts=_TS + i * 1_000_000_000) for i in range(5)]

    assert all(s is not None for s in snaps)
    bids = [s.bid_prices[0] for s in snaps]  # type: ignore[index]
    asks = [s.ask_prices[0] for s in snaps]  # type: ignore[index]
    assert len(set(bids)) == 1, f"bids should be identical (stale): {bids}"
    assert len(set(asks)) == 1, f"asks should be identical (stale): {asks}"


# ---------------------------------------------------------------------------
# Per-coin raw-delta retention (_prune_delta_retention, _prune_interval_seconds)
# ---------------------------------------------------------------------------

def test_prune_delta_retention_skips_unlimited_instrument(tmp_path: Path) -> None:
    deltas_dir = tmp_path / "data" / "order_book_deltas" / "BTC-USD-PERP.DYDX"
    deltas_dir.mkdir(parents=True)
    old = deltas_dir / "2020-01-01T00-00-00Z_2020-01-01T01-00-00Z.parquet"
    old.write_bytes(b"x")

    freed = _prune_delta_retention(str(tmp_path), {"BTC-USD-PERP.DYDX": None})

    assert freed == 0
    assert old.exists(), "retain_hours=None must mean unlimited -- never pruned"


def test_prune_delta_retention_prunes_finite_retention_instrument(tmp_path: Path) -> None:
    deltas_dir = tmp_path / "data" / "order_book_deltas" / "ETH-USD-PERP.DYDX"
    deltas_dir.mkdir(parents=True)
    old = deltas_dir / "2020-01-01T00-00-00Z_2020-01-01T01-00-00Z.parquet"
    old.write_bytes(b"x")

    freed = _prune_delta_retention(str(tmp_path), {"ETH-USD-PERP.DYDX": 48.0})

    assert freed > 0
    assert not old.exists()


def test_prune_interval_uses_shortest_active_retention_window() -> None:
    # A short per-coin retain_hours must shorten the check cadence below the
    # global non_config_retain_hours, or the per-coin window goes stale.
    interval = _prune_interval_seconds(24.0, {"BTC-USD-PERP.DYDX": 1.0})
    assert interval == 1.0 * 900


def test_prune_interval_ignores_unlimited_instruments() -> None:
    interval = _prune_interval_seconds(4.0, {"BTC-USD-PERP.DYDX": None})
    assert interval == 4.0 * 900


def test_prune_interval_has_a_minimum_floor() -> None:
    interval = _prune_interval_seconds(0.01, {})
    assert interval == 900
