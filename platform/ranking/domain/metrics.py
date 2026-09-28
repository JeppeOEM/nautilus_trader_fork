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


def price_stats_from_series(series: list[tuple[int, float]]) -> dict:
    """
    Latest price, pct change over the last 1h/24h, and return volatility (stdev), computed from an
    already-fetched ascending (ts_event, price) series.

    `pct_change_1h`/`pct_change_24h` are None when the series doesn't yet span that long -- no
    extrapolation from partial history.
    """
    if not series:
        return {"price": None, "pct_change_1h": None, "pct_change_24h": None, "volatility": None}

    ts = np.array([t for t, _ in series])
    px = np.array([p for _, p in series])
    latest_ts, latest_px = ts[-1], px[-1]

    def _pct_change(hours: float) -> float | None:
        cutoff = latest_ts - int(hours * 3_600 * 1e9)
        if ts[0] > cutoff:
            return None  # not enough history collected yet
        base_px = px[np.searchsorted(ts, cutoff)]
        return float((latest_px - base_px) / base_px * 100.0)

    returns = np.diff(px) / px[:-1]
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
