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
Ranking's value objects: the ranking mode, a USD 24 h volume reading and the volatility score.
"""

import math
from dataclasses import dataclass
from enum import StrEnum


class RankingMode(StrEnum):
    """
    The one global ranking mode (FR6), set last-write-wins by `ranking:control`.

    Invariant: the mode is one of exactly these two values -- `parse` returns None for anything
    else, so a malformed or unknown control message can never change it. A `StrEnum`, so it
    serialises to the same `"volume"`/`"volatility"` string the `rankings:live` payload carried.
    """

    VOLUME = "volume"
    VOLATILITY = "volatility"

    @classmethod
    def parse(cls, value: object) -> "RankingMode | None":
        """Return the mode `value` names, or None when it names none (never raises)."""
        if not isinstance(value, str):
            return None
        try:
            return cls(value)
        except ValueError:
            return None


@dataclass(frozen=True)
class VolumeReading:
    """
    One instrument's USD 24 h volume and when its venue poll completed.

    Invariant: `value_usd` is a finite, non-negative USD figure (`parse_usd_volume`) -- no volume is
    a missing reading, never a fabricated 0 (DATA-01).
    """

    value_usd: float
    observed_ns: int


# The cross-sectional stdev of returns (`VolatilityTracker.score`); None when history is too short.
type VolatilityScore = float


def parse_usd_volume(raw: object) -> float:
    """
    Parse a venue's USD volume string/number as a finite, non-negative float.

    Raises ValueError/TypeError on anything else -- an empty string, None, NaN or a negative value
    is unparseable, never a 0 (DATA-01).
    """
    if raw is None or raw == "" or isinstance(raw, bool):  # float(True) would be $1
        raise ValueError(f"empty or non-numeric volume {raw!r}")
    value = float(raw)  # type: ignore[arg-type]  # TypeError on a non-numeric type is the contract
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"volume {raw!r} is not a finite non-negative number")
    return value
