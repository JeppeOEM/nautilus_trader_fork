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
Bot ownership (DW-78): one live process per `bot_id` on a Redis bus.

`bots:status` is pub/sub, so it holds no presence a second process could check; two processes
hosting one `bot_id` (the paper fleet beside an exec config, or a cloned config) would silently
merge their status, control, incidents and history traffic. Each bot therefore holds a lease,
`bots:owner:{bot_id}`, whose value names this process life (`owner_value`). `python3 -m bots`
claims every hosted bot's lease before the node runs (`claim_all`), each `Supervisor` heartbeat
renews it and shutdown releases it (`release_all`).

Invariants: a start is refused only after a contested lease was observed for one full TTL plus
one heartbeat -- a crashed prior life's lease has expired by then, while a live holder renews
every heartbeat and is still there; the fleet never starts with its ownership unverified (an
unreachable Redis is retried, not skipped); a refused start leaves no lease of its own behind.
"""

import asyncio
import json
import time
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Sequence
from datetime import UTC
from datetime import datetime

from observability import error_ledger

from bots.application.ports import BusConnection
from bots.application.ports import Connect
from bots.application.ports import owner_key
from bots.application.supervise import OWNER_TTL_HEARTBEATS
from bots.application.supervise import STATUS_HEARTBEAT_SECONDS


OWNER_TTL_SECONDS = OWNER_TTL_HEARTBEATS * STATUS_HEARTBEAT_SECONDS
# How often a contested or unreachable claim is retried; each retry also renews the leases this
# start already holds, so waiting on one bot never lets another's expire.
OWNER_RETRY_SECONDS = 1.0


class BotIdCollision(RuntimeError):
    """A hosted `bot_id`'s lease is held by another live process: this process must not start."""


def owner_value(mode: str, config: str, host: str, token: str, claimed_at: float) -> str:
    """
    Return the lease value of one process life, built once and compared verbatim by every hold
    and release. `token` (a fresh uuid4 hex per process) makes it unique: a second process of the
    very same config and host is a collision too, never mistaken for the holder.
    """
    return json.dumps(
        {"mode": mode, "config": config, "host": host, "token": token, "claimed_at": claimed_at}
    )


def describe_owner(value: str) -> str:
    """Describe a holder for an operator; show the raw value when `owner_value` did not write it."""
    try:
        fields = json.loads(value)
        claimed = datetime.fromtimestamp(float(fields["claimed_at"]), UTC).isoformat()
        return (
            f"mode={fields['mode']} config={fields['config']} host={fields['host']} since {claimed}"
        )
    except (ValueError, KeyError, TypeError, OverflowError, OSError):
        return f"unrecognised holder {value!r}"


async def claim_all(
    connect: Connect,
    bot_ids: Sequence[str],
    value: str,
    *,
    ttl_seconds: float = OWNER_TTL_SECONDS,
    retry_seconds: float = OWNER_RETRY_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """
    Hold every bot's lease, or raise `BotIdCollision` naming each contested bot, its holder and
    this process, having released every lease this call held. A contested lease is retried until
    `ttl_seconds` plus one heartbeat after it was first seen contested; a Redis failure is
    ledgered (`bots.redis`) and retried for as long as it lasts, never counted against that wait:
    the contested window starts over after one. A round (or the refusal's holder read) Redis does
    not answer within one heartbeat counts as such a failure, so a hung connection is ledgered,
    never waited on silently, and a slow round cannot outlast the leases it renews.

    Known limit: each round holds the leases one key at a time, so two processes of overlapping
    bot sets starting at the same instant can split the contested ids between them; neither then
    holds its whole set, and both are refused after the wait -- the fleet stays down (loudly,
    never both running) until an operator removes the duplicate. Upgrade path: claim every lease
    in one all-or-nothing script, so exactly the first starter wins.
    """
    ttl_ms = max(1, round(ttl_seconds * 1000))
    round_seconds = ttl_seconds / OWNER_TTL_HEARTBEATS
    deadline: float | None = None
    while True:
        try:
            contested = await asyncio.wait_for(
                _hold_round(connect, bot_ids, value, ttl_ms), round_seconds
            )
            if not contested:
                return
            if deadline is None:
                deadline = clock() + ttl_seconds + STATUS_HEARTBEAT_SECONDS
            elif clock() >= deadline:
                await _refuse(connect, bot_ids, contested, value, round_seconds)
        except BotIdCollision:
            raise
        except Exception as exc:
            deadline = None
            error_ledger.record(
                "bots.redis",
                f"bots:owner claim failed, retrying in {retry_seconds}s: the fleet does not start "
                "until every bot's ownership is verified",
                exc,
            )
        await sleep(retry_seconds)


async def reconfirm(
    connect: Connect,
    bot_ids: Sequence[str],
    value: str,
    *,
    ttl_seconds: float = OWNER_TTL_SECONDS,
) -> None:
    """
    Renew every lease `claim_all` took, once, right before the node runs: building the node can
    take longer than a TTL, so a lease may have lapsed -- and been claimed by another starter --
    before the first heartbeat renews it. Raise `BotIdCollision` (having released ours) when one
    is held by another process now; never wait, since the node already owns the signal handlers
    and a stop would be swallowed by a wait. A Redis failure is ledgered (`bots.redis`) and the
    start goes on: ownership was verified by the claim, and each heartbeat holds the lease again.
    """
    ttl_ms = max(1, round(ttl_seconds * 1000))
    round_seconds = ttl_seconds / OWNER_TTL_HEARTBEATS
    try:
        contested = await asyncio.wait_for(
            _hold_round(connect, bot_ids, value, ttl_ms), round_seconds
        )
        if contested:
            await _refuse(connect, bot_ids, contested, value, round_seconds)
    except BotIdCollision:
        raise
    except Exception as exc:
        error_ledger.record(
            "bots.redis",
            "bots:owner renewal before the node runs failed; the heartbeats renew the leases",
            exc,
        )


async def _hold_round(
    connect: Connect, bot_ids: Sequence[str], value: str, ttl_ms: int
) -> list[str]:
    """Hold (claim or renew) every lease once; return the bot ids another holder still has."""
    async with connect() as connection:
        return [
            bot_id
            for bot_id in bot_ids
            if not await connection.hold(owner_key(bot_id), value, ttl_ms)
        ]


async def _refuse(
    connect: Connect,
    bot_ids: Sequence[str],
    contested: list[str],
    value: str,
    timeout: float,
) -> None:
    """
    Raise the collision for every contested bot still held; return (so the claim retries) when
    every one was freed since the round -- a lease released just now is no collision. The holder
    read is bounded by `timeout` (a hung Redis raises `TimeoutError`, never waits silently).
    """
    holders = await asyncio.wait_for(_read_holders(connect, contested), timeout)
    if not holders:
        return
    # Every id, not only the held ones: the release is a compare-and-delete on our own value.
    await release_all(connect, bot_ids, value)
    named = "; ".join(f"{bot_id!r} by {describe_owner(h)}" for bot_id, h in holders.items())
    raise BotIdCollision(
        f"bot_id already owned by another live process: {named}. "
        f"This process: {describe_owner(value)}. Stop the other process or rename the bot_id."
    )


async def _read_holders(connect: Connect, bot_ids: list[str]) -> dict[str, str]:
    async with connect() as connection:
        return await _holders(connection, bot_ids)


async def _holders(connection: BusConnection, bot_ids: list[str]) -> dict[str, str]:
    holders = {}
    for bot_id in bot_ids:
        holder = await connection.get(owner_key(bot_id))
        if holder is not None:
            holders[bot_id] = holder
    return holders


async def release_all(connect: Connect, bot_ids: Sequence[str], value: str) -> None:
    """
    Release every lease still holding `value` (another holder's is never touched). Best effort:
    a failure, or a Redis that does not answer within one heartbeat (shutdown must not wedge on
    it, and must finish inside Docker's 10 s stop grace period, or SIGKILL lands before the
    timeout is ledgered), is ledgered (`bots.ownership`) and the lease simply expires one TTL
    later, which only delays the next start of these bots by that long.
    """
    try:
        await asyncio.wait_for(_release_each(connect, bot_ids, value), STATUS_HEARTBEAT_SECONDS)
    except Exception as exc:
        error_ledger.record(
            "bots.ownership",
            f"bots:owner release failed for {list(bot_ids)}; the leases expire on their own",
            exc,
        )


async def _release_each(connect: Connect, bot_ids: Sequence[str], value: str) -> None:
    async with connect() as connection:
        for bot_id in bot_ids:
            await connection.release(owner_key(bot_id), value)
