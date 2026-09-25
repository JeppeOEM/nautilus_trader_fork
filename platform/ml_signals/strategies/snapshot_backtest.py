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
Deprecated re-export shim (Story 24.4): `ml_signals.strategies.snapshot_backtest` moved to the
research context (`research.strategies.snapshot_backtest`).

Pure re-export, defines nothing: every name here *is* the `research.strategies.snapshot_backtest`
object. Import from `research.strategies.snapshot_backtest` instead, e.g. `from
research.strategies.snapshot_backtest import run`.
"""

import warnings

from research.strategies.snapshot_backtest import run


__all__ = [
    "run",
]

REMOVE_AFTER = "25-2-ranking-context-rankingboard-replaces-module-globals"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "ml_signals.strategies.snapshot_backtest moved to research.strategies.snapshot_backtest"
    " (Story 24.4); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
