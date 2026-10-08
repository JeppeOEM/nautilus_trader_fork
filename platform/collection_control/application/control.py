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
`collector:control` (Story 6.1's published language, frozen by AD-D12): `{action, id, venue}` with

start          : `CollectionPlan.add` -- collect a new (or unpinned) id, lifting it out of
                 `exclude`; refused when already collected or at the cap.
unpin          : `CollectionPlan.unpin` -- stop collecting and exclude the id, so it also stays out
                 of every future liquidity pin (`start` reverses it). The TUI's `p` key.
stop           : `CollectionPlan.remove` -- stop collecting without excluding.
pin_top_liquid : `CollectionPlan.pin` -- fill the free slots with the top USD-volume liquid ids
                 that are neither collected nor excluded; never removes or replaces an entry.

`venue` (the `kernel.venues` token) was appended in Story 29.4, after the existing keys, so an
`{action, id}` message keeps its bytes as the prefix. Every venue's collector consumes the one
channel and acts only on its own venue's messages; a message without `venue` is dYdX's (every
sender before 29.4 drove only dYdX). A non-string `venue`, or one `kernel.venues` does not
know, is ledgered and ignored; a known other venue is ignored silently.

A refused command -- including an id of another venue than the plan's -- or an unknown action,
logs a WARNING and changes nothing. Since Story 29.5 a refused command (`PlanRejected`) is also
recorded as the status aggregate's `last_refusal` and published at once, so `bot_tui` can show why
an add did not happen; an unknown action is not a plan refusal and is only logged.

Two refusals guard the plan's integrity (DW-231/DW-232/DW-256). A `start` for an id the venue
does not list (capture's `CaptureStatus.listed`) is refused as a `PlanRejected`, and so is every
`start` before capture has fetched the venue's instruments (`listed` None: fail closed), so a typo
never holds a slot that stays `pending` forever; `stop`/`unpin` are not checked, so an unlisted id
already in the plan can still be removed. And a command whose save finds the plan file holding
another plan than the service runs (a hand edit the reload has not adopted yet, or a file that no
longer parses) is refused by the store (`PlanFileChanged`): nothing is written or applied, the
refusal is ledgered (`collector.control`) and published as `last_refusal`, and the next reload
adopts the edit (unless it refuses it too, ledgered `collector.config_reload`), after which the
command can be retried. A save that fails for any other reason is ledgered and published as
`last_refusal` as well. Hand edits are not checked against the listing: a reload adopts an
unlisted id, which capture ledgers and keeps `pending`. There is no
timer-driven reclassification: the collected set changes only on a command or a hand edit of the
file (`reload`).
"""

import asyncio
import json
import logging

from kernel.venues import VENUE_KINDS
from kernel.venues import has_venue
from observability import error_ledger

from collection_control.application.ports import Capture
from collection_control.application.ports import ControlChannel
from collection_control.application.ports import MarketsSource
from collection_control.application.ports import PlanFileChanged
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

# The venue a message without a `venue` field is addressed to: every sender before Story 29.4
# (the field's arrival) drove only dYdX's plan.
LEGACY_CONTROL_VENUE = "DYDX"


class ControlService:
    """
    Owns one venue's current `CollectionPlan` and changes it only through the plan's commands.

    Invariant: save, then apply, then publish -- a command's plan is validated through the one
    loader and persisted (`PlanStore.save`) before capture applies it, so capture never runs a
    plan the file does not hold; a failed save applies nothing and keeps the current plan. A save
    passes the plan the service runs as `expected`, so it never overwrites a hand edit the reload
    has not adopted yet (DW-231). One command (or reload) at a time, under a lock, so two cannot
    interleave a save and an apply.
    """

    def __init__(
        self,
        plan: CollectionPlan,
        store: PlanStore,
        capture: Capture,
        status: StatusPublisher,
        markets: MarketsSource | None,
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
                await self._reply_refused(action, iid, str(e))
                return
            if diff is None:
                return
            await self._commit(diff, action, iid)

    async def _reply_refused(self, action: str | None, iid: str | None, reason: str) -> None:
        """Record a refused command as the aggregate's `last_refusal` and publish it at once."""
        refused_id = None if action == "pin_top_liquid" else iid
        self._status.record_refusal(action, refused_id, reason)
        await self._publish(frozenset())

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
        if not has_venue(iid, self._plan.venue):
            raise PlanRejected(f"Cannot {action} {iid}: not a {self._plan.venue} instrument")
        if action == "start" and iid not in self._plan.collected:
            # A collected id gets the plan's own "already collected", not a listing refusal.
            self._refuse_unlisted(iid)
        diff = command(iid)
        # The plan refuses a full cap itself; this refuses the slots lingering subscriptions hold.
        lingering = self._lingering_over_cap(diff.plan)
        if action == "start" and lingering:
            raise PlanRejected(
                f"Cannot start {iid}: {lingering} are still subscribed after a failed "
                "unsubscribe and hold the remaining wire slots"
            )
        return diff

    def _refuse_unlisted(self, iid: str) -> None:
        """
        Raise `PlanRejected` unless capture has fetched the venue's instruments and `iid` is among
        them: an unlisted id would be saved, hold a slot and stay `pending` forever (DW-232/256).

        Known limit: the listing is capture's one fetch at `run()` (`CaptureService._listed`), so a
        market the venue lists after startup is refused here, and one it delists after startup
        passes, until the collector restarts. Upgrade path: capture's own, on an unlisted add
        refetch the instruments and reconnect the client with the new list.
        """
        listed = self._capture.capture_status().listed
        venue = self._plan.venue
        if listed is None:
            raise PlanRejected(
                f"Cannot start {iid}: {venue} markets are not known yet: capture has not "
                "fetched them"
            )
        if iid not in listed:
            raise PlanRejected(f"Cannot start {iid}: not listed on the {venue} venue")

    def _lingering_over_cap(self, plan: CollectionPlan) -> list[str]:
        """
        Return the ids still subscribed after a failed unsubscribe when, with them, `plan` would
        hold more wire subscriptions than its cap (else [], always for an uncapped plan). A lingering
        id `plan` collects again takes no extra slot: capture reuses its subscription.
        """
        if plan.cap is None:
            return []
        lingering = self._capture.capture_status().lingering - set(plan.collected)
        if len(plan.collected) + len(lingering) <= plan.cap:
            return []
        return sorted(lingering)

    def _wire_slots(self, free_slots: int) -> int:
        """
        Return the plan's free slots minus the ids capture still holds subscribed after a failed
        unsubscribe: the cap guards the venue's per-connection limit, which counts the wire, not
        the plan -- refilling over a lingering subscription would exceed it.
        """
        return free_slots - len(self._capture.capture_status().lingering)

    async def _pin_top_liquid(self) -> PlanDiff | None:
        plan = self._plan
        # Before the slots: an uncapped plan (Bybit, Hyperliquid) has no threshold by the plan's
        # own invariant, so this refusal is what every pin on it gets.
        if plan.min_liquidity_usd is None:
            raise PlanRejected(f"{plan.venue} plan has no liquidity threshold: it admits no pins")
        if plan.free_slots is None or self._markets is None:
            raise RuntimeError(f"{plan.venue} plan has a liquidity threshold but no cap or source")
        slots = self._wire_slots(plan.free_slots)
        if slots <= 0:
            return None  # no fetch: there is nowhere to put a result
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

    async def _commit(self, diff: PlanDiff, action: str | None, iid: str | None) -> None:
        try:
            self._store.save(diff.plan, expected=self._plan)
        except PlanFileChanged as e:
            # Not a fault of the command: the file holds an edit the reload has not adopted yet
            # (or one that broke it). Ledgered, since it is the operator's edit racing a command,
            # and answered like any refusal so the sender sees why nothing happened.
            error_ledger.record(
                "collector.control",
                f"plan save refused: {action} command not applied, plan unchanged",
                e,
            )
            await self._reply_refused(action, iid, str(e))
            return
        except Exception as e:
            error_ledger.record(
                "collector.control", "plan save failed: command not applied, plan unchanged", e
            )
            # Answered too: a save that failed outright must not look to the sender like silence.
            await self._reply_refused(action, iid, f"plan save failed: {type(e).__name__}: {e}")
            return
        self._plan = diff.plan
        await self._capture.apply(diff)
        await self._publish(diff.removed if action in ("stop", "unpin") else frozenset())

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
            venue = payload.get("venue", LEGACY_CONTROL_VENUE)
            if not isinstance(venue, str):
                error_ledger.record(
                    "collector.control", f"collector:control venue is not a string: {message!r}"
                )
                return
            if venue not in VENUE_KINDS:
                # No collector will ever act on it: a sender's bug, not another consumer's message.
                error_ledger.record(
                    "collector.control", f"collector:control unknown venue {venue!r}: {message!r}"
                )
                return
            if venue != self._plan.venue:
                return  # addressed to another venue's collector
            await self.handle(payload.get("action"), payload.get("id"))
        except Exception as e:
            error_ledger.record(
                "collector.control", f"collector:control message failed: {message!r}", e
            )
