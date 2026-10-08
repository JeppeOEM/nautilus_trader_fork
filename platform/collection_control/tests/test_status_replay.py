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
`collector:status` byte identity (Story 25.4, AD-D12): `fixtures/status_payloads.json` is what the
pre-move `DydxCollector._publish_status`/`_publish_removed` published for a fixed state (recorded
before the control plane moved). The same state, rebuilt through the plan, a real capture
`CaptureService` and `StatusPublisher`, must publish the identical strings -- except the aggregate,
which since Story 29.2 only gains keys appended after `unpinned_ids`.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from capture.application.capture_service import CaptureService
from capture.application.config import CoreConfig
from capture.application.ports import PlanChange
from capture.infrastructure.parquet_writer import ParquetArchiveWriter
from observability import error_ledger

from collection_control.application.ports import STATUS_CHANNEL
from collection_control.application.status import StatusPublisher
from collection_control.application.status import status_messages
from collection_control.domain.plan import CollectionPlan
from collection_control.domain.plan import InstrumentEntry


_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "status_payloads.json").read_text())
_MIN_USD = 20_000.0
_AGGREGATE_INDEX = 3  # the recorded publish: three rows, the aggregate, then the tombstone
_APPENDED_KEYS = (
    "venue",
    "cap",
    "accepts_commands",
    "min_liquidity_usd",
    "last_apply",
    "last_refusal",  # Story 29.5
    "liquidations",  # Story 33.1
)


class _Bus:
    def __init__(self) -> None:
        self.published: list[list[str]] = []
        self.collected: dict[str, str] = {}

    async def publish(self, message: str) -> None:
        self.published.append([STATUS_CHANNEL, message])

    async def store_collected(self, venue: str, snapshot: str) -> None:
        self.collected[venue] = snapshot

    async def aclose(self) -> None:
        pass


class _Markets:
    """Volumes that classify exactly the recorded liquid set as liquid at the plan's threshold."""

    def __init__(self, liquid: list[str], every: list[str]) -> None:
        self._liquid = liquid
        self._every = every

    async def fetch(self) -> dict[str, Any]:
        markets = {}
        for iid in self._every:
            ticker = iid.removesuffix("-PERP.DYDX")
            volume = _MIN_USD * 10 if iid in self._liquid else _MIN_USD / 10
            markets[ticker] = {"ticker": ticker, "volume24H": str(volume)}
        return {"markets": markets}


def _plan(state: dict[str, Any]) -> CollectionPlan:
    return CollectionPlan(
        venue="DYDX",
        instruments=tuple(InstrumentEntry(**entry) for entry in state["instruments"]),
        cap=30,
        excluded=frozenset(state["exclude"]),
        min_liquidity_usd=_MIN_USD,
        non_config_retain_hours=4.0,
    )


def _capture(tmp_path: Path, state: dict[str, Any], plan: CollectionPlan) -> CaptureService:
    capture = CaptureService(
        CoreConfig(environment="mainnet", catalog_path=str(tmp_path)),
        lambda _on_data, _ledger: object(),
        venue=plan.venue,
        plan=plan.collected,
        archive=ParquetArchiveWriter(str(tmp_path)),
        live_stream=None,
    )
    capture._applied.update(plan.collected)  # every recorded instrument was subscribed
    for iid, ns in state["last_book_update_ns"].items():
        capture._book(iid).last_update_ns = ns
    for iid, count in state["trade_backfill"].items():
        capture._intake(iid).backfilled = count
    return capture


def _recorded_publish(tmp_path: Path) -> list[list[str]]:
    state = _FIXTURE["state"]
    plan = _plan(state)
    bus = _Bus()
    markets = _Markets(state["liquid"], [e["id"] for e in state["instruments"]])
    publisher = StatusPublisher(
        _capture(tmp_path, state, plan), bus, markets, accepts_commands=True
    )

    async def _publish() -> None:
        await publisher.refresh(plan)
        await publisher.publish(plan)
        await publisher.publish_removed([state["removed"]])

    asyncio.run(_publish())
    return bus.published


def test_rows_and_tombstone_are_byte_identical_to_the_pre_move_recording(tmp_path: Path) -> None:
    published = _recorded_publish(tmp_path)
    recorded = _FIXTURE["published"]
    assert len(published) == len(recorded)
    assert published[:_AGGREGATE_INDEX] == recorded[:_AGGREGATE_INDEX]
    assert published[_AGGREGATE_INDEX + 1 :] == recorded[_AGGREGATE_INDEX + 1 :]


def test_the_aggregate_only_appends_keys_after_the_recorded_bytes(tmp_path: Path) -> None:
    channel, aggregate = _recorded_publish(tmp_path)[_AGGREGATE_INDEX]
    recorded_channel, recorded = _FIXTURE["published"][_AGGREGATE_INDEX]
    assert channel == recorded_channel
    assert aggregate.startswith(recorded.removesuffix("}") + ", ")
    parsed = json.loads(aggregate)
    assert list(parsed)[1:] == list(_APPENDED_KEYS)
    appended = {key: parsed.pop(key) for key in _APPENDED_KEYS}
    assert json.dumps(parsed) == recorded
    assert appended == {
        "venue": "DYDX",
        "cap": 30,
        "accepts_commands": True,
        "min_liquidity_usd": _MIN_USD,
        "last_apply": None,  # the fixture's state marks ids applied without an `apply`
        "last_refusal": None,  # no command refused since the collector started
        "liquidations": None,  # the fixture's capture has no liquidation feed
    }


def _flat_plan(ids: tuple[str, ...]) -> CollectionPlan:
    """Bybit's shape through the one loader: a flat list, uncapped (Story 29.4), no threshold."""
    return CollectionPlan(
        venue="BYBIT",
        instruments=tuple(InstrumentEntry(id=iid) for iid in ids),
        cap=None,
    )


def test_an_uncapped_plan_publishes_without_markets_before_its_first_apply(tmp_path: Path) -> None:
    ids = ("BTCUSDT-LINEAR.BYBIT", "BTCUSDT-SPOT.BYBIT")
    plan = _flat_plan(ids)
    capture = _capture(tmp_path, {"last_book_update_ns": {}, "trade_backfill": {}}, plan)
    bus = _Bus()
    publisher = StatusPublisher(capture, bus, None, accepts_commands=True)

    async def _publish() -> None:
        await publisher.refresh(plan)
        await publisher.publish(plan)

    asyncio.run(_publish())
    messages = [message for _, message in bus.published]
    assert messages[:2] == [
        f'{{"id": "{iid}", "liquid": false, "last_trade_ts": 0, "trade_backfill": 0}}'
        for iid in ids
    ]
    assert json.loads(messages[2]) == {
        "unpinned_ids": [],
        "venue": "BYBIT",
        "cap": None,  # uncapped (Story 29.4; a static plan published its own size before)
        "accepts_commands": True,
        "min_liquidity_usd": None,
        "last_apply": None,
        "last_refusal": None,
        "liquidations": None,  # a client without the liquidation feed (Story 33.1)
    }


class _LiquidationClient:
    """A client with the liquidation feed (Story 33.1's optional capability), reconnecting."""

    def liquidation_state(self) -> str:
        return "reconnecting"


def test_the_liquidation_feed_state_is_the_aggregates_last_key(tmp_path: Path) -> None:
    """Story 33.1: capture's `liquidation_state()` reaches the aggregate as its last key."""
    plan = _flat_plan(("BTCUSDT-LINEAR.BYBIT",))
    capture = CaptureService(
        CoreConfig(environment="mainnet", catalog_path=str(tmp_path)),
        lambda _on_data, _ledger: _LiquidationClient(),
        venue=plan.venue,
        plan=plan.collected,
        archive=ParquetArchiveWriter(str(tmp_path)),
        live_stream=None,
    )
    bus = _Bus()
    asyncio.run(StatusPublisher(capture, bus, None, accepts_commands=True).publish(plan))
    aggregate = json.loads(bus.published[-1][1])
    assert list(aggregate)[-1] == "liquidations"
    assert aggregate["liquidations"] == "reconnecting"


def test_a_threshold_without_markets_is_a_wiring_error(tmp_path: Path) -> None:
    state = _FIXTURE["state"]
    plan = _plan(state)
    publisher = StatusPublisher(
        _capture(tmp_path, state, plan), _Bus(), None, accepts_commands=True
    )
    with pytest.raises(RuntimeError, match="no markets"):
        asyncio.run(publisher.refresh(plan))


class _FailingFeed:
    """A venue client whose subscribe of the ids in `fail` raises."""

    def __init__(self, fail: set[str]) -> None:
        self.fail = fail

    async def fetch_instruments(self) -> list:
        return []

    async def connect(self, loop: asyncio.AbstractEventLoop, instruments: list) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def subscribe(self, iid: str) -> None:
        if iid in self.fail:
            raise ConnectionError(f"subscribe {iid} rejected")

    async def unsubscribe(self, iid: str) -> None:
        pass


def test_last_apply_reports_capture_s_most_recent_apply_sorted(tmp_path: Path) -> None:
    error_ledger.reset()
    ids = ("CCCUSDT-LINEAR.BYBIT", "AAAUSDT-LINEAR.BYBIT", "BBBUSDT-LINEAR.BYBIT")
    plan = _flat_plan(ids)
    feed = _FailingFeed({"BBBUSDT-LINEAR.BYBIT"})
    capture = CaptureService(
        CoreConfig(environment="mainnet", catalog_path=str(tmp_path)),
        lambda _on_data, _ledger: feed,
        venue=plan.venue,
        plan=(),
        archive=ParquetArchiveWriter(str(tmp_path)),
        live_stream=None,
    )
    asyncio.run(capture.apply(PlanChange(added=frozenset(ids))))
    status = capture.capture_status()
    aggregate = json.loads(status_messages(plan, frozenset(), status, accepts_commands=False)[-1])
    assert aggregate["last_apply"] == {
        "ts": status.last_applied_ns,
        "subscribed": ["AAAUSDT-LINEAR.BYBIT", "CCCUSDT-LINEAR.BYBIT"],
        "unsubscribed": [],
        "failed": ["BBBUSDT-LINEAR.BYBIT"],
    }


def test_an_unapplied_instrument_adds_only_the_pending_key(tmp_path: Path) -> None:
    state = _FIXTURE["state"]
    plan = _plan(state)
    capture = _capture(tmp_path, state, plan)
    capture._applied.discard("SOL-USD-PERP.DYDX")
    bus = _Bus()
    markets = _Markets(state["liquid"], [e["id"] for e in state["instruments"]])
    publisher = StatusPublisher(capture, bus, markets, accepts_commands=True)

    async def _publish() -> None:
        await publisher.refresh(plan)
        await publisher.publish(plan)

    asyncio.run(_publish())
    recorded = _FIXTURE["published"][2][1]
    assert bus.published[2][1] == recorded.removesuffix("}") + ', "pending": true}'
    assert bus.published[:2] == _FIXTURE["published"][:2]


class _YieldingBus(_Bus):
    """A bus whose every publish yields to the loop, as a network round trip does."""

    async def publish(self, message: str) -> None:
        await asyncio.sleep(0)
        await super().publish(message)


def test_concurrent_publishes_never_interleave_on_the_bus(tmp_path: Path) -> None:
    # dYdX's status loop and its ControlService publish at the same time; bot_tui drops the
    # rows an aggregate's burst did not republish, so each burst must reach the bus whole.
    ids = ("BTCUSDT-LINEAR.BYBIT", "ETHUSDT-LINEAR.BYBIT", "SOLUSDT-LINEAR.BYBIT")
    plan = _flat_plan(ids)
    capture = _capture(tmp_path, {"last_book_update_ns": {}, "trade_backfill": {}}, plan)
    bus = _YieldingBus()
    publisher = StatusPublisher(capture, bus, None, accepts_commands=False)

    async def _publish_twice() -> None:
        await asyncio.gather(publisher.publish(plan), publisher.publish(plan))

    asyncio.run(_publish_twice())
    burst = [*ids, "unpinned_ids"]
    shapes = [json.loads(message).get("id", "unpinned_ids") for _, message in bus.published]
    assert shapes == burst + burst
