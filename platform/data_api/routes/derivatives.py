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
`GET /api/coin/{instrument_id}/funding|open-interest|mark-index|liquidations|liquidation-bars`
(Story 33.4, `docs/DATA_DICTIONARY.md` §2.16): the archived derivatives and liquidations, on the
candles' `before_ns`/`limit`/`bar_seconds` cursor contract (`limit` clamped 1..500 and
`bar_seconds` 1..604800, both silently, as `/api/candles` does).

Format + transport only (SSOT-02): every page is `views.derivatives`' (bounded, gap-marked, spot
short-circuited to an empty page, never a 404). This module clamps the params, passes its own
`CATALOG_PATH`/`CANDLES_DB_DIR` and the live bus's unflushed liquidation tail in, builds the
response models, and maps `DerivativesReadError` (already ledgered by `views`) to a 500.

`/mark-index` and `/liquidation-bars` read the candle store, which folds only
`candles.domain.fold.BAR_SECONDS` (1m 5m 15m 1h 4h 1d); a wider width those tile is composed from
them (1W from 1D, `views.chart_series.stored_bar`). For a width none tiles (1..59 s, 90 s),
`/mark-index` still serves mark, index and `basis_mi_bps` with a null `basis_ml_bps` (no close: a
missing input), and `/liquidation-bars` is a 400 naming the widths, never an empty or null page
read as "no liquidations".

Prices, rates and open interest are exact decimal **strings**; liquidation sizes, prices and
notionals are integer units at the precisions carried beside them (formatted by the frontend's
`lib/units.ts`); basis and annualised funding are floats made at the read model's edge.
Bucketed items' `t` is the bucket start in ms (like the candles); event items' `t`/`ts_event`
are ns (like the live `derivs:`/`liquidations:` frames).

Known limit (ns cursor in JSON, audit D-170): `FundingItem.t` and `LiquidationItem.ts_event` are
ns integers near 1.8e18, which a browser's `JSON.parse` rounds to the nearest multiple of 256 ns
(at most 128 ns off), and the next page's cursor is the oldest item's time. Rounded up, the next
page re-serves the oldest tie group (`views.derivatives._kept` serves a group whole), deduped by
`venue_event_id` (liquidations) or `t` (funding); rounded down, it skips only rows less than 128 ns
older than that group. Bybit stamps `ts_event` in whole ms (liquidations, funding), so none exists;
Hyperliquid funding is stamped at receipt (`ts_event == ts_init`, ns), one row per
`activeAssetCtx` frame, so two rows of one coin 128 ns apart would need two frames decoded within
128 ns. Python clients get the exact int. Upgrade path: a string cursor (`next_before_ns` as text)
on the event pages.
"""

from collections.abc import Callable

from fastapi import APIRouter
from fastapi import HTTPException
from kernel.venues import market_kind
from kernel.venues import venue_of
from pydantic import BaseModel
from views import derivatives
from views.catalog_reads import NoInstrumentDefinition
from views.catalog_reads import instrument_precision

from data_api import buses
from data_api.settings import CANDLES_DB_DIR
from data_api.settings import CATALOG_PATH


# The candles route's silent clamps (AD-F3/MEM-01): a server bound, never a 422.
_MAX_LIMIT = 500
_MAX_BAR_SECONDS = 604_800  # 1w, the timeframe selector's widest bar

router = APIRouter()


class FundingItem(BaseModel):
    t: int  # ts_event, ns
    rate: str  # the per-interval rate, exact text
    interval: int | None  # seconds; None when the venue sent none
    next_funding_ns: int | None
    # rate x 31 536 000 / interval (simple, not compounded); None without an interval.
    annualised: float | None


class OpenInterestItem(BaseModel):
    t: int  # bucket start, ms
    oi: str | None = None  # the bucket's last open interest, exact text; None on a gap row
    oi_change: str | None = None  # against the previous known bucket; None when unknown


class MarkIndexItem(BaseModel):
    t: int  # bucket start, ms
    mark: str | None = None  # the bucket's last, exact text; None when it has none
    index: str | None = None
    basis_mi_bps: float | None = None  # (mark - index) / index x 10^4
    basis_ml_bps: float | None = None  # (mark - the bucket's traded close) / close x 10^4


class LiquidationItem(BaseModel):
    side: str  # "long" | "short": the liquidated position (a long's is a forced sell)
    size_units: int  # units of 10^-size_precision
    price_units: int  # units of 10^-price_precision
    price_precision: int
    size_precision: int
    venue_event_id: str
    ts_event: int  # ns
    ts_init: int
    price_kind: str  # "bankruptcy": Bybit's, the only feed, never the fill price
    # Story 33.5: size x bankruptcy price (`Liquidation.notional_units()`) in units of
    # 10^-notional_precision of the quote, notional_precision = price_precision + size_precision.
    # Known limit (JSON range, D-170): a notional over 2^53 units is rounded by a browser's
    # `JSON.parse`; Python consumers get the exact int. Upgrade path: a string encoding.
    notional_units: int
    notional_precision: int


class LiquidationBarItem(BaseModel):
    t: int  # bucket start, ms
    # Null is unknown, never 0: an id without the feed, a bucket before or straddling the feed
    # start (D-160), a gap row. 0 is a known bucket none landed in.
    long_v: int | None = None  # units of 10^-size_precision
    short_v: int | None = None
    n: int | None = None
    size_precision: int | None = None
    # Size x bankruptcy price in units of 10^-notional_precision of the quote; None when unknown.
    # Known limit (JSON range): a notional over 2^53 units is rounded by a browser's `JSON.parse`;
    # Python consumers get the exact int. Upgrade path: the candles' `pv` one, a string encoding.
    notional_units: int | None = None
    notional_precision: int | None = None
    # Story 33.5: the notional split by liquidated side, at the same `notional_precision`, null
    # exactly when `notional_units` is (a side with no rows is 0), so the chart's mirrored notional
    # bars need no browser arithmetic. The JSON-range Known limit above applies to each.
    long_notional_units: int | None = None
    short_notional_units: int | None = None


class _Page(BaseModel):
    has_more: bool
    venue: str
    market: str


class FundingResponse(_Page):
    items: list[FundingItem]


class OpenInterestResponse(_Page):
    items: list[OpenInterestItem]


class MarkIndexResponse(_Page):
    items: list[MarkIndexItem]


class _UnitsPage(_Page):
    # The catalog definition's own decimals (never derived from a value); None for a spot id,
    # whose page reads nothing.
    price_precision: int | None
    size_precision: int | None


class LiquidationsResponse(_UnitsPage):
    items: list[LiquidationItem]


class LiquidationBarsResponse(_UnitsPage):
    items: list[LiquidationBarItem]


def _clamped(limit: int, bar_seconds: int = 60) -> tuple[int, int]:
    return max(1, min(limit, _MAX_LIMIT)), max(1, min(bar_seconds, _MAX_BAR_SECONDS))


def _identity(instrument_id: str) -> tuple[str, str]:
    # A malformed id is a 400 (the app's ValueError handler), not a 404.
    return venue_of(instrument_id), market_kind(instrument_id)


def _page(read: Callable[[], tuple[list[dict], bool]]) -> tuple[list[dict], bool]:
    try:
        return read()
    except derivatives.UnsupportedBarSeconds as exc:
        # A width the candle store cannot serve: the request's fault, said plainly.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except derivatives.DerivativesReadError as exc:
        # A failed archive read (DATA-07), already ledgered by views: fail loud.
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _precision(instrument_id: str) -> tuple[int | None, int | None]:
    if derivatives.is_spot(instrument_id):
        return None, None
    try:
        precision = instrument_precision(CATALOG_PATH, instrument_id)
    except NoInstrumentDefinition as exc:
        # No definition: no precision to label units at, and none is guessed (DATA-01).
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return precision.price_precision, precision.size_precision


@router.get("/api/coin/{instrument_id}/funding")
def get_funding(instrument_id: str, before_ns: int, limit: int = 120) -> FundingResponse:
    venue, market = _identity(instrument_id)
    limit, _ = _clamped(limit)
    items, has_more = _page(
        lambda: derivatives.funding_page(instrument_id, before_ns, limit, catalog_path=CATALOG_PATH)
    )
    return FundingResponse(
        items=[FundingItem(**item) for item in items], has_more=has_more, venue=venue, market=market
    )


@router.get("/api/coin/{instrument_id}/open-interest")
def get_open_interest(
    instrument_id: str, before_ns: int, limit: int = 120, bar_seconds: int = 60
) -> OpenInterestResponse:
    venue, market = _identity(instrument_id)
    limit, bar_seconds = _clamped(limit, bar_seconds)
    items, has_more = _page(
        lambda: derivatives.open_interest_page(
            instrument_id, before_ns, limit, bar_seconds, catalog_path=CATALOG_PATH
        )
    )
    return OpenInterestResponse(
        items=[OpenInterestItem(**item) for item in items],
        has_more=has_more,
        venue=venue,
        market=market,
    )


@router.get("/api/coin/{instrument_id}/mark-index")
def get_mark_index(
    instrument_id: str, before_ns: int, limit: int = 120, bar_seconds: int = 60
) -> MarkIndexResponse:
    """
    Mark, index and basis per bucket, at every `bar_seconds`. `basis_ml_bps` is against the candle
    store's traded close: a stored width's own, a width its buckets tile composed (1W from 1D), and
    null for a width none tiles (1..59 s, 90 s).
    """
    venue, market = _identity(instrument_id)
    limit, bar_seconds = _clamped(limit, bar_seconds)
    items, has_more = _page(
        lambda: derivatives.mark_index_page(
            instrument_id,
            before_ns,
            limit,
            bar_seconds,
            catalog_path=CATALOG_PATH,
            candles_dir=CANDLES_DB_DIR,
        )
    )
    return MarkIndexResponse(
        items=[MarkIndexItem(**item) for item in items],
        has_more=has_more,
        venue=venue,
        market=market,
    )


@router.get("/api/coin/{instrument_id}/liquidations")
def get_liquidations(instrument_id: str, before_ns: int, limit: int = 120) -> LiquidationsResponse:
    venue, market = _identity(instrument_id)
    limit, _ = _clamped(limit)
    price_precision, size_precision = _precision(instrument_id)
    items, has_more = _page(
        lambda: derivatives.liquidations_page(
            instrument_id,
            before_ns,
            limit,
            catalog_path=CATALOG_PATH,
            recent_liquidations=buses.live_candle_bus.recent_liquidations,
        )
    )
    return LiquidationsResponse(
        items=[LiquidationItem(**item) for item in items],
        has_more=has_more,
        venue=venue,
        market=market,
        price_precision=price_precision,
        size_precision=size_precision,
    )


@router.get(
    "/api/coin/{instrument_id}/liquidation-bars",
    responses={
        400: {"description": "`bar_seconds` is not composable from the candle store's widths"}
    },
)
def get_liquidation_bars(
    instrument_id: str, before_ns: int, limit: int = 120, bar_seconds: int = 60
) -> LiquidationBarsResponse:
    """
    Liquidation sums per candle-store bucket. A width the store does not fold is composed from
    the widest stored one that tiles it (1W from 1D: sums, null when a constituent is missing or
    null, or the week straddles the feed start); a width none tiles (1..59 s, 90 s) is a 400.
    `notional_units` is summed from the archived rows, null unless the archive holds exactly `n`.
    """
    venue, market = _identity(instrument_id)
    limit, bar_seconds = _clamped(limit, bar_seconds)
    price_precision, size_precision = _precision(instrument_id)
    items, has_more = _page(
        lambda: derivatives.liquidation_bars(
            instrument_id,
            before_ns,
            limit,
            bar_seconds,
            catalog_path=CATALOG_PATH,
            candles_dir=CANDLES_DB_DIR,
        )
    )
    return LiquidationBarsResponse(
        items=[LiquidationBarItem(**item) for item in items],
        has_more=has_more,
        venue=venue,
        market=market,
        price_precision=price_precision,
        size_precision=size_precision,
    )
