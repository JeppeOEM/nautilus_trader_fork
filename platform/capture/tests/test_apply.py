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
`CaptureService.apply` (Story 25.4, spine AD-D17): the plan is the intent, the applied set is the fact.

A fake client that fails on the wire: a failed subscribe is pending and never sampled, then retried;
a failed unsubscribe leaves the id subscribed but never sampled, its messages counted; every wire
failure is ledgered once per attempt.
"""

import asyncio
import time
from pathlib import Path

from observability import error_ledger

from capture.application.capture_service import CaptureService
from capture.application.config import CoreConfig
from capture.application.ports import Applied
from capture.application.ports import PlanChange
from capture.infrastructure.parquet_writer import ParquetArchiveWriter
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_A = "AAAUSDT-LINEAR.BYBIT"
_B = "BBBUSDT-LINEAR.BYBIT"


class _WireClient:
    """Records every wire call; the ids in `fail_subscribe`/`fail_unsubscribe` raise."""

    def __init__(self, *, resync: bool = False) -> None:
        self.calls: list[str] = []
        self.fail_subscribe: set[str] = set()
        self.fail_unsubscribe: set[str] = set()
        if resync:
            self.resync_orderbook = self._resync

    async def subscribe(self, iid: str) -> None:
        self.calls.append(f"subscribe {iid}")
        if iid in self.fail_subscribe:
            raise ConnectionError(f"subscribe {iid} rejected")

    async def unsubscribe(self, iid: str) -> None:
        self.calls.append(f"unsubscribe {iid}")
        if iid in self.fail_unsubscribe:
            raise ConnectionError(f"unsubscribe {iid} rejected")

    async def _resync(self, iid: str) -> None:
        self.calls.append(f"resync {iid}")


def _collector(tmp_path: Path, client: _WireClient, plan: tuple[str, ...] = ()) -> CaptureService:
    config = CoreConfig(environment="mainnet", catalog_path=str(tmp_path))
    return CaptureService(
        config,
        lambda _on_data, _ledger: client,
        venue="BYBIT",
        plan=plan,
        archive=ParquetArchiveWriter(config.catalog_path),
        live_stream=None,
    )


def _apply(c: CaptureService, **diff: frozenset[str]) -> Applied:
    return asyncio.run(c.apply(PlanChange(**diff)))


def _book(iid: str) -> OrderBookDeltas:
    ts = time.time_ns()
    inst = InstrumentId.from_str(iid)
    deltas = [OrderBookDelta.clear(inst, 0, ts, ts)]
    for side, price in ((OrderSide.BUY, 100.0), (OrderSide.SELL, 100.5)):
        order = BookOrder(side, Price(price, 2), Quantity(1.0, 3), 0)
        deltas.append(OrderBookDelta(inst, BookAction.ADD, order, 0, 0, ts, ts))
    return OrderBookDeltas(inst, deltas)


def _trade(iid: str, n: int) -> TradeTick:
    ts = time.time_ns()
    return TradeTick(
        InstrumentId.from_str(iid),
        Price(100.0, 2),
        Quantity(1.0, 3),
        AggressorSide.BUYER,
        TradeId(str(n)),
        ts,
        ts,
    )


def _sampled(c: CaptureService) -> list[str]:
    batch = asyncio.run(c._sample_tick(time.time_ns()))
    return [str(snapshot.instrument_id) for snapshot in batch]


def test_a_subscribed_id_is_booked_and_sampled(tmp_path: Path) -> None:
    c = _collector(tmp_path, _WireClient())
    applied = _apply(c, added=frozenset({_A}))
    assert (applied.subscribed, applied.failed) == ({_A}, frozenset())
    c._process_data(_book(_A))
    c._feeds.last_book_message_ns = time.time_ns()
    assert _sampled(c) == [_A]
    assert c.capture_status().applied == {_A}


def test_a_subscribe_failed_on_the_wire_is_pending_ledgered_and_not_sampled(tmp_path: Path) -> None:
    error_ledger.reset()
    client = _WireClient()
    client.fail_subscribe.add(_A)
    c = _collector(tmp_path, client)
    applied = _apply(c, added=frozenset({_A}))
    assert applied.failed == {_A}
    assert c.capture_status().pending == {_A}
    assert error_ledger.counts() == {"collector.subscribe_failed": 1}
    c._process_data(_book(_A))  # the snapshot of a subscribe that half-happened
    assert c._live_book(_A) is None
    assert c._unplanned_messages == {_A: 1}
    assert _sampled(c) == []


def test_the_retry_loop_subscribes_a_failed_id_once_the_wire_accepts_it(tmp_path: Path) -> None:
    error_ledger.reset()
    client = _WireClient()
    client.fail_subscribe.add(_A)
    c = _collector(tmp_path, client)
    _apply(c, added=frozenset({_A}))
    asyncio.run(c._retry_subscriptions())  # still failing: ledgered once more
    assert error_ledger.counts() == {"collector.subscribe_failed": 2}
    client.fail_subscribe.clear()
    asyncio.run(c._retry_subscriptions())
    assert c.capture_status().applied == {_A}
    assert c.capture_status().pending == frozenset()
    assert c._retry_subscribe == set()


def test_the_subscribe_time_snapshot_is_booked_not_counted(tmp_path: Path) -> None:
    """The id is marked applied before the subscribe is awaited: its snapshot may beat the ack."""
    client = _WireClient()
    c = _collector(tmp_path, client)

    async def _subscribe_delivering_a_snapshot(iid: str) -> None:
        c._process_data(_book(iid))

    client.subscribe = _subscribe_delivering_a_snapshot  # type: ignore[method-assign]
    _apply(c, added=frozenset({_A}))
    assert c._live_book(_A) is not None
    assert c._unplanned_messages == {}


def test_an_unsubscribe_failed_on_the_wire_clears_the_book_and_counts_its_messages(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    client = _WireClient()
    client.fail_unsubscribe.add(_A)
    c = _collector(tmp_path, client, plan=(_A,))
    _apply(c, added=frozenset({_A}))
    c._process_data(_book(_A))
    applied = _apply(c, removed=frozenset({_A}))
    assert applied.failed == {_A}
    assert c._live_book(_A) is None  # cleared whether or not the wire unsubscribe succeeded
    assert (c._applied, c._plan_ids) == ({_A}, set())  # still subscribed, no longer planned
    c._process_data(_book(_A))
    c._process_data(_trade(_A, 1))
    assert c._live_book(_A) is None
    assert c._buffer.get((TradeTick, _A), []) == []  # never archived
    assert _sampled(c) == []
    c._report_stale_trades()  # the per-flush report
    assert error_ledger.counts() == {
        "collector.unsubscribe_failed": 1,
        "collector.unplanned_message": 1,
    }
    assert c._unplanned_messages == {}  # reset each flush


def test_the_retry_loop_retries_a_failed_unsubscribe(tmp_path: Path) -> None:
    client = _WireClient()
    client.fail_unsubscribe.add(_A)
    c = _collector(tmp_path, client, plan=(_A,))
    _apply(c, added=frozenset({_A}))
    _apply(c, removed=frozenset({_A}))
    client.fail_unsubscribe.clear()
    asyncio.run(c._retry_subscriptions())
    assert c._applied == set()
    assert client.calls[-1] == f"unsubscribe {_A}"


def test_re_adding_a_still_subscribed_id_cancels_the_retry_and_resyncs(tmp_path: Path) -> None:
    client = _WireClient(resync=True)
    client.fail_unsubscribe.add(_A)
    c = _collector(tmp_path, client, plan=(_A,))
    _apply(c, added=frozenset({_A}))
    _apply(c, removed=frozenset({_A}))
    applied = _apply(c, added=frozenset({_A}))
    assert applied.subscribed == {_A}
    assert c._retry_unsubscribe == set()
    # The subscribe restores a channel the failed unsubscribe may have ended (the client's is
    # idempotent per channel); the resync gives the cleared book a fresh baseline.
    assert client.calls == [
        f"subscribe {_A}",
        f"unsubscribe {_A}",
        f"subscribe {_A}",
        f"resync {_A}",
    ]


def test_a_lingering_re_add_whose_subscribe_fails_is_pending_and_retried(tmp_path: Path) -> None:
    client = _WireClient(resync=True)
    client.fail_unsubscribe.add(_A)
    c = _collector(tmp_path, client, plan=(_A,))
    _apply(c, added=frozenset({_A}))
    _apply(c, removed=frozenset({_A}))
    client.fail_subscribe.add(_A)
    applied = _apply(c, added=frozenset({_A}))
    assert applied.failed == {_A}
    assert c.capture_status().pending == {_A}
    assert (c._retry_subscribe, c._retry_unsubscribe) == ({_A}, set())


def test_removing_an_id_whose_subscribe_failed_unsubscribes_it(tmp_path: Path) -> None:
    """A failed subscribe may have sent part of it, or the client may replay it on reconnect."""
    client = _WireClient()
    client.fail_subscribe.add(_A)
    c = _collector(tmp_path, client)
    _apply(c, added=frozenset({_A}))
    applied = _apply(c, removed=frozenset({_A}))
    assert applied.unsubscribed == {_A}
    assert client.calls == [f"subscribe {_A}", f"unsubscribe {_A}"]
    assert (c._applied, c._retry_subscribe) == (set(), set())


def test_a_failed_release_of_a_pending_id_lingers_and_is_retried(tmp_path: Path) -> None:
    client = _WireClient()
    client.fail_subscribe.add(_A)
    client.fail_unsubscribe.add(_A)
    c = _collector(tmp_path, client)
    _apply(c, added=frozenset({_A}))
    applied = _apply(c, removed=frozenset({_A}))
    assert applied.failed == {_A}
    assert c.capture_status().lingering == {_A}
    client.fail_unsubscribe.clear()
    asyncio.run(c._retry_subscriptions())
    assert c.capture_status().lingering == frozenset()
    assert client.calls[-1] == f"unsubscribe {_A}"


def test_removing_an_unlisted_id_sends_nothing(tmp_path: Path) -> None:
    client = _WireClient()
    c = _collector(tmp_path, client)
    c._listed = frozenset()
    _apply(c, added=frozenset({_A}))
    _apply(c, removed=frozenset({_A}))
    assert client.calls == []


def test_an_id_the_venue_does_not_list_is_pending_and_never_retried(tmp_path: Path) -> None:
    error_ledger.reset()
    client = _WireClient()
    c = _collector(tmp_path, client)
    c._listed = frozenset({_B})
    applied = _apply(c, added=frozenset({_A, _B}))
    assert (applied.subscribed, applied.failed) == ({_B}, {_A})
    assert client.calls == [f"subscribe {_B}"]
    assert c._retry_subscribe == set()
    assert c.capture_status().pending == {_A}
    assert error_ledger.counts() == {"collector.subscribe_failed": 1}


def test_mark_and_funding_data_is_never_gated_by_the_applied_set(tmp_path: Path) -> None:
    """Venue-wide channels carry every market: those types are archived as before (Story 25.4)."""
    c = _collector(tmp_path, _WireClient())
    mark = MarkPriceUpdate(InstrumentId.from_str(_A), Price(1.0, 2), 1, 1)
    c._process_data(mark)
    assert c._buffer[(MarkPriceUpdate, _A)] == [mark]
    assert c._unplanned_messages == {}


def test_a_failed_unsubscribe_is_reported_lingering_not_applied(tmp_path: Path) -> None:
    client = _WireClient()
    client.fail_unsubscribe.add(_A)
    c = _collector(tmp_path, client, plan=(_A,))
    _apply(c, added=frozenset({_A}))
    _apply(c, removed=frozenset({_A}))
    status = c.capture_status()
    assert (status.applied, status.pending, status.lingering) == (set(), set(), {_A})


def test_a_removed_id_leaves_no_book_time_behind(tmp_path: Path) -> None:
    c = _collector(tmp_path, _WireClient(), plan=(_A,))
    _apply(c, added=frozenset({_A}))
    c._process_data(_book(_A))
    _apply(c, removed=frozenset({_A}))
    assert _A not in c.capture_status().last_book_update_ns


class _GatedClient(_WireClient):
    """A subscribe of `gated` blocks until `release` is set, so a round can be interleaved."""

    def __init__(self, gated: str) -> None:
        super().__init__()
        self.gated = gated
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def subscribe(self, iid: str) -> None:
        await super().subscribe(iid)
        if iid == self.gated:
            self.entered.set()
            await self.release.wait()


def test_a_removal_during_a_retry_round_leaves_no_untracked_subscription(tmp_path: Path) -> None:
    """
    Without the subscription lock the removal ran mid-round (B not yet applied, so no unsubscribe)
    and the round's snapshot then subscribed B: applied, unplanned, never unsubscribed. With it the
    removal waits for the round and unsubscribes what the round subscribed.
    """

    async def _run() -> tuple[CaptureService, _GatedClient]:
        client = _GatedClient(gated=_A)
        c = _collector(tmp_path, client, plan=(_A, _B))
        c._retry_subscribe = {_A, _B}
        retry = asyncio.create_task(c._retry_subscriptions())
        await client.entered.wait()  # the round is awaiting A's subscribe
        remove = asyncio.create_task(c.apply(PlanChange(removed=frozenset({_B}))))
        await asyncio.sleep(0)
        client.release.set()
        await asyncio.gather(retry, remove)
        return c, client

    c, client = asyncio.run(_run())
    assert client.calls[-1] == f"unsubscribe {_B}"
    assert (c._applied, c._plan_ids) == ({_A}, {_A})


class _GatedResyncClient(_WireClient):
    """A resync blocks between its unsubscribe and subscribe halves until `release` is set."""

    def __init__(self) -> None:
        super().__init__(resync=True)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.resync_orderbook = self._gated_resync

    async def _gated_resync(self, iid: str) -> None:
        self.calls.append(f"resync-unsubscribe {iid}")
        self.entered.set()
        await self.release.wait()
        self.calls.append(f"resync-subscribe {iid}")


def test_a_removal_during_a_resync_unsubscribes_after_it(tmp_path: Path) -> None:
    """
    Without the lock the removal's unsubscribe ran between the resync's two halves, and the
    resync's subscribe then left A's book subscribed with nothing tracking it.
    """

    async def _run() -> tuple[CaptureService, _GatedResyncClient]:
        client = _GatedResyncClient()
        c = _collector(tmp_path, client, plan=(_A,))
        await c.apply(PlanChange(added=frozenset({_A})))
        resync = asyncio.create_task(c._resync(_A))
        await client.entered.wait()
        remove = asyncio.create_task(c.apply(PlanChange(removed=frozenset({_A}))))
        await asyncio.sleep(0)
        client.release.set()
        await asyncio.gather(resync, remove)
        return c, client

    c, client = asyncio.run(_run())
    assert client.calls[-2:] == [f"resync-subscribe {_A}", f"unsubscribe {_A}"]
    assert c._applied == set()


def test_a_resync_during_a_wire_change_is_queued_not_sent(tmp_path: Path) -> None:
    async def _run() -> tuple[CaptureService, _WireClient]:
        client = _WireClient(resync=True)
        c = _collector(tmp_path, client, plan=(_A,))
        await c.apply(PlanChange(added=frozenset({_A})))
        async with c._subscription_lock:
            await c._resync(_A)
        return c, client

    c, client = asyncio.run(_run())
    assert client.calls == [f"subscribe {_A}"]
    assert c._resync_pending() == {_A}


def test_a_removed_id_drops_its_queued_resync(tmp_path: Path) -> None:
    client = _WireClient(resync=True)
    c = _collector(tmp_path, client, plan=(_A,))
    _apply(c, added=frozenset({_A}))
    c._book(_A).resync_pending = True
    _apply(c, removed=frozenset({_A}))
    asyncio.run(c._resync(_A))
    assert c._resync_pending() == set()
    assert f"resync {_A}" not in client.calls


class _BookingFailingClient(_WireClient):
    """Books a message for the id during the subscribe await, then fails the subscribe."""

    def __init__(self) -> None:
        super().__init__()
        self.collector: CaptureService | None = None

    async def subscribe(self, iid: str) -> None:
        assert self.collector is not None
        self.collector._process_data(_book(iid))
        raise ConnectionError(f"subscribe {iid} rejected")


def test_a_failed_subscribe_leaves_no_book_time_on_its_pending_row(tmp_path: Path) -> None:
    client = _BookingFailingClient()
    c = _collector(tmp_path, client, plan=(_A,))
    client.collector = c
    _apply(c, added=frozenset({_A}))
    status = c.capture_status()
    assert (status.pending, status.last_book_update_ns) == ({_A}, {})


def test_no_apply_is_reported_before_the_first_one(tmp_path: Path) -> None:
    status = _collector(tmp_path, _WireClient(), plan=(_A,)).capture_status()
    assert (status.last_applied, status.last_applied_ns) == (None, 0)


def test_the_most_recent_apply_is_reported_with_its_time(tmp_path: Path) -> None:
    error_ledger.reset()
    client = _WireClient()
    client.fail_subscribe.add(_B)
    c = _collector(tmp_path, client)
    before_ns = time.time_ns()
    failed = _apply(c, added=frozenset({_A, _B}))
    status = c.capture_status()
    assert status.last_applied == failed
    assert status.last_applied_ns >= before_ns
    removed = _apply(c, removed=frozenset({_A}))
    assert c.capture_status().last_applied == removed
    assert c.capture_status().last_applied_ns >= status.last_applied_ns


def test_a_successful_removal_forgets_the_book_buffers_nothing_and_deletes_no_file(
    tmp_path: Path,
) -> None:
    """Story 29.4 AC 3: a runtime removal (Bybit/Hyperliquid) never touches the archive."""
    archived = tmp_path / "data" / "trade_tick" / _A / "part-0.parquet"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"archived before the removal")
    before = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    client = _WireClient()
    c = _collector(tmp_path, client, plan=(_A,))
    _apply(c, added=frozenset({_A}))
    c._process_data(_book(_A))
    applied = _apply(c, removed=frozenset({_A}))
    assert applied.unsubscribed == {_A}
    assert client.calls == [f"subscribe {_A}", f"unsubscribe {_A}"]
    assert c._live_book(_A) is None
    c._process_data(_book(_A))  # a message already in flight when the unsubscribe landed
    c._process_data(_trade(_A, 1))
    assert c._live_book(_A) is None
    assert c._buffer.get((TradeTick, _A), []) == []
    assert _sampled(c) == []
    assert sorted(p for p in tmp_path.rglob("*") if p.is_file()) == before
    assert archived.read_bytes() == b"archived before the removal"
