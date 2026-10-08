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
instrument, then the per-venue aggregate; a `removed` tombstone when stop/unpin drops a row.

The bytes are the contract `bot_tui` reads: the key order and `json.dumps` separators are fixed,
and `collection_control/tests/test_status_replay.py` proves them against the pre-move recording.
Fields are only ever appended:
- Story 25.4: `"pending": true` on a planned instrument capture has not applied.
- Story 29.2: the aggregate carries, after `unpinned_ids`, the plan's `venue`, `cap`,
  `accepts_commands` (whether `collector:control` drives this plan), `min_liquidity_usd` (null
  when the plan classifies no liquidity) and `last_apply` (capture's most recent apply, null
  before the first). Every venue publishes it from its live plan. `cap` is null for an uncapped
  plan (Bybit, Hyperliquid since Story 29.4, which also set their `accepts_commands` true). A row
  carries no venue: `bot_tui` derives it from the id (SIGNAL-01).
- Story 29.5: the aggregate carries, after `last_apply`, `last_refusal`: the most recent command
  the plan refused (`PlanRejected`) since the collector started, `{ts, action, id, reason}` (`id`
  null for `pin_top_liquid`), or null before any. Without it a refused `bot_tui` add would read
  `pending` until the TUI's own no-answer timeout, with no reason shown.
- Story 33.1: the aggregate carries, after `last_refusal`, `liquidations`: the venue's liquidation
  socket, `"connected"`, `"reconnecting"` or `"down"` (capture's `CaptureStatus.liquidations`), or
  null for a venue without the feed. Always present, always last.
"""

import asyncio
import json
import time
from collections.abc import Callable
from collections.abc import Iterable

from capture.application.ports import Applied
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

# The full-republish cadence of a plan with no liquidity refresh (Bybit, Hyperliquid): a command or
# a reload publishes at once itself, so this only keeps `bot_tui`'s rows fresh -- the same cadence
# as dYdX's default `liquidity_check_seconds`, which `bot_tui`'s staleness window (3600 s) is sized
# against. A pending row or a new apply still republishes within `STATUS_CHANGE_POLL_SECONDS`.
# (Named `STATIC_PLAN_STATUS_SECONDS` until Story 29.4 made these plans commandable.)
# Known limit: Redis pub/sub keeps no history, so a `bot_tui` started between two publishes shows
# no section for this venue until the next one (up to this long, as for dYdX's
# `liquidity_check_seconds`); upgrade path: also keep the last publish in a Redis key the TUI
# reads on connect.
PLAN_STATUS_SECONDS = 1800.0


def pending_ids(plan: CollectionPlan, status: CaptureStatus) -> frozenset[str]:
    """
    Return the plan's ids capture has not applied: whose rows are marked pending. From capture's
    applied set, not its own `pending`: capture adopts an added id only when `apply` reaches it,
    so mid-apply its plan mirror lists such an id neither applied nor pending.
    """
    return frozenset(plan.collected) - status.applied


def last_apply_field(applied: Applied | None, applied_ns: int) -> dict[str, object] | None:
    """Return the aggregate's `last_apply`: None before capture's first apply, id lists sorted."""
    if applied is None:
        return None
    return {
        "ts": applied_ns,
        "subscribed": sorted(applied.subscribed),
        "unsubscribed": sorted(applied.unsubscribed),
        "failed": sorted(applied.failed),
    }


def plan_aggregate(
    plan: CollectionPlan,
    status: CaptureStatus,
    accepts_commands: bool,
    last_refusal: dict[str, object] | None = None,
) -> str:
    """
    Return the per-venue aggregate: `unpinned_ids` (every excluded id sorted) first,
    byte-identical to the pre-29.2 message up to its closing brace, then the appended plan facts,
    `last_refusal` (Story 29.5), then `liquidations` (Story 33.1) last.
    """
    aggregate: dict[str, object] = {
        "unpinned_ids": sorted(plan.excluded),
        "venue": plan.venue,
        "cap": plan.cap,
        "accepts_commands": accepts_commands,
        "min_liquidity_usd": plan.min_liquidity_usd,
        "last_apply": last_apply_field(status.last_applied, status.last_applied_ns),
        "last_refusal": last_refusal,
        "liquidations": status.liquidations,
    }
    return json.dumps(aggregate)


def status_messages(
    plan: CollectionPlan,
    liquid: frozenset[str],
    status: CaptureStatus,
    *,
    accepts_commands: bool,
    last_refusal: dict[str, object] | None = None,
) -> list[str]:
    """
    Every `collector:status` message for one publish, in order: a row per collected instrument
    (plan order) -- `id`, `liquid`, `last_trade_ts` (capture's last book update, ns; 0 = never),
    `trade_backfill` (trades recovered over REST since start), plus `"pending": true` when capture
    has not applied it (`pending_ids`) -- then the venue's aggregate (`plan_aggregate`).
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
    messages.append(plan_aggregate(plan, status, accepts_commands, last_refusal))
    return messages


def removed_message(iid: str) -> str:
    """Return the tombstone that makes `bot_tui` drop a row at once, not after its staleness."""
    return json.dumps({"id": iid, "removed": True})


def ledger_unparseable(classification: LiquidityClassification) -> None:
    """No volume is not zero volume (DATA-01): each unreadable `volume24H` is loud, once per read."""
    for iid, raw in classification.unparseable.items():
        error_ledger.record("open_interest.volume24h", f"{iid}: unparseable volume24H {raw}")


def _shown_state(
    plan: CollectionPlan, status: CaptureStatus
) -> tuple[tuple[str, ...], frozenset[str], int]:
    """
    Return what a publish showed that can change between refreshes: the collected ids, the pending
    ids and which apply `last_apply` reports -- an apply that moved no pending mark (a re-add of a
    lingering id, a reload that changed only delta storage) must not wait up to the full refresh
    interval to show.
    """
    return plan.collected, pending_ids(plan, status), status.last_applied_ns


class StatusPublisher:
    """
    Publishes `collector:status` from the plan plus capture's read-only counters.

    Invariant: a row's `liquid` label comes only from the last USD-volume classification made at
    the plan's own threshold with its `exclude` (OBS-03), and a planned instrument capture has not
    applied is always marked `pending` -- the status never shows as collected what the feed never
    subscribed (AD-D17). Until the first `refresh`, every row reads `liquid: false`, and a plan
    without a liquidity threshold (Bybit's, Hyperliquid's) is never classified: its rows stay `false`.

    `markets` is None only for a plan that classifies nothing; `accepts_commands` is published
    as-is and states whether the composition root wired a `ControlService` for this plan.
    """

    def __init__(
        self,
        capture: Capture,
        bus: StatusBus,
        markets: MarketsSource | None,
        *,
        accepts_commands: bool,
    ) -> None:
        self._capture = capture
        self._bus = bus
        self._markets = markets
        self._accepts_commands = accepts_commands
        self._liquid: frozenset[str] = frozenset()
        # One publish at a time: dYdX's status loop and its `ControlService` both publish, and
        # `bot_tui` drops a venue's rows its aggregate's burst did not republish, so two bursts
        # interleaving on the bus would hide live rows until the next publish.
        self._publishing = asyncio.Lock()
        # What `_sleep_until_refresh` compares against: the last publish's `_shown_state`.
        self._shown: tuple[tuple[str, ...], frozenset[str], int] | None = None
        # The aggregate's `last_refusal` (Story 29.5): the latest refused command, None before any.
        # In memory only, so a restarted collector publishes null again. `_shown_refusal` is the
        # one the last publish carried: a refusal whose own publish failed is republished within
        # `STATUS_CHANGE_POLL_SECONDS`, not only at the full refresh.
        self._last_refusal: dict[str, object] | None = None
        self._shown_refusal: dict[str, object] | None = None

    def record_refusal(self, action: str | None, iid: str | None, reason: str) -> None:
        """
        Remember `ControlService`'s latest refused command for the next publish's `last_refusal`,
        stamped now (`time.time_ns()`): the TUI orders refusals by its own receive time, so this
        stamp is shown, never compared across clocks.
        """
        self._last_refusal = {"ts": time.time_ns(), "action": action, "id": iid, "reason": reason}

    async def refresh(self, plan: CollectionPlan) -> None:
        """
        Re-classify liquidity (a display label, not a selection: no `max_liquid`). Raises
        `RuntimeError` for a plan with a threshold but no `markets` -- a wiring bug the loop ledgers.
        """
        if plan.min_liquidity_usd is None:
            return
        if self._markets is None:
            raise RuntimeError(
                f"{plan.venue} plan has a liquidity threshold but its status publisher no markets"
            )
        classification = classify_liquidity(
            await self._markets.fetch(), plan.min_liquidity_usd, plan.excluded
        )
        ledger_unparseable(classification)
        self._liquid = classification.liquid

    async def publish(self, plan: CollectionPlan) -> None:
        """Publish every row and then the aggregate as one burst no other publish interleaves."""
        async with self._publishing:
            status = self._capture.capture_status()
            # Read once: `record_refusal` takes no lock, so a refusal recorded during the awaits
            # below must not be marked shown -- it was never sent, and would otherwise wait for
            # the full refresh instead of `STATUS_CHANGE_POLL_SECONDS`.
            refusal = self._last_refusal
            messages = status_messages(
                plan,
                self._liquid,
                status,
                accepts_commands=self._accepts_commands,
                last_refusal=refusal,
            )
            for message in messages:
                await self._bus.publish(message)
            self._shown = _shown_state(plan, status)
            self._shown_refusal = refusal

    async def publish_removed(self, ids: Iterable[str]) -> None:
        async with self._publishing:
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
                # Separate: an indexer outage must not also withhold the rows, whose capture
                # counters need no indexer -- they publish with the last good liquidity labels.
                try:
                    await self.refresh(current())
                except Exception as e:
                    error_ledger.record(
                        "collector.status_loop", "liquidity refresh failed: last labels kept", e
                    )
                # Re-read: a command applied while the indexer fetch was in flight has already
                # published its plan, which the plan read before the fetch would overwrite.
                await self._publish_ledgered(current())
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
            shown_changed = self._shown != _shown_state(plan, self._capture.capture_status())
            if shown_changed or self._shown_refusal is not self._last_refusal:
                await self._publish_ledgered(plan)

    async def _publish_ledgered(self, plan: CollectionPlan) -> None:
        try:
            await self.publish(plan)
        except Exception as e:
            error_ledger.record("collector.status_loop", "status publish failed", e)
