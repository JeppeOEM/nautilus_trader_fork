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
r"""
Reconcile a closed day's 1 m bars, bar for bar, against the venue's own klines (story 22.13, D-51).

Usage:
    python -m collector_core.compare_klines --venue BYBIT --day 2026-09-20 \\
        --catalog /app/catalog --db /app/candles_dir/candles_bybit.db \\
        [--instrument BTCUSDT-LINEAR.BYBIT ...] [--environment mainnet|testnet] \\
        [--kline-source venue|catalog]

Ours: `candle_store.window(db, iid, 60, ...)` -- the bars `build_candles --day D` folded from the
rebuilt seconds. Theirs: the venue's 1 m klines, fetched with stdlib `urllib` and parsed from the
venue's decimal strings with `Decimal(text).scaleb(precision)` -- never through float (the pyo3
kline paths parse through `f64`, audit D-52, which cannot prove raw-unit equality). Every value is
compared as an integer count of the instrument's smallest unit (`price_precision` for OHLC,
`size_precision` for volume): exact, and there is no tolerance parameter (DATA-02 -- a mismatch
is root-caused, never tolerated). A minute either side has and the other lacks is a mismatch. A
venue kline with zero volume (dYdX/Bybit emit no-trade minutes) is dropped: no trade either side.

Bybit defines a kline differently: it is seeded with the previous kline's close, so its `open` is
that close (not the minute's first trade) and its `high`/`low` include it. Wire evidence, BTCUSDT
2026-09-20 (fetched 2026-09-21): open == previous close in 999/999 minutes, linear and spot alike;
the open never lies outside [low, high] and equals high or low in 17-24 % of minutes. Hyperliquid
(782/1439) and dYdX (32/506 traded minutes) show only natural ties: their open is the first trade.
So for Bybit our bars are put in Bybit's definition before comparing (`seed_with_previous_close`:
open = our previous traded minute's close, high/low widened to include it) -- an exact
transformation of our own integers, not a tolerance. A day whose previous close we lack (the first
collected day) keeps our first-trade open for its first minute, and mismatches honestly. The day's
first minute is seeded from the store's last traded close before midnight, which is provisional
if the previous day has not been rebuilt yet. A seed also chains: one missing or wrong minute
shifts the *next* traded minute's seed, so mismatches often come in pairs and the second is a
consequence of the first -- its ledger message says so (`seed from a mismatched minute`).

`--kline-source catalog` compares against story 22.9's backfilled `<iid>-1-MINUTE-LAST-EXTERNAL`
bars instead (close-stamped, so a bar opens at `ts_event - 60 s`). Those values went through the
adapters' `f64` parse (D-52), so it only reports and ledgers: it never writes `verified_days`
and so can never release trades to the prune.

Instruments: `--instrument`, else every id of `--venue` with snapshot or trade files on the day.
The venue symbol is the catalog instrument definition's `raw_symbol` (dYdX `BTC-USD`, Bybit
`BTCUSDT`, Hyperliquid `BTC`); Bybit's category comes from the id's `-LINEAR`/`-SPOT` suffix.

Per mismatch: `error_ledger.record("reconcile.kline_mismatch", "{iid} {minute} vol {ours}/{theirs}
ohlc {ours}/{theirs}")`, `-` for a missing side. Per instrument-day: an upsert into
`verified_days` (pass/fail + mismatch count), which gates trade retention in `prune_catalog`. A
per-instrument error -- a fetch error, a missing instrument definition, a value not representable
at the instrument's precision, a venue with no history for the day (no traded kline while we have
traded bars, or a Hyperliquid `candleSnapshot` starting after our first traded minute: outside its
retention, D-53) -- is `reconcile.error` and writes no `verified_days` row (the day stays
unverified). Prints a line per instrument and the venue's pass rate (instruments and minutes).
Exit 0 all passed, 2 findings (any mismatch or per-instrument error, all ledgered), 1 a run-level
failure (bad arguments, an open day, a missing catalog or candle store).
"""

import argparse
import glob
import http.client
import json
import logging
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from decimal import ROUND_HALF_EVEN
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any

from ml_signals import candle_store
from ml_signals import error_ledger
from ml_signals.catalog_stats import _stamp_to_ns

from collector_core.build_candles import _parse_date_ns
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.core.nautilus_pyo3 import get_dydx_http_url  # type: ignore[attr-defined]
from nautilus_trader.model.data import Bar
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

VENUES = ("DYDX", "BYBIT", "HYPERLIQUID")
_MINUTE_MS = 60_000
_DAY_MS = 86_400_000
_MS_NS = 1_000_000
# Candle-store o/h/l/c/v are REAL: a float is accepted as a whole number of units only when it is
# within this much of one. A representation guard, not a comparison tolerance -- two values a unit
# apart can never compare equal.
_FLOAT_RESIDUAL = Decimal("0.001")
_BYBIT_URLS = {"mainnet": "https://api.bybit.com", "testnet": "https://api-testnet.bybit.com"}
_HYPERLIQUID_URLS = {
    "mainnet": "https://api.hyperliquid.xyz/info",
    "testnet": "https://api.hyperliquid-testnet.xyz/info",
}
_DYDX_NETWORKS = {"mainnet": DydxNetwork.MAINNET, "testnet": DydxNetwork.TESTNET}
_DYDX_PAGE = 1000
_BYBIT_PAGE = 1000
_USER_AGENT = "nautilus-troll-reconcile/1.0"  # dYdX's indexer rejects urllib's default (403)
_SNAPSHOT_DIR = "custom_dydx_second_snapshot"


class KlineError(Exception):
    """A value or response that cannot be compared exactly; the instrument-day stays unverified."""


@dataclass(frozen=True)
class Kline:
    """One 1 m bar; prices in units of `10**-price_precision`, volume of `10**-size_precision`."""

    t_ms: int  # minute open
    o: int
    h: int
    low: int
    c: int
    v: int


HttpJson = Callable[[urllib.request.Request], Any]
KlineFetch = Callable[[Instrument, int], list[Kline]]


# -- exact conversions ----------------------------------------------------------------------------


def units(text: str, precision: int, what: str) -> int:
    """Convert a venue decimal string to a count of `10**-precision`; non-integral is an error."""
    try:
        scaled = Decimal(text).scaleb(precision)
    except InvalidOperation as e:
        raise KlineError(f"{what} {text!r} is not a decimal") from e
    if not scaled.is_finite() or scaled != scaled.to_integral_value():
        raise KlineError(f"{what} {text!r} is not representable at precision {precision}")
    return int(scaled)


def float_units(value: float, precision: int, what: str) -> int:
    """
    Convert a candle-store REAL to an integer count of `10**-precision` (round half-even).

    Known limit: exact while the value stays far below 2**53 units (a bar's volume summed from
    float seconds keeps ~15 significant digits); a residual of `_FLOAT_RESIDUAL` unit or more is
    refused as an error, never rounded into a pass. Upgrade path: an integer volume column in the
    candle store.
    """
    scaled = Decimal(value).scaleb(precision)
    rounded = scaled.to_integral_value(rounding=ROUND_HALF_EVEN)
    if abs(scaled - rounded) >= _FLOAT_RESIDUAL:
        raise KlineError(f"{what} {value!r} is not a whole number of 1e-{precision} units")
    return int(rounded)


def _in_day(klines: list[Kline], day_ms: int) -> list[Kline]:
    """Traded minutes of the day, oldest first (a venue's no-trade kline carries volume 0)."""
    return sorted(
        (k for k in klines if day_ms <= k.t_ms < day_ms + _DAY_MS and k.v != 0),
        key=lambda k: k.t_ms,
    )


# -- venue parsers (pure; tested on recorded responses) ------------------------------------------


def _kline(t_ms: int, ohlcv: tuple[str, str, str, str, str], price_p: int, size_p: int) -> Kline:
    o, h, low, c, v = ohlcv
    return Kline(
        t_ms,
        units(o, price_p, "open"),
        units(h, price_p, "high"),
        units(low, price_p, "low"),
        units(c, price_p, "close"),
        units(v, size_p, "volume"),
    )


def _iso_ms(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp() * 1000)


def parse_dydx_candles(payload: dict, price_p: int, size_p: int) -> list[Kline]:
    """Parse the dYdX indexer's `GET /v4/candles/perpetualMarkets/{ticker}` (any order)."""
    return [
        _kline(
            _iso_ms(c["startedAt"]),
            (c["open"], c["high"], c["low"], c["close"], c["baseTokenVolume"]),
            price_p,
            size_p,
        )
        for c in payload["candles"]
    ]


def parse_bybit_klines(payload: dict, price_p: int, size_p: int) -> list[Kline]:
    """Parse Bybit's `GET /v5/market/kline`; `retCode != 0` is an error."""
    if payload.get("retCode") != 0:
        raise KlineError(f"bybit retCode {payload.get('retCode')}: {payload.get('retMsg')}")
    return [
        _kline(int(row[0]), (row[1], row[2], row[3], row[4], row[5]), price_p, size_p)
        for row in payload["result"]["list"]
    ]


def parse_hyperliquid_candles(payload: list, price_p: int, size_p: int) -> list[Kline]:
    """Parse Hyperliquid's `POST /info {"type": "candleSnapshot"}`."""
    return [
        _kline(int(c["t"]), (c["o"], c["h"], c["l"], c["c"], c["v"]), price_p, size_p)
        for c in payload
    ]


# -- venue fetchers (paged; wire bounds verified live 2026-09-21 on 2026-09-20 data) --------------


def http_json(request: urllib.request.Request) -> Any:
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (fixed https URLs)
        return json.load(response)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _fetch_dydx(inst: Instrument, day_ms: int, environment: str, http: HttpJson) -> list[Kline]:
    """
    Fetch dYdX candles, paging backwards with `toISO` = the oldest `startedAt` seen.

    Verified: `fromISO` inclusive, `toISO` exclusive, newest first, `limit` at most 1000.
    """
    base = get_dydx_http_url(_DYDX_NETWORKS[environment])
    ticker = urllib.parse.quote(inst.raw_symbol.value)
    out: list[Kline] = []
    to_ms = day_ms + _DAY_MS
    while True:
        url = (
            f"{base}/v4/candles/perpetualMarkets/{ticker}?resolution=1MIN"
            f"&fromISO={_iso(day_ms)}&toISO={_iso(to_ms)}&limit={_DYDX_PAGE}"
        )
        payload = http(urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}))  # noqa: S310
        page = parse_dydx_candles(payload, inst.price_precision, inst.size_precision)
        out += page
        if len(page) < _DYDX_PAGE or min(k.t_ms for k in page) <= day_ms:
            return out
        to_ms = min(k.t_ms for k in page)


def _bybit_category(iid: str) -> str:
    if iid.endswith("-LINEAR.BYBIT"):
        return "linear"
    if iid.endswith("-SPOT.BYBIT"):
        return "spot"
    raise KlineError(f"{iid}: no Bybit kline category for this id suffix")


def _fetch_bybit(inst: Instrument, day_ms: int, environment: str, http: HttpJson) -> list[Kline]:
    """
    Fetch Bybit klines, paging backwards with `end` = the oldest start seen - 1 ms.

    Verified: `start` and `end` both inclusive on the kline's start time, newest first, `limit`
    up to 1000.
    """
    category = _bybit_category(inst.id.value)
    out: list[Kline] = []
    end_ms = day_ms + _DAY_MS - 1
    while True:
        url = (
            f"{_BYBIT_URLS[environment]}/v5/market/kline?category={category}"
            f"&symbol={urllib.parse.quote(inst.raw_symbol.value)}&interval=1"
            f"&start={day_ms}&end={end_ms}&limit={_BYBIT_PAGE}"
        )
        payload = http(urllib.request.Request(url, headers={"User-Agent": _USER_AGENT}))  # noqa: S310
        page = parse_bybit_klines(payload, inst.price_precision, inst.size_precision)
        out += page
        if len(page) < _BYBIT_PAGE or min(k.t_ms for k in page) <= day_ms:
            return out
        end_ms = min(k.t_ms for k in page) - 1


def _fetch_hyperliquid(
    inst: Instrument, day_ms: int, environment: str, http: HttpJson
) -> list[Kline]:
    """
    Fetch Hyperliquid candles, paging forward from the newest open seen.

    Verified: `startTime`/`endTime` both inclusive on the candle's open time, oldest first; a
    whole day (1440) came back in one response, and the forward paging still reads everything
    should the server cap a response below a day.
    """
    out: list[Kline] = []
    start_ms, end_ms = day_ms, day_ms + _DAY_MS - 1
    while start_ms <= end_ms:
        body = {
            "type": "candleSnapshot",
            "req": {
                "coin": inst.raw_symbol.value,
                "interval": "1m",
                "startTime": start_ms,
                "endTime": end_ms,
            },
        }
        request = urllib.request.Request(  # noqa: S310
            _HYPERLIQUID_URLS[environment],
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        )
        page = parse_hyperliquid_candles(http(request), inst.price_precision, inst.size_precision)
        if not page:
            return out
        out += page
        start_ms = max(k.t_ms for k in page) + _MINUTE_MS
    return out


_FETCHERS = {"DYDX": _fetch_dydx, "BYBIT": _fetch_bybit, "HYPERLIQUID": _fetch_hyperliquid}


def fetch_klines(
    venue: str, inst: Instrument, day_ms: int, environment: str, http: HttpJson = http_json
) -> list[Kline]:
    """Return the venue's traded 1 m klines of the UTC day starting at `day_ms`, oldest first."""
    fetched = _FETCHERS[venue](inst, day_ms, environment, http)
    unique = {k.t_ms: k for k in fetched}
    if len(unique) != len(fetched):
        raise KlineError(f"{inst.id}: venue returned the same minute twice")
    return _in_day(list(unique.values()), day_ms)


def catalog_klines(catalog: ParquetDataCatalog, inst: Instrument, day_ms: int) -> list[Kline]:
    """
    Story 22.9's backfilled `-1-MINUTE-LAST-EXTERNAL` bars of the day (close-stamped: the bar
    opens at `ts_event - 60 s`). Their values went through the adapters' f64 parse (D-52).
    """
    bar_type = f"{inst.id.value}-1-MINUTE-LAST-EXTERNAL"
    start_ns = (day_ms + _MINUTE_MS) * _MS_NS
    bars: list[Bar] = catalog.bars(
        bar_types=[bar_type], start=start_ns, end=start_ns + _DAY_MS * _MS_NS
    )
    return _in_day(
        [
            _kline(
                b.ts_event // _MS_NS - _MINUTE_MS,
                (str(b.open), str(b.high), str(b.low), str(b.close), str(b.volume)),
                inst.price_precision,
                inst.size_precision,
            )
            for b in bars
        ],
        day_ms,
    )


# -- ours and the comparison ----------------------------------------------------------------------


def our_klines(db: Any, iid: str, day_ms: int, price_p: int, size_p: int) -> list[Kline]:
    """Return the candle store's traded 1 m bars of the day, as exact integer units."""
    return _in_day(
        [
            Kline(
                b["t"],
                float_units(b["o"], price_p, "open"),
                float_units(b["h"], price_p, "high"),
                float_units(b["l"], price_p, "low"),
                float_units(b["c"], price_p, "close"),
                float_units(b["v"], size_p, "volume"),
            )
            for b in candle_store.window(db, iid, 60, day_ms + _DAY_MS, 1440)
        ],
        day_ms,
    )


def seed_with_previous_close(ours: list[Kline], previous_close: int | None) -> list[Kline]:
    """
    Put our traded bars (oldest first) in Bybit's kline definition: each is seeded with the previous
    kline's close -- the last traded close, since a no-trade minute carries it forward.
    """
    seeded, prev = [], previous_close
    for k in ours:
        if prev is None:
            seeded.append(k)
        else:
            seeded.append(Kline(k.t_ms, prev, max(k.h, prev), min(k.low, prev), k.c, k.v))
        prev = k.c
    return seeded


def _close_before(db: Any, iid: str, day_ms: int, price_p: int) -> int | None:
    """Our last traded 1 m close before the day (None when the store has none)."""
    before = candle_store.window(db, iid, 60, day_ms, 1)
    return float_units(before[0]["c"], price_p, "previous close") if before else None


def _ours_in_venue_definition(db: Any, iid: str, day_ms: int, inst: Instrument) -> list[Kline]:
    ours = our_klines(db, iid, day_ms, inst.price_precision, inst.size_precision)
    if not iid.endswith(".BYBIT"):
        return ours
    return seed_with_previous_close(ours, _close_before(db, iid, day_ms, inst.price_precision))


def _dec(value: int, precision: int) -> str:
    return str(Decimal(value).scaleb(-precision))


def mismatch_message(
    iid: str, t_ms: int, ours: Kline | None, theirs: Kline | None, price_p: int, size_p: int
) -> str:
    """Format one minute's ledger detail: `{iid} {minute} vol {ours}/{theirs} ohlc {o}/{t}`."""

    def vol(k: Kline | None) -> str:
        return "-" if k is None else _dec(k.v, size_p)

    def ohlc(k: Kline | None) -> str:
        return "-" if k is None else ",".join(_dec(x, price_p) for x in (k.o, k.h, k.low, k.c))

    minute = datetime.fromtimestamp(t_ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%MZ")
    return f"{iid} {minute} vol {vol(ours)}/{vol(theirs)} ohlc {ohlc(ours)}/{ohlc(theirs)}"


@dataclass
class InstrumentResult:
    """One instrument-day's verdict."""

    iid: str
    status: str  # "pass" | "fail" | "error"
    minutes: int = 0  # union of minutes either side traded in
    mismatches: list[str] = field(default_factory=list)


def compare(
    iid: str, ours: list[Kline], theirs: list[Kline], inst: Instrument, seeded: bool = False
) -> InstrumentResult:
    """
    Bar-for-bar, exact: every minute of the union must be present and equal on both sides. With
    `seeded` (Bybit), a mismatch right after a mismatched minute is marked as a seed consequence.
    """
    mine, venue = {k.t_ms: k for k in ours}, {k.t_ms: k for k in theirs}
    result = InstrumentResult(iid, "pass", minutes=len(mine.keys() | venue.keys()))
    previous_mismatched = False
    for t_ms in sorted(mine.keys() | venue.keys()):
        a, b = mine.get(t_ms), venue.get(t_ms)
        if a != b:
            message = mismatch_message(iid, t_ms, a, b, inst.price_precision, inst.size_precision)
            if seeded and previous_mismatched:
                message += " (seed from a mismatched minute)"
            result.mismatches.append(message)
        previous_mismatched = a != b
    if result.mismatches:
        result.status = "fail"
    return result


def _load_instrument(catalog: ParquetDataCatalog, iid: str) -> Instrument:
    found = catalog.instruments(instrument_ids=[iid])
    if not found:
        raise KlineError(f"{iid}: no instrument definition in the catalog")
    return found[0]


def _check_venue_history(iid: str, ours: list[Kline], theirs: list[Kline]) -> None:
    """
    Refuse (as a per-instrument error, not a verdict) a day the venue cannot serve: no traded
    kline at all while we traded, or a Hyperliquid `candleSnapshot` that starts after our first
    traded minute -- the day lies (partly) outside its ~5000-candle retention (D-53).
    """
    if ours and not theirs:
        raise KlineError(
            f"venue has no history for this day/range: no traded kline, ours has {len(ours)}"
        )
    if iid.endswith(".HYPERLIQUID") and ours and theirs and theirs[0].t_ms > ours[0].t_ms:
        raise KlineError(
            "venue has no history for this day/range: candleSnapshot starts at "
            f"{theirs[0].t_ms}, after our first traded minute {ours[0].t_ms} (retention, D-53)"
        )


def _record_verdict(db_path: str, iid: str, day: str, result: InstrumentResult) -> None:
    db = candle_store.connect_rw(db_path)
    try:
        candle_store.mark_verified(
            db, iid, day, result.status, len(result.mismatches), int(time.time() * 1000)
        )
    finally:
        db.close()


def reconcile_instrument(
    db_path: str,
    catalog: ParquetDataCatalog,
    iid: str,
    day_ms: int,
    fetch: KlineFetch,
    record_verdict: bool = True,
) -> InstrumentResult:
    """
    Compare one instrument-day, ledger every mismatch and (unless `record_verdict` is off, as for
    the f64 catalog klines) record the verdict. Any per-instrument failure is an "error" result.
    """
    day = _day_text(day_ms)
    try:
        inst = _load_instrument(catalog, iid)
        theirs = _in_day(fetch(inst, day_ms), day_ms)
        with candle_store.connect_ro(db_path) as ro:
            if ro is None:
                raise KlineError(f"candle store {db_path} does not exist")
            ours = _ours_in_venue_definition(ro, iid, day_ms, inst)
        _check_venue_history(iid, ours, theirs)
        result = compare(iid, ours, theirs, inst, seeded=iid.endswith(".BYBIT"))
        for message in result.mismatches:
            error_ledger.record("reconcile.kline_mismatch", message)
        if record_verdict:
            _record_verdict(db_path, iid, day, result)
    except (  # urllib's errors are OSErrors; sqlite3 from `mark_verified` on a locked store
        KlineError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        http.client.HTTPException,
        sqlite3.OperationalError,
    ) as e:
        error_ledger.record("reconcile.error", f"{iid} {day}: {e!r}; stays unverified", exc=e)
        return InstrumentResult(iid, "error")
    return result


def _day_text(day_ms: int) -> str:
    return datetime.fromtimestamp(day_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def instruments_on_day(catalog_path: str, venue: str, day_ms: int) -> list[str]:
    """Ids of `venue` with a snapshot or trade file overlapping the day (file names only)."""
    lo, hi = day_ms * _MS_NS, (day_ms + _DAY_MS) * _MS_NS - 1
    found: set[str] = set()
    for data_type in (_SNAPSHOT_DIR, "trade_tick"):
        pattern = os.path.join(catalog_path, "data", data_type, f"*.{venue}", "*.parquet")
        for path in glob.glob(pattern):
            first, _, last = Path(path).stem.partition("_")
            if _stamp_to_ns(first) <= hi and _stamp_to_ns(last) >= lo:
                found.add(Path(path).parent.name)
    return sorted(found)


def summary_line(venue: str, day: str, results: list[InstrumentResult]) -> str:
    """Format the venue's pass rate over instruments and over minutes (errors counted apart)."""
    judged = [r for r in results if r.status != "error"]
    passed = sum(r.status == "pass" for r in judged)
    minutes = sum(r.minutes for r in judged)
    matched = minutes - sum(len(r.mismatches) for r in judged)

    def pct(a: int, b: int) -> str:
        return f"{100 * a / b:.1f}%" if b else "n/a"

    return (
        f"compare_klines {venue} {day}: instruments pass {passed}/{len(judged)} "
        f"({pct(passed, len(judged))}), minutes matched {matched}/{minutes} "
        f"({pct(matched, minutes)}), errors {len(results) - len(judged)}"
    )


def run(
    db_path: str,
    catalog_path: str,
    venue: str,
    day_ms: int,
    iids: list[str],
    fetch: KlineFetch,
    record_verdict: bool = True,
) -> int:
    """
    Reconcile every instrument; returns the exit code: 0 all passed, 2 findings (mismatches or
    per-instrument errors), 1 when the candle store or catalog is unusable (nothing compared).
    """
    if not Path(db_path).exists() or not Path(catalog_path).is_dir():
        error_ledger.record(
            "reconcile.error", f"candle store {db_path} or catalog {catalog_path} missing"
        )
        return 1
    catalog = ParquetDataCatalog(catalog_path)
    day = _day_text(day_ms)
    results = []
    for iid in iids:
        result = reconcile_instrument(db_path, catalog, iid, day_ms, fetch, record_verdict)
        results.append(result)
        logger.info(
            "%s %s: %s -- minutes %d, mismatched %d",
            iid,
            day,
            result.status,
            result.minutes,
            len(result.mismatches),
        )
    logger.info("%s", summary_line(venue, day, results))
    return 2 if any(r.status != "pass" for r in results) else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the exit code (0 pass, 2 findings, 1 run-level failure)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--day", required=True, help="YYYY-MM-DD (UTC), a closed day")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--db", required=True, help="candles_<venue>.db")
    parser.add_argument("--instrument", action="append", help="repeatable; default: all on D")
    parser.add_argument("--environment", default="mainnet", choices=("mainnet", "testnet"))
    parser.add_argument("--kline-source", default="venue", choices=("venue", "catalog"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    day_ms = _parse_date_ns(args.day) // _MS_NS
    if day_ms + _DAY_MS > time.time() * 1000:
        error_ledger.record("reconcile.error", f"{args.day} is not a closed UTC day")
        return 1
    iids = args.instrument or instruments_on_day(args.catalog, args.venue, day_ms)
    wrong = [i for i in iids if not i.endswith(f".{args.venue}")]
    if wrong:
        parser.error(f"--instrument ids not of venue {args.venue}: {wrong}")
    catalog = ParquetDataCatalog(args.catalog)

    def fetch(inst: Instrument, day: int) -> list[Kline]:
        if args.kline_source == "catalog":
            return catalog_klines(catalog, inst, day)
        return fetch_klines(args.venue, inst, day, args.environment)

    if args.kline_source == "catalog":
        logger.info("kline source catalog (D-52 f64 bars): report only, verified_days untouched")
    return run(args.db, args.catalog, args.venue, day_ms, iids, fetch, args.kline_source == "venue")


if __name__ == "__main__":
    raise SystemExit(main())
