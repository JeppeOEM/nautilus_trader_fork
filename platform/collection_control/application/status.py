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
`collector:status` (Story 6.1's published language, frozen by AD-D12): one row per planned
instrument, then the `unpinned_ids` aggregate; a `removed` tombstone when stop/unpin drops a row.

The bytes are the contract `bot_tui` reads: the key order and `json.dumps` separators are fixed,
and `collection_control/tests/test_status_replay.py` proves them against the pre-move recording.
The one addition (Story 25.4) is `"pending": true` on a planned instrument capture has not applied.
"""

import asyncio
import json
from collections.abc import Callable
from collections.abc import Iterable

from capture.application.ports import CaptureStatus
from observability import error_ledger

from collection_control.application.ports import Capture
from collection_control.application.ports import MarketsSource
from collection_control.application.ports import StatusBus
from collection_control.domain.liquidity import LiquidityClassification
from collection_control.domain.liquidity import classify_liquidity
from collection_control.domain.plan import CollectionPlan


# How often the status loop checks, between full refreshes, whether capture's retry loop has since
# applied a pending row (or a row has become pending): a changed row is republished at once rather
# than after up to `liquidity_check_seconds` (1800 s). Capture retries every 30 s.
STATUS_CHANGE_POLL_SECONDS = 30.0


def pending_ids(plan: CollectionPlan, status: CaptureStatus) -> frozenset[str]:
    """
    Return the plan's ids capture has not applied: whose rows are marked pending. From capture's
    applied set, not its own `pending`: capture adopts an added id only when `apply` reaches it,
    so mid-apply its plan mirror lists such an id neither applied nor pending.
    """
    return frozenset(plan.collected) - status.applied


def status_messages(
    plan: CollectionPlan, liquid: frozenset[str], status: CaptureStatus
) -> list[str]:
    """
    Every `collector:status` message for one publish, in order: a row per collected instrument
    (plan order) -- `id`, `liquid`, `last_trade_ts` (capture's last book update, ns; 0 = never),
    `trade_backfill` (trades recovered over REST since start), plus `"pending": true` when capture
    has not applied it (`pending_ids`) -- then `{"unpinned_ids": [...]}`, every excluded id sorted.
    """
    pending = pending_ids(plan, status)
    messages = []
    for iid in plan.collected:
        row: dict[str, object] = {
            "id": iid,
            "liquid": iid in liquid,
            "last_trade_ts": status.last_book_update_ns.get(iid, 0),
            "trade_backfill": status.trade_backfill.get(iid, 0),
        }
        if iid in pending:
            row["pending"] = True
        messages.append(json.dumps(row))
    messages.append(json.dumps({"unpinned_ids": sorted(plan.excluded)}))
    return messages


def removed_message(iid: str) -> str:
    """Return the tombstone that makes `bot_tui` drop a row at once, not after its staleness."""
    return json.dumps({"id": iid, "removed": True})


def ledger_unparseable(classification: LiquidityClassification) -> None:
    """No volume is not zero volume (DATA-01): each unreadable `volume24H` is loud, once per read."""
    for iid, raw in classification.unparseable.items():
        error_ledger.record("open_interest.volume24h", f"{iid}: unparseable volume24H {raw}")


class StatusPublisher:
    """
    Publishes `collector:status` from the plan plus capture's read-only counters.

    Invariant: a row's `liquid` label comes only from the last USD-volume classification made at
    the plan's own threshold with its `exclude` (OBS-03), and a planned instrument capture has not
    applied is always marked `pending` -- the status never shows as collected what the feed never
    subscribed (AD-D17). Until the first `refresh`, every row reads `liquid: false`.
    """

    def __init__(self, capture: Capture, bus: StatusBus, markets: MarketsSource) -> None:
        self._capture = capture
        self._bus = bus
        self._markets = markets
        self._liquid: frozenset[str] = frozenset()
        # (collected ids, pending ids) as last published: what `_sleep_until_refresh` compares.
        self._shown: tuple[tuple[str, ...], frozenset[str]] | None = None

    async def refresh(self, plan: CollectionPlan) -> None:
        """Re-classify liquidity (a display label, not a selection: no `max_liquid`)."""
        if plan.min_liquidity_usd is None:
            return
        classification = classify_liquidity(
            await self._markets.fetch(), plan.min_liquidity_usd, plan.excluded
        )
        ledger_unparseable(classification)
        self._liquid = classification.liquid

    async def publish(self, plan: CollectionPlan) -> None:
        status = self._capture.capture_status()
        for message in status_messages(plan, self._liquid, status):
            await self._bus.publish(message)
        self._shown = (plan.collected, pending_ids(plan, status))

    async def publish_removed(self, ids: Iterable[str]) -> None:
        for iid in sorted(ids):
            await self._bus.publish(removed_message(iid))

    async def loop(
        self,
        current: Callable[[], CollectionPlan],
        interval: float,
        poll: float = STATUS_CHANGE_POLL_SECONDS,
    ) -> None:
        """
        Refresh and publish at once, then every `interval` seconds -- run-then-sleep: a sleep-first
        loop left `bot_tui`'s Collector page on "waiting for collector:status" for up to 30 minutes
        after every restart (a production bug). In between, every `poll` seconds, a row whose
        pending state changed is republished at once. The bus is closed when the loop ends.
        """
        try:
            while True:
                plan = current()
                # Separate: an indexer outage must not also withhold the rows, whose capture
                # counters need no indexer -- they publish with the last good liquidity labels.
                try:
                    await self.refresh(plan)
                except Exception as e:
                    error_ledger.record(
                        "collector.status_loop", "liquidity refresh failed: last labels kept", e
                    )
                await self._publish_ledgered(plan)
                await self._sleep_until_refresh(current, interval, poll)
        finally:
            await self._bus.aclose()

    async def _sleep_until_refresh(
        self, current: Callable[[], CollectionPlan], interval: float, poll: float
    ) -> None:
        clock = asyncio.get_running_loop()
        deadline = clock.time() + interval
        while (left := deadline - clock.time()) > 0:
            await asyncio.sleep(min(poll, left))
            plan = current()
            if self._shown != (plan.collected, pending_ids(plan, self._capture.capture_status())):
                await self._publish_ledgered(plan)

    async def _publish_ledgered(self, plan: CollectionPlan) -> None:
        try:
            await self.publish(plan)
        except Exception as e:
            error_ledger.record("collector.status_loop", "status publish failed", e)
