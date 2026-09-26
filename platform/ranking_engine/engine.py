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
Deprecated re-export shim (Story 25.2): `ranking_engine.engine` moved to the ranking context --
the pure volume parsers to `ranking.infrastructure.volume_{dydx,bybit,hyperliquid}`, the
publisher to `ranking.domain.board`. The module globals and loops it held became state and
methods of `RankingBoard`/`RankingEngine`; the process now runs as `python3 -m ranking`.

Pure re-export, defines nothing: every name here *is* the new object.
"""

import warnings

from ranking.domain.board import RankingsPublisher
from ranking.infrastructure.volume_bybit import parse_bybit_volume_24h
from ranking.infrastructure.volume_dydx import parse_volume_24h
from ranking.infrastructure.volume_hyperliquid import parse_hyperliquid_volume_24h


__all__ = [
    "RankingsPublisher",
    "parse_bybit_volume_24h",
    "parse_hyperliquid_volume_24h",
    "parse_volume_24h",
]

REMOVE_AFTER = "25-4-collection-control-plan-intent-vs-applied-set"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "ranking_engine.engine moved to the ranking context (Story 25.2; run `python3 -m ranking`); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
