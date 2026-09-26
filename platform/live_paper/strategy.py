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
Deprecated re-export shim (Story 25.3): `live_paper.strategy` moved to `bots.strategies.dummy`.

Pure re-export, defines nothing: every name here *is* the `bots.strategies.dummy` object.
"""

import warnings

from bots.strategies.dummy import DummyStrategy
from bots.strategies.dummy import DummyStrategyConfig


__all__ = [
    "DummyStrategy",
    "DummyStrategyConfig",
]

REMOVE_AFTER = "26-1-livebook-tradeintake-feedgroup-pure-secondsampler-in-place"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "live_paper.strategy moved to bots.strategies.dummy (Story 25.3); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
