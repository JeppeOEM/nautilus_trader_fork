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
Deprecated re-export shim (Story 24.4): `ml_signals.strategies.ofi_strategy` moved to the research
context (`research.strategies.ofi_strategy`).

Pure re-export, defines nothing: every name here *is* the `research.strategies.ofi_strategy` object.
Import from `research.strategies.ofi_strategy` instead, e.g. `from research.strategies.ofi_strategy
import OFIStrategy`.

An `ImportableStrategyConfig` string path `"ml_signals.strategies.ofi_strategy:OFIStrategy"` still
resolves here, to the same class; write `"research.strategies.ofi_strategy:OFIStrategy"` instead.
"""

import warnings

from research.strategies.ofi_strategy import OFIStrategy
from research.strategies.ofi_strategy import OFIStrategyConfig


__all__ = [
    "OFIStrategy",
    "OFIStrategyConfig",
]

REMOVE_AFTER = "25-2-ranking-context-rankingboard-replaces-module-globals"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "ml_signals.strategies.ofi_strategy moved to research.strategies.ofi_strategy (Story 24.4); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
