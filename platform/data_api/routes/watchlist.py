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
`GET`/`PUT /api/watchlist` (Story 33.12): the chart page's pinned instruments, one server-side list
(`views.preferences`' `chart_watchlist.toml`), so the watchlist rail follows the operator to another
browser. A UI-only list: not `research/watchlist.py`, the collection plan or the coin ranking.

Format + transport only, the filter-presets routes' contract (`routes/rankings.py`): the store and
its validation are `views.preferences`'; invalid JSON is a 400, a body that is not a storable list
a 422 naming the entry, an unreadable or corrupt file a 500 (never shown as an empty list).
"""

import json
import tomllib
from pathlib import Path

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from views import preferences

from data_api import settings
from data_api.routes import indicators as _indicators


router = APIRouter()


class WatchlistResponse(BaseModel):
    """The pinned instrument ids, in the operator's order."""

    instruments: list[str]


def _watchlist_path() -> Path:
    # Read per call (not bound at import) so a test or an operator override is honoured.
    return Path(settings.CHART_WATCHLIST_PATH)


def _load_watchlist_or_500() -> list[str]:
    try:
        return preferences.load_watchlist(_watchlist_path())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, preferences.WatchlistError) as exc:
        # Fail loud (DATA-02): a hand-edited file that no longer parses is never shown as "none".
        raise HTTPException(
            status_code=500, detail=f"chart_watchlist.toml is corrupt: {exc}"
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to read chart_watchlist.toml: {exc}"
        ) from exc


@router.get("/api/watchlist")
def get_watchlist() -> WatchlistResponse:
    """Return the pinned ids; none pinned yet is `[]`, a corrupt file a 500."""
    return WatchlistResponse(instruments=_load_watchlist_or_500())


def _store_watchlist(instrument_ids: list[str]) -> None:
    with _indicators.PREFERENCES_LOCK:
        # The stored file is read first so a corrupt one is a 500, never silently replaced: the
        # operator repairs (or deletes) a hand-edited file rather than losing what it held.
        _load_watchlist_or_500()
        try:
            preferences.save_watchlist(instrument_ids, _watchlist_path())
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"failed to write chart_watchlist.toml: {exc}"
            ) from exc


@router.put(
    "/api/watchlist",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/WatchlistResponse"}}
            },
        }
    },
)
async def put_watchlist(request: Request) -> WatchlistResponse:
    """
    Replace the whole list with the body's `{"instruments": [...]}` and return what is stored.

    Invalid JSON is a 400; a body that is not a storable list a 422 naming the entry
    (`instruments[3]: ...`), and nothing is written unless all of it passes. A corrupt stored file
    is a 500 and is left as it is. The write is a full rewrite under `PREFERENCES_LOCK`, published
    atomically.

    Known limit: whole-list last-write-wins with no version, so two tabs or browsers pinning at once
    overwrite each other (the later save wins), and the lock is in-process only. Upgrade path: a
    `version` returned by the GET and sent back by the PUT, a 409 on a mismatch.
    """
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid watchlist payload: {exc}") from exc
    try:
        instrument_ids = preferences.validate_watchlist(payload)
    except preferences.WatchlistError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await run_in_threadpool(_store_watchlist, instrument_ids)
    return WatchlistResponse(instruments=instrument_ids)
