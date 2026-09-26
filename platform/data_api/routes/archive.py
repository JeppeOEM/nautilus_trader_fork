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
Story 25.1b: the nightly-maintenance status and its "run now" command.

`GET /api/archive/status` passes through the latest `archive:status` message `archive.scheduler`
published (cached by `buses.archive_bus`). `POST /api/archive/run` publishes a `run_now` command on
`archive:control` and returns: the scheduler queues it behind any running job, and its next status
shows the run. This service never runs maintenance or writes the catalog itself -- the scheduler,
a separate process, is the one place maintenance runs.

Both channel names and the command shape are local copies of the scheduler's (the `archive`
context is not importable from `data_api`, `platform/tests/test_boundaries.py`), as for
`ranking:control` in `routes/rankings.py`, whose publish-and-503 discipline this mirrors.
"""

import datetime as dt
import json
import re
import time

import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import field_validator

from data_api import buses
from data_api.settings import REDIS_URL


router = APIRouter()

# Module-level so tests can monkeypatch a unique channel: a live local `archive` service listens
# on the real one, and a test publish there would start a real maintenance run.
ARCHIVE_CONTROL_CHANNEL = "archive:control"

# A blackholed Redis must fail the request promptly (503), not hang the page's button.
_REDIS_TIMEOUT_SECONDS = 2.0

_DAY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


class ArchiveStep(BaseModel):
    venue: str | None
    name: str
    exit: int
    duration_s: float


class ArchiveRun(BaseModel):
    """One maintenance run. `finished` is null while it is running."""

    run_id: str
    kind: str
    day: str | None
    days: list[str] | None = None
    started: str
    finished: str | None = None
    steps: list[ArchiveStep]


class ArchiveStatusResponse(BaseModel):
    next_run: str
    next_intraday: str | None = None
    running: ArchiveRun | None = None
    last_run: ArchiveRun | None
    last_intraday: ArchiveRun | None = None


@router.get(
    "/api/archive/status",
    responses={503: {"description": "No `archive:status` message received yet, or none recently"}},
)
def get_archive_status() -> ArchiveStatusResponse:
    """
    503 until the bus has cached its first message -- the scheduler may be down or still
    starting, and an invented "never ran" status would be a lie -- and 503 again once the
    scheduler's 30 s heartbeat has been silent for the bus's stale bound, so a dead `archive`
    service never shows as a frozen "ok" or "running". Read through `buses` (not an imported
    name) so tests can swap the bus.
    """
    bus = buses.archive_bus
    latest = bus.latest
    if latest is None:
        raise HTTPException(status_code=503, detail="Archive status not yet available")
    if bus.is_stale(time.monotonic()):
        raise HTTPException(
            status_code=503,
            detail="Archive status stale: no archive:status heartbeat (is the archive service up?)",
        )
    return ArchiveStatusResponse.model_validate(latest)


class ArchiveRunRequest(BaseModel):
    """
    `day` is a closed UTC day (`YYYY-MM-DD`, before today), or null for yesterday. The scheduler
    refuses today and future days too; checking here turns a command it would only ledger and
    drop into an immediate 422 for the operator.
    """

    model_config = ConfigDict(extra="forbid")

    day: str | None = None

    @field_validator("day")
    @classmethod
    def _closed_calendar_day(cls, day: str | None) -> str | None:
        if day is None:
            return None
        if not _DAY_PATTERN.fullmatch(day):
            raise ValueError("day must be YYYY-MM-DD")
        parsed = dt.date.fromisoformat(day)  # ValueError on a non-existent date -> 422
        if parsed >= dt.datetime.now(dt.UTC).date():
            raise ValueError("day must be a closed UTC day (before today)")
        return day


class ArchiveRunResponse(BaseModel):
    day: str | None


@router.post(
    "/api/archive/run",
    status_code=202,
    responses={503: {"description": "No subscriber on `archive:control`, or Redis failed"}},
)
async def post_archive_run(body: ArchiveRunRequest) -> ArchiveRunResponse:
    """
    Ask the scheduler to run the full nightly sequence for `day`. 202: it is queued, and
    `archive:status` confirms it. A short-lived connection per call -- a rare human action.

    503 when nobody received it (`publish()` counts 0 subscribers: the `archive` service is
    down) or Redis fails or stalls -- the caller sees a lost command (DATA-07).

    Known limit: `publish()` counts every subscriber, so a stray `redis-cli SUBSCRIBE
    archive:control` makes a 202 while the scheduler is down, and a command the scheduler
    rejects (`archive.control_rejected`) or drops as a duplicate is still a 202 here. The page
    shows the run only once `archive:status` carries it. Upgrade path: an acknowledged command
    (the scheduler echoes a request id in `archive:status`).
    """
    payload = json.dumps({"command": "run_now", "day": body.day})
    try:
        async with aioredis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
            socket_timeout=_REDIS_TIMEOUT_SECONDS,
        ) as client:
            receivers = await client.publish(ARCHIVE_CONTROL_CHANNEL, payload)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"failed to publish to {ARCHIVE_CONTROL_CHANNEL}: {exc!r}"
        ) from exc
    if receivers == 0:
        raise HTTPException(
            status_code=503, detail=f"no archive scheduler subscribed to {ARCHIVE_CONTROL_CHANNEL}"
        )
    return ArchiveRunResponse(day=body.day)
