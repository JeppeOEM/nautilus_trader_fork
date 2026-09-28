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
Hyperliquid's capture config: its measured book cadence and where its `config.toml` lives. It
adds no key to the core's, so its thresholds are a plain `CoreConfig` from the one loader,
`capture.infrastructure.config` (`VENUE_SCHEMAS["HYPERLIQUID"]`).
"""

import os
from pathlib import Path


# `HYPERLIQUID_COLLECTOR_CONFIG` is the compose-mounted file; without it the file baked into the
# image.
CONFIG_PATH = Path(
    os.environ.get("HYPERLIQUID_COLLECTOR_CONFIG", Path(__file__).parent / "config.toml")
)

# The loader's default `stale_book_seconds`: l2Book pushes every ~5.4 s (max 6 s in the 22.5 raw
# capture, see its config.toml), so the core's 5 s stale guard would skip most samples -- 2x the
# cadence.
STALE_BOOK_SECONDS = 12.0
