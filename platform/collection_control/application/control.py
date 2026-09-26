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
`collector:control` (Story 6.1's published language, frozen by AD-D12): `{action, id}` with

start          : `CollectionPlan.add` -- collect a new (or unpinned) id, lifting it out of
                 `exclude`; refused when already collected or at the cap.
unpin          : `CollectionPlan.unpin` -- stop collecting and exclude the id, so it also stays out
                 of every future liquidity pin (`start` reverses it). The TUI's `p` key.
stop           : `CollectionPlan.remove` -- stop collecting without excluding.
pin_top_liquid : `CollectionPlan.pin` -- fill the free slots with the top USD-volume liquid ids
                 that are neither collected nor excluded; never removes or replaces an entry.

A refused command, or an unknown action, logs a WARNING and changes nothing. There is no timer-
driven reclassification: the collected set changes only on a command or a hand edit of the file
(`reload`).
"""

import asyncio
import json
import logging

from observability import error_ledger

from collection_control.application.ports import Capture
from collection_control.application.ports import ControlChannel
from collection_control.application.ports import MarketsSource
from collection_control.application.ports import PlanStore
from collection_control.application.status import StatusPublisher
from collection_control.application.status import ledger_unparseable
from collection_control.domain.liquidity import classify_liquidity
from collection_control.domain.plan import CollectionPlan
from collection_control.domain.plan import PlanDiff
from collection_control.domain.plan import PlanRejected


logger = logging.getLogger(__name__)

# The wait before re-subscribing to `collector:control` after a lost connection.
CONTROL_RECONNECT_SECONDS = 2.0


class ControlService:
    """
    Owns one venue's current `CollectionPlan` and changes it only through the plan's commands.

    Invariant: save, then apply, then publish -- a command's plan is validated through the one
    loader and persisted (`PlanStore.save`) before capture applies it, so capture never runs a
    plan the file does not hold; a failed save applies nothing and keeps the current plan. One
    command (or reload) at a time, under a lock, so two cannot interleave a save and an apply.
    """

    def __init__(
        self,
        plan: CollectionPlan,
        store: PlanStore,
        capture: Capture,
        status: StatusPublisher,
        markets: MarketsSource,
    ) -> None:
        self._plan = plan
        self._store = store
        self._capture = capture
        self._status = status
        self._markets = markets
        self._lock = asyncio.Lock()

    @property
    def plan(self) -> CollectionPlan:
        return self._plan

    async def handle(self, action: str | None, iid: str | None) -> None:
        """Dispatch one `collector:control` message."""
        async with self._lock:
            try:
                diff = await self._command(action, iid)
            except PlanRejected as e:
                logger.warning("%s", e)
                return
            if diff is None:
                return
            await self._commit(diff, publish_removed=action in ("stop", "unpin"))

    async def _command(self, action: str | None, iid: str | None) -> PlanDiff | None:
        if action == "pin_top_liquid":
            return await self._pin_top_liquid()
        commands = {"start": self._plan.add, "unpin": self._plan.unpin, "stop": self._plan.remove}
        command = commands.get(action or "")
        if command is None:
            logger.warning("Unknown collector:control action: %r", action)
            return None
        if not iid:
            raise PlanRejected(f"Cannot {action}: no instrument id given")
        diff = command(iid)
        # The plan refuses a full cap itself; this refuses the slots lingering subscriptions hold.
        lingering = self._lingering_over_cap(diff.plan)
        if action == "start" and lingering:
            raise PlanRejected(
                f"Cannot start {iid}: {lingering} are still subscribed after a failed "
                "unsubscribe and hold the remaining wire slots"
            )
        return diff

    def _lingering_over_cap(self, plan: CollectionPlan) -> list[str]:
        """
        Return the ids still subscribed after a failed unsubscribe when, with them, `plan` would
        hold more wire subscriptions than its cap (else []). A lingering id `plan` collects again
        takes no extra slot: capture reuses its subscription.
        """
        lingering = self._capture.capture_status().lingering - set(plan.collected)
        if len(plan.collected) + len(lingering) <= plan.cap:
            return []
        return sorted(lingering)

    def _wire_slots(self) -> int:
        """
        Return the plan's free slots minus the ids capture still holds subscribed after a failed
        unsubscribe: the cap guards the venue's per-connection limit, which counts the wire, not
        the plan -- refilling over a lingering subscription would exceed it.
        """
        return self._plan.free_slots - len(self._capture.capture_status().lingering)

    async def _pin_top_liquid(self) -> PlanDiff | None:
        plan = self._plan
        slots = self._wire_slots()
        if slots <= 0:
            return None  # no fetch: there is nowhere to put a result
        if plan.min_liquidity_usd is None:
            raise PlanRejected(f"{plan.venue} plan has no liquidity threshold: it admits no pins")
        classification = classify_liquidity(
            await self._markets.fetch(),
            plan.min_liquidity_usd,
            exclude=plan.excluded | frozenset(plan.collected),
            max_liquid=slots,
        )
        ledger_unparseable(classification)
        diff = plan.pin(classification)
        if not diff.added:
            logger.info("pin_top_liquid: no liquid id left to pin; plan unchanged")
            return None  # nothing to save: a rewrite would only drop the file's comments
        return diff

    async def _commit(self, diff: PlanDiff, *, publish_removed: bool) -> None:
        try:
            self._store.save(diff.plan)
        except Exception as e:
            error_ledger.record(
                "collector.control", "plan save failed: command not applied, plan unchanged", e
            )
            return
        self._plan = diff.plan
        await self._capture.apply(diff)
        await self._publish(diff.removed if publish_removed else frozenset())

    async def _publish(self, removed: frozenset[str]) -> None:
        """
        Publish the changed plan at once. A failed publish is ledgered here, not raised: the
        command was saved and applied, and the status loop republishes on its next tick.
        """
        try:
            await self._status.publish(self._plan)
            await self._status.publish_removed(removed)
        except Exception as e:
            error_ledger.record("collector.status_loop", "status publish after a change failed", e)

    async def reload(self) -> None:
        """Adopt a hand-edited plan file; a file that fails validation keeps the current plan."""
        async with self._lock:
            try:
                new_plan = self._store.load()
                diff = self._plan.reload(new_plan)
            except Exception as e:
                error_ledger.record(
                    "collector.config_reload", "plan reload failed: keeping the current plan", e
                )
                return
            if new_plan == self._plan:
                return
            lingering = self._lingering_over_cap(new_plan) if diff.added else []
            if lingering:
                # Retried every reload tick: the edit is adopted once capture's retry has ended
                # those subscriptions (the cap guards the venue's per-connection limit).
                error_ledger.record(
                    "collector.config_reload",
                    f"plan reload refused: {lingering} are still subscribed after a failed "
                    "unsubscribe, so the edited plan would exceed the wire cap; current plan kept",
                )
                return
            store_deltas_changed = new_plan.delta_store_ids != self._plan.delta_store_ids
            self._plan = new_plan
            if diff.changed or store_deltas_changed:
                await self._capture.apply(diff)
            # A hand edit shows at once, like a command: added rows appear, removed ones drop.
            await self._publish(diff.removed)

    async def control_loop(self, channel: ControlChannel) -> None:
        """
        Act on every `collector:control` message. A message that fails is ledgered and the next
        one is served; a lost connection is ledgered and re-subscribed after 2 s. The channel is
        closed when the loop ends.
        """
        try:
            while True:
                try:
                    async for message in channel.listen():
                        await self._handle_message(message)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    error_ledger.record(
                        "collector.control_redis",
                        f"collector:control listener error, reconnecting in "
                        f"{CONTROL_RECONNECT_SECONDS:.0f}s",
                        e,
                    )
                    await asyncio.sleep(CONTROL_RECONNECT_SECONDS)
        finally:
            await channel.aclose()

    async def _handle_message(self, message: str) -> None:
        try:
            payload = json.loads(message)
            await self.handle(payload.get("action"), payload.get("id"))
        except Exception as e:
            error_ledger.record(
                "collector.control", f"collector:control message failed: {message!r}", e
            )
