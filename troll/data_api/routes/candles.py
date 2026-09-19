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
`GET /api/candles/{instrument_id}` -- cursor-paginated candle history (AD-F3): the
frontend's chart page fetches its initial 120-bar window and every scroll-back page
through this one `before_ns`/`limit` contract, never an unbounded full-range load.

Reuses `ml_signals.candles.candle_dicts_from_snapshots` and
`ml_signals.catalog_stats.query_second_snapshots` unchanged (AD-F2) -- this module adds
no new aggregation/query logic, only bounded-query construction, gap-marker insertion,
and the `has_more` probe.

Own module-level `CATALOG_PATH` constant, same pattern as `redis_bus.py`'s own
`REDIS_URL` -- `routes/candles.py` cannot `from data_api.app import CATALOG_PATH`
without a circular import, since `app.py` imports this module.
"""

import os

from fastapi import APIRouter
from pydantic import BaseModel

from ml_signals import catalog_stats as _catalog_stats
from ml_signals.candles import candle_dicts_for_window
from ml_signals.candles import choose_candle_source


CATALOG_PATH: str = os.environ.get("CATALOG_PATH", "troll/dydx_collector/catalog")

# Server-enforced upper bound on `limit`, regardless of what the client requests (AC #7,
# MEM-01 extended to the API surface) -- a module constant, not per-request configurable.
_MAX_CANDLES_LIMIT = 500

# Server-enforced bound on `bar_seconds` -- same silent-clamp philosophy as `limit` above
# (AC #7's "no error, silently clamped"), not a 422: a non-positive value would zero or
# invert every window/bucket computation below, and an unbounded one would let a client
# blow up the query span this route otherwise keeps deliberately bounded (AD-F3/MEM-01).
_MAX_BAR_SECONDS = 604_800  # 1w -- the timeframe selector's widest bar

# How many multiples of `limit * bar_seconds` to look back for the main query window.
# Real second-snapshot coverage has gaps (thin trading, collector downtime), so a 1x
# window can come up short of `limit` candles even when enough history exists a bit
# further back -- 3x gives headroom without unbounding the read (still a fixed multiple
# of a bounded window, never open-ended).
_QUERY_WINDOW_MULTIPLIER = 3

# Hard cap on any single query's total time span (MEM-01), independent of the
# limit/bar_seconds product that produced it -- `_MAX_CANDLES_LIMIT * _MAX_BAR_SECONDS *
# _QUERY_WINDOW_MULTIPLIER` alone would allow a single request to pull ~4 years of raw
# 1-second snapshots, defeating the bounded-read guarantee this route exists to provide.
_MAX_QUERY_SPAN_SECONDS = 7 * 86_400

# Wide bars (> ROLLUP_THRESHOLD_SECONDS) read the minute rollup (1/60th the rows), so the
# same MEM-01 bound can span longer -- but it also bounds the raw-1s fallback the dispatch
# uses before the rollup's first row, so it stays a fixed cap, not open-ended.
_MAX_ROLLUP_QUERY_SPAN_SECONDS = 30 * 86_400

router = APIRouter()


class CandleItem(BaseModel):
    t: int
    o: float | None = None
    h: float | None = None
    l: float | None = None  # noqa: E741 -- matches the wire field name (candle low)
    c: float | None = None
    v: float | None = None


class CandlesResponse(BaseModel):
    items: list[CandleItem]
    has_more: bool


def _window_start_ns(before_ns: int, limit: int, bar_seconds: int) -> int:
    cap = (
        _MAX_ROLLUP_QUERY_SPAN_SECONDS
        if choose_candle_source(bar_seconds) == "rollup_1m"
        else _MAX_QUERY_SPAN_SECONDS
    )
    span_seconds = min(limit * bar_seconds * _QUERY_WINDOW_MULTIPLIER, cap)
    return before_ns - span_seconds * 1_000_000_000


def _insert_gap_markers(candles: list[dict], bar_seconds: int) -> list[CandleItem]:
    """
    Insert one explicit null-OHLC gap item wherever two consecutive kept candles' `t`
    (ms) differ by more than one `bar_seconds` interval (AC #5, AD-F6) -- `t` is placed
    immediately after the earlier candle so lightweight-charts' whitespace data renders
    the break starting right where real data stops, not at the next candle's own time.
    """
    bar_ms = bar_seconds * 1000
    items: list[CandleItem] = []
    for i, c in enumerate(candles):
        if i > 0 and c["t"] - candles[i - 1]["t"] > bar_ms:
            items.append(CandleItem(t=candles[i - 1]["t"] + bar_ms))
        items.append(CandleItem(**c))
    return items


def _has_more(instrument_id: str, earliest_kept_ns: int, limit: int, bar_seconds: int) -> bool:
    """
    One bounded probe query for the window immediately preceding the page's earliest
    kept candle -- `ParquetDataCatalog.query()` has no cheap "does earlier data exist"
    primitive, so this is the sanctioned approach (Design Notes): `has_more = True` iff
    the probe returns any snapshot at all.

    `end` is `earliest_kept_ns - 1`, not `earliest_kept_ns` -- `ParquetDataCatalog.query()`'s
    `end` bound is inclusive (`ts_init <= end`, verified in
    `nautilus_trader/persistence/catalog/parquet.py`), so probing with an inclusive end
    exactly at the earliest kept candle's own bucket-open time would re-find that
    candle's own underlying snapshot every time, making this report `True`
    unconditionally regardless of whether older data actually exists.
    """
    probe_end_ns = earliest_kept_ns - 1
    probe_start_ns = _window_start_ns(probe_end_ns, limit, bar_seconds)
    if choose_candle_source(bar_seconds) == "rollup_1m" and _catalog_stats.query_minute_rollups(
        CATALOG_PATH, instrument_id, probe_start_ns, probe_end_ns,
    ):
        return True
    # Pre-rollup history is raw-only; probe a bounded raw window so it still pages back.
    raw_start_ns = max(probe_start_ns, probe_end_ns - _MAX_QUERY_SPAN_SECONDS * 1_000_000_000)
    return len(_catalog_stats.query_second_snapshots(
        CATALOG_PATH, instrument_id, raw_start_ns, probe_end_ns,
    )) > 0


@router.get("/api/candles/{instrument_id}")
def get_candles(
    instrument_id: str, before_ns: int, limit: int = 120, bar_seconds: int = 60,
) -> CandlesResponse:
    limit = max(1, min(limit, _MAX_CANDLES_LIMIT))
    bar_seconds = max(1, min(bar_seconds, _MAX_BAR_SECONDS))
    before_ms = before_ns // 1_000_000

    start_ns = _window_start_ns(before_ns, limit, bar_seconds)
    candles = [
        c
        for c in candle_dicts_for_window(
            instrument_id,
            start_ns,
            before_ns,
            bar_seconds,
            snapshot_rows_fn=lambda i, a, b: _catalog_stats.query_second_snapshots(CATALOG_PATH, i, a, b),
            rollup_rows_fn=lambda i, a, b: _catalog_stats.query_minute_rollups(CATALOG_PATH, i, a, b),
        )
        if c["t"] < before_ms
    ]
    kept = candles[-limit:]

    if not kept:
        return CandlesResponse(items=[], has_more=False)

    earliest_kept_ns = kept[0]["t"] * 1_000_000
    has_more = _has_more(instrument_id, earliest_kept_ns, limit, bar_seconds)
    return CandlesResponse(items=_insert_gap_markers(kept, bar_seconds), has_more=has_more)
