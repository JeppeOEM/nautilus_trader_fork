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

The three catalog/config handlers are `ml_signals/dashboard.py`'s `_merged_indicator_catalog`,
`coin_indicator_config_handler`, `save_coin_indicator_config_handler` (~1985-2058) relocated
verbatim (AD-F1/AD-F2): same TOML file, same load/save calls, same error semantics, only the
web framework (aiohttp -> FastAPI) and response envelope changed. `dashboard.py` itself is left
fully untouched -- it is slated for retirement in a later story, so this module does not import
from it; the handful of tiny wire-format helpers below (`_indicator_id`, spec parsing) are
deliberately re-declared here rather than imported, same as `routes/candles.py`/
`routes/indicator_series.py` each keep their own copies of shared window-math constants instead
of importing one another's.

The indicator-values route reuses `chart_indicators.replay_indicator` (native) /
`custom_indicators.replay_indicator` (custom, via `ReplayWindow`) unchanged (AD-F2/SSOT-01) --
no new indicator math lives here, only bounded-window construction (mirroring `candles.py`'s
`before_ns`/`limit`/`bar_seconds` contract) and dispatch/response shaping.

`CATALOG_PATH` comes from `data_api.settings` (a leaf module, avoiding an `app.py` import cycle).
"""

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from pydantic import BaseModel

from data_api.routes import paging
from data_api.settings import CATALOG_PATH
from ml_signals import catalog_stats as _catalog_stats
from ml_signals import chart_indicator_config
from ml_signals import chart_indicators
from ml_signals import custom_indicators
from ml_signals.candles import candle_dicts_from_snapshots
from ml_signals.venue import venue_of


# Default mirrors dashboard.py:85-88 exactly (same env var name, same default path).
CHART_INDICATOR_CONFIG_PATH: str = os.environ.get(
    "CHART_INDICATOR_CONFIG_PATH",
    "troll/ml_signals/chart_indicators.toml",
)

# Same clamp/window/span constants as candles.py/indicator_series.py -- kept as this module's
# own copies rather than importing either route module's (MEM-01 extended to this route,
# independently of whatever those modules' own values happen to be).
_MAX_INDICATOR_VALUES_LIMIT = 500
_MAX_BAR_SECONDS = 86_400
_QUERY_WINDOW_MULTIPLIER = 3
_MAX_QUERY_SPAN_SECONDS = 7 * 86_400

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


def _merged_indicator_catalog() -> dict[str, IndicatorCatalogEntry]:
    """
    Native + custom catalogs, each entry tagged with which one it came from.

    Tagging happens here, not inside either catalog module -- chart_indicators.py and
    custom_indicators.py stay unaware of each other (DESIGN-02); only this call site
    knows both exist. A name registered in both catalogs is a real bug (whichever one
    the picker lists would silently disagree with the native-first dispatch order in
    the values route below) -- raise immediately rather than let the two catalogs silently
    diverge (DATA-02: no mysteries).
    """
    collisions = set(chart_indicators.INDICATOR_CATALOG) & set(
        custom_indicators.CUSTOM_INDICATOR_CATALOG
    )
    if collisions:
        raise ValueError(f"Indicator name(s) registered in both catalogs: {sorted(collisions)}")
    merged: dict[str, IndicatorCatalogEntry] = {}
    for name, entry in chart_indicators.catalog_json().items():
        merged[name] = IndicatorCatalogEntry(**entry, category="native")
    for name, entry in custom_indicators.catalog_json().items():
        merged[name] = IndicatorCatalogEntry(**entry, category="custom")
    return merged


@router.get("/api/indicators/catalog")
def get_indicators_catalog() -> dict[str, IndicatorCatalogEntry]:
    return _merged_indicator_catalog()


# ---------------------------------------------------------------------------------------------
# GET / PUT /api/coin/{instrument_id}/indicators
# ---------------------------------------------------------------------------------------------


class IndicatorConfigEntry(BaseModel):
    name: str
    params: dict[str, Any] = {}
    category: str


@router.get("/api/coin/{instrument_id}/indicators")
def get_coin_indicator_config(instrument_id: str) -> list[IndicatorConfigEntry]:
    """Return this instrument's persisted picker selection. No saved entry is a legitimate
    "nothing saved yet" state -- `[]`, not an error."""
    try:
        config = chart_indicator_config.load_config(Path(CHART_INDICATOR_CONFIG_PATH))
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
    """Persist this instrument's current indicator selection (explicit Save/change, never
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
            chart_indicator_config.IndicatorEntry(
                name=e["name"],
                params=e.get("params", {}),
                category=e["category"],
            )
            for e in payload
        ]
        config = chart_indicator_config.load_config(path)
        config[instrument_id] = entries
    except (
        json.JSONDecodeError, KeyError, TypeError, AttributeError, tomllib.TOMLDecodeError,
        UnicodeDecodeError,
    ) as exc:
        raise HTTPException(
            status_code=400, detail=f"invalid indicator config payload: {exc}"
        ) from exc
    unknown = [e.name for e in entries if e.name not in _merged_indicator_catalog()]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown indicator(s): {unknown}")
    try:
        chart_indicator_config.save_config(config, path)
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
    # Keyed by f"{_indicator_id(name, params)}.{output_attr}" -- a gap-marker row (AD-F6) is
    # represented as an item with an empty `values` dict, the same "row present, no data"
    # shape candles.py/indicator_series.py use for their own gap markers.
    values: dict[str, float | None] = {}


class IndicatorValuesResponse(BaseModel):
    items: list[IndicatorValuesItem]
    has_more: bool
    # `_indicator_id(name, params)` -> message, for entries whose replay failed. The other
    # entries' values are still served; one bad/stale entry must not blank every pane.
    errors: dict[str, str] = {}
    venue: str


def _indicator_id(name: str, params: dict[str, Any]) -> str:
    """Mirrors `dashboard.py`'s own `_indicator_id(name, params)` scheme exactly (same
    format, re-declared here rather than imported -- see module docstring) -- this is the
    registry key `LightweightChart.tsx`'s pane `Map` already expects (AD-F4)."""
    if not params:
        return name
    return name + "_" + ",".join(f"{k}={v}" for k, v in sorted(params.items()))


def _window_start_ns(before_ns: int, limit: int, bar_seconds: int) -> int:
    span_seconds = min(limit * bar_seconds * _QUERY_WINDOW_MULTIPLIER, _MAX_QUERY_SPAN_SECONDS)
    return before_ns - span_seconds * 1_000_000_000


def _parse_entries(
    entries: str, model: type[IndicatorValueRequestEntry] = IndicatorValueRequestEntry
) -> list[IndicatorValueRequestEntry]:
    """Parse the `entries` query param (a JSON-encoded array of `{"name", "params"}` objects)
    into validated request entries. Raises `HTTPException(400)` for any malformed input --
    invalid JSON, a non-array body, or an entry missing `name` -- never a `500` for a
    client-input problem (DATA-02, same posture as the PUT config route above)."""
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


def _replay_entry(
    candles: list[dict], entry: IndicatorValueRequestEntry, window: custom_indicators.ReplayWindow
) -> dict[str, list[float | None]]:
    """Dispatch one requested indicator: native catalog first, then custom (via
    `ReplayWindow`) -- same lookup order `dashboard.py`'s retired `_indicators_json` used, so
    a name registered in exactly one catalog behaves identically here.

    Checks for a name registered in both catalogs before dispatching, same guard
    `_merged_indicator_catalog` applies for the picker's own listing -- without it, this
    route could silently serve the native catalog's replay for a colliding name while the
    catalog route fails loud for the exact same name (DATA-02: no mysteries)."""
    in_native = entry.name in chart_indicators.INDICATOR_CATALOG
    in_custom = entry.name in custom_indicators.CUSTOM_INDICATOR_CATALOG
    if in_native and in_custom:
        raise ValueError(f"Indicator name registered in both catalogs: {entry.name!r}")
    if in_native:
        return chart_indicators.replay_indicator(candles, entry.name, entry.params)
    if in_custom:
        return custom_indicators.replay_indicator(candles, entry.name, entry.params, window)
    raise ValueError(f"Unknown indicator: {entry.name}")


def _values_by_time(
    candles: list[dict],
    entries: list[IndicatorValueRequestEntry],
    window: custom_indicators.ReplayWindow,
) -> tuple[dict[int, dict[str, float | None]], dict[str, str]]:
    """Replay every requested entry over the same bounded candle window and merge into one
    `t -> {series_key: value}` mapping -- every entry shares the identical candle list, so
    their outputs are already aligned 1:1 by index. An entry that fails is reported in the
    second (`indicator_id -> message`) result and skipped, never failing the others."""
    by_time: dict[int, dict[str, float | None]] = {c["t"]: {} for c in candles}
    errors: dict[str, str] = {}
    for entry in entries:
        indicator_id = _indicator_id(entry.name, entry.params)
        try:
            outputs = _replay_entry(candles, entry, window)
        except Exception as exc:
            # Broad by design (DATA-02): params are untrusted and every current and future
            # indicator's replay may raise something different (e.g. period=0).
            errors[indicator_id] = str(exc)
            continue
        for attr, values in outputs.items():
            key = f"{indicator_id}.{attr}"
            for candle, value in zip(candles, values, strict=True):
                by_time[candle["t"]][key] = value
    return by_time, errors


def _insert_gap_markers(
    items: list[IndicatorValuesItem], bar_seconds: int
) -> list[IndicatorValuesItem]:
    """Same bar-boundary-spacing gap-marker rule as `candles.py`/`indicator_series.py`'s own
    `_insert_gap_markers` (AD-F6) -- an empty-`values` row inserted wherever two consecutive
    kept points are more than one `bar_seconds` interval apart."""
    bar_ms = bar_seconds * 1000
    out: list[IndicatorValuesItem] = []
    for i, item in enumerate(items):
        if i > 0 and item.t - items[i - 1].t > bar_ms:
            out.append(IndicatorValuesItem(t=items[i - 1].t + bar_ms))
        out.append(item)
    return out


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
    per-requested-name `replay_indicator` dispatch, results keyed by `_indicator_id(name,
    params)` (AD-F2/AD-F3).

    Re-derives its own bounded window server-side rather than accepting client-supplied
    candles in the request body (Design Notes: DESIGN-01) -- always a bounded *historical*
    replay of `[start_ns, before_ns)`, mirroring `indicator_series.py`'s own purely-historical
    scroll-back model; the candlestick's live edge has its own sanctioned path (AD-F7) that
    this route does not duplicate.

    A custom indicator whose instrument never opted into raw-delta capture returns `None` for
    every point (pre-existing `custom_indicators.py` behavior, reused unchanged) -- not an
    error (I/O matrix: "Values route returns None per-point for an uncaptured custom
    indicator, not an error").
    """
    limit = max(1, min(limit, _MAX_INDICATOR_VALUES_LIMIT))
    bar_seconds = max(1, min(bar_seconds, _MAX_BAR_SECONDS))
    before_ms = before_ns // 1_000_000
    parsed_entries = _parse_entries(entries)

    def fetch(start_ns: int, end_ns: int) -> list[dict]:
        snapshots = _catalog_stats.query_second_snapshots(CATALOG_PATH, instrument_id, start_ns, end_ns)
        return [c for c in candle_dicts_from_snapshots(snapshots, bar_seconds) if c["t"] < before_ms]

    try:
        ranges = _catalog_stats.data_file_ranges(CATALOG_PATH, instrument_id)
        span_ns = before_ns - _window_start_ns(before_ns, limit, bar_seconds)
        kept = paging.fetch_page(fetch, ranges, before_ns, span_ns)[-limit:]
    except Exception as exc:
        # A catalog read failure (missing/corrupt catalog dir, I/O error) is a server-side
        # condition, not a client-input problem -- 500, never a silent/opaque failure
        # (DATA-02).
        raise HTTPException(status_code=500, detail=f"failed to read catalog: {exc}") from exc

    if not kept:
        return IndicatorValuesResponse(items=[], has_more=False, venue=venue_of(instrument_id))

    start_ms = kept[0]["t"]
    end_ms = kept[-1]["t"] + bar_seconds * 1000
    window = custom_indicators.ReplayWindow(
        instrument_id=instrument_id, bar_seconds=bar_seconds, start_ms=start_ms, end_ms=end_ms,
    )
    by_time, errors = _values_by_time(kept, parsed_entries, window)
    items = [IndicatorValuesItem(t=t, values=values) for t, values in sorted(by_time.items())]
    return IndicatorValuesResponse(
        items=_insert_gap_markers(items, bar_seconds),
        has_more=paging.has_older_data(ranges, items[0].t * 1_000_000),
        errors=errors,
        venue=venue_of(instrument_id),
    )
