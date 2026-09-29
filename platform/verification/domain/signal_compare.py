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
How a production float is judged against a reference `Decimal` (Story 31.3,
`docs/DATA_DICTIONARY.md` §2.13), and the per-signal tally of the verdicts.

Two rules, never a third and never a wider bound:

- `at_places`: a value that is exact at a known number of decimal places (mid p+1, spread p,
  CVD/volume delta/depth sums/count OFI s, OHLC p, candle volume s) is compared by quantizing the
  production float to those places (half-even). Equal after quantizing and the float is the double
  nearest that decimal: EXACT; equal but a different double (`85891.90000000001`): FLOAT_NOISE, its
  own reported class; not equal: DIFFERENT, always a failure.
- `relative`: a division or statistical output (microprice, OBI, avg trade size, pct, basis,
  funding per hour, USD OFI, z-score, stdevs, Pearson, lead-lag) agrees within `REL_TOL` of the
  reference's magnitude or `ABS_TOL`, whichever is larger.

Stdlib only, like the reference: `decimal`, `enum`, `math`.
"""

import math
from decimal import ROUND_HALF_EVEN
from decimal import Decimal
from decimal import localcontext
from enum import StrEnum


# The tolerance of every division/statistical comparison, never widened to make a case pass
# (DATA_DICTIONARY §2.13 states the same budget). Each input is the float64 decode of an exact
# stored decimal (at most 0.5 ulp, 1.1e-16 relative); a sum or mean over at most 3600 terms adds
# under ~1e-12 relative (3600 * 2^-53 of the summed magnitudes). A ratio of products and sums
# (microprice, OBI, avg trade size, USD OFI, z-score) therefore lands within ~1e-12 relative:
# 1e-9 is a 1000x margin.
REL_TOL = 1e-9
# The one case the relative bound does not cover is a *difference of two decoded prices* divided
# by a price: a return, a pct change, the stdev of returns. Its absolute error is about 2 decode
# errors over the price, 2 * 1.1e-16 whatever the move, so relative to a small move it grows as
# price/move: a one-unit move on the soak's largest stored price, BTCUSDT linear at 8.4e6 units
# (p=2), is a 1.2e-7 return carrying up to ~1.9e-9 relative -- past REL_TOL -- but only ~2.2e-16
# absolute (~2.2e-14 as a pct). ABS_TOL = 1e-12 covers that with a 100x margin, and a reference of
# exactly 0 (a flat return, a zero basis) with it; it is still far below any stored unit (10^-9 at
# precision 9). A basis in bps (x1e4) of two nearly equal prices is the one value where this floor
# is not enough (2.2e-12 absolute): it is judged relative, with its bps move far above one unit.
ABS_TOL = 1e-12
# The microprice lean cancels: `microprice - mid`, two floats of the price's magnitude. The mid is
# the float sum of two decoded prices halved (<= 1.5 ulp of it off the exact mid); the microprice
# is two products, a sum and a division of decoded values (<= ~4 ulp). Their difference is exact
# (Sterbenz), so its error is the sum, <= ~6 ulp of |mid|; 8 ulp is that bound with a margin. A
# lean further off is a real difference, never widened.
LEAN_ULPS = 8
_DIGITS = 60


class Agreement(StrEnum):
    """One comparison's verdict. DIFFERENT and UNDEFINED_MISMATCH fail; the rest pass."""

    EXACT = "exact"
    FLOAT_NOISE = "float_noise"
    WITHIN_TOL = "within_tol"
    DIFFERENT = "different"
    BOTH_UNDEFINED = "both_undefined"
    UNDEFINED_MISMATCH = "undefined_mismatch"
    # A depth sum that disagrees with the exact reference only through levels lying within
    # production's 1e-9 relative edge slack (`kernel.indicators._BPS_EDGE_SLACK`): a pinned Known
    # limit, counted on its own and never folded into EXACT (DATA_DICTIONARY §2.13).
    EDGE_SLACK = "edge_slack"
    # A difference of two floats of one magnitude (the microprice lean, `microprice - mid`) within
    # `LEAN_ULPS` ulps of that magnitude: the operands' own rounding, reported apart from WITHIN_TOL
    # because relative to the (small) difference it is not bounded by REL_TOL.
    WITHIN_ULPS = "within_ulps"


FAILING = frozenset({Agreement.DIFFERENT, Agreement.UNDEFINED_MISMATCH})

Prod = float | int | None
Ref = Decimal | float | None


def _undefined(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _definedness(prod: object, ref: object) -> Agreement | None:
    """
    BOTH_UNDEFINED / UNDEFINED_MISMATCH when either side is None or NaN, DIFFERENT for an infinite
    production value (never a defined signal), None when both exist.
    """
    if isinstance(prod, float) and math.isinf(prod):
        return Agreement.DIFFERENT
    if _undefined(prod) and _undefined(ref):
        return Agreement.BOTH_UNDEFINED
    if _undefined(prod) or _undefined(ref):
        return Agreement.UNDEFINED_MISMATCH
    return None


def _decimal(ref: Decimal | float) -> Decimal:
    """
    Return a reference as a Decimal; a float is read as its shortest repr (the value it was
    written as), never its binary expansion: `0.1` is `Decimal("0.1")`, not 0.1000000000000000055.
    """
    return Decimal(repr(ref)) if isinstance(ref, float) else Decimal(ref)


def at_places(prod: Prod, ref: Ref, places: int) -> Agreement:
    """
    Judge a value exact at `places` decimals: the production float quantized half-even to those
    places must equal the reference (itself exact there, else `ValueError`: a misuse).
    """
    undefined = _definedness(prod, ref)
    if undefined is not None or prod is None or ref is None:
        return undefined or Agreement.UNDEFINED_MISMATCH
    with localcontext(prec=_DIGITS):
        quantum = Decimal(1).scaleb(-places)
        expected = _decimal(ref)
        if expected != expected.quantize(quantum, rounding=ROUND_HALF_EVEN):
            raise ValueError(f"the reference {expected} is not exact at {places} places")
        quantized = Decimal(prod).quantize(quantum, rounding=ROUND_HALF_EVEN)
    if quantized != expected:
        return Agreement.DIFFERENT
    return Agreement.EXACT if prod == float(expected) else Agreement.FLOAT_NOISE


def relative(prod: Prod, ref: Ref, rel: float = REL_TOL, abs_: float = ABS_TOL) -> Agreement:
    """Judge a division or statistical output: `|prod - ref| <= max(rel * |ref|, abs_)`."""
    undefined = _definedness(prod, ref)
    if undefined is not None or prod is None or ref is None:
        return undefined or Agreement.UNDEFINED_MISMATCH
    with localcontext(prec=_DIGITS):
        expected = _decimal(ref)
        gap = abs(Decimal(prod) - expected)
        bound = max(Decimal(rel) * abs(expected), Decimal(abs_))
    if gap > bound:
        return Agreement.DIFFERENT
    return Agreement.EXACT if prod == float(expected) else Agreement.WITHIN_TOL


def within_ulps(prod: Prod, ref: Ref, magnitude: float, ulps: int = LEAN_ULPS) -> Agreement:
    """
    Judge a difference of two floats of `magnitude`: `relative` first; failing that, WITHIN_ULPS
    when `|prod - ref| <= ulps * ulp(magnitude)`, else DIFFERENT.
    """
    verdict = relative(prod, ref)
    if verdict is not Agreement.DIFFERENT or prod is None or ref is None or math.isinf(prod):
        return verdict
    with localcontext(prec=_DIGITS):
        gap = abs(Decimal(prod) - _decimal(ref))
    bound = Decimal(ulps) * Decimal(math.ulp(abs(magnitude)))
    return Agreement.WITHIN_ULPS if gap <= bound else Agreement.DIFFERENT


def equal(prod: object, ref: object) -> Agreement:
    """Judge an integer or a key (a bucket start, a count): EXACT or DIFFERENT, nothing between."""
    undefined = _definedness(prod, ref)
    if undefined is not None:
        return undefined
    return Agreement.EXACT if prod == ref else Agreement.DIFFERENT


class Tally:
    """
    Verdict counts per signal. Invariant: counts only grow, one per recorded comparison, so a
    report's totals are exactly the comparisons made (`record` is the only writer).
    """

    def __init__(self) -> None:
        self._counts: dict[str, dict[Agreement, int]] = {}

    def record(self, signal: str, verdict: Agreement) -> Agreement:
        per_signal = self._counts.setdefault(signal, {})
        per_signal[verdict] = per_signal.get(verdict, 0) + 1
        return verdict

    def count(self, signal: str, verdict: Agreement) -> int:
        return self._counts.get(signal, {}).get(verdict, 0)

    def total(self, signal: str) -> int:
        return sum(self._counts.get(signal, {}).values())

    def signals(self) -> list[str]:
        return sorted(self._counts)

    def failures(self) -> dict[str, int]:
        """Return the signals with a failing verdict, and how many."""
        failing = {
            signal: sum(n for verdict, n in verdicts.items() if verdict in FAILING)
            for signal, verdicts in self._counts.items()
        }
        return {signal: n for signal, n in failing.items() if n}

    def report(self) -> list[str]:
        """Return one line per signal: every class's count, float noise included."""
        return [
            f"{signal}: "
            + ", ".join(f"{verdict.value}={self.count(signal, verdict)}" for verdict in Agreement)
            for signal in self.signals()
        ]
