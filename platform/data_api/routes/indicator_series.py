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
`GET /api/indicator-series/{instrument_id}` -- cursor-paginated OFI/OBI/microprice/spread
history (AD-F3): the frontend's indicator sub-panes fetch their initial window and every
scroll-back page through this one `before_ns`/`limit`/`bar_seconds` contract, mirroring
`routes/candles.py`'s own contract exactly so both routes can be co-paged from the same
scroll-back trigger.

Reuses `kernel.indicators.MultiLevelOFI`/`MultiLevelOBI`/`microprice`/`spread`
unchanged (AD-F2, SSOT-01) -- this module adds no new indicator math, only bounded-query
construction, chronological replay/bucketing, gap-marker insertion, and the `has_more`
probe.

SSOT-02 governs *live* rolling OFI/OBI ("ranking_engine is the sole owner... dashboard/
bot_tui are pure readers, never independent computers of it") -- it does not apply here.
This route computes a bounded, deterministic *historical* replay for one HTTP request's
own fixed time window, the same category of usage `custom_indicators.py`'s existing
`_ofi_replay`/`_cancel_pressure_replay` (also independent, request-scoped, "historical
only" replays, see its own module docstring) already establishes as sanctioned -- not a
second live computer of the same rolling metric ranking_engine publishes.

Deliberately its own file, not folded into the structural seed's planned
`routes/indicators.py` (Story 15.6's `/api/indicators/catalog` + config-persistence
concern) -- see this story's spec for the full rationale.

`CATALOG_PATH` comes from `data_api.settings` (a leaf module -- routes can't import it from
`app.py`, which imports them).
"""

from fastapi import APIRouter
from kernel import catalog_files
from kernel.indicators import MultiLevelOBI
from kernel.indicators import MultiLevelOFI
from kernel.indicators import microprice as _microprice
from kernel.indicators import spread as _spread
from kernel.venues import market_kind
from kernel.venues import venue_of
from ml_signals import catalog_stats as _catalog_stats
from pydantic import BaseModel

from data_api.routes import paging
from data_api.settings import CATALOG_PATH


# Same clamp/window/span constants as candles.py -- kept as this module's own copies
# rather than importing candles.py's (MEM-01 extended to this route, independently of
# whatever candles.py's own values happen to be).
_MAX_INDICATOR_SERIES_LIMIT = 500
_MAX_BAR_SECONDS = 86_400
_QUERY_WINDOW_MULTIPLIER = 3
_MAX_QUERY_SPAN_SECONDS = 7 * 86_400

router = APIRouter()


class IndicatorSeriesPoint(BaseModel):
    t: int
    ofi: float | None = None
    obi: float | None = None
    microprice: float | None = None
    spread: float | None = None


class IndicatorSeriesResponse(BaseModel):
    items: list[IndicatorSeriesPoint]
    has_more: bool
    venue: str
    market: str


def _window_start_ns(before_ns: int, limit: int, bar_seconds: int) -> int:
    span_seconds = min(limit * bar_seconds * _QUERY_WINDOW_MULTIPLIER, _MAX_QUERY_SPAN_SECONDS)
    return before_ns - span_seconds * 1_000_000_000


def _replay_bucket_samples(snapshots: list, bar_seconds: int) -> dict[int, IndicatorSeriesPoint]:
    """
    Replay `MultiLevelOFI`/`MultiLevelOBI` in chronological order over every queried
    snapshot, and compute stateless `microprice`/`spread` per snapshot -- keeping the
    last-computed value per `bar_seconds`-wide bucket (`ts_event // (bar_seconds *
    1_000_000_000)`), same bucket-sampling technique `custom_indicators.py`'s
    `_ofi_bucket_samples` uses (Dev Notes), adapted from delta-driven to snapshot-driven
    input.

    A page's OFI/OBI replay starts fresh at that page's own window start -- it cannot
    carry state across pages, since pages are fetched independently and out of full-
    history order (same explicit per-page-reset scope choice `custom_indicators.py`'s
    CVD replay already documents). OFI's first snapshot in this window only seeds its
    `_prev_*` state and yields no value -- only record `ofi` once `.initialized` is True.
    """
    bar_ns = bar_seconds * 1_000_000_000
    ofi = MultiLevelOFI(levels=10, window=50)
    obi = MultiLevelOBI(levels=10)
    buckets: dict[int, IndicatorSeriesPoint] = {}

    for snapshot in sorted(snapshots, key=lambda s: s.ts_event):
        ofi.update_raw(
            snapshot.bid_prices,
            snapshot.bid_sizes,
            snapshot.ask_prices,
            snapshot.ask_sizes,
        )
        obi.update_raw(snapshot.bid_sizes, snapshot.ask_sizes)
        snapshot_dict = {
            "bid_prices": snapshot.bid_prices,
            "bid_sizes": snapshot.bid_sizes,
            "ask_prices": snapshot.ask_prices,
            "ask_sizes": snapshot.ask_sizes,
        }
        bucket = snapshot.ts_event // bar_ns
        buckets[bucket] = IndicatorSeriesPoint(
            t=bucket * bar_seconds * 1000,
            ofi=ofi.value if ofi.initialized else None,
            obi=obi.value if obi.initialized else None,
            microprice=_microprice(snapshot_dict),
            spread=_spread(snapshot_dict),
        )

    return buckets


def _insert_gap_markers(
    points: list[IndicatorSeriesPoint],
    bar_seconds: int,
) -> list[IndicatorSeriesPoint]:
    """
    Same bar-boundary-spacing gap-marker rule as `candles.py`'s `_insert_gap_markers`
    (AC #5, AD-F6) -- consistency matters here specifically because this pane shares a
    time axis with the candlestick pane.
    """
    bar_ms = bar_seconds * 1000
    items: list[IndicatorSeriesPoint] = []
    for i, p in enumerate(points):
        if i > 0 and p.t - points[i - 1].t > bar_ms:
            items.append(IndicatorSeriesPoint(t=points[i - 1].t + bar_ms))
        items.append(p)
    return items


@router.get("/api/indicator-series/{instrument_id}")
def get_indicator_series(
    instrument_id: str,
    before_ns: int,
    limit: int = 120,
    bar_seconds: int = 60,
) -> IndicatorSeriesResponse:
    limit = max(1, min(limit, _MAX_INDICATOR_SERIES_LIMIT))
    bar_seconds = max(1, min(bar_seconds, _MAX_BAR_SECONDS))
    before_ms = before_ns // 1_000_000

    def fetch(start_ns: int, end_ns: int) -> list[IndicatorSeriesPoint]:
        snapshots = _catalog_stats.query_second_snapshots(
            CATALOG_PATH, instrument_id, start_ns, end_ns
        )
        buckets = _replay_bucket_samples(snapshots, bar_seconds)
        return [p for _, p in sorted(buckets.items()) if p.t < before_ms]

    ranges = catalog_files.data_file_ranges(CATALOG_PATH, instrument_id)
    span_ns = before_ns - _window_start_ns(before_ns, limit, bar_seconds)
    kept = paging.fetch_page(fetch, ranges, before_ns, span_ns)[-limit:]

    if not kept:
        return IndicatorSeriesResponse(
            items=[],
            has_more=False,
            venue=venue_of(instrument_id),
            market=market_kind(instrument_id),
        )

    has_more = paging.has_older_data(ranges, kept[0].t * 1_000_000)
    return IndicatorSeriesResponse(
        items=_insert_gap_markers(kept, bar_seconds),
        has_more=has_more,
        venue=venue_of(instrument_id),
        market=market_kind(instrument_id),
    )
