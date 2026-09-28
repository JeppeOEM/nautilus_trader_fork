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
Bybit's capture config: `BybitConfig` (the core thresholds plus the REST open-interest poll
cadence) and where its `config.toml` lives. Read by the one loader,
`capture.infrastructure.config` (`VENUE_SCHEMAS["BYBIT"]`).
"""

import os
from dataclasses import dataclass
from pathlib import Path

from capture.application.config import CoreConfig


# `BYBIT_COLLECTOR_CONFIG` is the compose-mounted file; without it the file baked into the image.
CONFIG_PATH = Path(os.environ.get("BYBIT_COLLECTOR_CONFIG", Path(__file__).parent / "config.toml"))


@dataclass(frozen=True)
class BybitConfig(CoreConfig):
    """The core thresholds plus the REST open-interest poll cadence (the linear WS drops OI)."""

    open_interest_poll_seconds: int = 300
