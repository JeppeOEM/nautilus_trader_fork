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
Ranking's price math (DDD spine AD-D10): the one pct-change/volatility formula in `platform/`.

`price_stats_from_series` was the catalog-stats module's (Story 13.2 extracted it so the in-memory
`PriceSeriesStore` and the old catalog-backed path shared one formula); Story 25.2 moved it here.
Views and research read the values it produces from `rankings:live`/`metrics.db`, never recompute
them (SSOT-02); `platform/tests/test_boundaries.py` fails a second definition.
"""

import numpy as np


_NS_PER_HOUR = 3_600 * 1_000_000_000

# The most the base point of `pct_change_1h`/`pct_change_24h` may lie past its cutoff (Story 31.3):
# a gap straddling the cutoff would otherwise make the first point after it the base, silently
# shortening the horizon (a "1h" change over 40 minutes). 300 s: five slow-loop cycles, far above
# the 1 s cadence of a trading instrument, well under 1h; past it the change is None, never shorter.
# Known limit: a quiet market with no trade within 300 s after the cutoff (an illiquid spot pair)
# gives None although the price at the cutoff is known -- it is the last close before it. Upgrade
# path: an as-of base, the last close at or before the cutoff, bounded by a staleness limit.
PCT_MAX_SHORTFALL_NS = 300 * 1_000_000_000

# `volatility` is the 24h figure its label names ("Vol 24h sigma (trade closes)"): the series is kept
# for 25h (`price_series.PRICE_LOOKBACK_HOURS`, the backfill margin), so the window is cut here.
VOLATILITY_WINDOW_NS = 24 * _NS_PER_HOUR


def price_stats_from_series(series: list[tuple[int, float]]) -> dict:
    """
    Latest price, pct change over the last 1h/24h, and return volatility (stdev), computed from an
    already-fetched ascending (ts_event, price) series of trade closes.

    `pct_change_1h`/`pct_change_24h` are None when the series doesn't yet span that long -- no
    extrapolation from partial history -- and when the first point at or after the cutoff lies
    more than `PCT_MAX_SHORTFALL_NS` past it (a gap there would shorten the horizon).
    `volatility` is the population standard deviation (ddof=0) of consecutive pct returns over the
    points within `VOLATILITY_WINDOW_NS` (24h) of the latest; None under two returns.
    """
    if not series:
        return {"price": None, "pct_change_1h": None, "pct_change_24h": None, "volatility": None}

    ts = np.array([t for t, _ in series])
    px = np.array([p for _, p in series])
    latest_ts, latest_px = ts[-1], px[-1]

    def _pct_change(hours: int) -> float | None:
        cutoff = latest_ts - hours * _NS_PER_HOUR
        if ts[0] > cutoff:
            return None  # not enough history collected yet
        base = np.searchsorted(ts, cutoff)
        if ts[base] - cutoff > PCT_MAX_SHORTFALL_NS:
            return None  # a gap at the cutoff: the horizon would be shorter than named
        base_px = px[base]
        return float((latest_px - base_px) / base_px * 100.0)

    recent = px[ts >= latest_ts - VOLATILITY_WINDOW_NS]
    returns = np.diff(recent) / recent[:-1]
    volatility = float(np.std(returns)) if len(returns) > 1 else None

    return {
        "price": float(latest_px),
        "pct_change_1h": _pct_change(1),
        "pct_change_24h": _pct_change(24),
        "volatility": volatility,
    }


def pct_change_from(current: float | None, base: float | None) -> float | None:
    """Percent change from `base` to `current`; None if either is missing (no history yet)."""
    if current is None or not base:
        return None
    return (current - base) / base * 100.0
