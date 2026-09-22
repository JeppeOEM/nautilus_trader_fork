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
REST trade backfill after a WebSocket reconnect (story 22.14, audit D-47/D-48).

`fetch_trades` returns an instrument's venue trades since `since_ns`, built as `TradeTick`s exactly
as the live WS parser builds them: the same trade id, `ts_event` from the venue's trade time as an
exact integer count of nanoseconds, the aggressor from the taker side, and price/size converted
from the venue's decimal strings at the instrument's own precisions via `Price.from_str` /
`Quantity.from_str` of a string first proven exact (`exact_text`) -- never through `float`, never
rounded. A value not representable at the precision is an error, never a rounded trade.

Wire facts, verified live 2026-09-21 (fixtures under `tests/fixtures/*_trades_*_20260921.json`):

- dYdX: `GET {indexer}/v4/trades/perpetualMarket/{ticker}?limit=1000[&createdBeforeOrAt=<iso>]`
  returns `{"trades": [{id, side BUY|SELL, size, price, createdAt ISO-8601 ms}]}`, newest first;
  `createdBeforeOrAt` is inclusive, so pages overlap by id. The WS parser
  (`crates/adapters/dydx/src/websocket/parse.rs`) uses the same `id` and the nanoseconds of the
  same `createdAt`. The indexer rejects urllib's default User-Agent (403).
- Bybit: `GET /v5/market/recent-trade?category=linear|spot&symbol=&limit=1000` returns
  `result.list[{execId, price, size, side Buy|Sell (taker), time ms}]`, newest first, no paging.
  Linear returned 1000 trades (28-64 s of BTCUSDT); **spot returns at most 60**, even with
  `limit=1000` (1.3-7 s of BTCUSDT). The WS id is `i` (`websocket/parse.rs`).
- Hyperliquid: `POST /info {"type": "recentTrades", "coin"}` returns `[{coin, side B|A, px, sz,
  time ms, tid}]`, newest first, **exactly the last 10 trades**; `startTime` is ignored. The WS id
  is `tid`.

WS id == REST id, wire-verified 2026-09-21 (live pyo3 WS clients vs these endpoints, same interval):
Bybit linear BTCUSDT 999/999 and spot ETHUSDT 60/60, Hyperliquid 19/19, dYdX 3/3 -- same ids, and
price, size and aggressor equal on every one; so the dedup takes the union across sources exactly.
Hyperliquid's live `ts_event` alone differed (<= 128 ns, the adapter's f64 path, audit D-62); the
Hyperliquid client re-stamps it to the exact millisecond.

Known limit: Bybit's depth is the last 1000 linear trades (28-64 s of BTCUSDT in two samples) or
60 spot trades, and Hyperliquid's the last 10 trades: a longer outage is reported
`unrecoverable` with the uncovered seconds. Upgrade path: a second, independent trades-only
connection (`trade_feeds = 2`), which closes a one-sided outage without REST at all.

Known limit: dYdX is paged back no further than `floor_ns` (the caller's fetch time minus
`kernel.clocks.MAX_TS_INIT_SKEW_NS`, 5 minutes) and never beyond `_DYDX_MAX_PAGES`: the nightly
rebuild and the prune find trades by `ts_init` within that margin of `ts_event`, so an older
backfilled trade would be invisible to them. A dYdX outage longer than 5 minutes stays partly unrecovered, and says so.
Upgrade path: a backfill-span marker (like `archive_gaps`) that the rebuild and prune read to
widen their window.

Known limit: dYdX pages by `createdAt` (block time, shared by every trade of a block), so a block
with more than 1000 trades of one market makes a page with no progress; paging stops there and the
rest is reported `unrecoverable`. Upgrade path: page on `createdBeforeOrAtHeight` below the block.
"""

import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from kernel.venue_http import DYDX_NETWORKS
from kernel.venue_http import HttpJson
from kernel.venue_http import bybit_url
from kernel.venue_http import dydx_indexer_url
from kernel.venue_http import get_request
from kernel.venue_http import http_json
from kernel.venue_http import hyperliquid_info_url
from kernel.venue_http import post_json_request
from kernel.venues import bybit_category

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_DYDX_PAGE = 1000
_DYDX_MAX_PAGES = 20
_BYBIT_LIMIT = 1000
# A response this long may have been cut by the venue's depth: its oldest trade bounds coverage.
# Known limit: only the wire-verified categories (22.14); an `-INVERSE.BYBIT` id is refused, not
# guessed, until its recent-trade depth is verified against the venue (then add it here).
_BYBIT_FULL = {"linear": 1000, "spot": 60}
_HYPERLIQUID_FULL = 10
_MS_NS = 1_000_000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

_DYDX_SIDES = {"BUY": AggressorSide.BUYER, "SELL": AggressorSide.SELLER}
_BYBIT_SIDES = {"Buy": AggressorSide.BUYER, "Sell": AggressorSide.SELLER}
_HYPERLIQUID_SIDES = {"B": AggressorSide.BUYER, "A": AggressorSide.SELLER}


class BackfillError(Exception):
    """A venue response or value that cannot be turned into an exact `TradeTick`."""


@dataclass(frozen=True)
class Fetched:
    """
    One instrument's backfill fetch.

    `trades`: oldest first, `ts_event >= since_ns`. `reached_since`: the venue's history covered
    `since_ns` (oldest trade at or before it, or a response that was not cut by the venue's
    depth). `oldest_ns`: the oldest `ts_event` the venue returned, before the `since` filter.
    `rejected`: trades skipped for a value that is not exact (the instrument is then an error).
    """

    trades: list[TradeTick]
    reached_since: bool
    oldest_ns: int | None
    rejected: list[str] = field(default_factory=list)


# -- exact conversions ----------------------------------------------------------------------------


def exact_text(text: str, precision: int) -> str:
    """Return the decimal string at exactly `precision` places; `BackfillError` if that rounds."""
    try:
        value = Decimal(text)
        quantized = value.quantize(Decimal(1).scaleb(-precision))
    except (InvalidOperation, TypeError) as e:
        raise BackfillError(f"{text!r} is not a decimal at precision {precision}") from e
    if not value.is_finite() or quantized != value:
        raise BackfillError(f"{text!r} is not representable at precision {precision}")
    return format(quantized, "f")


def _price(text: str, instrument: Instrument) -> Price:
    price = Price.from_str(exact_text(text, instrument.price_precision))
    if price.precision != instrument.price_precision:
        raise BackfillError(f"price {text!r} parsed at precision {price.precision}")
    return price


def _size(text: str, instrument: Instrument) -> Quantity:
    exact = exact_text(text, instrument.size_precision)
    if Decimal(exact) <= 0:
        raise BackfillError(f"size {text!r} is not positive")
    size = Quantity.from_str(exact)
    if size.precision != instrument.size_precision:
        raise BackfillError(f"size {text!r} parsed at precision {size.precision}")
    return size


def iso_to_ns(text: str) -> int:
    """
    Convert an ISO-8601 UTC timestamp (dYdX's `createdAt`, millisecond precision) to integer
    nanoseconds: the value chrono's `timestamp_nanos_opt` gives the WS parser. Integer arithmetic.
    """
    try:
        moment = datetime.fromisoformat(text)
    except (ValueError, TypeError) as e:
        raise BackfillError(f"createdAt {text!r} is not ISO-8601") from e
    if moment.tzinfo is None:
        raise BackfillError(f"createdAt {text!r} has no timezone")
    return (moment - _EPOCH) // timedelta(microseconds=1) * 1000


def ms_to_ns(value: Any) -> int:
    """Convert a venue millisecond timestamp (JSON integer or integer string) to nanoseconds."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise BackfillError(f"time {value!r} is not an integer millisecond timestamp")
    try:
        return int(value) * _MS_NS
    except ValueError as e:
        raise BackfillError(f"time {value!r} is not an integer millisecond timestamp") from e


def _side(sides: dict[str, AggressorSide], value: Any) -> AggressorSide:
    side = sides.get(value) if isinstance(value, str) else None
    if side is None:
        raise BackfillError(f"unknown taker side {value!r}")
    return side


def _tick(
    instrument: Instrument,
    values: tuple[str, str, AggressorSide, str, int],
    ts_init: int,
) -> TradeTick:
    price, size, side, trade_id, ts_event = values
    return TradeTick(
        instrument.id,
        _price(price, instrument),
        _size(size, instrument),
        side,
        TradeId(trade_id),
        ts_event,
        ts_init,
    )


# -- per-venue rows (pure; tested on recorded responses) ------------------------------------------


def _dydx_rows(payload: Any) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("trades"), list):
        raise BackfillError(f"dydx trades response has no trade list: {str(payload)[:200]}")
    return payload["trades"]


def _dydx_time(row: dict) -> int:
    return iso_to_ns(row["createdAt"])


def _dydx_trade(row: dict, instrument: Instrument, ts_init: int) -> TradeTick:
    side = _side(_DYDX_SIDES, row["side"])
    values = (row["price"], row["size"], side, str(row["id"]), _dydx_time(row))
    return _tick(instrument, values, ts_init)


def _bybit_rows(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        raise BackfillError(f"bybit recent-trade response is not an object: {str(payload)[:200]}")
    if payload.get("retCode") != 0:
        raise BackfillError(f"bybit retCode {payload.get('retCode')}: {payload.get('retMsg')}")
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("list"), list):
        raise BackfillError(f"bybit recent-trade response has no trade list: {str(payload)[:200]}")
    return result["list"]


def _bybit_time(row: dict) -> int:
    return ms_to_ns(row["time"])


def _bybit_trade(row: dict, instrument: Instrument, ts_init: int) -> TradeTick:
    side = _side(_BYBIT_SIDES, row["side"])
    values = (row["price"], row["size"], side, str(row["execId"]), _bybit_time(row))
    return _tick(instrument, values, ts_init)


def _hyperliquid_rows(payload: Any) -> list[dict]:
    if not isinstance(payload, list):
        raise BackfillError(f"hyperliquid recentTrades is not a list: {str(payload)[:200]}")
    return payload


def _hyperliquid_time(row: dict) -> int:
    return ms_to_ns(row["time"])


def _hyperliquid_trade(row: dict, instrument: Instrument, ts_init: int) -> TradeTick:
    side = _side(_HYPERLIQUID_SIDES, row["side"])
    values = (row["px"], row["sz"], side, str(row["tid"]), _hyperliquid_time(row))
    return _tick(instrument, values, ts_init)


def parse_dydx_trades(payload: Any, instrument: Instrument, ts_init: int) -> list[TradeTick]:
    """Parse the dYdX indexer's trades, newest first as sent; an inexact value raises."""
    return [_dydx_trade(row, instrument, ts_init) for row in _dydx_rows(payload)]


def parse_bybit_trades(payload: Any, instrument: Instrument, ts_init: int) -> list[TradeTick]:
    """Parse Bybit's `recent-trade`, newest first as sent; `retCode != 0` or inexactness raises."""
    return [_bybit_trade(row, instrument, ts_init) for row in _bybit_rows(payload)]


def parse_hyperliquid_trades(payload: Any, instrument: Instrument, ts_init: int) -> list[TradeTick]:
    """Parse Hyperliquid's `recentTrades`, newest first as sent; an inexact value raises."""
    return [_hyperliquid_trade(row, instrument, ts_init) for row in _hyperliquid_rows(payload)]


# -- fetch ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Venue:
    time_ns: Callable[[dict], int]
    trade: Callable[[dict, Instrument, int], TradeTick]


def _time_or_none(venue: _Venue, row: Any) -> int | None:
    """Return the row's venue time, or None for a malformed row (rejected alone, not the fetch)."""
    try:
        return venue.time_ns(row)
    except (BackfillError, KeyError, TypeError, ValueError):
        return None


_DYDX = _Venue(_dydx_time, _dydx_trade)
_BYBIT = _Venue(_bybit_time, _bybit_trade)
_HYPERLIQUID = _Venue(_hyperliquid_time, _hyperliquid_trade)


def _get(url: str, http: HttpJson) -> Any:
    return http(get_request(url))


def _dydx_page_url(instrument: Instrument, environment: str, before: str | None) -> str:
    ticker = urllib.parse.quote(instrument.raw_symbol.value)
    url = dydx_indexer_url(
        DYDX_NETWORKS[environment], f"/v4/trades/perpetualMarket/{ticker}?limit={_DYDX_PAGE}"
    )
    if before is not None:
        url += f"&createdBeforeOrAt={urllib.parse.quote(before)}"
    return url


def _fetch_dydx(
    instrument: Instrument, since_ns: int, floor_ns: int, environment: str, http: HttpJson
) -> tuple[list[dict], bool]:
    """
    Page backwards with `createdBeforeOrAt` = the oldest `createdAt` seen (inclusive, so pages
    overlap and are deduplicated by id). Stops, with `reached`: at a short page (history
    exhausted) or once the oldest trade is at or before `since_ns`; without: once it is older
    than `floor_ns`, when a full page makes no progress, or after `_DYDX_MAX_PAGES`.
    """
    rows: dict[str, dict] = {}
    before: str | None = None
    for _ in range(_DYDX_MAX_PAGES):
        page = _dydx_rows(_get(_dydx_page_url(instrument, environment, before), http))
        for row in page:
            key = str(row.get("id")) if isinstance(row, dict) else repr(row)
            rows.setdefault(key, row)
        if len(page) < _DYDX_PAGE:
            return list(rows.values()), True
        timed = [(t, row) for row in page if (t := _time_or_none(_DYDX, row)) is not None]
        if not timed:
            return list(rows.values()), False
        oldest_ns, oldest = min(timed, key=lambda pair: pair[0])
        if oldest_ns <= since_ns:
            return list(rows.values()), True
        if oldest_ns < floor_ns or oldest["createdAt"] == before:
            return list(rows.values()), False
        before = oldest["createdAt"]
    return list(rows.values()), False


def _fetch_bybit(
    instrument: Instrument, environment: str, http: HttpJson
) -> tuple[list[dict], bool]:
    category = bybit_category(instrument.id.value)
    if category not in _BYBIT_FULL:
        raise BackfillError(
            f"{instrument.id}: Bybit {category} trade backfill is not wire-verified"
        )
    url = bybit_url(
        environment,
        f"/v5/market/recent-trade?category={category}"
        f"&symbol={urllib.parse.quote(instrument.raw_symbol.value)}&limit={_BYBIT_LIMIT}",
    )
    rows = _bybit_rows(_get(url, http))
    return rows, len(rows) < _BYBIT_FULL[category]


def _fetch_hyperliquid(
    instrument: Instrument, environment: str, http: HttpJson
) -> tuple[list[dict], bool]:
    body = {"type": "recentTrades", "coin": instrument.raw_symbol.value}
    request = post_json_request(hyperliquid_info_url(environment), body)
    rows = _hyperliquid_rows(http(request))
    return rows, len(rows) < _HYPERLIQUID_FULL


def _collect(
    rows: list[dict], venue: _Venue, instrument: Instrument, since_ns: int, ts_init: int
) -> tuple[list[TradeTick], list[str], int | None]:
    """Build the trades at or after `since_ns`, oldest first; a malformed or inexact one is rejected."""
    trades: list[TradeTick] = []
    rejected: list[str] = []
    timed: list[tuple[int, dict]] = []
    for row in reversed(rows):  # reversed: oldest first on ties
        ts_event = _time_or_none(venue, row)
        if ts_event is None:
            rejected.append(f"{str(row)[:120]}: no valid trade time")
        else:
            timed.append((ts_event, row))
    for ts_event, row in sorted(timed, key=lambda pair: pair[0]):
        if ts_event < since_ns:
            continue
        try:
            trades.append(venue.trade(row, instrument, ts_init))
        except (BackfillError, KeyError, TypeError, ValueError) as e:
            rejected.append(f"{str(row)[:120]}: {e}")
    return trades, rejected, min((t for t, _ in timed), default=None)


def fetch_trades(
    instrument: Instrument,
    since_ns: int,
    floor_ns: int,
    environment: str,
    ts_init: int,
    http: HttpJson = http_json,
) -> Fetched:
    """Return the venue's trades of `instrument` since `since_ns` (see the module docstring)."""
    venue_name = instrument.id.venue.value
    if venue_name == "DYDX":
        rows, reached = _fetch_dydx(instrument, since_ns, floor_ns, environment, http)
        venue = _DYDX
    elif venue_name == "BYBIT":
        rows, reached = _fetch_bybit(instrument, environment, http)
        venue = _BYBIT
    elif venue_name == "HYPERLIQUID":
        rows, reached = _fetch_hyperliquid(instrument, environment, http)
        venue = _HYPERLIQUID
    else:
        raise BackfillError(f"{instrument.id}: no trade backfill for venue {venue_name}")
    trades, rejected, oldest_ns = _collect(rows, venue, instrument, since_ns, ts_init)
    reached = reached or (oldest_ns is not None and oldest_ns <= since_ns)
    return Fetched(trades, reached, oldest_ns, rejected)
