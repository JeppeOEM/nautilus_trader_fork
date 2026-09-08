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

Registered indicators grow one story at a time: CumulativeVolumeDelta (Story 10.2), Cancel
Pressure/OFI to follow (Stories 10.3-10.4). Per DESIGN-02, the two catalogs stay unaware of
each other's contents -- this module never reads `INDICATOR_CATALOG`, `chart_indicators.py`
never reads `CUSTOM_INDICATOR_CATALOG`, and they're merged only at the dashboard.py call site.
The one shared import below (`Panel`) is a type alias, not a coupling to catalog internals.
"""

import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ml_signals.chart_indicators import Panel
from ml_signals.indicators import trade_aggregates


# Duplicated from dashboard.py's own module-level constant (same env var, same default) --
# this module cannot import it from there without a circular import (dashboard.py imports
# this module). One line, not worth a shared-constants module for just this (DESIGN-01) --
# `_second_snapshots` below duplicates a larger chunk (the catalog-query + CustomData-unwrap
# logic itself); that duplication is worth revisiting into a shared helper once a second
# real call site needs the identical pattern (Story 10.3/10.4 need a different one, for
# OrderBookDelta, so this may end up staying a one-off).
_CATALOG_PATH = os.environ.get("CATALOG_PATH", "troll/dydx_collector/catalog")


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


def _second_snapshots(window: ReplayWindow) -> list[dict]:
    """Fetch this window's DydxSecondSnapshot rows from the catalog, as plain dicts.

    Mirrors dashboard.py's _historical_lines_json exactly (same catalog-query +
    CustomData-unwrap pattern) -- duplicated here rather than imported, since importing
    from dashboard.py would be circular (dashboard.py imports this module).
    """
    from dydx_collector.second_snapshot import DydxSecondSnapshot
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog = ParquetDataCatalog(_CATALOG_PATH)
    results = catalog.query(
        data_cls=DydxSecondSnapshot, identifiers=[window.instrument_id],
        start=window.start_ms * 1_000_000, end=window.end_ms * 1_000_000,
    )
    snapshots = [r.data if hasattr(r, "data") else r for r in results]
    # buy_count/sell_count are required by trade_aggregates()'s reduction below even though
    # _cvd_replay only consumes the volume totals it returns -- not dead data, just an unused
    # part of a shared function's output.
    return [
        {
            "buy_volume": s.buy_volume, "sell_volume": s.sell_volume,
            "buy_count": s.buy_count, "sell_count": s.sell_count, "ts_event": s.ts_event,
        }
        for s in snapshots
    ]


def _cvd_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow,
) -> dict[str, list[float | None]]:
    """Per-candle running-cumulative buy_volume - sell_volume for the currently-requested
    window -- an unbounded, request-anchored total (resets to 0 at whichever candle happens
    to be first in the current view), not the 5-minute rolling/decaying oscillator the old
    chart_data.py row computed. This is a deliberate scope choice for the picker version (see
    epics.md Story 10.2 AC #1) -- panning/resizing the visible window changes where the sum
    restarts, so read it as "net flow within the current view," not an absolute level.

    Unrelated to ofi_strategy.py's own "cum_delta" signal (a live Strategy's independent
    5-minute rolling-window implementation) -- same name, different metric, different code.

    Historical only -- the old fixed row was never live either (it always replayed the
    date-range form's explicit window, never an in-process live buffer). Live requests get
    None for every candle, a real gap, not a fabricated value (DATA-01).

    A candle bucket with zero snapshot rows is *not* treated as "no volume" (which would
    silently paper over a genuine second-snapshot collection gap as a flat/unchanged value,
    DATA-01) -- it gets None, and the running total resumes from its last real value on the
    next bucket that does have data.
    """
    if window.start_ms is None or window.end_ms is None:
        return {"value": [None] * len(candles)}
    bar_ns = window.bar_seconds * 1_000_000_000
    buckets: dict[int, list[dict]] = defaultdict(list)
    for row in _second_snapshots(window):
        buckets[(row["ts_event"] // bar_ns) * bar_ns].append(row)
    running_total = 0.0
    values: list[float | None] = []
    for candle in candles:
        rows = buckets.get(candle["t"] * 1_000_000, [])
        if not rows:
            values.append(None)
            continue
        buy_vol, sell_vol, _, _ = trade_aggregates(rows)
        running_total += buy_vol - sell_vol
        values.append(running_total)
    return {"value": values}


CUSTOM_INDICATOR_CATALOG["CumulativeVolumeDelta"] = CustomIndicatorSpec(
    params={}, panel="oscillator", replay=_cvd_replay,
)
