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

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from kernel import catalog_files
from kernel.venues import venue_of
from ml_signals import candle_store
from ml_signals import custom_indicators
from ml_signals import screener_columns_config
from ml_signals.candles import candle_dicts_from_snapshots
from observability import error_ledger
from pydantic import BaseModel

from data_api import redis_bus
from data_api.routes import indicators as _indicators
from data_api.settings import CANDLES_DB_DIR
from data_api.settings import CATALOG_PATH


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

SCREENER_COLUMNS_CONFIG_PATH: str = os.environ.get(
    "SCREENER_COLUMNS_CONFIG_PATH", "platform/ml_signals/screener_columns.toml"
)

# Recent-window replay per coin: enough bars for the slowest common indicator warm-up. The candle
# store serves any bar size cheaply (an indexed read), so 250 covers SMA(200); the Parquet fallback
# is far costlier per bar, so it keeps a smaller window (60 for 4H+, and never more than a week of raw seconds).
_TECHNICALS_STORE_BARS = 250
_TECHNICALS_BARS = 120
_FALLBACK_MAX_SPAN_S = 7 * 86_400
_TECHNICALS_WIDE_BARS = 60
# The column timeframes the UI offers; anything else is a 400, not a silent fallback.
_TECHNICALS_BAR_SIZES = (60, 300, 900, 3600, 14400, 86400)
# Catalog writes lag by a flush interval, so the newest candle is normally a minute or two old;
# older than this the coin's data has genuinely stopped and its value must not read as live.
_TECHNICALS_MAX_CANDLE_AGE_BARS = 5
# Bulk values are identical for every viewer of the same entries/ranked set -- a short TTL cache
# keeps a second tab or a slow poll from stacking another full per-coin catalog pass.
_TECHNICALS_CACHE_TTL_S = 90.0
_technicals_cache: dict[str, tuple[float, dict[str, dict[str, float | None]]]] = {}


class _CatalogReadError(Exception):
    """
    A catalog read failed. Deliberately not a ValueError: pyarrow's ArrowInvalid is one, and
    would otherwise be mistaken for bad client indicator params (a whole-request 400).
    """


class TechnicalsColumn(_indicators.IndicatorConfigEntry):
    """A per-coin picker entry plus the bar size it is computed on."""

    bar_seconds: int = screener_columns_config.DEFAULT_BAR_SECONDS


class TechnicalsRequestEntry(_indicators.IndicatorValueRequestEntry):
    bar_seconds: int = screener_columns_config.DEFAULT_BAR_SECONDS


@router.get("/api/rankings/technicals-columns")
def get_technicals_columns() -> list[TechnicalsColumn]:
    try:
        entries = screener_columns_config.load_config(Path(SCREENER_COLUMNS_CONFIG_PATH))
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
            screener_columns_config.ColumnEntry(
                name=e["name"],
                params=e.get("params", {}),
                category=e["category"],
                bar_seconds=e.get("bar_seconds", screener_columns_config.DEFAULT_BAR_SECONDS),
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
    # instrument_id -> why its values are missing. A coin listed here has NO data because its
    # computation failed -- distinct from a coin with `None` values (genuinely no data yet).
    errors: dict[str, str] = {}


def _recent_candles(instrument_id: str, bar_seconds: int, now_ns: int) -> list[dict]:
    """
    Newest candles for one bar size: the SQLite candle store when it holds the coin (no Parquet
    I/O), else the slow archive read.
    """
    try:
        store = Path(CANDLES_DB_DIR) / f"candles_{venue_of(instrument_id).lower()}.db"
        with candle_store.connect_ro(str(store)) as db:
            if db is not None:
                stored = candle_store.window(
                    db, instrument_id, bar_seconds, 1 << 62, _TECHNICALS_STORE_BARS
                )
                if stored:
                    return stored
    except Exception as exc:
        raise _CatalogReadError(str(exc)) from exc
    return _read_candles(instrument_id, bar_seconds, now_ns)


def _read_candles(instrument_id: str, bar_seconds: int, now_ns: int) -> list[dict]:
    """
    Read candles the slow way, for a coin the candle store does not hold.

    Raw 1s columns aggregated to `bar_seconds`, over at most a week (MEM-01), so 4H+ columns may get
    fewer bars than the store gives.
    """
    bars = _TECHNICALS_BARS if bar_seconds <= 3600 else _TECHNICALS_WIDE_BARS
    span_ns = min((bars + 5) * bar_seconds, _FALLBACK_MAX_SPAN_S) * 1_000_000_000
    try:
        rows = catalog_files.query_second_ohlc(
            CATALOG_PATH, instrument_id, now_ns - span_ns, now_ns
        )
        return candle_dicts_from_snapshots(rows, bar_seconds)[-bars:]
    except Exception as exc:
        raise _CatalogReadError(str(exc)) from exc


def _latest_values(
    instrument_id: str, entries: list[TechnicalsRequestEntry], now_ns: int
) -> dict[str, float | None]:
    """
    Each requested indicator's latest value for one instrument, via the chart's own
    `replay_indicator` dispatch, each entry over its own column's timeframe (no indicator math
    here). Candles are built once per distinct bar size.
    """
    keyed: dict[str, float | None] = {}
    for bar_seconds in sorted({e.bar_seconds for e in entries}):
        candles = _recent_candles(instrument_id, bar_seconds, now_ns)
        max_age_ms = _TECHNICALS_MAX_CANDLE_AGE_BARS * bar_seconds * 1000
        if not candles or now_ns // 1_000_000 - candles[-1]["t"] > max_age_ms:
            continue  # no data / stopped: an honest gap, never a stale value shown as current (DATA-01)
        window = custom_indicators.ReplayWindow(
            instrument_id=instrument_id,
            bar_seconds=bar_seconds,
            start_ms=candles[0]["t"],
            end_ms=candles[-1]["t"] + bar_seconds * 1000,
        )
        group = [(i, e) for i, e in enumerate(entries) if e.bar_seconds == bar_seconds]
        by_time, errors = _indicators._values_by_time(candles, [e for _, e in group], window)
        if (
            errors
        ):  # unlike the chart, one bad column fails the request: a half-filled column reads as data
            raise ValueError(next(iter(errors.values())))
        latest = by_time[candles[-1]["t"]]
        for index, entry in group:
            prefix = _indicators._indicator_id(entry.name, entry.params) + "."
            keyed.update(
                {
                    f"{index}.{k.removeprefix(prefix)}": v
                    for k, v in latest.items()
                    if k.startswith(prefix)
                }
            )
    return keyed


@router.get("/api/rankings/technicals-values")
def get_technicals_values(entries: str) -> TechnicalsValuesResponse:
    """
    Latest value of every requested indicator for every currently-ranked instrument.

    Computes nothing itself (AD-F2): it dispatches to the chart's own `replay_indicator`, so a
    column always equals what that coin's chart shows. Known limit: sequential per-coin catalog
    reads behind a 90s TTL cache (> the client's 60s poll, or it never hits) (one live key) -- no single-flight, add one if concurrent
    viewers ever load the box.
    """
    parsed = _indicators._parse_entries(entries, TechnicalsRequestEntry)
    if any(e.bar_seconds not in _TECHNICALS_BAR_SIZES for e in parsed):
        raise HTTPException(
            status_code=400, detail=f"bar_seconds must be one of {_TECHNICALS_BAR_SIZES}"
        )
    _require_known_indicators([e.name for e in parsed])
    latest = redis_bus.bus.latest
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
            result[iid] = _latest_values(iid, parsed, now_ns)
        except ValueError as exc:  # bad indicator params -- the same for every coin, client input
            raise HTTPException(status_code=400, detail=f"invalid indicator entry: {exc}") from exc
        except Exception as exc:
            error_ledger.record("technicals.values", f"technicals values failed for {iid}", exc)
            errors[iid] = repr(exc)
    if not errors:  # never cache a failure: the next poll must retry it
        _technicals_cache.clear()  # one live key at a time; bounded (MEM-01)
        _technicals_cache[cache_key] = (time.monotonic(), result)
    return TechnicalsValuesResponse(values=result, errors=errors)
