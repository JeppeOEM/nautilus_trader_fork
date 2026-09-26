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
`GET /api/indicators/catalog`, `GET`/`PUT /api/coin/{instrument_id}/indicators` -- Story 15.6's
per-coin indicator picker/config-persistence endpoints, and `GET
/api/coin/{instrument_id}/indicator-values` -- the new cursor-paginated route that actually
computes a picker-configured indicator's plotted values.

Format + transport only (Story 24.2): the merged catalog and the per-name dispatch are
`views.indicator_picker` (`merged_catalog`, `replay_entry`, `values_by_time`), the persisted
selections `views.preferences` (`load_chart_indicators`/`save_chart_indicators`, same TOML file,
same error semantics), and the values page `views.chart_series.indicator_values_page` -- the
chart's own `candle_page` plus the replay and its gap rows. This module parses and bounds the
untrusted request, passes the candles route's `CATALOG_PATH`/`CANDLES_DB_DIR` in (the values
read candles exactly as the chart does), builds the response models and maps failures to 400/500.
"""

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from kernel.venues import market_kind
from kernel.venues import venue_of
from pydantic import BaseModel
from views import chart_series
from views import indicator_picker
from views import preferences

from data_api import buses
from data_api.routes import candles as _candles


# Default mirrors dashboard.py:85-88 exactly (same env var name, same default path).
CHART_INDICATOR_CONFIG_PATH: str = os.environ.get(
    "CHART_INDICATOR_CONFIG_PATH",
    "platform/data/chart_indicators.toml",
)

# Own clamps (MEM-01 extended to this route, independently of candles.py's values). The candles
# themselves come from `views.chart_series.candle_page`, so this route can never disagree with the
# chart.
_MAX_INDICATOR_VALUES_LIMIT = 500
_MAX_BAR_SECONDS = 86_400

# Unlike limit/bar_seconds, `entries` has no natural client-side bound -- the picker only
# ever sends its own configured list, but the query param is untrusted input like any
# other. Caps the per-request replay fan-out at something far beyond any real picker
# config (MEM-01 extended to this route's own request shape).
_MAX_INDICATOR_VALUES_ENTRIES = 50

router = APIRouter()


# ---------------------------------------------------------------------------------------------
# GET /api/indicators/catalog
# ---------------------------------------------------------------------------------------------


class IndicatorCatalogEntry(BaseModel):
    params: dict[str, Any]
    panel: str
    category: str


@router.get("/api/indicators/catalog")
def get_indicators_catalog() -> dict[str, IndicatorCatalogEntry]:
    return {
        name: IndicatorCatalogEntry(**entry)
        for name, entry in indicator_picker.merged_catalog().items()
    }


# ---------------------------------------------------------------------------------------------
# GET / PUT /api/coin/{instrument_id}/indicators
# ---------------------------------------------------------------------------------------------


class IndicatorConfigEntry(BaseModel):
    name: str
    params: dict[str, Any] = {}
    category: str


@router.get("/api/coin/{instrument_id}/indicators")
def get_coin_indicator_config(instrument_id: str) -> list[IndicatorConfigEntry]:
    """
    Return this instrument's persisted picker selection. No saved entry is a legitimate
    "nothing saved yet" state -- `[]`, not an error.
    """
    try:
        config = preferences.load_chart_indicators(Path(CHART_INDICATOR_CONFIG_PATH))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, KeyError, TypeError) as exc:
        # Fail loud (DATA-02), not a silent empty list -- the file is meant to be
        # human-editable, so a corrupt hand-edit is a real, diagnosable state.
        raise HTTPException(
            status_code=500, detail=f"chart_indicators.toml is corrupt: {exc}"
        ) from exc
    entries = config.get(instrument_id, [])
    return [
        IndicatorConfigEntry(name=e.name, params=e.params, category=e.category) for e in entries
    ]


@router.put("/api/coin/{instrument_id}/indicators")
async def put_coin_indicator_config(instrument_id: str, request: Request) -> dict[str, bool]:
    """
    Persist this instrument's current indicator selection (explicit Save/change, never
    auto-save-per-keystroke -- matches today's handler's docstring).

    Reads the raw JSON body itself (rather than a typed Pydantic body parameter) so a
    malformed entry -- missing `name`/`category`, or invalid JSON outright -- degrades to a
    real `400` with a message, never FastAPI's automatic `422`, matching the pre-existing
    handler's contract exactly (DATA-02: never a silent/opaque failure for a client-input
    problem).
    """
    path = Path(CHART_INDICATOR_CONFIG_PATH)
    try:
        payload = await request.json()
        entries = [
            preferences.IndicatorEntry(
                name=e["name"],
                params=e.get("params", {}),
                category=e["category"],
            )
            for e in payload
        ]
        config = preferences.load_chart_indicators(path)
        config[instrument_id] = entries
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        AttributeError,
        tomllib.TOMLDecodeError,
        UnicodeDecodeError,
    ) as exc:
        raise HTTPException(
            status_code=400, detail=f"invalid indicator config payload: {exc}"
        ) from exc
    unknown = [e.name for e in entries if e.name not in indicator_picker.merged_catalog()]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown indicator(s): {unknown}")
    try:
        preferences.save_chart_indicators(config, path)
    except OSError as exc:
        # Distinct from the client-input branch above (400): the payload was fine, the
        # write itself failed (e.g. the docker-mounted file isn't actually writable) --
        # a server-side condition, never a silent/opaque failure (DATA-02).
        raise HTTPException(
            status_code=500, detail=f"failed to write chart_indicators.toml: {exc}"
        ) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------------------------
# GET /api/coin/{instrument_id}/indicator-values
# ---------------------------------------------------------------------------------------------


class IndicatorValueRequestEntry(BaseModel):
    name: str
    params: dict[str, Any] = {}


class IndicatorValuesItem(BaseModel):
    t: int
    # Keyed by f"{indicator_id(name, params)}.{output_attr}" -- a gap-marker row (AD-F6) is
    # represented as an item with an empty `values` dict, the same "row present, no data"
    # shape candles.py/indicator_series.py use for their own gap markers.
    values: dict[str, float | None] = {}


class IndicatorValuesResponse(BaseModel):
    items: list[IndicatorValuesItem]
    has_more: bool
    # `indicator_picker.indicator_id(name, params)` -> message, for entries whose replay failed. The other
    # entries' values are still served; one bad/stale entry must not blank every pane.
    errors: dict[str, str] = {}
    venue: str
    market: str


def _parse_entries[E: IndicatorValueRequestEntry](entries: str, model: type[E]) -> list[E]:
    """
    Parse the `entries` query param (a JSON-encoded array of `{"name", "params"}` objects)
    into validated request entries. Raises `HTTPException(400)` for any malformed input --
    invalid JSON, a non-array body, or an entry missing `name` -- never a `500` for a
    client-input problem (DATA-02, same posture as the PUT config route above).
    """
    try:
        raw = json.loads(entries)
        parsed = [model(**e) for e in raw]
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid entries payload: {exc}") from exc
    if len(parsed) > _MAX_INDICATOR_VALUES_ENTRIES:
        raise HTTPException(
            status_code=400,
            detail=f"too many entries requested: {len(parsed)} > {_MAX_INDICATOR_VALUES_ENTRIES}",
        )
    return parsed


@router.get("/api/coin/{instrument_id}/indicator-values")
def get_indicator_values(
    instrument_id: str,
    before_ns: int,
    entries: str,
    limit: int = 120,
    bar_seconds: int = 60,
) -> IndicatorValuesResponse:
    """
    Bounded candle window (mirrors `candles.py`'s window/limit/bar_seconds contract), then
    per-requested-name `indicator_picker.replay_entry` dispatch, results keyed by
    `indicator_picker.indicator_id(name, params)` (AD-F2/AD-F3).

    Re-derives its own bounded window server-side rather than accepting client-supplied
    candles in the request body (Design Notes: DESIGN-01) -- always a bounded *historical*
    replay of `[start_ns, before_ns)`, mirroring `indicator_series.py`'s own purely-historical
    scroll-back model; the candlestick's live edge has its own sanctioned path (AD-F7) that
    this route does not duplicate.

    A custom indicator whose instrument never opted into raw-delta capture returns `None` for
    every point (pre-existing custom-indicator behavior, reused unchanged) -- not an
    error (I/O matrix: "Values route returns None per-point for an uncaptured custom
    indicator, not an error").
    """
    limit = max(1, min(limit, _MAX_INDICATOR_VALUES_LIMIT))
    bar_seconds = max(1, min(bar_seconds, _MAX_BAR_SECONDS))
    parsed_entries = _parse_entries(entries, IndicatorValueRequestEntry)

    try:
        rows, has_more, errors = chart_series.indicator_values_page(
            instrument_id,
            before_ns,
            limit,
            bar_seconds,
            parsed_entries,
            catalog_path=_candles.CATALOG_PATH,
            candles_dir=_candles.CANDLES_DB_DIR,
            recent_rows=buses.live_candle_bus.recent_rows,
        )
    except chart_series.ImpossibleCandle as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except chart_series.CandleReadError as exc:
        # A catalog read failure (missing/corrupt catalog dir, I/O error) is a server-side
        # condition, not a client-input problem -- 500, never a silent/opaque failure
        # (DATA-02).
        raise HTTPException(status_code=500, detail=f"failed to read catalog: {exc}") from exc

    if not rows:
        return IndicatorValuesResponse(
            items=[],
            has_more=False,
            venue=venue_of(instrument_id),
            market=market_kind(instrument_id),
        )
    return IndicatorValuesResponse(
        items=[IndicatorValuesItem(**row) for row in rows],
        has_more=has_more,
        errors=errors,
        venue=venue_of(instrument_id),
        market=market_kind(instrument_id),
    )
