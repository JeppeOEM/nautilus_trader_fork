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

`CATALOG_PATH` comes from `data_api.settings` (a leaf module -- routes can't import it from
`app.py`, which imports them).
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi import HTTPException
from pydantic import BaseModel

from common.venues import market_kind
from data_api import live_candles
from data_api.routes import paging
from data_api.settings import CANDLES_DB_DIR
from data_api.settings import CATALOG_PATH
from ml_signals import candle_store
from ml_signals import catalog_stats as _catalog_stats
from ml_signals import error_ledger
from ml_signals.candles import candle_dicts_for_window
from ml_signals.candles import is_valid_candle
from ml_signals.venue import venue_of


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

# Floor on the main query window for sub-minute bars. A bar only exists for a second that traded, so at 1s/5s the
# `limit * bar_seconds * 3` window (6 min at 1s) can hold 0-1 candles on a quiet coin -- the
# chart then has nothing to scroll and never refills. An hour of raw 1s is only ~3.6k rows.
_MIN_QUERY_WINDOW_SECONDS = 3600

# Hard cap on any single query's total time span (MEM-01), independent of the
# limit/bar_seconds product that produced it -- `_MAX_CANDLES_LIMIT * _MAX_BAR_SECONDS *
# _QUERY_WINDOW_MULTIPLIER` alone would allow a single request to pull ~4 years of raw
# 1-second snapshots, defeating the bounded-read guarantee this route exists to provide.
_MAX_QUERY_SPAN_SECONDS = 7 * 86_400

router = APIRouter()


def _catalog_plus_recent(instrument_id: str, start_ns: int, end_ns: int) -> list:
    """Catalog rows plus the live tail the collector has not flushed yet (see live_candles.RECENT_SECONDS)."""
    rows = _catalog_stats.query_second_ohlc(CATALOG_PATH, instrument_id, start_ns, end_ns)
    have = {r.ts_event for r in rows}
    tail = live_candles.live_candle_bus.recent_rows(instrument_id, start_ns, end_ns)
    return rows + [r for r in tail if r.ts_event not in have]


class CandleItem(BaseModel):
    t: int
    o: float | None = None
    h: float | None = None
    l: float | None = None  # noqa: E741 -- matches the wire field name (candle low)
    c: float | None = None
    v: float | None = None
    # Rollup-sourced bucket observed for < 90% of its span (collector gaps, D-15): its
    # high/low/volume are understated. Absent (None) on raw-1s candles and gap markers.
    partial: bool | None = None


class CandlesResponse(BaseModel):
    items: list[CandleItem]
    has_more: bool
    venue: str
    market: str


def _window_start_ns(before_ns: int, limit: int, bar_seconds: int) -> int:
    span_seconds = limit * bar_seconds * _QUERY_WINDOW_MULTIPLIER
    if bar_seconds < 60:
        span_seconds = max(span_seconds, _MIN_QUERY_WINDOW_SECONDS)
    span_seconds = min(span_seconds, _MAX_QUERY_SPAN_SECONDS)
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


def _checked(instrument_id: str, bar_seconds: int, c: dict) -> dict:
    # An impossible candle means upstream code malfunctioned (DATA-07): never serve it,
    # never drop it quietly -- fail the request so the chart shows an error, and count it.
    if not is_valid_candle(c):
        detail = f"impossible candle for {instrument_id} (bar_seconds={bar_seconds}): {c!r}"
        error_ledger.record("candles.invalid_candle", detail)
        raise HTTPException(status_code=500, detail=detail)
    return c


def _parquet_page(instrument_id: str, before_ns: int, limit: int, bar_seconds: int) -> tuple[list[dict], bool]:
    """One page straight from the Parquet archive (slow: reads a window of tiny files). Serves
    history the candle store does not hold (older than its first day, or pruned)."""
    before_ms = before_ns // 1_000_000

    def fetch(start_ns: int, end_ns: int) -> list[dict]:
        return [
            c
            for c in candle_dicts_for_window(
                instrument_id,
                start_ns,
                end_ns,
                bar_seconds,
                snapshot_rows_fn=_catalog_plus_recent,
            )
            if c["t"] < before_ms and _checked(instrument_id, bar_seconds, c)
        ]

    ranges = _catalog_stats.data_file_ranges(CATALOG_PATH, instrument_id)
    span_ns = before_ns - _window_start_ns(before_ns, limit, bar_seconds)
    kept = paging.fetch_page(fetch, ranges, before_ns, span_ns)[-limit:]
    return kept, bool(kept) and paging.has_older_data(ranges, kept[0]["t"] * 1_000_000)


def _store_path(instrument_id: str) -> str:
    return str(Path(CANDLES_DB_DIR) / f"candles_{venue_of(instrument_id).lower()}.db")


def _store_page(
    instrument_id: str, before_ns: int, limit: int, bar_seconds: int
) -> tuple[list[dict], bool, int | None]:
    """One page from the SQLite candle store (an indexed read, no Parquet I/O): `(candles,
    store_has_more, start of the store's coverage in ms)`. Empty when the store is missing or has
    nothing for this coin."""
    if bar_seconds not in candle_store.BAR_SECONDS:
        return [], False, None
    with candle_store.connect_ro(_store_path(instrument_id)) as db:
        if db is None:
            return [], False, None
        kept = candle_store.window(db, instrument_id, bar_seconds, before_ns // 1_000_000, limit)
        if not kept:
            return [], False, candle_store.oldest_t(db, instrument_id, bar_seconds, traded_only=False)
        oldest = candle_store.oldest_t(db, instrument_id, bar_seconds)
        coverage = candle_store.oldest_t(db, instrument_id, bar_seconds, traded_only=False)
        return [_checked(instrument_id, bar_seconds, c) for c in kept], oldest is not None and oldest < kept[0]["t"], coverage


def candle_page(instrument_id: str, before_ns: int, limit: int, bar_seconds: int) -> tuple[list[dict], bool]:
    """The one candle source for the chart, its indicator panes and anything else that must agree
    with them: `(candles oldest-first, has_more)` for the `limit` bars before `before_ns`. Reads the
    SQLite candle store, and only what it does not cover (history older than its first bucket, or
    pruned) from Parquet -- never when the store already reaches the archive's first file."""
    kept, store_has_more, coverage_ms = _store_page(instrument_id, before_ns, limit, bar_seconds)
    if store_has_more:
        return kept, True
    ranges = _catalog_stats.data_file_ranges(CATALOG_PATH, instrument_id)  # a directory listing
    if coverage_ms is not None and not paging.has_older_data(ranges, coverage_ms * 1_000_000):
        return kept, False
    if len(kept) >= limit:
        return kept, True  # older archive history exists beyond this full page
    older_before_ns = kept[0]["t"] * 1_000_000 if kept else before_ns
    older, has_more = _parquet_page(instrument_id, older_before_ns, limit - len(kept), bar_seconds)
    return older + kept, has_more


@router.get("/api/candles/{instrument_id}")
def get_candles(
    instrument_id: str, before_ns: int, limit: int = 120, bar_seconds: int = 60,
) -> CandlesResponse:
    limit = max(1, min(limit, _MAX_CANDLES_LIMIT))
    bar_seconds = max(1, min(bar_seconds, _MAX_BAR_SECONDS))
    kept, has_more = candle_page(instrument_id, before_ns, limit, bar_seconds)
    if not kept:
        return CandlesResponse(items=[], has_more=False, venue=venue_of(instrument_id), market=market_kind(instrument_id))
    return CandlesResponse(
        items=_insert_gap_markers(kept, bar_seconds), has_more=has_more, venue=venue_of(instrument_id), market=market_kind(instrument_id),
    )
