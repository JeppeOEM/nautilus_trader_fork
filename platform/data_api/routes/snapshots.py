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
`GET /api/snapshots/{instrument_id}` -- cursor-paginated Lines-mode history (AD-F3): the
frontend chart page's Lines mode fetches its initial window and every scroll-back page
through this one `before_ns`/`limit` contract, mirroring `routes/candles.py`'s exact
cursor-pagination shape on a new route (no `bar_seconds` -- snapshots are per-second rows,
there is no bar/aggregation concept here).

Reuses `ml_signals.catalog_stats.query_second_snapshots` unchanged (AD-F2) and
`ml_signals.indicators.microprice` unchanged (SSOT-01) -- this module adds no new
aggregation/query logic, only bounded-query construction, the ported `_price_series_rows`
math (SSOT-03, see below), and the `has_more` probe.

`CATALOG_PATH` comes from `data_api.settings` (a leaf module -- routes can't import it from
`app.py`, which imports them).

`_price_series_rows` below is a verbatim relocation of `ml_signals.dashboard._price_series_
rows` (AD-F1) -- `dashboard.py` is scheduled for deletion in Story 15.10, so importing from
it here at runtime would create a real dependency the cutover can't safely remove. The math
itself (CVD-weighted price, crossed-book skip, gap-marker insertion) must never be
reimplemented a second time (SSOT-03, `dashboard.py`'s own docstring) -- this is a relocation
of that one function, not a new implementation. `data_api/tests/test_snapshots.py` proves the
two copies stay byte-identical via a test-only oracle import of `ml_signals.dashboard`'s
original (never a runtime import).
"""

from collector_core.second_snapshot import DydxSecondSnapshot
from common.venues import market_kind
from fastapi import APIRouter
from ml_signals import catalog_stats as _catalog_stats
from ml_signals.indicators import microprice as _microprice
from ml_signals.venue import venue_of
from pydantic import BaseModel

from data_api.routes import paging
from data_api.settings import CATALOG_PATH


# Server-enforced upper bound on `limit` (MEM-01/AD-F3) -- sized in rows-per-second terms,
# much larger than a candle limit (`_MAX_CANDLES_LIMIT = 500` bars) since there is no
# bar-aggregation step here: 5000 one-second rows is a bit under 1.5 hours of raw snapshot
# history in one response, comfortably above the ~15-minute/900-row default page while
# still bounding the per-request read/serialization cost.
_MAX_SNAPSHOTS_LIMIT = 5_000

# How many multiples of `limit` (in seconds, since rows are ~1/second) to look back for the
# main query window -- real snapshot coverage has gaps (thin trading, collector downtime),
# so a 1x window can come up short of `limit` rows even when enough history exists a bit
# further back. Mirrors `candles.py`'s own `_QUERY_WINDOW_MULTIPLIER` reasoning.
_QUERY_WINDOW_MULTIPLIER = 2

# Hard cap on any single query's total time span (MEM-01), independent of the limit that
# produced it -- same guardrail role as `candles.py`'s `_MAX_QUERY_SPAN_SECONDS`.
_MAX_QUERY_SPAN_SECONDS = 7 * 86_400

# Matches `ml_signals.dashboard._CHART_GAP_THRESHOLD_MS` exactly (ported constant, not
# imported -- `dashboard.py` is not a runtime dependency of this module, see module
# docstring). A gap this wide between two consecutive valid snapshots means the collector's
# staleness guard skipped stale books during a WS reconnect (DATA-01) -- render it as an
# explicit break, never an interpolated flat line (AC #4, AD-F6).
_SNAPSHOT_GAP_THRESHOLD_MS = 2500

router = APIRouter()


class SnapshotSeriesPoint(BaseModel):
    t: int
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    micro: float | None = None
    price: float | None = None


class SnapshotSeriesResponse(BaseModel):
    items: list[SnapshotSeriesPoint]
    has_more: bool
    venue: str
    market: str


def _snapshot_to_row_dict(snapshot: DydxSecondSnapshot) -> dict:
    """
    Build the 7-key dict `_price_series_rows` reads, inline from a queried
    `DydxSecondSnapshot` -- the same 7 keys `dashboard.py`'s own `_historical_lines_json`
    builds (`ml_signals/dashboard.py:1577-1588`), not a cross-route-module import of
    `app.py`'s private `_snapshot_to_dict` (which also carries 4 unrelated OHLC keys
    `_price_series_rows` never reads).
    """
    return {
        "bid_prices": snapshot.bid_prices,
        "bid_sizes": snapshot.bid_sizes,
        "ask_prices": snapshot.ask_prices,
        "ask_sizes": snapshot.ask_sizes,
        "buy_volume": snapshot.buy_volume,
        "sell_volume": snapshot.sell_volume,
        "ts_event": snapshot.ts_event,
    }


def _price_series_rows(snaps: list[dict]) -> list[dict]:
    """
    Verbatim port of `ml_signals.dashboard._price_series_rows` (AD-F1, SSOT-03) -- build
    bid/ask/mid/micro/price rows from a snapshot list, in time order. Do not change this
    function's math independently of its original; see module docstring.

    price = CVD-weighted effective trade price: skews from mid toward ask on net buying,
    toward bid on net selling. Equals mid when no trades occurred in that second.
    Timestamps are milliseconds (matching `/api/candles`'s `CandleItem.t` unit exactly, so
    both series land on the same chart time axis).
    """
    rows: list[dict] = []
    prev_ts_ms: int | None = None
    for s in snaps:
        if not s["bid_prices"] or not s["ask_prices"]:
            continue
        bp, ap = s["bid_prices"][0], s["ask_prices"][0]
        if bp >= ap:  # crossed/touched snapshot -- skip (stale data from reconnect)
            continue
        curr_ts_ms = s["ts_event"] // 1_000_000
        # Gap detection: if consecutive valid snapshots are further apart than the
        # threshold, insert a null data point so the chart renders an explicit break
        # instead of a misleading flat line across the gap (AC #4, AD-F6, DATA-01).
        if prev_ts_ms is not None and (curr_ts_ms - prev_ts_ms) > _SNAPSHOT_GAP_THRESHOLD_MS:
            rows.append(
                {
                    "t": curr_ts_ms - 1,
                    "bid": None,
                    "ask": None,
                    "mid": None,
                    "micro": None,
                    "price": None,
                }
            )
        prev_ts_ms = curr_ts_ms
        mid = (bp + ap) / 2
        micro_value = _microprice(s)
        micro = micro_value if micro_value is not None else mid
        tv = s["buy_volume"] + s["sell_volume"]
        if tv > 0:
            price = mid + ((s["buy_volume"] - s["sell_volume"]) / tv) * (ap - bp) * 0.5
        else:
            price = mid
        rows.append(
            {"t": curr_ts_ms, "bid": bp, "ask": ap, "mid": mid, "micro": micro, "price": price}
        )
    return rows


def _window_start_ns(before_ns: int, limit: int) -> int:
    span_seconds = min(limit * _QUERY_WINDOW_MULTIPLIER, _MAX_QUERY_SPAN_SECONDS)
    return before_ns - span_seconds * 1_000_000_000


def _take_last_n_real_rows(rows: list[dict], limit: int) -> list[dict]:
    """
    Slice to the most recent `limit` REAL rows (gap markers, `bid is None`, are structural
    breaks, not data, and must never eat into the requested row budget) -- mirrors
    `candles.py`'s own order of operations (slice to `limit` real candles first, insert gap
    markers into the kept slice second), adapted for `_price_series_rows`' shape, which
    already interleaves markers with real rows in one pass.

    Slicing at a real row's own index (never mid-window) also means any gap marker sitting
    immediately before the new earliest-kept real row is naturally dropped -- the same
    "no boundary gap at the very edge of a page" behavior `candles.py`'s `_insert_gap_
    markers` has (it only checks gaps strictly inside its own `kept` slice; a page-boundary
    gap is instead the frontend's own seam check across two pages, see `useCandles.ts`).
    """
    real_indices = [i for i, r in enumerate(rows) if r["bid"] is not None]
    if not real_indices:
        return []
    start_index = real_indices[-limit] if len(real_indices) > limit else 0
    return rows[start_index:]


@router.get("/api/snapshots/{instrument_id}")
def get_snapshots(instrument_id: str, before_ns: int, limit: int = 900) -> SnapshotSeriesResponse:
    limit = max(1, min(limit, _MAX_SNAPSHOTS_LIMIT))
    before_ms = before_ns // 1_000_000

    def fetch(start_ns: int, end_ns: int) -> list[dict]:
        snapshots = _catalog_stats.query_second_snapshots(
            CATALOG_PATH, instrument_id, start_ns, end_ns
        )
        snap_dicts = [_snapshot_to_row_dict(s) for s in sorted(snapshots, key=lambda s: s.ts_event)]
        return _take_last_n_real_rows(
            [r for r in _price_series_rows(snap_dicts) if r["t"] < before_ms],
            limit,
        )

    ranges = _catalog_stats.data_file_ranges(CATALOG_PATH, instrument_id)
    span_ns = before_ns - _window_start_ns(before_ns, limit)
    kept = paging.fetch_page(fetch, ranges, before_ns, span_ns)

    if not kept:
        return SnapshotSeriesResponse(
            items=[],
            has_more=False,
            venue=venue_of(instrument_id),
            market=market_kind(instrument_id),
        )

    has_more = paging.has_older_data(ranges, kept[0]["t"] * 1_000_000)
    return SnapshotSeriesResponse(
        items=[SnapshotSeriesPoint(**row) for row in kept],
        has_more=has_more,
        venue=venue_of(instrument_id),
        market=market_kind(instrument_id),
    )
