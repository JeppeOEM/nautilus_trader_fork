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
bot_tui's own collector:status reader + collector:control publisher (Story 6.1).

Same shape as bots_state.py's bots:status/bots:control pair (Story 4.4) -- a separate,
independent Redis connection for this channel pair (AD-4/AD-9's precedent), and one
message per instrument rather than a single aggregated payload.

Story 29.2: every venue's collector publishes here, each with its own plan aggregate
(`unpinned_ids` plus `venue`, `cap`, `accepts_commands`, `min_liquidity_usd`, `last_apply`),
kept per venue. A row carries no venue: it is derived from the id (SIGNAL-01). Only a plan
that accepts commands can be driven from here; the rest are shown read-only. Story 29.4: every
`collector:control` command carries its venue, and Bybit's and Hyperliquid's plans accept
commands too.

Story 29.5: each aggregate's `last_refusal` (the plan's latest refused command) is kept per venue
with this TUI's own receive time, set only when it changes; and the market browser's adds sent
from here are remembered (`record_sent_add`) until their row appears, so a row can read `pending`,
`failed: <reason>` or `no answer` before -- or instead of -- any `collector:status` row. A new
refusal of a `start` naming a sent add is copied onto that add (`add_refused_reason`) the moment it
arrives, because the aggregate holds only one refusal per venue: a second refusal, or a restarted
collector's null, would otherwise replace the answer before the browser showed it.
"""

import asyncio
import json
import logging
import os
import time

import redis.asyncio as aioredis
from kernel.venues import VENUE_KINDS
from kernel.venues import MalformedInstrumentId
from kernel.venues import venue_of


logger = logging.getLogger(__name__)

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")

_LATEST_COLLECTOR_STATUS: dict[str, dict] = {}
_LATEST_RECEIVED_AT: dict[str, float] = {}

# The latest plan aggregate per venue: its `unpinned_ids` (the plan's exclude -- via a past
# "unpin" or a hand-edit of config.toml -- which has no per-instrument row of its own) and,
# from Story 29.2 producers, the plan facts appended after it. Keyed by venue so one venue's
# aggregate never overwrites another's.
_LATEST_PLANS: dict[str, dict] = {}
_PLAN_RECEIVED_AT: dict[str, float] = {}

# Per venue, the row ids received since that venue's latest aggregate: which rows the next
# aggregate's publish republished. Arrival order, not receipt time, so a wall-clock step (an NTP
# correction) can never make a just-republished row look older than the aggregate before it.
_REPUBLISHED_SINCE_PLAN: dict[str, set[str]] = {}

# Per venue, the aggregate's latest non-null `last_refusal` and when this TUI received it. The time
# is local and taken only when the refusal differs from the previous one: the browser compares it
# with its own send time, never with the collector's clock (Story 29.5).
_LAST_REFUSAL: dict[str, dict] = {}
_REFUSAL_RECEIVED_AT: dict[str, float] = {}

# The market browser's adds sent from this TUI: id -> local send time, forgotten once the id's row
# appears (Story 29.5). Human-paced, one entry per add, so it stays tiny.
_SENT_ADDS: dict[str, float] = {}

# The answer to a sent add when it was a refusal: id -> the refusal's reason, recorded on arrival
# (`_track_refusal`) and dropped by a re-send or the id's row (Story 29.5). Arrival order is the
# ordering -- the add was recorded before it was published -- so no clock is compared.
_ADD_REFUSALS: dict[str, str] = {}

# The venue of an aggregate without `venue`: only dYdX published one before Story 29.2.
LEGACY_PLAN_VENUE = "DYDX"

# The section a row whose id has no venue suffix is grouped under; nothing can control it.
UNKNOWN_VENUE = "UNKNOWN"

# collector.py's _status_loop publishes on its own liquidity_check_seconds cadence
# (default 1800s) *plus* immediately after any control action -- a row is only
# considered stale if nothing has updated it for several multiples of the slow cadence.
_STATUS_STALE_SECONDS: float = 3600.0


def _handle_plan_message(message: dict) -> None:
    ids = message["unpinned_ids"]
    venue = message.get("venue", LEGACY_PLAN_VENUE)
    well_formed = isinstance(ids, list) and all(isinstance(i, str) for i in ids)
    if not (well_formed and isinstance(venue, str) and venue):
        logger.warning("collector:status plan aggregate malformed, ignoring: %r", message)
        return
    if venue in _LATEST_PLANS:
        _drop_rows_not_republished(venue, _REPUBLISHED_SINCE_PLAN.get(venue, set()))
    _REPUBLISHED_SINCE_PLAN[venue] = set()
    _LATEST_PLANS[venue] = message
    _PLAN_RECEIVED_AT[venue] = time.time()
    _track_refusal(venue, message.get("last_refusal"))


def _track_refusal(venue: str, refusal: object) -> None:
    """
    Keep `venue`'s `last_refusal`, stamped with the local receive time when it differs from the
    one kept (every publish repeats the same refusal). Null -- a collector that has refused nothing
    since it (re)started, or a pre-29.5 aggregate without the key -- clears it; a value that is not
    an object is logged and ignored.
    """
    if refusal is None:
        _LAST_REFUSAL.pop(venue, None)
        _REFUSAL_RECEIVED_AT.pop(venue, None)
        return
    if not isinstance(refusal, dict):
        logger.warning("collector:status last_refusal malformed, ignoring: %r", refusal)
        return
    if _LAST_REFUSAL.get(venue) != refusal:
        _LAST_REFUSAL[venue] = refusal
        _REFUSAL_RECEIVED_AT[venue] = time.time()
        _answer_sent_add(refusal)


def _answer_sent_add(refusal: dict) -> None:
    """
    Record a new refusal as the answer to this TUI's outstanding add of its id -- only a refused
    `start`, the add's own wire verb, so a refused `stop`/`unpin` of the id never reads as the add
    failing. Known limit: another sender's refused `start` of the same id, arriving while this
    TUI's add is outstanding, is taken as this add's answer (the refusal names no sender); upgrade
    path: a per-command request id echoed in `last_refusal`.
    """
    iid = refusal.get("id")
    if refusal.get("action") == "start" and isinstance(iid, str) and iid in _SENT_ADDS:
        reason = refusal.get("reason")
        _ADD_REFUSALS[iid] = reason if isinstance(reason, str) else "?"


def latest_refusal(venue: str) -> tuple[dict, float] | None:
    """Return `venue`'s latest refusal and when this TUI received it, or None."""
    refusal = _LAST_REFUSAL.get(venue)
    if refusal is None:
        return None
    return refusal, _REFUSAL_RECEIVED_AT[venue]


def record_sent_add(instrument_id: str, now: float) -> None:
    """
    Remember that this TUI sent an add of `instrument_id` at local time `now`, dropping the answer
    to an earlier add of it: called before the add is published, so any refusal arriving after
    this answers the new add.
    """
    _SENT_ADDS[instrument_id] = now
    _ADD_REFUSALS.pop(instrument_id, None)


def forget_sent_add(instrument_id: str, sent_at: float) -> None:
    """
    Drop the add of `instrument_id` sent at `sent_at` whose publish failed: it never reached a
    collector, so it must not read `pending` and then "no answer from <VENUE> collector". A later
    re-send (another `sent_at`) is kept.
    """
    if _SENT_ADDS.get(instrument_id) == sent_at:
        del _SENT_ADDS[instrument_id]


def add_refused_reason(instrument_id: str) -> str | None:
    """Return the reason the collector refused this TUI's outstanding add of the id, or None."""
    return _ADD_REFUSALS.get(instrument_id)


def sent_adds() -> dict[str, float]:
    """Return every add this TUI sent that no row has answered yet: id -> local send time."""
    return dict(_SENT_ADDS)


def sent_add_at(instrument_id: str) -> float | None:
    """Return when this TUI last sent an add of `instrument_id` not yet answered by a row."""
    return _SENT_ADDS.get(instrument_id)


def _drop_rows_not_republished(venue: str, republished: set[str]) -> None:
    """
    Drop `venue`'s rows not republished since its previous aggregate: every publish sends all of a
    plan's rows and then its aggregate, as one uninterrupted burst (`StatusPublisher.publish`
    serializes its publishes), so such a row left the plan without a tombstone -- a plan file
    edited while its collector was down, or a stop missed while this TUI was down.
    Known limit: a listener that reconnects in the middle of a publish drops the rows it missed
    until the next one (a republish within `STATUS_CHANGE_POLL_SECONDS` of a change, else at the
    full cadence); upgrade path: a per-publish sequence number on every message.
    """
    for iid in [i for i in _LATEST_COLLECTOR_STATUS if venue_of_row(i) == venue]:
        if iid not in republished:
            _LATEST_COLLECTOR_STATUS.pop(iid, None)
            _LATEST_RECEIVED_AT.pop(iid, None)


def _handle_status_message(message: dict) -> None:
    if "unpinned_ids" in message:
        _handle_plan_message(message)
        return

    iid = message.get("id")
    if not isinstance(iid, str) or not iid:
        logger.warning("collector:status message missing string 'id', ignoring: %r", message)
        return
    if message.get("removed"):
        # stop/unpin fully removed this instrument -- drop it now rather than waiting
        # up to _STATUS_STALE_SECONDS for is_stale() to notice it stopped republishing.
        _LATEST_COLLECTOR_STATUS.pop(iid, None)
        _LATEST_RECEIVED_AT.pop(iid, None)
        _REPUBLISHED_SINCE_PLAN.get(venue_of_row(iid), set()).discard(iid)
        return
    _LATEST_COLLECTOR_STATUS[iid] = message
    _LATEST_RECEIVED_AT[iid] = time.time()
    _REPUBLISHED_SINCE_PLAN.setdefault(venue_of_row(iid), set()).add(iid)
    # The row answers the add: from here on `collector:status` alone says what the id is.
    _SENT_ADDS.pop(iid, None)
    _ADD_REFUSALS.pop(iid, None)


def is_stale(instrument_id: str, now: float | None = None) -> bool:
    received_at = _LATEST_RECEIVED_AT.get(instrument_id, 0.0)
    if received_at == 0.0:
        return True
    if now is None:
        now = time.time()
    return (now - received_at) > _STATUS_STALE_SECONDS


def plan_is_stale(venue: str, now: float | None = None) -> bool:
    """Return whether `venue`'s aggregate is missing or older than `_STATUS_STALE_SECONDS`."""
    received_at = _PLAN_RECEIVED_AT.get(venue, 0.0)
    if received_at == 0.0:
        return True
    if now is None:
        now = time.time()
    return (now - received_at) > _STATUS_STALE_SECONDS


def venue_of_row(instrument_id: str) -> str:
    """Return the row's venue from its id suffix (SIGNAL-01), `UNKNOWN_VENUE` if it has none."""
    try:
        return venue_of(instrument_id)
    except MalformedInstrumentId:
        return UNKNOWN_VENUE


def plan_cap(venue: str) -> int | None:
    """Return `venue`'s published cap, or None when unknown (no aggregate, or an older one)."""
    cap = _LATEST_PLANS.get(venue, {}).get("cap")
    return cap if isinstance(cap, int) and not isinstance(cap, bool) else None


def collected_count(venue: str) -> int:
    """Return how many rows (collected or pending) `venue` has: what its cap counts."""
    return sum(1 for iid in _LATEST_COLLECTOR_STATUS if venue_of_row(iid) == venue)


def command_refusal(venue: str, now: float | None = None) -> str | None:
    """
    Return why `venue`'s plan cannot take a `collector:control` command, or None if it can.

    Every command carries its venue (Story 29.4), and each venue's collector acts only on its
    own, so only a plan whose aggregate says `accepts_commands` is driven from here (an aggregate
    without that key, from a pre-29.2 producer, keeps the pre-story contract: only dYdX). With no
    aggregate cached at all -- the collector is down, or has not published since this TUI started
    -- every venue is refused, dYdX included, so nothing is sent to a consumer that may not
    exist (e.g. after `make down-dydx`). An aggregate older than the staleness window
    (`_STATUS_STALE_SECONDS`) is refused too.

    Known limit: a collector stopped less than `_STATUS_STALE_SECONDS` ago still gets commands
    (with no error shown), because a plan's aggregate republishes only every 1800 s
    (`PLAN_STATUS_SECONDS`/`liquidity_check_seconds`) and the window must outlast that. For the
    same reason a TUI started between two publishes refuses every command for up to 1800 s,
    dYdX's too (which, before Story 29.4, it sent blind), until a publish arrives: a command,
    a reload, a pending row settling or a collector restart publishes at once. Upgrade path for
    both: a faster aggregate heartbeat, then a window of a few heartbeats (or the last publish
    kept in a Redis key this TUI reads on connect, `PLAN_STATUS_SECONDS`'s own limit).
    """
    if venue == UNKNOWN_VENUE:
        return "unknown venue: the id has no venue suffix"
    if venue not in VENUE_KINDS:
        return f"unknown venue {venue!r}: not one of {', '.join(sorted(VENUE_KINDS))}"
    plan = _LATEST_PLANS.get(venue)
    if plan is None:
        return f"waiting for {venue} plan on collector:status"
    if plan_is_stale(venue, now):
        minutes = _STATUS_STALE_SECONDS / 60
        return f"{venue}: no collector:status for over {minutes:.0f} min (collector down?)"
    if plan.get("accepts_commands", venue == LEGACY_PLAN_VENUE) is True:
        return None
    return f"{venue}: static plan: edit platform/capture/venues/{venue.lower()}/config.toml"


async def publish_control(
    redis_url: str, action: str, instrument_id: str | None = None, venue: str | None = None
) -> bool:
    """
    Publish a start/unpin/stop/pin_top_liquid request to collector:control; return whether Redis
    took it (a failure is logged, and the caller tells the operator).
    `instrument_id` is omitted for pin_top_liquid, which targets no single id. `venue` (Story
    29.4) is appended last, so the pre-29.4 `{action, id}` bytes stay the payload's prefix; each
    venue's collector acts only on its own venue, and one without it is dYdX's.
    Short-lived per-call connection -- same reasoning as bots_state.publish_control:
    a rare, human-triggered action, not worth a persistent publisher connection.
    """
    payload: dict = {"action": action}
    if instrument_id is not None:
        payload["id"] = instrument_id
    if venue is not None:
        payload["venue"] = venue
    try:
        async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
            await client.publish("collector:control", json.dumps(payload))
    except Exception as exc:
        logger.warning("failed to publish collector:control %s: %s", action, exc)
        return False
    return True


async def _redis_listener(redis_url: str) -> None:
    logger.info("bot_tui collector:status listener starting, url=%s", redis_url)
    while True:
        try:
            logger.info("bot_tui collector:status listener connecting...")
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe("collector:status")
                logger.info("bot_tui collector:status listener subscribed")
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                        _handle_status_message(payload)
                    except Exception as exc:
                        logger.warning("collector:status message parse/ingest error: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bot_tui collector:status listener error — reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)
