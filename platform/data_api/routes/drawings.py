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
"""
`GET`/`PUT /api/coin/{instrument_id}/drawings` -- the chart's drawings (horizontal lines,
trendlines, Fibonacci retracements, long/short positions) as one server-side resource per
instrument (Story 32.5), so a drawing follows the operator to another browser.

Format + transport only: the file is `views.preferences`'s `load_chart_drawings`/
`save_chart_drawings` (`chart_drawings.toml` in `CHART_PREFERENCES_DIR`) and every item is checked
by `views.preferences.validate_drawing`. A malformed item is a 422 naming the field, never dropped
silently (DATA-07); the PUT replaces the instrument's whole list (the client persists its full
state, debounced), so a drawing removed in the browser is removed from the file.

Known limit: the PUT is whole-list last-write-wins with no version or ETag, so two browsers
editing one coin's drawings overwrite each other (the later save wins). Upgrade path: a `version`
field returned by the GET and sent back by the PUT, a 409 on a mismatch, and a client-side merge.
"""

import json
import tomllib
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from kernel.venues import venue_of
from pydantic import BaseModel
from views import preferences

from data_api import settings


router = APIRouter()


class DrawingsResponse(BaseModel):
    # Tagged items (`kind` = hline | trendline | fib | position | anchored_vp | anchored_vwap | ray |
    # extended | vline | rect | channel | text | arrow | fib_extension | price_range | date_range,
    # `views.preferences.DRAWING_KINDS`); their per-kind fields are
    # `views.preferences.validate_drawing`'s, mirrored by the frontend's `lib/drawings.ts`.
    items: list[dict[str, Any]]


def _path() -> Path:
    # Read per call (not bound at import) so a test or an operator override of the settings value
    # is honoured.
    return Path(settings.CHART_DRAWINGS_PATH)


@router.get("/api/coin/{instrument_id}/drawings")
def get_coin_drawings(instrument_id: str) -> DrawingsResponse:
    """Return this instrument's saved drawings; none saved yet is `[]`, not an error."""
    venue_of(instrument_id)  # a malformed id is a 400 (the app's handler), as on every coin route
    try:
        config = preferences.load_chart_drawings(_path())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, preferences.DrawingError) as exc:
        # Fail loud (DATA-02): a hand-edited file that no longer parses must be seen, not shown
        # as an empty chart.
        raise HTTPException(
            status_code=500, detail=f"chart_drawings.toml is corrupt: {exc}"
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to read chart_drawings.toml: {exc}"
        ) from exc
    return DrawingsResponse(items=config.get(instrument_id, []))


@router.put("/api/coin/{instrument_id}/drawings")
async def put_coin_drawings(instrument_id: str, request: Request) -> dict[str, bool]:
    """
    Replace this instrument's drawings with the body's `{"items": [...]}`.

    Invalid JSON is a 400; a body or item that is not a storable drawing is a 422 whose detail
    names the field (`items[2].anchors: ...`). Nothing is written unless every item passes.
    """
    venue_of(instrument_id)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid drawings payload: {exc}") from exc
    if not isinstance(payload, dict) or "items" not in payload:
        raise HTTPException(status_code=422, detail='items: the body must be {"items": [...]}')
    items = payload["items"]
    try:
        preferences.validate_drawings(items)
    except preferences.DrawingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    path = _path()
    try:
        config = preferences.load_chart_drawings(path)
        config[instrument_id] = items
        preferences.save_chart_drawings(config, path)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, preferences.DrawingError) as exc:
        raise HTTPException(
            status_code=500, detail=f"chart_drawings.toml is corrupt: {exc}"
        ) from exc
    except OSError as exc:
        # The payload was fine; the write failed (an unwritable mount): a server condition.
        raise HTTPException(
            status_code=500, detail=f"failed to write chart_drawings.toml: {exc}"
        ) from exc
    return {"ok": True}
