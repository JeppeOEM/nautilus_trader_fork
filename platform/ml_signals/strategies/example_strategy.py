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
Deprecated re-export shim (Story 24.4): `ml_signals.strategies.example_strategy` moved to the
research context (`research.strategies.example_strategy`).

Pure re-export, defines nothing: every name here *is* the `research.strategies.example_strategy`
object. Import from `research.strategies.example_strategy` instead, e.g. `from
research.strategies.example_strategy import LogisticTrendConfig`.

An `ImportableStrategyConfig` string path
`"ml_signals.strategies.example_strategy:LogisticTrendStrategy"` still resolves here, to the same
class; write `"research.strategies.example_strategy:LogisticTrendStrategy"` instead.
"""

import warnings

from research.strategies.example_strategy import LogisticTrendConfig
from research.strategies.example_strategy import LogisticTrendStrategy


__all__ = [
    "LogisticTrendConfig",
    "LogisticTrendStrategy",
]

REMOVE_AFTER = "25-2-ranking-context-rankingboard-replaces-module-globals"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "ml_signals.strategies.example_strategy moved to research.strategies.example_strategy"
    " (Story 24.4); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
