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
`Collector` and `StatusPublisher`, must publish the identical strings.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from collector_core.collector import Collector
from collector_core.config import CoreConfig

from collection_control.application.ports import STATUS_CHANNEL
from collection_control.application.status import StatusPublisher
from collection_control.domain.plan import CollectionPlan
from collection_control.domain.plan import InstrumentEntry


_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "status_payloads.json").read_text())
_MIN_USD = 20_000.0


class _Bus:
    def __init__(self) -> None:
        self.published: list[list[str]] = []

    async def publish(self, message: str) -> None:
        self.published.append([STATUS_CHANNEL, message])

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


def _capture(tmp_path: Path, state: dict[str, Any], plan: CollectionPlan) -> Collector:
    capture = Collector(
        CoreConfig(environment="mainnet", catalog_path=str(tmp_path)), object(), plan=plan.collected
    )
    capture._applied.update(plan.collected)  # every recorded instrument was subscribed
    capture._last_book_update_ns.update(state["last_book_update_ns"])
    capture._trade_backfill_counts.update(state["trade_backfill"])
    return capture


def test_status_publish_is_byte_identical_to_the_pre_move_recording(tmp_path: Path) -> None:
    state = _FIXTURE["state"]
    plan = _plan(state)
    bus = _Bus()
    markets = _Markets(state["liquid"], [e["id"] for e in state["instruments"]])
    publisher = StatusPublisher(_capture(tmp_path, state, plan), bus, markets)

    async def _publish() -> None:
        await publisher.refresh(plan)
        await publisher.publish(plan)
        await publisher.publish_removed([state["removed"]])

    asyncio.run(_publish())
    assert bus.published == _FIXTURE["published"]


def test_an_unapplied_instrument_adds_only_the_pending_key(tmp_path: Path) -> None:
    state = _FIXTURE["state"]
    plan = _plan(state)
    capture = _capture(tmp_path, state, plan)
    capture._applied.discard("SOL-USD-PERP.DYDX")
    bus = _Bus()
    markets = _Markets(state["liquid"], [e["id"] for e in state["instruments"]])
    publisher = StatusPublisher(capture, bus, markets)

    async def _publish() -> None:
        await publisher.refresh(plan)
        await publisher.publish(plan)

    asyncio.run(_publish())
    recorded = _FIXTURE["published"][2][1]
    assert bus.published[2][1] == recorded.removesuffix("}") + ', "pending": true}'
    assert bus.published[:2] == _FIXTURE["published"][:2]
