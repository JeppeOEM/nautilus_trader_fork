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
Self-check: re-stamping a Price at fixed precision never changes its value. Also: a subscribe or
unsubscribe of an instrument's two channels is idempotent and converges after a partial failure,
including a channel the Rust client may replay on reconnect (Story 25.4's retry).
"""

import asyncio
import contextlib
from collections.abc import Callable
from collections.abc import Coroutine
from decimal import Decimal
from typing import Any

import pytest
from observability import error_ledger

from capture.venues.dydx.client import DydxClient
from capture.venues.dydx.client import _at_fixed_precision
from nautilus_trader.core.nautilus_pyo3 import FIXED_PRECISION
from nautilus_trader.model.objects import Price


# Includes the exact value that exposed a real corruption bug in
# Price(decimal, precision) for precision == FIXED_PRECISION: it silently
# returned 61090.5985500000026624 instead of 61090.59855.
VALUES_AT_VARYING_PRECISION = [
    ("1644.710689", 6),
    ("1647.81348", 5),
    ("61090.59855", 5),
    ("100", 0),
    ("0.000001", 6),
]


def test_value_is_preserved_exactly_across_precisions() -> None:
    for value_str, precision in VALUES_AT_VARYING_PRECISION:
        original = Price(Decimal(value_str), precision)
        fixed = _at_fixed_precision(original)

        assert fixed.as_decimal() == Decimal(value_str)
        assert fixed.precision == FIXED_PRECISION


def test_all_results_share_the_same_precision_label() -> None:
    # The whole point: ticks that started with different precisions must end
    # up with one consistent label, or the catalog's Arrow merge still breaks.
    fixed_prices = [
        _at_fixed_precision(Price(Decimal(value_str), precision))
        for value_str, precision in VALUES_AT_VARYING_PRECISION
    ]
    assert len({p.precision for p in fixed_prices}) == 1


def _channels(fail: set[str]) -> tuple[DydxClient, list[str]]:
    """Build a client whose four channel calls are recorded; the names in `fail` raise."""
    client = object.__new__(DydxClient)  # no pyo3 clients: only the per-channel logic is tested
    client._held = set()
    client._replayable = set()
    calls: list[str] = []

    def _channel(name: str) -> Callable[[str], Coroutine[Any, Any, None]]:
        async def call(iid: str) -> None:
            calls.append(name)
            if name in fail:
                raise ConnectionError(f"{name} {iid} rejected")

        return call

    for name in (
        "subscribe_trades",
        "subscribe_orderbook",
        "unsubscribe_trades",
        "unsubscribe_orderbook",
    ):
        setattr(client, name, _channel(name))
    return client, calls


_ID = "BTC-USD-PERP.DYDX"


def _run(call: Coroutine[Any, Any, None]) -> None:
    """Run one wire call; a failure is the scenario under test, not the assertion."""
    with contextlib.suppress(ConnectionError):
        asyncio.run(call)


def test_a_repeated_subscribe_sends_nothing() -> None:
    client, calls = _channels(set())
    _run(client.subscribe(_ID))
    _run(client.subscribe(_ID))
    assert calls == ["subscribe_trades", "subscribe_orderbook"]


def test_a_failed_subscribe_raises_and_keeps_the_half_that_went_through() -> None:
    client, _ = _channels({"subscribe_orderbook"})
    with pytest.raises(ConnectionError, match="subscribe_orderbook"):
        asyncio.run(client.subscribe(_ID))
    assert client._held == {("trades", _ID)}
    assert client._replayable == {("orderbook", _ID)}


def test_a_retried_subscribe_forces_a_fresh_snapshot_of_a_replayable_orderbook() -> None:
    """The Rust client may have replayed the failed topic: a duplicate subscribe sends no snapshot."""
    fail = {"subscribe_orderbook"}
    client, calls = _channels(fail)
    _run(client.subscribe(_ID))
    fail.clear()
    calls.clear()
    _run(client.subscribe(_ID))
    assert calls == ["subscribe_orderbook", "unsubscribe_orderbook", "subscribe_orderbook"]
    assert (client._held, client._replayable) == ({("trades", _ID), ("orderbook", _ID)}, set())


def test_a_fresh_snapshot_interrupted_by_a_failed_unsubscribe_is_forced_again() -> None:
    fail = {"subscribe_orderbook"}
    client, calls = _channels(fail)
    _run(client.subscribe(_ID))
    fail.clear()
    fail.add("unsubscribe_orderbook")
    _run(client.subscribe(_ID))  # held again, but the fresh snapshot is still owed
    fail.clear()
    calls.clear()
    _run(client.subscribe(_ID))
    assert calls == ["unsubscribe_orderbook", "subscribe_orderbook"]


def test_unsubscribe_ends_a_channel_only_a_reconnect_replay_may_hold() -> None:
    """With no reference held the Rust unsubscribe is a no-op: take one first, then end it."""
    fail = {"subscribe_orderbook"}
    client, calls = _channels(fail)
    _run(client.subscribe(_ID))
    fail.clear()
    calls.clear()
    _run(client.unsubscribe(_ID))
    assert calls == ["unsubscribe_trades", "subscribe_orderbook", "unsubscribe_orderbook"]
    assert (client._held, client._replayable) == (set(), set())


def test_unsubscribe_of_a_never_sent_channel_sends_nothing() -> None:
    client, calls = _channels(set())
    _run(client.unsubscribe(_ID))
    assert calls == []


def test_a_failed_unsubscribe_keeps_the_rest_held_and_a_re_subscribe_restores_only_the_ended() -> (
    None
):
    fail = {"unsubscribe_orderbook"}
    client, calls = _channels(fail)
    _run(client.subscribe(_ID))
    with pytest.raises(ConnectionError, match="unsubscribe_orderbook"):
        asyncio.run(client.unsubscribe(_ID))
    assert client._held == {("orderbook", _ID)}
    calls.clear()
    _run(client.subscribe(_ID))  # a re-add of the lingering id
    assert calls == ["subscribe_trades"]


def test_a_half_failed_resync_is_completed_by_the_removal() -> None:
    fail: set[str] = set()
    client, calls = _channels(fail)
    _run(client.subscribe(_ID))
    fail.add("subscribe_orderbook")
    _run(client.resync_orderbook(_ID))  # the unsubscribe half went through
    fail.clear()
    calls.clear()
    _run(client.unsubscribe(_ID))
    assert calls == ["unsubscribe_trades", "subscribe_orderbook", "unsubscribe_orderbook"]
    assert (client._held, client._replayable) == (set(), set())


if __name__ == "__main__":
    test_value_is_preserved_exactly_across_precisions()
    test_all_results_share_the_same_precision_label()
    print("ok")


def _handler_client() -> tuple[DydxClient, list[object]]:
    """Only the message dispatch: no pyo3 clients are needed to decode a message."""
    received: list[object] = []
    client = object.__new__(DydxClient)
    client._on_data = received.append
    client._ledger = error_ledger.record
    return client, received


@pytest.mark.parametrize("info_type", ["block_height", "new_instrument_discovered"])
def test_the_rust_clients_info_dicts_are_ignored_by_name(info_type: str) -> None:
    error_ledger.reset()
    client, received = _handler_client()
    client._handle_message({"type": info_type, "height": 1})
    assert received == []
    assert error_ledger.counts() == {}


@pytest.mark.parametrize("message", [{"type": "surprise"}, {"no": "type"}, 42])
def test_any_other_undecoded_message_is_ledgered(message: object) -> None:
    error_ledger.reset()
    client, received = _handler_client()
    client._handle_message(message)
    assert received == []
    assert error_ledger.counts() == {"collector.unknown_message": 1}
    detail = error_ledger.last_details()["collector.unknown_message"]
    assert f"{type(message).__name__} not decoded" in detail
