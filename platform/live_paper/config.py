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
Deprecated re-export shim (Story 25.3): `live_paper.config`'s value objects moved to
`bots.domain.config`; the loaders moved to `bots.infrastructure.config` and now return the
`PaperFleet`/`ExecBot` aggregates (a changed shape, so they are not served here).

Pure re-export, defines nothing: every name here *is* the `bots.domain.config` object.
"""

import warnings

from bots.domain.config import BotConfig
from bots.domain.config import ExecConfig
from bots.domain.config import PaperConfig
from bots.domain.config import VenuePaperConfig


__all__ = [
    "BotConfig",
    "ExecConfig",
    "PaperConfig",
    "VenuePaperConfig",
]

REMOVE_AFTER = "26-1-livebook-tradeintake-feedgroup-pure-secondsampler-in-place"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "live_paper.config moved to bots.domain.config / bots.infrastructure.config (Story 25.3); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
