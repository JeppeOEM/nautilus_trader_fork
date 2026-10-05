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
The exact kline reconciliation (story 22.13, D-51): our 1 m bars against the venue's, bar for bar.

Every value is compared as an integer count of the instrument's smallest unit (`price_precision`
for OHLC, `size_precision` for volume): exact, and there is no tolerance parameter (DATA-02 -- a
mismatch is root-caused, never tolerated). A minute either side has and the other lacks is a
mismatch. A venue kline with zero volume (dYdX/Bybit emit no-trade minutes) is dropped: no trade
either side. Pure: the venue fetchers (`archive.infrastructure.klines_*`) and the candle-store read
(`archive.application.reconcile_day`) hand these functions integers only.
"""

from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from decimal import ROUND_HALF_EVEN
from decimal import Decimal
from decimal import InvalidOperation

from nautilus_trader.model.instruments import Instrument


VENUES = ("DYDX", "BYBIT", "HYPERLIQUID")
MINUTE_MS = 60_000
DAY_MS = 86_400_000
# Candle-store o/h/l/c/v are REAL: a float is accepted as a whole number of units only when it is
# within this much of one. A representation guard, not a comparison tolerance -- two values a unit
# apart can never compare equal.
_FLOAT_RESIDUAL = Decimal("0.001")


class KlineError(Exception):
    """A value or response that cannot be compared exactly; the instrument-day stays unverified."""


# The most pages one instrument-day's kline fetch may take: every legitimate non-final page moves
# the paging cursor past at least one of the day's 1440 minutes, so a fetch needing more is a
# venue (or adapter) that stopped advancing -- refused, never looped on forever.
MAX_KLINE_PAGES = DAY_MS // MINUTE_MS


def next_kline_cursor(
    iid: str,
    pages: int,
    cursor: int,
    proposed: int,
    *,
    backwards: bool,
    max_pages: int = MAX_KLINE_PAGES,
) -> int:
    """
    Return `proposed` as the cursor of the next page after `pages` pages were fetched; raise
    `KlineError` when it does not move strictly in the paging direction (a full page repeating the
    same window would be fetched forever) or when `max_pages` pages are already spent (a venue
    with a known page size passes the tighter cap it implies).
    """
    advanced = proposed < cursor if backwards else proposed > cursor
    if not advanced:
        direction = "back" if backwards else "forward"
        raise KlineError(
            f"{iid}: kline paging did not move {direction} (cursor {cursor} -> {proposed})"
        )
    if pages >= max_pages:
        raise KlineError(f"{iid}: kline paging exceeded {max_pages} pages for one day")
    return proposed


@dataclass(frozen=True)
class Kline:
    """One 1 m bar; prices in units of `10**-price_precision`, volume of `10**-size_precision`."""

    t_ms: int  # minute open
    o: int
    h: int
    low: int
    c: int
    v: int


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


def kline_from_text(
    t_ms: int, ohlcv: tuple[str, str, str, str, str], price_p: int, size_p: int
) -> Kline:
    """Build one kline from the venue's decimal strings, exactly (`units`)."""
    o, h, low, c, v = ohlcv
    return Kline(
        t_ms,
        units(o, price_p, "open"),
        units(h, price_p, "high"),
        units(low, price_p, "low"),
        units(c, price_p, "close"),
        units(v, size_p, "volume"),
    )


def traded_in_day(klines: list[Kline], day_ms: int) -> list[Kline]:
    """Traded minutes of the day, oldest first (a venue's no-trade kline carries volume 0)."""
    return sorted(
        (k for k in klines if day_ms <= k.t_ms < day_ms + DAY_MS and k.v != 0),
        key=lambda k: k.t_ms,
    )


def unique_traded_in_day(fetched: list[Kline], iid: str, day_ms: int) -> list[Kline]:
    """Return the venue's traded minutes of the day, oldest first; refuse a repeated minute."""
    unique = {k.t_ms: k for k in fetched}
    if len(unique) != len(fetched):
        raise KlineError(f"{iid}: venue returned the same minute twice")
    return traded_in_day(list(unique.values()), day_ms)


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
class ReconciliationResult:
    """
    One instrument-day's outcome: `pass`/`fail` are verdicts (persisted as `verified_days`),
    `error` (could not be compared) and `not_rebuilt` (outside the run's `RebuildProof`) are
    findings that write nothing.
    """

    iid: str
    status: str  # "pass" | "fail" | "error" | "not_rebuilt"
    minutes: int = 0  # union of minutes either side traded in
    mismatches: list[str] = field(default_factory=list)


def compare(
    iid: str, ours: list[Kline], theirs: list[Kline], inst: Instrument, seeded: bool = False
) -> ReconciliationResult:
    """
    Bar-for-bar, exact: every minute of the union must be present and equal on both sides. With
    `seeded` (Bybit), a mismatch right after a mismatched minute is marked as a seed consequence.
    """
    mine, venue = {k.t_ms: k for k in ours}, {k.t_ms: k for k in theirs}
    result = ReconciliationResult(iid, "pass", minutes=len(mine.keys() | venue.keys()))
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
