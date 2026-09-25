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
Deprecated re-export shim (Story 24.1): `ml_signals.candles` moved to the `candles` context.

Pure re-export, defines nothing: every name here *is* the `candles` object. Import from `candles`
instead, e.g. `from candles.domain.candle import is_valid_candle`,
`from candles.application.queries import candle_dicts_for_window`.

The three aggregation helpers this module used to own are retired, not moved: `build_candles`
(trades -> bars, superseded by `kernel.fold.fold_trades` plus the one seconds -> bars fold),
`aggregate_ohlc` and `candle_dicts_from_snapshots` (a second, disagreeing seconds -> bars fold).
They raise, naming `candles.application.forming.forming_bar`, because serving a lookalike would
reintroduce exactly the divergence Story 24.1 removed.
"""

import warnings

from candles.application.queries import candle_dicts_for_window
from candles.domain.candle import PARTIAL_OBSERVED_FRACTION
from candles.domain.candle import TIMEFRAMES
from candles.domain.candle import Candle
from candles.domain.candle import is_valid_candle


__all__ = [
    "PARTIAL_OBSERVED_FRACTION",
    "TIMEFRAMES",
    "Candle",
    "candle_dicts_for_window",
    "is_valid_candle",
]

REMOVE_AFTER = "24-3-alerting-context-as-forming-bar-observer"

_REPLACED_NAMES: dict[str, str] = {
    "build_candles": "candles.application.forming.forming_bar(rows, bar_seconds)",
    "aggregate_ohlc": "candles.application.forming.forming_bar(rows, bar_seconds)",
    "candle_dicts_from_snapshots": (
        "candles.application.forming.bars_from_rows(rows, bar_seconds), or forming_bar for the last"
    ),
}


def __getattr__(name: str) -> object:
    if name in _REPLACED_NAMES:
        raise AttributeError(
            f"ml_signals.candles.{name} was replaced by {_REPLACED_NAMES[name]} (Story 24.1)"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "ml_signals.candles moved to the candles context (Story 24.1); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
