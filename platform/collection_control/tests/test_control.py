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
`ControlService` and `StatusPublisher` (Story 6.1's control actions, moved in Story 25.4) against
fake ports: the plan's commands, save-then-apply-then-publish, and the failure matrix.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from capture.application.ports import Applied
from capture.application.ports import CaptureStatus
from capture.application.ports import PlanDiff
from observability import error_ledger

import collection_control.application.control as control_module
from collection_control.application.control import ControlService
from collection_control.application.ports import STATUS_CHANNEL
from collection_control.application.status import StatusPublisher
from collection_control.domain.plan import CollectionPlan
from collection_control.domain.plan import InstrumentEntry


_CAP = 30
_MIN_USD = 100_000.0
# What `bot_tui.collector_state.publish_control` sent before Story 29.2, recorded from it unchanged.
_CONTROL_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "control_payloads.json").read_text()
)


def _plan(*ids: str, excluded: frozenset[str] = frozenset()) -> CollectionPlan:
    return CollectionPlan(
        venue="DYDX",
        instruments=tuple(InstrumentEntry(id=iid) for iid in ids),
        cap=_CAP,
        excluded=excluded,
        min_liquidity_usd=_MIN_USD,
        non_config_retain_hours=4.0,
    )


class _Capture:
    """
    Records every diff; everything planned counts as applied unless listed in `pending`. Reports
    the last diff as its last apply, time-stamped with the apply count.
    """

    def __init__(self) -> None:
        self.diffs: list[PlanDiff] = []
        self.planned: tuple[str, ...] = ()
        self.pending: frozenset[str] = frozenset()
        self.lingering: frozenset[str] = frozenset()
        self.last_applied: Applied | None = None

    async def apply(self, diff: PlanDiff) -> Applied:
        self.diffs.append(diff)
        self.last_applied = Applied(subscribed=diff.added, unsubscribed=diff.removed)
        return self.last_applied

    def capture_status(self) -> CaptureStatus:
        return CaptureStatus(
            applied=frozenset(self.planned) - self.pending,
            pending=self.pending,
            last_book_update_ns={},
            trade_backfill={},
            lingering=self.lingering,
            last_applied=self.last_applied,
            last_applied_ns=len(self.diffs),  # a fake clock: one tick per apply
        )


class _Store:
    def __init__(self, plan: CollectionPlan) -> None:
        self.plan = plan
        self.saved: list[CollectionPlan] = []
        self.fail: Exception | None = None

    def load(self) -> CollectionPlan:
        if self.fail is not None:
            raise self.fail
        return self.plan

    def save(self, plan: CollectionPlan) -> None:
        if self.fail is not None:
            raise self.fail
        self.saved.append(plan)
        self.plan = plan


class _Bus:
    def __init__(self) -> None:
        self.published: list[str] = []
        self.closed = False

    async def publish(self, message: str) -> None:
        self.published.append(message)

    async def aclose(self) -> None:
        self.closed = True


class _Markets:
    def __init__(self, volumes: dict[str, Any] | None = None) -> None:
        self.volumes = volumes or {}
        self.fetches = 0

    async def fetch(self) -> dict[str, Any]:
        self.fetches += 1
        return {"markets": {t: {"ticker": t, "volume24H": v} for t, v in self.volumes.items()}}


class _Rig:
    def __init__(self, plan: CollectionPlan, volumes: dict[str, Any] | None = None) -> None:
        self.capture = _Capture()
        self.capture.planned = plan.collected
        self.store = _Store(plan)
        self.bus = _Bus()
        # A plan with no threshold (Bybit, Hyperliquid) is wired with no markets source.
        self.markets = _Markets(volumes)
        markets = self.markets if plan.min_liquidity_usd is not None else None
        self.status = StatusPublisher(self.capture, self.bus, markets, accepts_commands=True)
        self.control = ControlService(plan, self.store, self.capture, self.status, markets)

    def handle(self, action: str | None, iid: str | None = None) -> None:
        asyncio.run(self.control.handle(action, iid))

    def payloads(self) -> list[dict]:
        return [json.loads(message) for message in self.bus.published]


# -- the four actions ----------------------------------------------------------------------------


def test_start_saves_then_applies_then_publishes() -> None:
    rig = _Rig(_plan())
    rig.handle("start", "SOL-USD-PERP.DYDX")
    assert rig.control.plan.collected == ("SOL-USD-PERP.DYDX",)
    assert rig.store.saved == [rig.control.plan]
    assert [d.added for d in rig.capture.diffs] == [{"SOL-USD-PERP.DYDX"}]
    aggregate = rig.payloads()[-1]
    assert aggregate["unpinned_ids"] == []
    assert aggregate["last_apply"] == {
        "ts": 1,
        "subscribed": ["SOL-USD-PERP.DYDX"],
        "unsubscribed": [],
        "failed": [],
    }


def test_start_clears_the_id_from_exclude() -> None:
    rig = _Rig(_plan(excluded=frozenset({"SOL-USD-PERP.DYDX"})))
    rig.handle("start", "SOL-USD-PERP.DYDX")
    assert rig.control.plan.excluded == frozenset()


def test_unpin_removes_excludes_and_publishes_the_tombstone() -> None:
    rig = _Rig(_plan("BTC-USD-PERP.DYDX"))
    rig.handle("unpin", "BTC-USD-PERP.DYDX")
    assert rig.store.plan.collected == ()
    assert rig.store.plan.excluded == {"BTC-USD-PERP.DYDX"}
    assert [d.removed for d in rig.capture.diffs] == [{"BTC-USD-PERP.DYDX"}]
    aggregate, tombstone = rig.payloads()[-2:]
    assert aggregate["unpinned_ids"] == ["BTC-USD-PERP.DYDX"]
    assert tombstone == {"id": "BTC-USD-PERP.DYDX", "removed": True}


def test_stop_removes_without_excluding_and_publishes_the_tombstone() -> None:
    rig = _Rig(_plan("BTC-USD-PERP.DYDX"))
    rig.handle("stop", "BTC-USD-PERP.DYDX")
    assert (rig.control.plan.collected, rig.control.plan.excluded) == ((), frozenset())
    assert {"id": "BTC-USD-PERP.DYDX", "removed": True} in rig.payloads()


@pytest.mark.parametrize(
    ("plan", "action", "iid", "warning"),
    [
        (_plan("BTC-USD-PERP.DYDX"), "start", "BTC-USD-PERP.DYDX", "already collected"),
        (
            _plan(*(f"C{i}-USD-PERP.DYDX" for i in range(_CAP))),
            "start",
            "NEW-USD-PERP.DYDX",
            "at 30-instrument cap",
        ),
        (_plan(), "unpin", "UNKNOWN-USD-PERP.DYDX", "not currently collected"),
        (_plan(), "stop", "UNKNOWN-USD-PERP.DYDX", "not currently collected"),
        (
            _plan("BTC-USD-PERP.DYDX"),
            "frobnicate",
            "BTC-USD-PERP.DYDX",
            "Unknown collector:control action",
        ),
        (_plan(), "start", None, "no instrument id"),
    ],
)
def test_a_refused_command_warns_and_changes_nothing(
    plan: CollectionPlan, action: str, iid: str | None, warning: str, caplog: Any
) -> None:
    rig = _Rig(plan)
    with caplog.at_level(logging.WARNING, logger=control_module.__name__):
        rig.handle(action, iid)
    assert warning in caplog.text
    assert (rig.control.plan, rig.store.saved, rig.capture.diffs) == (plan, [], [])
    # A plan refusal is published as `last_refusal` (Story 29.5); an unknown action is not one.
    if action == "frobnicate":
        assert rig.bus.published == []
        return
    aggregates = [p for p in rig.payloads() if "unpinned_ids" in p]
    assert len(aggregates) == 1  # one publish: the rows, then this aggregate last
    assert rig.payloads()[-1] == aggregates[0]
    refusal = aggregates[0]["last_refusal"]
    assert (refusal["action"], refusal["id"]) == (action, iid)
    assert warning in refusal["reason"]


def test_a_refused_start_is_recorded_and_published_at_once() -> None:
    error_ledger.reset()
    rig = _Rig(_plan(*(f"C{i}-USD-PERP.DYDX" for i in range(_CAP))))
    before = time.time_ns()
    rig.handle("start", "NEW-USD-PERP.DYDX")
    aggregate = rig.payloads()[-1]
    refusal = aggregate["last_refusal"]
    assert list(aggregate)[-3:] == ["last_apply", "last_refusal", "liquidations"]
    assert list(refusal) == ["ts", "action", "id", "reason"]
    assert (refusal["action"], refusal["id"]) == ("start", "NEW-USD-PERP.DYDX")
    assert "at 30-instrument cap" in refusal["reason"]
    assert before <= refusal["ts"] <= time.time_ns()
    assert [p.get("id") for p in rig.payloads()[:-1]] == list(rig.control.plan.collected)
    assert error_ledger.counts() == {}


def test_a_refused_pin_top_liquid_is_published_with_a_null_id() -> None:
    rig = _Rig(_bybit_plan())
    _route(rig, _message("pin_top_liquid", venue="BYBIT"))
    refusal = rig.payloads()[-1]["last_refusal"]
    assert (refusal["action"], refusal["id"]) == ("pin_top_liquid", None)
    assert "admits no pins" in refusal["reason"]


def test_a_later_success_keeps_the_last_refusal_until_the_next_one() -> None:
    rig = _Rig(_plan("BTC-USD-PERP.DYDX"))
    rig.handle("start", "BTC-USD-PERP.DYDX")
    rig.handle("start", "SOL-USD-PERP.DYDX")
    refusal = rig.payloads()[-1]["last_refusal"]
    assert (refusal["action"], refusal["id"]) == ("start", "BTC-USD-PERP.DYDX")


def test_start_one_below_the_cap_succeeds() -> None:
    rig = _Rig(_plan(*(f"C{i}-USD-PERP.DYDX" for i in range(_CAP - 1))))
    rig.handle("start", "NEW-USD-PERP.DYDX")
    assert len(rig.control.plan.collected) == _CAP


# -- pin_top_liquid ------------------------------------------------------------------------------


def test_pin_top_liquid_fills_the_empty_slots() -> None:
    rig = _Rig(_plan("PIN-USD-PERP.DYDX"), {"AAA": 500_000.0, "BBB": 400_000.0, "LOW": 50.0})
    rig.handle("pin_top_liquid")
    assert rig.control.plan.collected == ("PIN-USD-PERP.DYDX", "AAA-PERP.DYDX", "BBB-PERP.DYDX")
    assert rig.capture.diffs[0].added == {"AAA-PERP.DYDX", "BBB-PERP.DYDX"}


def test_pin_top_liquid_never_re_adds_an_unpinned_id() -> None:
    """An unpin must stick: re-adding is always an explicit `start`."""
    rig = _Rig(_plan(excluded=frozenset({"AAA-PERP.DYDX"})), {"AAA": 500_000.0})
    rig.handle("pin_top_liquid")
    assert rig.control.plan.collected == ()
    assert rig.control.plan.excluded == {"AAA-PERP.DYDX"}


def test_pin_top_liquid_never_removes_or_exceeds_the_cap() -> None:
    existing = tuple(f"PIN{i}-PERP.DYDX" for i in range(5))
    rig = _Rig(_plan(*existing), {f"COIN{i}": float(1_000_000 - i) for i in range(40)})
    rig.handle("pin_top_liquid")
    assert rig.control.plan.collected[:5] == existing
    assert len(rig.control.plan.collected) == _CAP
    assert rig.control.plan.collected[5:] == tuple(
        sorted(f"COIN{i}-PERP.DYDX" for i in range(_CAP - 5))
    )


def test_pin_top_liquid_does_not_duplicate_a_collected_id() -> None:
    rig = _Rig(_plan("AAA-PERP.DYDX"), {"AAA": 500_000.0})
    rig.handle("pin_top_liquid")
    assert rig.control.plan.collected == ("AAA-PERP.DYDX",)


def test_pin_top_liquid_at_the_cap_fetches_nothing() -> None:
    rig = _Rig(_plan(*(f"C{i}-USD-PERP.DYDX" for i in range(_CAP))), {"AAA": 500_000.0})
    rig.handle("pin_top_liquid")
    assert rig.markets.fetches == 0
    assert rig.store.saved == []


def test_an_unparseable_volume_is_ledgered_never_pinned() -> None:
    error_ledger.reset()
    rig = _Rig(_plan(), {"BAD": "n/a", "AAA": 500_000.0})
    rig.handle("pin_top_liquid")
    assert rig.control.plan.collected == ("AAA-PERP.DYDX",)
    assert error_ledger.counts() == {"open_interest.volume24h": 1}


# -- the failure matrix --------------------------------------------------------------------------


def test_a_failed_save_applies_nothing_and_keeps_the_plan() -> None:
    error_ledger.reset()
    plan = _plan("BTC-USD-PERP.DYDX")
    rig = _Rig(plan)
    rig.store.fail = ValueError("file invalid mid-edit")
    rig.handle("stop", "BTC-USD-PERP.DYDX")
    assert (rig.control.plan, rig.capture.diffs, rig.bus.published) == (plan, [], [])
    assert error_ledger.counts() == {"collector.control": 1}


def test_a_reload_of_a_bad_file_keeps_the_current_plan() -> None:
    error_ledger.reset()
    plan = _plan("BTC-USD-PERP.DYDX")
    rig = _Rig(plan)
    rig.store.fail = ValueError("hand-edited typo")
    asyncio.run(rig.control.reload())
    assert (rig.control.plan, rig.capture.diffs) == (plan, [])
    assert error_ledger.counts() == {"collector.config_reload": 1}


def test_a_reload_applies_what_the_hand_edit_changed() -> None:
    rig = _Rig(_plan("A.DYDX", "B.DYDX"))
    rig.store.plan = _plan("B.DYDX", "C.DYDX")
    asyncio.run(rig.control.reload())
    assert rig.control.plan == _plan("B.DYDX", "C.DYDX")
    assert [(d.added, d.removed) for d in rig.capture.diffs] == [({"C.DYDX"}, {"A.DYDX"})]


def test_a_reload_that_only_changes_delta_storage_is_applied() -> None:
    rig = _Rig(_plan("A.DYDX"))
    rig.store.plan = CollectionPlan(
        venue="DYDX",
        instruments=(InstrumentEntry("A.DYDX", store_order_book_deltas=True),),
        cap=_CAP,
        min_liquidity_usd=_MIN_USD,
        non_config_retain_hours=4.0,
    )
    asyncio.run(rig.control.reload())
    assert [d.store_deltas for d in rig.capture.diffs] == [{"A.DYDX"}]


def test_a_reload_publishes_at_once_like_a_command() -> None:
    rig = _Rig(_plan("A.DYDX", "B.DYDX"))
    rig.store.plan = _plan("B.DYDX", "C.DYDX")
    asyncio.run(rig.control.reload())
    assert [p.get("id") for p in rig.payloads()] == ["B.DYDX", "C.DYDX", None, "A.DYDX"]
    assert rig.payloads()[-1] == {"id": "A.DYDX", "removed": True}


def test_start_is_refused_while_lingering_subscriptions_hold_the_wire_slots() -> None:
    """The cap guards the venue's per-connection limit, which counts the wire, not the plan."""
    plan = _plan(*(f"C{i}-USD-PERP.DYDX" for i in range(_CAP - 1)))
    rig = _Rig(plan)
    rig.capture.lingering = frozenset(
        {"OLD-USD-PERP.DYDX"}
    )  # its unsubscribe failed: still on the wire
    rig.handle("start", "NEW-USD-PERP.DYDX")
    assert (rig.control.plan, rig.capture.diffs) == (plan, [])


def test_starting_a_lingering_id_again_needs_no_new_wire_slot() -> None:
    """Capture reuses a lingering id's subscription on re-add, so it holds no extra slot."""
    rig = _Rig(_plan(*(f"C{i}-USD-PERP.DYDX" for i in range(_CAP - 1))))
    rig.capture.lingering = frozenset({"OLD-USD-PERP.DYDX"})
    rig.handle("start", "OLD-USD-PERP.DYDX")
    assert [d.added for d in rig.capture.diffs] == [{"OLD-USD-PERP.DYDX"}]


def test_a_reload_that_would_exceed_the_wire_cap_keeps_the_current_plan() -> None:
    error_ledger.reset()
    plan = _plan(*(f"C{i}-USD-PERP.DYDX" for i in range(_CAP - 1)))
    rig = _Rig(plan)
    rig.capture.lingering = frozenset({"OLD-USD-PERP.DYDX"})
    rig.store.plan = _plan(*(f"C{i}-USD-PERP.DYDX" for i in range(_CAP - 1)), "NEW-USD-PERP.DYDX")
    asyncio.run(rig.control.reload())
    assert (rig.control.plan, rig.capture.diffs) == (plan, [])
    assert error_ledger.counts() == {"collector.config_reload": 1}


def test_a_reload_that_only_removes_is_adopted_despite_lingering_ids() -> None:
    rig = _Rig(_plan(*(f"C{i}-USD-PERP.DYDX" for i in range(_CAP))))
    rig.capture.lingering = frozenset({"OLD-USD-PERP.DYDX"})
    rig.store.plan = _plan(*(f"C{i}-USD-PERP.DYDX" for i in range(_CAP - 1)))
    asyncio.run(rig.control.reload())
    assert [d.removed for d in rig.capture.diffs] == [{f"C{_CAP - 1}-USD-PERP.DYDX"}]


def test_pin_top_liquid_with_nothing_to_pin_saves_nothing() -> None:
    rig = _Rig(_plan("AAA-PERP.DYDX"), {"AAA": 500_000.0, "LOW": 5.0})
    rig.handle("pin_top_liquid")
    assert (rig.store.saved, rig.capture.diffs) == ([], [])


def test_pin_top_liquid_leaves_the_lingering_subscriptions_their_slots() -> None:
    rig = _Rig(
        _plan(*(f"C{i}-USD-PERP.DYDX" for i in range(_CAP - 3))), {"X": 9e6, "Y": 8e6, "Z": 7e6}
    )
    rig.capture.lingering = frozenset({"OLD1", "OLD2"})
    rig.handle("pin_top_liquid")
    assert rig.capture.diffs[0].added == {"X-PERP.DYDX"}


def test_a_failed_publish_after_a_saved_and_applied_change_is_ledgered_not_raised() -> None:
    error_ledger.reset()
    rig = _Rig(_plan("BTC-USD-PERP.DYDX"))

    async def _broken(message: str) -> None:
        raise ConnectionError("redis down")

    rig.bus.publish = _broken  # type: ignore[method-assign]
    rig.handle("stop", "BTC-USD-PERP.DYDX")
    assert (rig.control.plan.collected, len(rig.capture.diffs)) == ((), 1)
    assert error_ledger.counts() == {"collector.status_loop": 1}


def test_an_unchanged_reload_applies_nothing() -> None:
    rig = _Rig(_plan("A.DYDX"))
    asyncio.run(rig.control.reload())
    assert rig.capture.diffs == []


# -- collector:status ----------------------------------------------------------------------------


def test_a_planned_instrument_capture_has_not_applied_is_published_pending() -> None:
    rig = _Rig(_plan("A.DYDX", "B.DYDX"))
    rig.capture.pending = frozenset({"B.DYDX"})
    asyncio.run(rig.status.publish(rig.control.plan))
    assert rig.payloads()[:2] == [
        {"id": "A.DYDX", "liquid": False, "last_trade_ts": 0, "trade_backfill": 0},
        {"id": "B.DYDX", "liquid": False, "last_trade_ts": 0, "trade_backfill": 0, "pending": True},
    ]


def test_an_added_instrument_capture_has_not_reached_yet_is_published_pending() -> None:
    """Mid-apply, capture's plan mirror lists the id neither applied nor pending."""
    rig = _Rig(_plan("A.DYDX", "B.DYDX"))
    rig.capture.planned = ("A.DYDX",)
    asyncio.run(rig.status.publish(rig.control.plan))
    assert rig.payloads()[1] == {
        "id": "B.DYDX",
        "liquid": False,
        "last_trade_ts": 0,
        "trade_backfill": 0,
        "pending": True,
    }


def test_hand_edited_exclude_entries_are_published_as_unpinned() -> None:
    rig = _Rig(_plan(excluded=frozenset({"HANDEDITED-PERP.DYDX"})))
    asyncio.run(rig.status.publish(rig.control.plan))
    assert rig.payloads()[-1]["unpinned_ids"] == ["HANDEDITED-PERP.DYDX"]


def test_the_status_loop_still_publishes_when_the_indexer_is_down() -> None:
    error_ledger.reset()
    rig = _Rig(_plan("BTC-PERP.DYDX"))

    async def _down() -> dict[str, Any]:
        raise ConnectionError("indexer unreachable")

    rig.markets.fetch = _down  # type: ignore[method-assign]

    async def _run() -> None:
        task = asyncio.create_task(rig.status.loop(lambda: rig.control.plan, 999_999))
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    assert rig.payloads()[0]["id"] == "BTC-PERP.DYDX"
    assert error_ledger.counts() == {"collector.status_loop": 1}


def test_the_status_loop_republishes_a_row_a_retry_applied_before_its_next_refresh() -> None:
    """Capture retries every 30 s; the row must not read pending for the 1800 s refresh interval."""
    rig = _Rig(_plan("A.DYDX"))
    rig.capture.pending = frozenset({"A.DYDX"})

    async def _run() -> None:
        task = asyncio.create_task(rig.status.loop(lambda: rig.control.plan, 999_999, poll=0.01))
        await asyncio.sleep(0.03)
        rig.capture.pending = frozenset()  # the retry loop subscribed it
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    rows = [p for p in rig.payloads() if p.get("id") == "A.DYDX"]
    assert [row.get("pending", False) for row in rows] == [True, False]


def test_a_refusal_recorded_mid_publish_is_republished_at_the_next_poll() -> None:
    """
    `record_refusal` takes no lock: one landing while a burst is on the bus was not in that burst,
    so it must not count as shown -- else it waits for the 1800 s refresh and the TUI's add reads
    "no answer" instead of the reason.
    """
    rig = _Rig(_plan("A.DYDX"))
    publish = rig.bus.publish

    async def _refuse_during_first_burst(message: str) -> None:
        if not rig.bus.published:
            rig.status.record_refusal("start", "NEW.DYDX", "at 30-instrument cap")
        await publish(message)

    rig.bus.publish = _refuse_during_first_burst  # type: ignore[method-assign]

    async def _run() -> None:
        task = asyncio.create_task(rig.status.loop(lambda: rig.control.plan, 999_999, poll=0.01))
        await asyncio.sleep(0.08)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    refusals = [p["last_refusal"] for p in rig.payloads() if "unpinned_ids" in p]
    assert refusals[0] is None
    assert refusals[1] is not None
    assert refusals[1]["id"] == "NEW.DYDX"


def test_the_status_loop_publishes_before_its_first_sleep() -> None:
    """A sleep-first loop left the TUI on "waiting for collector:status" for 30 min (prod bug)."""
    rig = _Rig(_plan("BTC-PERP.DYDX"), {"BTC": 500_000.0})

    async def _run() -> None:
        task = asyncio.create_task(rig.status.loop(lambda: rig.control.plan, 999_999))
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    assert rig.payloads()[0] == {
        "id": "BTC-PERP.DYDX",
        "liquid": True,
        "last_trade_ts": 0,
        "trade_backfill": 0,
    }
    assert rig.bus.closed


# -- collector:control ---------------------------------------------------------------------------


class _Channel:
    """Yields `batches` one `listen()` at a time; a batch ending in an exception raises it."""

    def __init__(self, *batches: list[object]) -> None:
        self.batches = list(batches)
        self.closed = False

    async def listen(self) -> AsyncIterator[str]:
        if not self.batches:
            await asyncio.Event().wait()  # connected, idle
        for item in self.batches.pop(0):
            if isinstance(item, Exception):
                raise item
            yield str(item)

    async def aclose(self) -> None:
        self.closed = True


def test_the_control_loop_survives_a_bad_message_and_a_lost_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error_ledger.reset()
    monkeypatch.setattr(control_module, "CONTROL_RECONNECT_SECONDS", 0.0)
    rig = _Rig(_plan())
    channel = _Channel(
        ["not json", ConnectionError("redis gone")],
        [json.dumps({"action": "start", "id": "SOL-USD-PERP.DYDX"})],
    )

    async def _run() -> None:
        task = asyncio.create_task(rig.control.control_loop(channel))
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    assert rig.control.plan.collected == ("SOL-USD-PERP.DYDX",)
    assert error_ledger.counts() == {"collector.control": 1, "collector.control_redis": 1}
    assert channel.closed


def test_the_status_channel_is_the_published_name() -> None:
    assert STATUS_CHANNEL == "collector:status"


_BTC = "BTC-USD-PERP.DYDX"


@pytest.mark.parametrize(
    ("index", "before", "collected", "excluded"),
    [
        (0, (), (_BTC,), frozenset()),  # start
        (1, (_BTC,), (), frozenset({_BTC})),  # unpin
        (2, (_BTC,), (), frozenset()),  # stop
        (3, (), (_BTC,), frozenset()),  # pin_top_liquid
    ],
)
def test_each_recorded_control_payload_maps_to_its_plan_command(
    index: int, before: tuple[str, ...], collected: tuple[str, ...], excluded: frozenset[str]
) -> None:
    """The recorded `collector:control` bytes (no `venue` field) still drive dYdX's plan."""
    channel_name, payload = _CONTROL_FIXTURE["published"][index]
    assert channel_name == "collector:control"
    rig = _Rig(_plan(*before), {"BTC-USD": 500_000.0})

    # The loop's own per-message step, awaited to completion: no wall-clock wait to race.
    asyncio.run(rig.control._handle_message(payload))
    assert (rig.control.plan.collected, rig.control.plan.excluded) == (collected, excluded)


# -- venue routing (Story 29.4) ------------------------------------------------------------------

_SOL_BYBIT = "SOLUSDT-LINEAR.BYBIT"


def _bybit_plan(*ids: str) -> CollectionPlan:
    return CollectionPlan(
        venue="BYBIT", instruments=tuple(InstrumentEntry(id=iid) for iid in ids), cap=None
    )


def _message(action: str, iid: str | None = None, **extra: object) -> str:
    payload: dict[str, object] = {"action": action}
    if iid is not None:
        payload["id"] = iid
    return json.dumps({**payload, **extra})


def _route(rig: _Rig, message: str) -> None:
    asyncio.run(rig.control._handle_message(message))


def test_a_venue_addressed_message_drives_only_that_venue() -> None:
    error_ledger.reset()
    bybit, dydx = _Rig(_bybit_plan()), _Rig(_plan())
    for rig in (bybit, dydx):
        _route(rig, _message("start", _SOL_BYBIT, venue="BYBIT"))
    assert bybit.control.plan.collected == (_SOL_BYBIT,)
    assert (dydx.store.saved, dydx.capture.diffs) == ([], [])
    assert error_ledger.counts() == {}  # a known other venue is ignored silently


def test_a_legacy_message_is_dydx_s_and_ignored_by_every_other_venue() -> None:
    bybit, dydx = _Rig(_bybit_plan()), _Rig(_plan())
    for rig in (bybit, dydx):
        _route(rig, _message("start", "SOL-USD-PERP.DYDX"))
    assert dydx.control.plan.collected == ("SOL-USD-PERP.DYDX",)
    assert (bybit.store.saved, bybit.capture.diffs, bybit.bus.published) == ([], [], [])


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        (_bybit_plan(), _message("start", "SOL-USD-PERP.HYPERLIQUID", venue="BYBIT")),
        (_plan(), _message("start", _SOL_BYBIT)),  # a legacy message carrying a Bybit id
        (_bybit_plan(), _message("start", "NO-VENUE-SUFFIX", venue="BYBIT")),
    ],
)
def test_an_id_of_another_venue_is_refused_by_the_receiving_plan(
    plan: CollectionPlan, message: str, caplog: Any
) -> None:
    rig = _Rig(plan)
    with caplog.at_level(logging.WARNING, logger=control_module.__name__):
        _route(rig, message)
    assert f"not a {plan.venue} instrument" in caplog.text
    assert (rig.control.plan, rig.store.saved, rig.capture.diffs) == (plan, [], [])


@pytest.mark.parametrize("venue", [3, "BINANCE", "bybit"])
def test_a_malformed_or_unknown_venue_is_ledgered_and_ignored(venue: object) -> None:
    error_ledger.reset()
    rig = _Rig(_bybit_plan())
    _route(rig, _message("start", _SOL_BYBIT, venue=venue))
    assert (rig.store.saved, rig.capture.diffs) == ([], [])
    assert error_ledger.counts() == {"collector.control": 1}


def test_an_uncapped_plan_starts_whatever_lingers_on_the_wire() -> None:
    rig = _Rig(_bybit_plan(*(f"C{i}USDT-LINEAR.BYBIT" for i in range(40))))
    rig.capture.lingering = frozenset({"OLDUSDT-LINEAR.BYBIT"})
    rig.handle("start", _SOL_BYBIT)
    assert [d.added for d in rig.capture.diffs] == [{_SOL_BYBIT}]


def test_pin_top_liquid_on_an_uncapped_plan_is_refused_without_a_fetch(caplog: Any) -> None:
    rig = _Rig(_bybit_plan())
    with caplog.at_level(logging.WARNING, logger=control_module.__name__):
        _route(rig, _message("pin_top_liquid", venue="BYBIT"))
    assert "admits no pins" in caplog.text
    assert (rig.markets.fetches, rig.store.saved) == (0, [])


def test_an_uncapped_plan_publishes_a_null_cap() -> None:
    rig = _Rig(_bybit_plan(_SOL_BYBIT))
    asyncio.run(rig.status.publish(rig.control.plan))
    assert (rig.payloads()[-1]["cap"], rig.payloads()[-1]["accepts_commands"]) == (None, True)


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_a_recorded_payload_with_the_dydx_venue_appended_acts_as_the_recorded_one(
    index: int,
) -> None:
    _, payload = _CONTROL_FIXTURE["published"][index]
    addressed = payload[:-1] + ', "venue": "DYDX"}'
    plans = []
    for message in (payload, addressed):
        rig = _Rig(_plan() if index in (0, 3) else _plan(_BTC), {"BTC-USD": 500_000.0})
        _route(rig, message)
        plans.append(rig.control.plan)
    assert plans[0] == plans[1]
    assert plans[0] != (_plan() if index in (0, 3) else _plan(_BTC))
