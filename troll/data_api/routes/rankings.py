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
"""

import json
import logging
import os
import time
import tomllib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from data_api import redis_bus
from data_api.routes import indicators as _indicators
from ml_signals import catalog_stats as _catalog_stats
from ml_signals import custom_indicators
from ml_signals import screener_columns_config
from ml_signals.candles import candle_dicts_from_snapshots


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
    fabricated empty snapshot (I/O matrix). Referencing `redis_bus.bus` via the module
    (not importing the `bus` name directly) so tests can `monkeypatch.setattr(redis_bus,
    "bus", RankingsBus())` to exercise this route against an isolated cache.
    """
    latest = redis_bus.bus.latest
    if latest is None:
        raise HTTPException(status_code=503, detail="Rankings not yet available")
    return RankingsResponse(
        items=latest["ranks"],
        updated_at=latest["updated_at"],
        mode=latest["mode"],
        stale_instrument_ids=latest.get("stale_instrument_ids", []),
    )


# ---------------------------------------------------------------------------------------------
# Story 17.5: Technicals tab -- screener-wide column selection + bulk per-instrument values
# ---------------------------------------------------------------------------------------------

CATALOG_PATH: str = os.environ.get("CATALOG_PATH", "troll/dydx_collector/catalog")
SCREENER_COLUMNS_CONFIG_PATH: str = os.environ.get(
    "SCREENER_COLUMNS_CONFIG_PATH", "troll/ml_signals/screener_columns.toml"
)

# Recent-window replay per coin: enough 1m bars for the slowest common indicator warm-up.
_TECHNICALS_BARS = 120
_TECHNICALS_BAR_SECONDS = 60
# Catalog writes lag by a flush interval, so the newest candle is normally a minute or two old;
# older than this the coin's data has genuinely stopped and its value must not read as live.
_TECHNICALS_MAX_CANDLE_AGE_BARS = 5
# Bulk values are identical for every viewer of the same entries/ranked set -- a short TTL cache
# keeps a second tab or a slow poll from stacking another full per-coin catalog pass.
_TECHNICALS_CACHE_TTL_S = 30.0
_technicals_cache: dict[str, tuple[float, dict[str, dict[str, float | None]]]] = {}


class TechnicalsColumn(_indicators.IndicatorConfigEntry):
    """Same wire shape as a per-coin picker entry -- only its persistence scope differs."""


@router.get("/api/rankings/technicals-columns")
def get_technicals_columns() -> list[TechnicalsColumn]:
    try:
        entries = screener_columns_config.load_config(Path(SCREENER_COLUMNS_CONFIG_PATH))
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=500, detail=f"screener_columns.toml is corrupt: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to read screener_columns.toml: {exc}") from exc
    return [TechnicalsColumn(name=e.name, params=e.params, category=e.category) for e in entries]


def _require_known_indicators(names: list[str]) -> None:
    """A saved name the catalogs don't know would make every later values poll fail."""
    known = _indicators._merged_indicator_catalog()
    unknown = [n for n in names if n not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown indicator(s): {unknown}")


@router.put("/api/rankings/technicals-columns")
async def put_technicals_columns(request: Request) -> dict[str, bool]:
    """Persist the FULL screener-wide column list (add/remove/reorder/param change)."""
    try:
        payload = await request.json()
        entries = [
            screener_columns_config.IndicatorEntry(
                name=e["name"], params=e.get("params", {}), category=e["category"]
            )
            for e in payload
        ]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid columns payload: {exc}") from exc
    _require_known_indicators([e.name for e in entries])
    try:
        screener_columns_config.save_config(entries, Path(SCREENER_COLUMNS_CONFIG_PATH))
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


def _latest_values(
    instrument_id: str, entries: list[_indicators.IndicatorValueRequestEntry], now_ns: int
) -> dict[str, float | None]:
    """Each requested indicator's latest value for one instrument, via the chart's own
    `replay_indicator` dispatch over a short recent 1m window (no indicator math here)."""
    # Exactly the window needed (not the chart route's 3x scroll-back margin): 120 bars of 1m
    # is 2h of 1s snapshots per coin, not 6h.
    start_ns = now_ns - (_TECHNICALS_BARS + 5) * _TECHNICALS_BAR_SECONDS * 1_000_000_000
    snapshots = _catalog_stats.query_second_snapshots(CATALOG_PATH, instrument_id, start_ns, now_ns)
    candles = candle_dicts_from_snapshots(snapshots, _TECHNICALS_BAR_SECONDS)[-_TECHNICALS_BARS:]
    max_age_ms = _TECHNICALS_MAX_CANDLE_AGE_BARS * _TECHNICALS_BAR_SECONDS * 1000
    if not candles or now_ns // 1_000_000 - candles[-1]["t"] > max_age_ms:
        return {}  # no data / stopped: an honest gap, never a stale value shown as current (DATA-01)
    window = custom_indicators.ReplayWindow(
        instrument_id=instrument_id,
        bar_seconds=_TECHNICALS_BAR_SECONDS,
        start_ms=candles[0]["t"],
        end_ms=candles[-1]["t"] + _TECHNICALS_BAR_SECONDS * 1000,
    )
    latest = _indicators._values_by_time(candles, entries, window)[candles[-1]["t"]]
    keyed: dict[str, float | None] = {}
    for index, entry in enumerate(entries):
        prefix = _indicators._indicator_id(entry.name, entry.params) + "."
        keyed.update(
            {f"{index}.{k.removeprefix(prefix)}": v for k, v in latest.items() if k.startswith(prefix)}
        )
    return keyed


@router.get("/api/rankings/technicals-values")
def get_technicals_values(entries: str) -> TechnicalsValuesResponse:
    """
    Latest value of every requested indicator for every currently-ranked instrument.

    Computes nothing itself (AD-F2): it dispatches to the chart's own `replay_indicator`, so a
    column always equals what that coin's chart shows. ponytail: sequential per-coin catalog
    reads, cached only by the client's poll interval -- add a server-side cache if 50 coins x
    a 60s poll ever loads the box.
    """
    parsed = _indicators._parse_entries(entries)
    _require_known_indicators([e.name for e in parsed])
    latest = redis_bus.bus.latest
    if latest is None:
        raise HTTPException(status_code=503, detail="Rankings not yet available")
    iids = [row["instrument_id"] for row in latest["ranks"]]
    cache_key = json.dumps([entries, iids])
    cached = _technicals_cache.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < _TECHNICALS_CACHE_TTL_S:
        return TechnicalsValuesResponse(values=cached[1])
    now_ns = time.time_ns()
    result: dict[str, dict[str, float | None]] = {}
    for iid in iids:
        try:
            result[iid] = _latest_values(iid, parsed, now_ns)
        except ValueError as exc:  # bad indicator params -- the same for every coin, client input
            raise HTTPException(status_code=400, detail=f"invalid indicator entry: {exc}") from exc
        except Exception:  # noqa: BLE001 -- one coin's catalog read must not blank every coin
            logger.exception("technicals values failed for %s", iid)
            result[iid] = {}
    _technicals_cache.clear()  # one live key at a time; bounded (MEM-01)
    _technicals_cache[cache_key] = (time.monotonic(), result)
    return TechnicalsValuesResponse(values=result)
