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
"""`top_levels_mismatch` (pure), the feed-liveness gate and the aligned REST cross-check (22.5, D-64)."""

import asyncio
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from ml_signals import error_ledger

from collector_core import collector as collector_mod
from collector_core.book_check import BookSnapshot
from collector_core.book_check import top_levels_mismatch
from collector_core.collector import Collector
from collector_core.config import CoreConfig
from collector_core.config import core_config_from_dict
from collector_core.tests.test_collector import _BYBIT
from collector_core.tests.test_collector import _HL
from collector_core.tests.test_collector import _S
from collector_core.tests.test_collector import _collector
from collector_core.tests.test_collector import _deltas
from collector_core.tests.test_collector import _tick
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_BIDS = [(100.0, 1.0), (99.5, 2.0), (99.0, 3.0)]


def test_identical_books_agree() -> None:
    assert top_levels_mismatch(_BIDS, list(_BIDS)) == []


def test_one_level_shifted_within_tolerance() -> None:
    rest = [(100.0, 1.0), (99.5, 2.0), (98.5, 3.0)]  # tail level moved: 1 absent price allowed
    assert top_levels_mismatch(_BIDS, rest) == []


def test_best_price_differs() -> None:
    (msg, *_) = top_levels_mismatch(_BIDS, [(100.5, 1.0), *_BIDS[1:]])
    assert msg.startswith("best price")


def test_size_drift_over_tolerance() -> None:
    (msg,) = top_levels_mismatch(_BIDS, [(100.0, 1.0), (99.5, 2.5), (99.0, 3.0)])
    assert "size at 99.5" in msg


def test_rest_shorter_than_depth_is_not_a_mismatch() -> None:
    assert top_levels_mismatch(_BIDS, _BIDS[:2]) == []


def test_one_side_empty_is_a_mismatch() -> None:
    assert top_levels_mismatch(_BIDS, []) == ["one side empty: live=3 rest=0"]
    assert top_levels_mismatch([], []) == []


def test_config_keys_validated() -> None:
    cfg = core_config_from_dict(
        {"feed_stale_seconds": 12, "book_crosscheck_seconds": 0}, ("mainnet",)
    )
    assert (cfg.feed_stale_seconds, cfg.book_crosscheck_seconds) == (12.0, 0.0)
    with pytest.raises(ValueError, match="feed_stale_seconds"):
        core_config_from_dict({"feed_stale_seconds": 0}, ("mainnet",))
    with pytest.raises(ValueError, match="book_crosscheck_seconds"):
        core_config_from_dict({"book_crosscheck_seconds": -1}, ("mainnet",))


def test_gate_names_dead_feed_vs_silent_instrument(tmp_path: Path, caplog) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    now = time.time_ns()
    # Feed alive (message just now) but this instrument's book is old -> instrument silent.
    c._last_book_update_ns[_BYBIT] = now - 60 * _S
    with caplog.at_level(logging.WARNING, logger="collector_core.collector"):
        assert _tick(c, now) == []
    assert "instrument silent" in caplog.text
    # Nothing at all for a minute -> feed dead, said so.
    caplog.clear()
    c._last_book_update_ns[_BYBIT] = now
    c._last_feed_message_ns = now - 60 * _S
    with caplog.at_level(logging.WARNING, logger="collector_core.collector"):
        assert _tick(c, now) == []
    assert "feed dead" in caplog.text


def _framed(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    *,
    seq: int,
    ts: int,
    iid: str = _BYBIT,
) -> OrderBookDeltas:
    """Build a full-book frame stamped like a venue's: `sequence` (Bybit `seq`) and `ts_event`."""
    inst = InstrumentId.from_str(iid)
    deltas = [OrderBookDelta.clear(inst, seq, ts, ts)]
    for side, levels in ((OrderSide.BUY, bids), (OrderSide.SELL, asks)):
        for price, size in levels:
            order = BookOrder(side, Price(price, 2), Quantity(size, 3), 0)
            deltas.append(OrderBookDelta(inst, BookAction.ADD, order, 0, seq, ts, ts))
    return OrderBookDeltas(inst, deltas)


class _RestClient:
    """REST double: returns `snapshots` in order; `on_fetch` runs before each return."""

    def __init__(self, *snapshots: BookSnapshot, on_fetch: Callable[[], None] | None = None):
        self.snapshots = list(snapshots)
        self.on_fetch = on_fetch
        self.fetches = 0

    async def fetch_book_snapshot(self, iid: str) -> BookSnapshot:
        self.fetches += 1
        if self.on_fetch is not None:
            self.on_fetch()
        return self.snapshots.pop(0)


async def _drive(
    c: Collector,
    iid: str,
    arrivals: list[OrderBookDeltas],
    after_arrival: Callable[[], None] | None = None,
) -> None:
    """
    Run `_crosscheck_one` and feed it one book message per armed round, the way the WS would:
    the round arms, the frame arrives, REST is fetched, the round aligns.
    """
    task = asyncio.create_task(c._crosscheck_one(iid))
    for frame in arrivals:
        while not task.done() and c._crosscheck_arrivals.get(iid) != 0:
            await asyncio.sleep(0)
        if task.done():
            break
        c._process_data(frame)
        if after_arrival is not None:
            for _ in range(3):
                await asyncio.sleep(0)
            after_arrival()
    await task


_T0 = 1_700_000_000 * _S
_BOOK = ([(100.0, 1.0), (99.5, 2.0)], [(100.5, 1.0)])


def _seq_collector(tmp_path: Path, rest: object, **fast: object) -> Collector:
    c = _collector(tmp_path, client=rest)
    c._process_data(_framed(*_BOOK, seq=10, ts=_T0))
    return c


def test_sequence_aligned_round_brackets_rest_between_two_frames(tmp_path: Path) -> None:
    """REST `seq` 11 lies between frames 11 and 12: a level equal to either bracket is clean."""
    error_ledger.reset()
    rest = _RestClient(
        BookSnapshot([(100.0, 1.0), (99.5, 3.0)], [(100.5, 1.0)], sequence=11),
        # Frame 12 changes 99.5 to 3.0 -- REST already saw it; frame 11 did not.
        on_fetch=lambda: c._process_data(
            _framed([(100.0, 1.0), (99.5, 3.0)], [(100.5, 1.0)], seq=12, ts=_T0 + _S)
        ),
    )
    c = _seq_collector(tmp_path, rest)
    asyncio.run(_drive(c, _BYBIT, [_framed(*_BOOK, seq=11, ts=_T0 + _S)]))
    assert rest.fetches == 1
    assert error_ledger.counts() == {}
    assert c._crosscheck_unaligned[_BYBIT] == 0


def test_sequence_aligned_mismatch_is_ledgered_after_a_second_aligned_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A level wrong against both brackets on two rounds is a missed delta, ledgered once."""
    error_ledger.reset()
    monkeypatch.setattr(collector_mod, "_CROSSCHECK_CONFIRM_SECONDS", 0.0)
    wrong = BookSnapshot([(100.0, 1.0), (99.5, 5.0)], [(100.5, 1.0)], sequence=11)
    rest = _RestClient(
        wrong,
        BookSnapshot([(100.0, 1.0), (99.5, 5.0)], [(100.5, 1.0)], sequence=13),
        on_fetch=lambda: c._process_data(
            _framed(*_BOOK, seq=12 if rest.fetches == 1 else 14, ts=_T0 + _S)
        ),
    )
    c = _seq_collector(tmp_path, rest)
    asyncio.run(
        _drive(
            c,
            _BYBIT,
            [_framed(*_BOOK, seq=11, ts=_T0 + _S), _framed(*_BOOK, seq=13, ts=_T0 + 2 * _S)],
        )
    )
    assert rest.fetches == 2
    assert error_ledger.counts() == {"collector.book_crosscheck": 1}
    assert "same sequence" in error_ledger.last_details()["collector.book_crosscheck"]


def test_sequence_mismatch_that_does_not_repeat_is_not_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A level that changed twice between two frames disagrees once, then agrees: skew, not loss."""
    error_ledger.reset()
    monkeypatch.setattr(collector_mod, "_CROSSCHECK_CONFIRM_SECONDS", 0.0)
    rest = _RestClient(
        BookSnapshot([(100.0, 1.0), (99.5, 5.0)], [(100.5, 1.0)], sequence=11),
        BookSnapshot(*_BOOK, sequence=13),
        on_fetch=lambda: c._process_data(
            _framed(*_BOOK, seq=12 if rest.fetches == 1 else 14, ts=_T0 + _S)
        ),
    )
    c = _seq_collector(tmp_path, rest)
    asyncio.run(
        _drive(
            c,
            _BYBIT,
            [_framed(*_BOOK, seq=11, ts=_T0 + _S), _framed(*_BOOK, seq=13, ts=_T0 + 2 * _S)],
        )
    )
    assert error_ledger.counts() == {}


def test_rest_behind_every_capture_is_skipped_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    REST answered from before the live book's first capture: not judged, counted, ledgered
    once the streak reaches the bound.
    """
    error_ledger.reset()
    monkeypatch.setattr(collector_mod, "_CROSSCHECK_UNALIGNED_STREAK", 1)
    rest = _RestClient(
        BookSnapshot([(1.0, 1.0)], [(2.0, 1.0)], sequence=5),
        on_fetch=lambda: c._process_data(_framed(*_BOOK, seq=12, ts=_T0 + _S)),
    )
    c = _seq_collector(tmp_path, rest)
    asyncio.run(_drive(c, _BYBIT, [_framed(*_BOOK, seq=11, ts=_T0 + _S)]))
    assert c._crosscheck_unaligned[_BYBIT] == 1
    assert error_ledger.counts() == {"collector.book_crosscheck_unaligned": 1}


def test_stream_never_passing_rest_seq_times_out_as_unaligned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    monkeypatch.setattr(collector_mod, "_VENUE_AHEAD_NS", 0)  # align timeout = 1 s
    rest = _RestClient(BookSnapshot(*_BOOK, sequence=99))
    c = _seq_collector(tmp_path, rest)
    asyncio.run(_drive(c, _BYBIT, [_framed(*_BOOK, seq=11, ts=_T0 + _S)]))
    assert c._crosscheck_unaligned[_BYBIT] == 1
    assert error_ledger.counts() == {}
    assert _BYBIT not in c._crosscheck_captures  # disarmed after the round


def test_ts_event_aligned_round_compares_only_the_push_with_the_same_time(
    tmp_path: Path,
) -> None:
    """
    Hyperliquid: REST with the push's own `time` must equal it; a differing size is ledgered
    at once (same venue state).
    """
    error_ledger.reset()
    push_ts = _T0 + 5 * _S
    rest = _RestClient(
        BookSnapshot(*_BOOK, ts_event_ns=push_ts),
        BookSnapshot([(100.0, 1.0), (99.5, 2.5)], [(100.5, 1.0)], ts_event_ns=push_ts + 5 * _S),
    )
    c = _collector(tmp_path, iid=_HL, client=rest)
    c._process_data(_framed(*_BOOK, seq=0, ts=_T0, iid=_HL))
    asyncio.run(_drive(c, _HL, [_framed(*_BOOK, seq=0, ts=push_ts, iid=_HL)]))
    assert error_ledger.counts() == {}
    asyncio.run(_drive(c, _HL, [_framed(*_BOOK, seq=0, ts=push_ts + 5 * _S, iid=_HL)]))
    assert error_ledger.counts() == {"collector.book_crosscheck": 1}
    assert "same ts_event" in error_ledger.last_details()["collector.book_crosscheck"]
    assert rest.fetches == 2


def test_ts_event_alignment_tolerates_the_adapters_f64_millisecond_rounding(
    tmp_path: Path,
) -> None:
    """
    The live push's `ts_event` can sit 128 ns off the venue's ms (D-62); REST's `time` is
    exact. Same millisecond = same state, judged exactly.
    """
    error_ledger.reset()
    push_ts = _T0 + 5 * _S
    rest = _RestClient(BookSnapshot(*_BOOK, ts_event_ns=push_ts))
    c = _collector(tmp_path, iid=_HL, client=rest)
    c._process_data(_framed(*_BOOK, seq=0, ts=_T0, iid=_HL))
    asyncio.run(_drive(c, _HL, [_framed(*_BOOK, seq=0, ts=push_ts - 128, iid=_HL)]))
    assert c._crosscheck_unaligned[_HL] == 0
    assert error_ledger.counts() == {}


def test_ts_event_from_a_later_block_than_the_push_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    monkeypatch.setattr(collector_mod, "_VENUE_AHEAD_NS", 0)
    push_ts = _T0 + 5 * _S
    rest = _RestClient(BookSnapshot(*_BOOK, ts_event_ns=push_ts + 500_000_000))
    c = _collector(tmp_path, iid=_HL, client=rest)
    c._process_data(_framed(*_BOOK, seq=0, ts=_T0, iid=_HL))
    asyncio.run(_drive(c, _HL, [_framed(*_BOOK, seq=0, ts=push_ts, iid=_HL)]))
    assert c._crosscheck_unaligned[_HL] == 1
    assert error_ledger.counts() == {}


def test_venue_mode_aligns_on_the_capture_taken_at_drain_time(tmp_path: Path) -> None:
    """With held deltas the capture happens when the second closes, after REST returned."""
    error_ledger.reset()
    os.environ["CANDLES_DB_PATH"] = str(tmp_path / "candles.db")
    push_ts = _T0 + 5 * _S
    rest = _RestClient(BookSnapshot(*_BOOK, ts_event_ns=push_ts))
    cfg = CoreConfig(
        environment="mainnet",
        catalog_path=str(tmp_path),
        instruments=(_HL,),
        book_time_source="venue",
        hold_back_seconds=0.0,
    )
    c = Collector(cfg, rest)
    c._process_data(_framed(*_BOOK, seq=0, ts=_T0, iid=_HL))
    c._drain_pending_deltas(_T0 + _S)
    assert _HL in c._live_books
    asyncio.run(
        _drive(
            c,
            _HL,
            [_framed(*_BOOK, seq=0, ts=push_ts, iid=_HL)],
            after_arrival=lambda: c._drain_pending_deltas(push_ts + _S),
        )
    )
    assert rest.fetches == 1
    assert c._crosscheck_unaligned[_HL] == 0
    assert error_ledger.counts() == {}


def test_persistent_keeps_only_levels_wrong_in_both_rounds() -> None:
    from collector_core.book_check import persistent

    first = ["bids size at 99.5: live=1 rest=2", "asks best price: live=1 rest=2"]
    second = ["bids size at 99.5: live=1 rest=3", "bids size at 99.0: live=1 rest=2"]
    assert persistent(first, second) == ["bids size at 99.5: live=1 rest=3"]
