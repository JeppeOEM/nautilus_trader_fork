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
`GET /api/rankings` -- verbatim passthrough of `ranking_engine`'s cached `rankings:live`
message (AD-F2: never recompute rank/volatility), with exactly one sanctioned reshape:
`ranks` -> `items` (epics AC1's literal `{"items": [...], "updated_at": ...}` shape).
Every other field (`mode`, `stale_instrument_ids`, and every field inside each ranking
entry) passes through byte-for-byte from the cached Redis payload.

The Technicals tab's routes are format + transport only (Story 24.2): the column selection is
`views.preferences` (`screener_columns.toml`), each coin's values
`views.ranking_columns.technicals_values` (the chart's own indicator dispatch over the chart's own
candles). This module validates the untrusted request, passes its own `CATALOG_PATH`/
`CANDLES_DB_DIR` in, keeps the short TTL cache and maps failures (bad params -> 400, one coin's
failed read -> that coin's `errors` entry).

`PUT /api/rankings/mode` (Story 25.1a) is the one ranking-mode switch now that rankings are
web-only: it publishes the message `ranking_engine` reads on `ranking:control`, byte-identical to
what the retired TUI `m` key sent, and never touches the mode itself -- the page shows the new
mode only once `rankings:live` carries it (publish-and-wait, last write wins).
"""

import json
import logging
import time
import tomllib
from pathlib import Path
from typing import Literal

import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from observability import error_ledger
from pydantic import BaseModel
from pydantic import ConfigDict
from views import indicator_picker
from views import preferences
from views import ranking_columns

from data_api import buses
from data_api.routes import indicators as _indicators
from data_api.settings import CANDLES_DB_DIR
from data_api.settings import CATALOG_PATH
from data_api.settings import REDIS_URL
from data_api.settings import SCREENER_COLUMNS_CONFIG_PATH


router = APIRouter()
logger = logging.getLogger(__name__)


class RankingsResponse(BaseModel):
    items: list[dict]
    updated_at: int
    mode: str
    stale_instrument_ids: list[str]


@router.get("/api/rankings")
def get_rankings() -> RankingsResponse:
    """
    503 before the bus has cached its first message -- an honest transient state, not a
    fabricated empty snapshot (I/O matrix). Referencing `buses.bus` via the module
    (not importing the `bus` name directly) so tests can `monkeypatch.setattr(buses,
    "bus", RankingsBus())` to exercise this route against an isolated cache.
    """
    latest = buses.bus.latest
    if latest is None:
        raise HTTPException(status_code=503, detail="Rankings not yet available")
    return RankingsResponse(
        items=latest["ranks"],
        updated_at=latest["updated_at"],
        mode=latest["mode"],
        stale_instrument_ids=latest.get("stale_instrument_ids", []),
    )


# ---------------------------------------------------------------------------------------------
# Story 25.1a: ranking-mode switch
# ---------------------------------------------------------------------------------------------

# `ranking_engine`'s control channel (a frozen published name, epic 25). Module-level so tests
# can monkeypatch a unique channel: the local stack's live `ranking_engine` listens on the real
# one, and a test publish there would flip the live mode.
RANKING_CONTROL_CHANNEL = "ranking:control"

# A blackholed Redis must fail the request promptly (503), not hang it -- and with it the page's
# mode buttons, which stay disabled while a switch is in flight.
_REDIS_TIMEOUT_SECONDS = 2.0


class RankingModeRequest(BaseModel):
    """The whole request: one of the two modes `ranking_engine` accepts, nothing else."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["volume", "volatility"]


class RankingModeResponse(BaseModel):
    mode: Literal["volume", "volatility"]


@router.put(
    "/api/rankings/mode",
    status_code=202,
    responses={503: {"description": "No subscriber on `ranking:control`, or Redis failed"}},
)
async def put_ranking_mode(body: RankingModeRequest) -> RankingModeResponse:
    """
    Ask `ranking_engine` to switch the global ranking mode. 202, not 200: the switch is
    applied asynchronously, and `rankings:live` confirms it.

    The payload is `json.dumps({"mode": mode})`, the exact bytes the retired TUI toggle
    published (replay test: `tests/test_rankings_mode.py`). A short-lived connection per call,
    as the TUI had: a mode switch is a rare human action, not worth a held publisher.

    503 when nobody received it (`publish()` counts 0 subscribers, i.e. `ranking_engine` is
    down) or Redis fails or stalls past `_REDIS_TIMEOUT_SECONDS`. The TUI ignored both, so a
    switch sent while the engine was down vanished silently; here the caller sees it (DATA-07).

    Known limit: `publish()` counts every subscriber of the channel, so a stray `redis-cli
    SUBSCRIBE ranking:control` makes a 202 while `ranking_engine` is down. The page still never
    shows the new mode until `rankings:live` carries it. Upgrade path: an acknowledged command
    (the engine echoes a request id on `rankings:live`) once `RankingBoard.switch_mode` exists
    (Story 25.2).
    """
    payload = json.dumps({"mode": body.mode})
    try:
        async with aioredis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
            socket_timeout=_REDIS_TIMEOUT_SECONDS,
        ) as client:
            receivers = await client.publish(RANKING_CONTROL_CHANNEL, payload)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"failed to publish to {RANKING_CONTROL_CHANNEL}: {exc!r}"
        ) from exc
    if receivers == 0:
        raise HTTPException(
            status_code=503,
            detail=f"no ranking_engine subscribed to {RANKING_CONTROL_CHANNEL}",
        )
    return RankingModeResponse(mode=body.mode)


# ---------------------------------------------------------------------------------------------
# Story 17.5: Technicals tab -- screener-wide column selection + bulk per-instrument values
# ---------------------------------------------------------------------------------------------

# The column timeframes the UI offers; anything else is a 400, not a silent fallback.
_TECHNICALS_BAR_SIZES = (60, 300, 900, 3600, 14400, 86400)
# Bulk values are identical for every viewer of the same entries/ranked set -- a short TTL cache
# keeps a second tab or a slow poll from stacking another full per-coin catalog pass.
_TECHNICALS_CACHE_TTL_S = 90.0
_technicals_cache: dict[str, tuple[float, dict[str, dict[str, float | None]]]] = {}


class TechnicalsColumn(_indicators.PickerEntry):
    """A per-coin picker entry plus the bar size it is computed on."""

    bar_seconds: int = preferences.DEFAULT_BAR_SECONDS


class TechnicalsRequestEntry(_indicators.IndicatorValueRequestEntry):
    bar_seconds: int = preferences.DEFAULT_BAR_SECONDS


@router.get("/api/rankings/technicals-columns")
def get_technicals_columns() -> list[TechnicalsColumn]:
    try:
        entries = preferences.load_screener_columns(Path(SCREENER_COLUMNS_CONFIG_PATH))
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=500, detail=f"screener_columns.toml is corrupt: {exc}"
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to read screener_columns.toml: {exc}"
        ) from exc
    return [TechnicalsColumn(**vars(e)) for e in entries]


def _require_known_indicators(names: list[str]) -> None:
    """A saved name the catalogs don't know would make every later values poll fail."""
    known = indicator_picker.merged_catalog()
    unknown = [n for n in names if n not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown indicator(s): {unknown}")


@router.put("/api/rankings/technicals-columns")
async def put_technicals_columns(request: Request) -> dict[str, bool]:
    """Persist the FULL screener-wide column list (add/remove/reorder/param change)."""
    try:
        payload = await request.json()
        entries = [
            preferences.ColumnEntry(
                name=e["name"],
                params=e.get("params", {}),
                category=e["category"],
                bar_seconds=e.get("bar_seconds", preferences.DEFAULT_BAR_SECONDS),
            )
            for e in payload
        ]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid columns payload: {exc}") from exc
    if not all(isinstance(e.params, dict) for e in entries):
        raise HTTPException(
            status_code=400, detail="invalid columns payload: params must be an object"
        )
    if len(entries) > _indicators._MAX_INDICATOR_VALUES_ENTRIES:
        raise HTTPException(
            status_code=400,
            detail=f"too many columns: {len(entries)} > {_indicators._MAX_INDICATOR_VALUES_ENTRIES}",
        )
    if any(e.bar_seconds not in _TECHNICALS_BAR_SIZES for e in entries):
        raise HTTPException(
            status_code=400, detail=f"bar_seconds must be one of {_TECHNICALS_BAR_SIZES}"
        )
    _require_known_indicators([e.name for e in entries])
    try:
        preferences.save_screener_columns(entries, Path(SCREENER_COLUMNS_CONFIG_PATH))
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to write screener_columns.toml: {exc}"
        ) from exc
    return {"ok": True}


class TechnicalsValuesResponse(BaseModel):
    # instrument_id -> "{entry_index}.{output_attr}" -> latest value (None: no data yet).
    # Keyed by the request entry's position, not the chart's `indicator_id` scheme, so the
    # client never has to reproduce that server-side param-formatting to find its column.
    values: dict[str, dict[str, float | None]]
    # instrument_id -> why its values are missing. A coin listed here has NO data because its
    # computation failed -- distinct from a coin with `None` values (genuinely no data yet).
    errors: dict[str, str] = {}


@router.get("/api/rankings/technicals-values")
def get_technicals_values(entries: str) -> TechnicalsValuesResponse:
    """
    Latest value of every requested indicator for every currently-ranked instrument.

    Computes nothing itself (AD-F2): `views.ranking_columns.technicals_values` dispatches to the
    chart's own indicator replay, so a column always equals what that coin's chart shows. Known limit: sequential per-coin catalog
    reads behind a 90s TTL cache (> the client's 60s poll, or it never hits) (one live key) -- no single-flight, add one if concurrent
    viewers ever load the box.
    """
    parsed = _indicators._parse_entries(entries, TechnicalsRequestEntry)
    _indicators._check_sources(parsed)
    if any(e.bar_seconds not in _TECHNICALS_BAR_SIZES for e in parsed):
        raise HTTPException(
            status_code=400, detail=f"bar_seconds must be one of {_TECHNICALS_BAR_SIZES}"
        )
    _require_known_indicators([e.name for e in parsed])
    latest = buses.bus.latest
    if latest is None:
        raise HTTPException(status_code=503, detail="Rankings not yet available")
    iids = [row["instrument_id"] for row in latest["ranks"]]
    # Order-independent: rank order shifts constantly and must not defeat the cache.
    cache_key = json.dumps([entries, sorted(iids)])
    cached = _technicals_cache.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < _TECHNICALS_CACHE_TTL_S:
        return TechnicalsValuesResponse(values=cached[1])
    now_ns = time.time_ns()
    result: dict[str, dict[str, float | None]] = {}
    errors: dict[str, str] = {}
    for iid in iids:
        try:
            result[iid] = ranking_columns.technicals_values(
                iid, parsed, now_ns, catalog_path=CATALOG_PATH, candles_dir=CANDLES_DB_DIR
            )
        except ValueError as exc:  # bad indicator params -- the same for every coin, client input
            raise HTTPException(status_code=400, detail=f"invalid indicator entry: {exc}") from exc
        except Exception as exc:
            error_ledger.record("technicals.values", f"technicals values failed for {iid}", exc)
            errors[iid] = repr(exc)
    if not errors:  # never cache a failure: the next poll must retry it
        _technicals_cache.clear()  # one live key at a time; bounded (MEM-01)
        _technicals_cache[cache_key] = (time.monotonic(), result)
    return TechnicalsValuesResponse(values=result, errors=errors)
