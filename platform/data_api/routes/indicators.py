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
import logging
import threading
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from typing import Literal

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from kernel.venues import market_kind
from kernel.venues import venue_of
from observability import error_ledger
from pydantic import BaseModel
from views import chart_series
from views import indicator_picker
from views import preferences

from data_api import buses
from data_api.routes import candles as _candles
from data_api.settings import CHART_INDICATOR_CONFIG_PATH


# Own clamps (MEM-01 extended to this route, independently of candles.py's values). The candles
# themselves come from `views.chart_series.candle_page`, so this route can never disagree with the
# chart.
_MAX_INDICATOR_VALUES_LIMIT = 500
# 1W, the candles route's own bound: a 1W pane is computed on 1W buckets (Monday-anchored, like its
# candles), never silently on 1D ones (Story 31.3). The candles come from `candle_page` (store, then
# 7-day-bounded archive reads), and the custom indicators' raw-second/delta replay window is capped
# at `chart_series.MAX_QUERY_SPAN_SECONDS` back from the page's end (`indicator_values_page`): bars
# before that cap carry None for a custom indicator. Known limit: a 1W page therefore has one custom
# value (its last bar), a 1D page seven; upgrade path: a stored per-bar aggregate of the inputs.
_MAX_BAR_SECONDS = 604_800

# Unlike limit/bar_seconds, `entries` has no natural client-side bound -- the picker only
# ever sends its own configured list, but the query param is untrusted input like any
# other. Caps the per-request replay fan-out at something far beyond any real picker
# config (MEM-01 extended to this route's own request shape).
_MAX_INDICATOR_VALUES_ENTRIES = 50

# Serializes every read-modify-write of the preference files that more than one route writes
# (`chart_indicators.toml`: this PUT and the layout route's seed/reset), so two requests in the one
# `data_api` process never lose each other's update. Known limit: in-process only, not across
# processes. Upgrade path: a file lock (`fcntl.flock`) on a sibling lock file.
PREFERENCES_LOCK = threading.Lock()
_log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------------------------
# GET /api/indicators/catalog
# ---------------------------------------------------------------------------------------------


class IndicatorCatalogEntry(BaseModel):
    params: dict[str, Any]
    panel: str
    category: str
    # Enum param -> its allowed member names (the picker's dropdown); `{}` for custom entries.
    choices: dict[str, list[str]] = {}
    # True only for a native indicator fed exactly the close: the legend's Source select (32.3).
    source_selectable: bool = False
    # Output -> `price`/`size`/`size_mean`/`count`/`ratio` (`indicator_picker.INDICATOR_UNITS`, Story 33.6): the
    # legend formats that output at the instrument's precision; `{}` (every native entry) = generic.
    units: dict[str, str] = {}
    # Every output name the entry's replay can return (Story 33.8): the alert form's output select.
    outputs: list[str]
    # Output -> how it is drawn (`indicator_picker.PlotStyle`, Story 33.11); an absent output is a
    # line: `steps` (pivot levels), `points` (Parabolic SAR), `swing` (ZigZag's joined pivots).
    plot: dict[str, Literal["line", "steps", "points", "swing"]] = {}
    # A short legend note after the entry's title (Story 33.11: ZigZag's "repaints last leg").
    note: str | None = None


@router.get("/api/indicators/catalog")
def get_indicators_catalog() -> dict[str, IndicatorCatalogEntry]:
    return {
        name: IndicatorCatalogEntry(**entry)
        for name, entry in indicator_picker.merged_catalog().items()
    }


# ---------------------------------------------------------------------------------------------
# GET / PUT /api/coin/{instrument_id}/indicators
# ---------------------------------------------------------------------------------------------


class PickerEntry(BaseModel):
    """The three keys every persisted selection has; the screener's columns stop here."""

    name: str
    params: dict[str, Any] = {}
    category: str


class IndicatorConfigEntry(PickerEntry):
    # Price a close-fed indicator reads; part of its series id unless `close`.
    source: str = "close"
    # The legend's eye: a hidden indicator stays configured but is not drawn.
    hidden: bool = False
    # Output label -> {color, line_width, line_style, ...}; `{}` = the pane palette default.
    style: dict[str, dict[str, Any]] = {}
    # The copy number of an indicator added more than once; 1 for the first (and only) copy.
    instance: int = 1


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
        IndicatorConfigEntry(
            name=e.name,
            params=e.params,
            category=e.category,
            source=_servable_source(instrument_id, e),
            hidden=e.hidden,
            style=e.style,
            instance=e.instance,
        )
        for e in entries
    ]


def _servable_source(instrument_id: str, entry: preferences.IndicatorEntry) -> str:
    """
    Return the entry's source, or `close` with one warning when its indicator cannot take it (a
    hand-edit, or an indicator that stopped being close-fed). Served as is, it would make the
    client's values request a 422 and blank every pane of the coin, and every later save a 422
    too -- the same "wrong value -> default + warning" rule the loader applies to a wrong type.
    """
    try:
        indicator_picker.check_source(entry.name, entry.source)
    except ValueError as exc:
        _log.warning(
            "chart_indicators.toml: %s entry %r: %s; serving source 'close'",
            instrument_id,
            entry.name,
            exc,
        )
        return indicator_picker.DEFAULT_SOURCE
    return entry.source


def _parse_config_entry(e: dict[str, Any]) -> preferences.IndicatorEntry:
    """
    One PUT body entry. A wrong type for `params`/`source`/`hidden`/`style`/`instance` is a
    `TypeError` (the route's 400); TOML cannot hold `null` and JSON cannot hold `NaN`, so style
    leaves must be strings, integers, booleans or finite floats (`preferences.is_valid_style`, the
    loader's rule too), and `instance` an integer >= 1 (`preferences.is_valid_instance`).
    """
    source, hidden, style = e.get("source", "close"), e.get("hidden", False), e.get("style", {})
    params = e.get("params", {})
    if not isinstance(source, str) or not isinstance(hidden, bool):
        raise TypeError("source must be a string and hidden a boolean")
    if not isinstance(params, dict):
        raise TypeError("params must be an object")
    if not preferences.is_valid_style(style):
        raise TypeError("style must be an object of per-output objects of finite scalar values")
    instance = e.get("instance", 1)
    if not preferences.is_valid_instance(instance):
        raise TypeError("instance must be an integer >= 1")
    return preferences.IndicatorEntry(
        name=e["name"],
        params=params,
        category=e["category"],
        source=source,
        hidden=hidden,
        style=style,
        instance=instance,
    )


def _check_sources(entries: Sequence[indicator_picker.IndicatorRequest]) -> None:
    """Raise a 422 naming `source` for any entry whose source its indicator cannot take."""
    for entry in entries:
        try:
            indicator_picker.check_source(entry.name, entry.source)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid source: {exc}") from exc


def check_entry_params(name: str, params: Any) -> None:
    """
    Raise a 400 naming the indicator and the param for params its replay would refuse
    (`indicator_picker.check_params`): saved, they would fail every later values request.
    """
    try:
        indicator_picker.check_params(name, params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid params for {name}: {exc}") from exc


def check_indicator_entries(entries: Sequence[preferences.IndicatorEntry]) -> None:
    """
    Apply the one validation every writer of an indicator list shares (the PUT here, the layout
    route's seed and reset): names must be in the merged catalog (400), params ones the indicator
    can be built with (400) and each source one its indicator can take (422).
    """
    unknown = [e.name for e in entries if e.name not in indicator_picker.merged_catalog()]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown indicator(s): {unknown}")
    for entry in entries:
        check_entry_params(entry.name, entry.params)
    _check_sources(entries)
    _check_distinct_copies(entries)


def _check_distinct_copies(entries: Sequence[preferences.IndicatorEntry]) -> None:
    """
    Raise a 422 for two entries with the same name, params, source and `instance`: on the chart they
    would be one legend row and one series key, so neither could be removed or restyled alone.
    """
    seen: set[tuple[str, int]] = set()
    for entry in entries:
        series_id = indicator_picker.indicator_id(entry.name, entry.params, entry.source)
        if (series_id, entry.instance) in seen:
            raise HTTPException(
                status_code=422,
                detail=f"duplicate indicator: {series_id} instance {entry.instance} is listed twice",
            )
        seen.add((series_id, entry.instance))


def validate_indicator_payload(payload: Any) -> list[preferences.IndicatorEntry]:
    """Parse and validate a raw JSON entry list (400 malformed or unknown name, 422 bad source)."""
    try:
        entries = [_parse_config_entry(e) for e in payload]
    except (KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=400, detail=f"invalid indicator config payload: {exc}"
        ) from exc
    check_indicator_entries(entries)
    return entries


def _store_entries(instrument_id: str, entries: list[preferences.IndicatorEntry]) -> None:
    path = Path(CHART_INDICATOR_CONFIG_PATH)
    with PREFERENCES_LOCK:
        try:
            config = preferences.load_chart_indicators(path)
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, KeyError, TypeError) as exc:
            # The stored file, not the payload, is at fault: a server-side condition, answered
            # like the GET (and the layout routes' `_file_errors`) -- never blamed on the client.
            raise HTTPException(
                status_code=500, detail=f"chart_indicators.toml is corrupt: {exc}"
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"failed to read chart_indicators.toml: {exc}"
            ) from exc
        config[instrument_id] = entries
        try:
            preferences.save_chart_indicators(config, path)
        except OSError as exc:
            # The payload was fine, the write itself failed (e.g. the docker-mounted file isn't
            # actually writable) -- a server-side condition, never a silent/opaque failure
            # (DATA-02).
            raise HTTPException(
                status_code=500, detail=f"failed to write chart_indicators.toml: {exc}"
            ) from exc


def json_array_body(component: str) -> dict[str, Any]:
    """
    Return the `openapi_extra` declaring a raw-read PUT body as a JSON array of `component` items:
    the route still reads `request.json()` itself (malformed -> 400, never FastAPI's 422), but the
    schema -- and the frontend codegen from it -- names the body's shape.
    """
    schema = {"type": "array", "items": {"$ref": f"#/components/schemas/{component}"}}
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": schema}}}}


@router.put(
    "/api/coin/{instrument_id}/indicators",
    openapi_extra=json_array_body("IndicatorConfigEntry"),
)
async def put_coin_indicator_config(instrument_id: str, request: Request) -> dict[str, bool]:
    """
    Persist this instrument's current indicator selection (explicit Save/change, never
    auto-save-per-keystroke -- matches today's handler's docstring). The body is a JSON array of
    `IndicatorConfigEntry` (declared through `openapi_extra`).

    Reads the raw JSON body itself (rather than a typed Pydantic body parameter) so a
    malformed entry -- missing `name`/`category`, or invalid JSON outright -- degrades to a
    real `400` with a message, never FastAPI's automatic `422`, matching the pre-existing
    handler's contract exactly (DATA-02: never a silent/opaque failure for a client-input
    problem). An unknown name or params the indicator refuses are 400 too, a source the
    indicator cannot take 422; nothing is written then. A corrupt or unreadable stored file is
    500.
    """
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=400, detail=f"invalid indicator config payload: {exc}"
        ) from exc
    entries = validate_indicator_payload(payload)
    # Blocking file I/O under a threading lock: off the event loop.
    await run_in_threadpool(_store_entries, instrument_id, entries)
    return {"ok": True}


# ---------------------------------------------------------------------------------------------
# GET /api/coin/{instrument_id}/indicator-values
# ---------------------------------------------------------------------------------------------


class IndicatorValueRequestEntry(BaseModel):
    name: str
    params: dict[str, Any] = {}
    source: str = "close"


class IndicatorValuesItem(BaseModel):
    t: int
    # Keyed by f"{indicator_id(name, params, source)}.{output_attr}" -- a gap-marker row (AD-F6) is
    # represented as an item with an empty `values` dict, the same "row present, no data"
    # shape candles.py uses for its own gap markers.
    values: dict[str, float | None] = {}


class IndicatorValuesResponse(BaseModel):
    items: list[IndicatorValuesItem]
    has_more: bool
    # `indicator_picker.indicator_id(name, params, source)` -> message, for entries whose replay
    # failed. The other entries' values are still served; one bad/stale entry must not blank every
    # pane.
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
    `indicator_picker.indicator_id(name, params, source)` (AD-F2/AD-F3).

    Re-derives its own bounded window server-side rather than accepting client-supplied
    candles in the request body (Design Notes: DESIGN-01) -- always a bounded *historical*
    replay of `[start_ns, before_ns)`, the purely-historical scroll-back model the deleted
    OFI/OBI series route (Story 33.4) had; the candlestick's live edge has its own sanctioned
    path (AD-F7) that this route does not duplicate.

    A custom indicator whose instrument never opted into raw-delta capture returns `None` for
    every point (pre-existing custom-indicator behavior, reused unchanged) -- not an
    error (I/O matrix: "Values route returns None per-point for an uncaptured custom
    indicator, not an error").
    """
    limit = max(1, min(limit, _MAX_INDICATOR_VALUES_LIMIT))
    bar_seconds = max(1, min(bar_seconds, _MAX_BAR_SECONDS))
    parsed_entries = _parse_entries(entries, IndicatorValueRequestEntry)
    _check_sources(parsed_entries)

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
            recent_liquidations=buses.live_candle_bus.recent_liquidations,
        )
    except chart_series.ImpossibleCandle as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except chart_series.CandleReadError as exc:
        # A catalog read failure (missing/corrupt catalog dir, I/O error) is a server-side
        # condition, not a client-input problem -- 500, never a silent/opaque failure
        # (DATA-02). Ledgered here, where the request fails: views wraps the cause unledgered
        # (a corrupt or repeatedly vanishing catalog file, `CatalogReadError`; DATA-07).
        error_ledger.record("data_api.indicator_values_catalog_read", str(exc), exc)
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
