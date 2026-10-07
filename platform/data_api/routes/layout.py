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
`GET`/`PUT /api/coin/{instrument_id}/layout` and the default-template endpoints -- a coin's chart
layout (timeframe, mode, volume, crosshair, dragged pane heights, zoom, volume-profile settings) as
one server-side resource per instrument (Story 32.6), so a chart comes back as it was left.

Format + transport only: the file is `views.preferences`'s `load_chart_layouts`/
`save_chart_layouts` (`chart_layouts.toml` in `CHART_PREFERENCES_DIR`), every layout is checked by
`views.preferences.validate_layout` (a 422 naming the key, nothing written) and the indicator list
lives in `chart_indicators.toml`, validated by the indicators route's shared
`check_indicator_entries`. Drawings are never read, copied or touched here.

- A table is only ever created for an id the catalog holds an instrument definition for: a first
  GET, a reset or a PUT of an id with neither a table nor a definition (a typo, a probe) is a 404
  and writes nothing, so a request can never grow the preference files with phantom coins.
- `GET /layout` -> `{"layout": {...}, "seeded": bool}`. A coin with no table is seeded from the
  `[default]` template (or the built-in layout): its `default_indicators` are written to the coin's
  list in `chart_indicators.toml` FIRST, but only when the coin has no list there yet (a coin set
  up before layouts existed keeps its indicators, and the template's are then not even checked),
  then the layout table, so a crash between the two leaves a coin that is simply seeded again on
  the next GET (idempotent), never one with a layout and no indicators. A stored
  `bar_seconds`/`mode` outside the supported set is returned as stored (the client falls back, see
  `views.preferences.validate_layout`).
- `PUT /layout` `{"layout": {...}}` -> `{"layout": {...}}`; 400 bad JSON, 422 naming the key.
- `POST /layout/save-as-default` -> `{"default": {...}}`: this coin's layout and its current
  indicator list become `[default]`.
- `POST /layout/reset-to-default` -> `{"layout": {...}}`: this coin's layout and indicator list are
  replaced by `[default]` (the built-in layout and no indicators when there is none).

Known limit: the read-modify-write of the preference files is serialized by the indicators route's
`PREFERENCES_LOCK` (shared, because both routes write `chart_indicators.toml`), so it is safe within
the one `data_api` process, not across processes; and, like the drawings, a PUT is whole-layout
last-write-wins with no version or ETag. Upgrade path: a file lock plus a `version` returned by the
GET, a 409 on a mismatch and a client-side merge.
"""

import copy
import json
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from kernel.venues import venue_of
from views import preferences
from views.catalog_reads import NoInstrumentDefinition
from views.catalog_reads import instrument_precision

from data_api import settings
from data_api.routes import indicators as indicators_routes


router = APIRouter()


def _path() -> Path:
    # Read per call (not bound at import) so a test or an operator override is honoured.
    return Path(settings.CHART_LAYOUTS_PATH)


def _indicators_path() -> Path:
    # The indicators route's own name, so both routes always address the same file.
    return Path(indicators_routes.CHART_INDICATOR_CONFIG_PATH)


@contextmanager
def _file_errors() -> Iterator[None]:
    """Map a corrupt or unreadable preference file to a 500 (DATA-02, as the drawings route)."""
    try:
        yield
    # KeyError/TypeError: `load_chart_indicators` on a malformed entry (as the indicators route).
    except (
        tomllib.TOMLDecodeError,
        UnicodeDecodeError,
        KeyError,
        TypeError,
        preferences.LayoutError,
    ) as exc:
        raise HTTPException(status_code=500, detail=f"preference file is corrupt: {exc}") from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to access a preference file: {exc}"
        ) from exc


def _template(
    layouts: preferences.ChartLayouts, instrument_id: str
) -> tuple[dict[str, Any], list[Any]]:
    """
    Return the `[default]` layout and indicator list, or the built-in layout and none, as a copy
    for `instrument_id`: a template compare of that coin itself is dropped from the copy (the
    template keeps it), so a seed or reset never stores a layout the coin's own PUT refuses.
    """
    if layouts.default is None:
        return copy.deepcopy(preferences.BUILTIN_DEFAULT_LAYOUT), []
    layout = copy.deepcopy(layouts.default)
    compare = layout["compare"]
    compare["symbols"] = [iid for iid in compare["symbols"] if iid != instrument_id]
    return layout, list(layouts.default_indicators)


def _write_template_indicators(
    instrument_id: str, entries: list[Any], *, keep_existing: bool = False
) -> None:
    """
    Revalidate the template's indicators (the catalog may have changed) and write the coin's list;
    with `keep_existing` a coin that already has a list there is left alone (first-open seeding),
    before the template is checked, so a stale default never blocks a coin it would not touch.
    """
    path = _indicators_path()
    config = preferences.load_chart_indicators(path)
    if keep_existing and instrument_id in config:
        return
    try:
        indicators_routes.check_indicator_entries(entries)
    except HTTPException as exc:
        raise HTTPException(
            status_code=500, detail=f"the default's indicators are invalid: {exc.detail}"
        ) from exc
    config[instrument_id] = entries
    preferences.save_chart_indicators(config, path)


def _stored_layout(instrument_id: str) -> dict[str, Any] | None:
    with indicators_routes.PREFERENCES_LOCK, _file_errors():
        return preferences.load_chart_layouts(_path()).layouts.get(instrument_id)


def _require_definition(instrument_id: str) -> None:
    """404 for an id the catalog defines no instrument for (as the candles route): no table for it."""
    try:
        instrument_precision(settings.CATALOG_PATH, instrument_id)
    except NoInstrumentDefinition as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/coin/{instrument_id}/layout")
def get_coin_layout(instrument_id: str) -> dict[str, Any]:
    """Return this coin's layout, seeding it from the default on its first open."""
    venue_of(instrument_id)  # a malformed id is a 400 (the app's handler), as on every coin route
    stored = _stored_layout(instrument_id)
    if stored is not None:
        return {"layout": stored, "seeded": False}
    _require_definition(instrument_id)  # outside the lock: a catalog read, first open only
    with indicators_routes.PREFERENCES_LOCK, _file_errors():
        layouts = preferences.load_chart_layouts(_path())
        if instrument_id in layouts.layouts:  # seeded by a concurrent first GET meanwhile
            return {"layout": layouts.layouts[instrument_id], "seeded": False}
        layout, entries = _template(layouts, instrument_id)
        _write_template_indicators(instrument_id, entries, keep_existing=True)  # then the layout
        layouts.layouts[instrument_id] = layout
        preferences.save_chart_layouts(layouts, _path())
        return {"layout": layout, "seeded": True}


def _store_layout(instrument_id: str, layout: dict[str, Any]) -> None:
    with indicators_routes.PREFERENCES_LOCK, _file_errors():
        layouts = preferences.load_chart_layouts(_path())
        layouts.layouts[instrument_id] = layout
        preferences.save_chart_layouts(layouts, _path())


@router.put("/api/coin/{instrument_id}/layout")
async def put_coin_layout(instrument_id: str, request: Request) -> dict[str, Any]:
    """
    Replace this coin's layout with the body's `{"layout": {...}}` (every field required; the
    indicator list and drawings are other resources). Invalid JSON is a 400, a layout that is not
    storable a 422 naming the key; nothing is written unless it passes, and no other coin changes.
    """
    venue_of(instrument_id)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid layout payload: {exc}") from exc
    if not isinstance(payload, dict) or "layout" not in payload:
        raise HTTPException(status_code=422, detail='layout: the body must be {"layout": {...}}')
    try:
        layout = preferences.validate_layout(payload["layout"])
    except preferences.LayoutError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if instrument_id in layout["compare"]["symbols"]:
        # A coin compared with itself is a client bug, refused (DATA-07). Only the coin's own PUT
        # knows its id: the `[default]` template keeps any id, and `_template` drops it from the
        # copy a seed or reset stores.
        raise HTTPException(
            status_code=422, detail="compare.symbols: must not contain the coin's own instrument id"
        )
    if await run_in_threadpool(_stored_layout, instrument_id) is None:
        await run_in_threadpool(_require_definition, instrument_id)
    await run_in_threadpool(_store_layout, instrument_id, layout)
    return {"layout": layout}


@router.post("/api/coin/{instrument_id}/layout/save-as-default")
def save_coin_layout_as_default(instrument_id: str) -> dict[str, Any]:
    """Make this coin's layout and current indicator list the `[default]` template."""
    venue_of(instrument_id)
    with indicators_routes.PREFERENCES_LOCK, _file_errors():
        layouts = preferences.load_chart_layouts(_path())
        if instrument_id not in layouts.layouts:
            raise HTTPException(status_code=404, detail=f"{instrument_id} has no saved layout yet")
        try:
            # Strict: a stale timeframe tolerated on read must not become the template.
            layouts.default = preferences.validate_layout(layouts.layouts[instrument_id])
        except preferences.LayoutError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        entries = preferences.load_chart_indicators(_indicators_path()).get(instrument_id, [])
        try:
            indicators_routes.check_indicator_entries(entries)
        except HTTPException as exc:
            raise HTTPException(
                status_code=422, detail=f"the coin's indicators cannot be a default: {exc.detail}"
            ) from exc
        layouts.default_indicators = entries
        preferences.save_chart_layouts(layouts, _path())
        return {"default": layouts.default}


@router.post("/api/coin/{instrument_id}/layout/reset-to-default")
def reset_coin_layout_to_default(instrument_id: str) -> dict[str, Any]:
    """Replace this coin's layout and indicator list with the template; drawings are untouched."""
    venue_of(instrument_id)
    if _stored_layout(instrument_id) is None:
        _require_definition(instrument_id)
    with indicators_routes.PREFERENCES_LOCK, _file_errors():
        layouts = preferences.load_chart_layouts(_path())
        layout, entries = _template(layouts, instrument_id)
        _write_template_indicators(instrument_id, entries)
        layouts.layouts[instrument_id] = layout
        preferences.save_chart_layouts(layouts, _path())
        return {"layout": layout}
