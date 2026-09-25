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
hands out must satisfy -- all finite, `l <= min(o, c) <= max(o, c) <= h`, `v >= 0`. A violator is a
bug upstream of the reader (DATA-02/DATA-07), never something to clamp: callers fail the request
loudly and ledger it.

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


def is_valid_candle(c: dict) -> bool:
    """
    Hard shape invariants for a served candle: all finite, l <= min(o,c) <= max(o,c) <= h, v >= 0.

    A violator is a data bug (DATA-02), never something to clamp or repair -- callers drop it.
    """
    try:
        o, h, low, cl, v = (float(c[k]) for k in ("o", "h", "l", "c", "v"))
    except (KeyError, TypeError, ValueError):
        return False
    if not all(map(math.isfinite, (o, h, low, cl, v))):
        return False
    return v >= 0 and low <= min(o, cl) and max(o, cl) <= h


def is_partial(seconds_observed: int, bar_seconds: int) -> bool:
    """Whether a bucket was observed for less than `PARTIAL_OBSERVED_FRACTION` of its span."""
    return seconds_observed < PARTIAL_OBSERVED_FRACTION * bar_seconds
