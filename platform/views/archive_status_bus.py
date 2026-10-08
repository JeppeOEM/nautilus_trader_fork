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
Story 25.1b: the one `archive:status` subscriber behind `GET /api/archive/status`.

`archive.scheduler` (compose service `archive`) publishes its whole status -- next slots, the
running job and the last finished nightly/intraday runs with every step's exit and duration --
after every step, on start and on a 30 s heartbeat. Each message is complete, so this bus keeps
only the newest valid one; there is nothing to fan out.

Same shape and reconnect discipline as `views.rankings_bus.RankingsBus`: a plain class the
interface constructs once (`data_api.buses`), `.latest` is `None` until the first valid message,
and a malformed message leaves the previous cache untouched. The channel name is a local copy,
never imported: neither `views` nor `data_api` may import `archive`
(`platform/tests/test_boundaries.py`), exactly as they keep their own copy of
`ranking:control`/`collector:status`.

The shape check is complete enough that the route's typed response model can never fail on a
cached message (a 500 instead of the honest status), and tolerant of additive keys. A message
that fails it is the publisher's bug, so it is ledgered (DATA-07), not merely logged.

Staleness: the publisher heartbeats every 30 s, so `STALE_AFTER_SECONDS` (4 heartbeats) of silence
means the scheduler is down or this subscription is dead (a half-open TCP connection never errors
on its own). The bus then resubscribes, and `is_stale` lets the route answer 503 instead of
serving a frozen "running" or "ok" as if it were current.
"""

import asyncio
import json
import logging
import time

import redis.asyncio as aioredis
from observability import error_ledger
from observability.pubsub_liveness import receive_until_silent


logger = logging.getLogger(__name__)

ARCHIVE_STATUS_CHANNEL = "archive:status"

_LEDGER_SITE = "views.archive_status"

# Four of the scheduler's 30 s heartbeats (`archive.application.scheduler.HEARTBEAT_SECONDS`).
STALE_AFTER_SECONDS = 120.0
_POLL_SECONDS = 5.0
# `backup` (Story 26.1b): whether the service's full runs end in the off-site backup. Optional, so
# a status from a scheduler before that story still reads.
_BACKUP_STATES = ("enabled", "disabled")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_step(step: object) -> bool:
    return (
        isinstance(step, dict)
        and (step.get("venue") is None or isinstance(step.get("venue"), str))
        and isinstance(step.get("name"), str)
        and _is_int(step.get("exit"))
        and (_is_int(step.get("duration_s")) or isinstance(step.get("duration_s"), float))
    )


def _optional_str(run: dict, key: str) -> bool:
    return run.get(key) is None or isinstance(run.get(key), str)


def _valid_days(days: object) -> bool:
    return days is None or (isinstance(days, list) and all(isinstance(d, str) for d in days))


def _valid_run(run: object, *, finished: bool) -> bool:
    """
    One `running`/`last_run`/`last_intraday` object. `finished` is required on a completed run
    only (and a string wherever present); `day` may be null (a multi-day catch-up names its days
    in `days`, a list of strings when present).
    """
    if not isinstance(run, dict):
        return False
    steps = run.get("steps")
    return (
        isinstance(run.get("run_id"), str)
        and isinstance(run.get("kind"), str)
        and _optional_str(run, "day")
        and _valid_days(run.get("days"))
        and isinstance(run.get("started"), str)
        and (isinstance(run.get("finished"), str) if finished else _optional_str(run, "finished"))
        and isinstance(steps, list)
        and all(_valid_step(step) for step in steps)
    )


def valid_status(message: object) -> bool:
    """
    `next_run` and `last_run` must be present (the latter may be null); `next_intraday`,
    `running` and `last_intraday` are optional and may be null; `backup` is optional and, when
    present, `"enabled"` or `"disabled"`.
    """
    if not isinstance(message, dict) or "last_run" not in message:
        return False
    if not isinstance(message.get("next_run"), str):
        return False
    next_intraday = message.get("next_intraday")
    if next_intraday is not None and not isinstance(next_intraday, str):
        return False
    if "backup" in message and message["backup"] not in _BACKUP_STATES:
        return False
    running = message.get("running")
    last_run = message.get("last_run")
    last_intraday = message.get("last_intraday")
    return (
        (running is None or _valid_run(running, finished=False))
        and (last_run is None or _valid_run(last_run, finished=True))
        and (last_intraday is None or _valid_run(last_intraday, finished=True))
    )


class ArchiveStatusBus:
    """
    Caches the latest valid `archive:status` message.

    Invariant: `.latest` is either `None` or a message `valid_status` accepted -- the route reads
    it through `data_api.buses` and 503s on `None` rather than fabricating a status.
    """

    def __init__(self) -> None:
        self.latest: dict | None = None
        self.received_at: float | None = None  # time.monotonic() of the last valid message

    def is_stale(self, now: float) -> bool:
        """Whether no valid message arrived for `STALE_AFTER_SECONDS` (`now`: monotonic)."""
        return self.received_at is None or now - self.received_at > STALE_AFTER_SECONDS

    def handle_message(self, message: object) -> None:
        if not valid_status(message):
            error_ledger.record(
                _LEDGER_SITE, f"malformed {ARCHIVE_STATUS_CHANNEL} message, cache kept: {message!r}"
            )
            return
        assert isinstance(message, dict)
        self.latest = message
        self.received_at = time.monotonic()

    async def run(self, redis_url: str) -> None:
        """Subscribe forever, reconnecting 2 s after any error (`RankingsBus.run`'s discipline)."""
        logger.info("ArchiveStatusBus starting, url=%s", redis_url)
        while True:
            try:
                async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                    pubsub = client.pubsub()
                    await pubsub.subscribe(ARCHIVE_STATUS_CHANNEL)
                    logger.info("ArchiveStatusBus subscribed to %s", ARCHIVE_STATUS_CHANNEL)
                    await self._receive(pubsub)
            except asyncio.CancelledError:
                raise  # propagate cancellation cleanly (app shutdown)
            except Exception as exc:
                logger.warning("ArchiveStatusBus subscriber error — reconnecting in 2s: %s", exc)
                await asyncio.sleep(2)

    async def _receive(self, pubsub: aioredis.client.PubSub) -> None:
        """
        Ingest messages until `STALE_AFTER_SECONDS` pass without one, then raise so `run`
        resubscribes: a heartbeat-driven liveness check, since `listen()` blocks forever on a
        half-open connection.
        """
        await receive_until_silent(
            pubsub,
            ARCHIVE_STATUS_CHANNEL,
            STALE_AFTER_SECONDS,
            self._ingest,
            poll_seconds=_POLL_SECONDS,
        )

    def _ingest(self, data: str) -> None:
        try:
            payload = json.loads(data)
        except ValueError as exc:
            error_ledger.record(
                _LEDGER_SITE, f"unparseable {ARCHIVE_STATUS_CHANNEL} message: {data!r}", exc
            )
            return
        self.handle_message(payload)
