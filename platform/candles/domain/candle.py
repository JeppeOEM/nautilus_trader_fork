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
The candle value type and the two rules that judge one: shape validity and coverage.

Invariant (a served bar is possible): `is_valid_candle` states the shape every candle this context
hands out must satisfy -- all finite, `l <= min(o, c) <= max(o, c) <= h`, `v >= 0`, and, where the
Story 33.3 order-flow columns are known, `buy_v + sell_v` is `v` in units, no count or volume is
negative and the liquidation count agrees with the liquidated volume. A violator is a bug upstream
of the reader (DATA-02/DATA-07), never something to clamp: callers fail the request loudly and
ledger it.

Invariant (coverage is never implied): a bucket the collector only saw part of has an understated
high/low/volume that no later read can repair, so `is_partial` marks it from the counted
`seconds_observed` rather than letting a reader assume a full bucket.

Pure: no I/O, no store, no clock.
"""

import math
from dataclasses import dataclass


TIMEFRAMES = {
    "1m": 60,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800,
    "45m": 2700,
    "1h": 3600,
    "1d": 86400,
    "1w": 604800,
}

# A bucket observed for less than this fraction of its span is flagged `partial` (D-15):
# collector downtime/gaps leave its high/low/volume understated, which no later read can repair.
PARTIAL_OBSERVED_FRACTION = 0.9


@dataclass
class Candle:
    """One bar: `ts_open` in nanoseconds, OHLC in price units, `volume` in size units."""

    ts_open: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


# The order-flow group (Story 33.3): known together or not at all, `size_precision` their units'.
_FLOW_KEYS = ("buy_v", "sell_v", "buy_n", "sell_n", "pv", "price_precision", "size_precision")
_NON_NEGATIVE_KEYS = ("buy_v", "sell_v", "buy_n", "sell_n", "liq_long_v", "liq_short_v", "liq_n")


def is_valid_candle(c: dict) -> bool:
    """
    Hard shape invariants for a served candle: all finite, l <= min(o,c) <= max(o,c) <= h, v >= 0,
    and the order-flow rules (`order_flow_is_valid`) when its keys are present.

    A violator is a data bug (DATA-02), never something to clamp or repair -- callers fail loudly.
    """
    try:
        o, h, low, cl, v = (float(c[k]) for k in ("o", "h", "l", "c", "v"))
    except (KeyError, TypeError, ValueError):
        return False
    if not all(map(math.isfinite, (o, h, low, cl, v))):
        return False
    if not (v >= 0 and low <= min(o, cl) and max(o, cl) <= h):
        return False
    return order_flow_is_valid(c, v)


def order_flow_is_valid(c: dict, v: float) -> bool:
    """
    Judge the Story 33.3 columns of one candle: absent or all null is valid (an old shape, a pre-migration
    bar); partly null flow is not; no count or volume is negative; `buy_v + sell_v` equals
    `round(v * 10**size_precision)`; `liq_n` is 0 exactly when both liquidated volumes are.

    Known limit (the `v` identity's ceiling): `v` is a float64 sum of the bar's decoded seconds
    (`domain.fold`, unchanged byte for byte), so it differs from the exact `buy_v + sell_v` by up
    to about `n * u * U` units (`u` = 2^-53 the unit roundoff, `n` the summed seconds, `U` the
    bar's volume in units); the check stays exact, so it fails once that error reaches half a unit.
    The worst-case bound is reached at `U ~= 2^52 / n`: 1D (n = 86,400) about 5.2e10 units, 1W
    (n = 604,800, a read-time bar folded from raw seconds) about 7.4e9. The busiest collected
    instrument, Bybit BTCUSDT spot, has size precision 6 (`basePrecision` 0.000001: the instrument
    item in `verification/tests/test_derivs.py` and the recorded six-decimal wire sizes in
    `capture/venues/bybit/tests/fixtures/bybit_trades_btcusdt_spot_20260921.json`), so ~2e4 BTC a
    day is ~2e10 units a day (under the 1D bound) and up to ~1e11 a week -- the 1W worst case IS
    exceeded for BTC spot (~7 units of possible error). The typical error, rounding to nearest
    being a random walk, is about `u * U * sqrt(n)`: ~1e-16 x 1e11 x 780 ~= 1e-2 units there, far
    below 0.5, so a false refusal is not expected -- but it is not proven impossible. A breach is
    loud -- the request fails 500 and `candles.invalid_candle` is ledgered (`views.chart_series.
    _checked`) -- never silent and never clamped. Upgrade path: derive `v` from the units
    (`(buy_v + sell_v) / 10**size_precision`) once the store's OHLCV columns are integer
    (`domain.fold`'s own Known limit), which makes the identity hold by construction.
    """
    flow = [c.get(key) for key in _FLOW_KEYS]
    if any(value is not None for value in flow) and any(value is None for value in flow):
        return False
    if any((c.get(key) or 0) < 0 for key in _NON_NEGATIVE_KEYS):
        return False
    buy_v, sell_v, size_precision = c.get("buy_v"), c.get("sell_v"), c.get("size_precision")
    if (
        buy_v is not None
        and size_precision is not None
        and buy_v + sell_v != round(v * 10**size_precision)
    ):
        return False
    return _liquidations_agree(c.get("liq_long_v"), c.get("liq_short_v"), c.get("liq_n"))


def _liquidations_agree(long_v: int | None, short_v: int | None, n: int | None) -> bool:
    group = (long_v, short_v, n)
    if all(value is None for value in group):
        return True
    if long_v is None or short_v is None or n is None:
        return False
    return (n == 0) == (long_v == 0 and short_v == 0)


def is_partial(seconds_observed: int, bar_seconds: int) -> bool:
    """Whether a bucket was observed for less than `PARTIAL_OBSERVED_FRACTION` of its span."""
    return seconds_observed < PARTIAL_OBSERVED_FRACTION * bar_seconds
