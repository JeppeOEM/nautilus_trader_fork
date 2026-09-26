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
Bybit `u` canary (DATA-08): the pure policy (`bybit_collector.policies`) -- verdict matrix, message
key, the zero-level case -- plus the policy running inside the core's `LiveBook` through a real
`BybitCollector` with real OrderBookDeltas.
"""

import time
from pathlib import Path

import pytest
from collector_core.config import BybitConfig
from observability import error_ledger

from bybit_collector.collector import BybitCollector
from bybit_collector.policies import BybitSequenceCanary
from bybit_collector.policies import message_u as _message_u
from bybit_collector.policies import sequence_verdict as _sequence_verdict
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_IID = "BTCUSDT-LINEAR.BYBIT"


@pytest.mark.parametrize(
    ("last_u", "u", "is_snapshot", "expected"),
    [
        (None, 50, True, "snapshot"),
        (900, 1, False, "snapshot"),  # documented service-restart snapshot
        (None, 50, False, "ok"),  # no baseline: cannot judge
        (10, 11, False, "ok"),
        (10, 13, False, "gap"),
        (10, 10, False, "regress"),
        (10, 4, False, "regress"),
    ],
)
def test_sequence_verdict(last_u, u, is_snapshot, expected) -> None:
    assert _sequence_verdict(last_u, u, is_snapshot) == expected


def _msg(u: int, snapshot: bool = False, bid: float = 100.0, ask: float = 100.5) -> OrderBookDeltas:
    ts = time.time_ns()
    inst = InstrumentId.from_str(_IID)
    action = BookAction.ADD if snapshot else BookAction.UPDATE
    deltas = [OrderBookDelta.clear(inst, 0, ts, ts)] if snapshot else []
    for side, price in ((OrderSide.BUY, bid), (OrderSide.SELL, ask)):
        order = BookOrder(side, Price(price, 2), Quantity(1.0, 3), u)  # Rust stamps u as order_id
        deltas.append(OrderBookDelta(inst, action, order, 0, 0, ts, ts))
    return OrderBookDeltas(inst, deltas)


def _collector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BybitCollector:
    # Pin the candle store inside this test's tmp_path: the default resolves beside the catalog,
    # i.e. into pytest's shared tmp root, where every test here would open the same file read-write
    # and leave it open (`CandleStore` is meant to have exactly one writer per file).
    monkeypatch.setenv("CANDLES_DB_PATH", str(tmp_path / "candles.db"))
    c = BybitCollector(BybitConfig(environment="mainnet", catalog_path=str(tmp_path)), (_IID,))
    c._applied.add(_IID)  # as `run()`'s initial apply leaves it (no network here)
    return c


def test_message_u_reads_order_id_and_skips_clear() -> None:
    assert _message_u(_msg(7, snapshot=True)) == 7


def test_regress_ledgers_drops_book_and_queues_resync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    c = _collector(tmp_path, monkeypatch)
    c._apply_deltas(_IID, _msg(100, snapshot=True))
    c._apply_deltas(_IID, _msg(101))
    assert error_ledger.counts() == {}
    c._apply_deltas(_IID, _msg(101))  # replayed id
    assert error_ledger.counts() == {"collector.book_sequence": 1}
    assert c._live_book(_IID) is None
    assert _IID in c._resync_pending()
    assert c._book(_IID).last_u is None


def test_gap_ledgers_drops_book_and_queues_resync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skipped `u` is lost messages: `u` was measured contiguous in a healthy stream (D-41)."""
    error_ledger.reset()
    c = _collector(tmp_path, monkeypatch)
    c._apply_deltas(_IID, _msg(100, snapshot=True))
    c._apply_deltas(_IID, _msg(105))
    assert error_ledger.counts() == {"collector.book_sequence": 1}
    assert c._live_book(_IID) is None
    assert _IID in c._resync_pending()
    assert c._book(_IID).last_u is None


def test_restart_snapshot_rebaselines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    error_ledger.reset()
    c = _collector(tmp_path, monkeypatch)
    c._apply_deltas(_IID, _msg(900, snapshot=True))
    c._apply_deltas(_IID, _msg(1, snapshot=True))
    assert error_ledger.counts() == {}
    assert c._book(_IID).last_u == 1


def test_a_zero_level_message_carries_no_u_and_leaves_the_baseline() -> None:
    """
    A message with no level (the adapter's `total_levels == 0` branch) has no `u`: the canary is
    not asked, so the baseline stays where it was (DATA-08's documented, still-open no-verdict
    case: the next message is judged against the older `u`).
    """
    inst = InstrumentId.from_str(_IID)
    ts = time.time_ns()
    levelless = OrderBookDeltas(inst, [OrderBookDelta.clear(inst, 0, ts, ts)])
    assert BybitSequenceCanary().message_key(levelless) is None


def test_the_canary_value_is_what_the_live_book_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _collector(tmp_path, monkeypatch)
    c._apply_deltas(_IID, _msg(100, snapshot=True))
    c._apply_deltas(_IID, _msg(101))
    assert c._book(_IID).last_u == 101
    assert isinstance(c._canary, BybitSequenceCanary)
