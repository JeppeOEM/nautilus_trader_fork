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
Dispatch/metadata for dYdX-specific chart indicators that are not `nautilus_trader.indicators`
classes and cannot be computed from OHLCV candle fields alone (CVD, Cancel Pressure, OFI --
Stories 10.2-10.4). Mirrors `chart_indicators.py`'s catalog/replay/catalog_json shape for
params/panel/dispatch, but deliberately does not mirror its `enum_params` round-tripping (no
custom indicator needs an enum-typed param yet -- add it if one does, DESIGN-01) and a custom
indicator's `replay` receives a `ReplayWindow` (instrument id + window bounds) alongside the
candle list, since it needs to fetch its own order-book/trade-level/second-snapshot rows for
that window -- a candle dict alone (o/h/l/c/v) doesn't carry that data.

This story (10.1) registers no real indicator -- `CUSTOM_INDICATOR_CATALOG` is empty until
Stories 10.2-10.4 add CVD/Cancel Pressure/OFI. Per DESIGN-02, the two catalogs stay unaware of
each other's contents -- this module never reads `INDICATOR_CATALOG`, `chart_indicators.py`
never reads `CUSTOM_INDICATOR_CATALOG`, and they're merged only at the dashboard.py call site.
The one shared import below (`Panel`) is a type alias, not a coupling to catalog internals.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ml_signals.chart_indicators import Panel


@dataclass(frozen=True)
class ReplayWindow:
    """The window context a custom indicator's `replay` needs beyond the candle list itself.

    `start_ms`/`end_ms` are `None` for the live (not-yet-closed) window -- mirrors
    `coin_indicators_handler`'s existing live/historical branch in dashboard.py, which already
    picks `_live_candles_json` vs `_historical_candles_json` on exactly this same condition.
    """

    instrument_id: str
    bar_seconds: int
    start_ms: int | None
    end_ms: int | None


ReplayFn = Callable[[list[dict], dict[str, Any], ReplayWindow], dict[str, list[float | None]]]


@dataclass
class CustomIndicatorSpec:
    # JSON-safe default params (same role as IndicatorSpec.params in chart_indicators.py).
    params: dict[str, Any]
    panel: Panel
    # Computes every registered output attribute for the given candles/params/window, aligned
    # 1:1 with `candles` -- identical output contract to chart_indicators.replay_indicator.
    replay: ReplayFn


CUSTOM_INDICATOR_CATALOG: dict[str, CustomIndicatorSpec] = {}


def replay_indicator(
    candles: list[dict], name: str, params: dict[str, Any], window: ReplayWindow,
) -> dict[str, list[float | None]]:
    """Look up `name` in `CUSTOM_INDICATOR_CATALOG` and run its `replay` function."""
    if name not in CUSTOM_INDICATOR_CATALOG:
        raise ValueError(f"Unknown custom indicator: {name!r}")
    spec = CUSTOM_INDICATOR_CATALOG[name]
    merged = {**spec.params, **params}
    return spec.replay(candles, merged, window)


def catalog_json() -> dict[str, Any]:
    """`CUSTOM_INDICATOR_CATALOG` serialized for the merged `/data/indicators/catalog` response."""
    return {
        name: {"params": spec.params, "panel": spec.panel}
        for name, spec in CUSTOM_INDICATOR_CATALOG.items()
    }
