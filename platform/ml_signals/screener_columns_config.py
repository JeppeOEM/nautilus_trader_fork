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
Screener-wide Technicals column selection (Story 17.5): one flat list applied to every row
of the Rankings table -- deliberately NOT keyed by instrument_id like
`chart_indicator_config.py`'s per-coin selection, which it otherwise mirrors (tomllib to
read, tomli_w to write, full rewrite). Reuses that module's `IndicatorEntry`.

Schema: a top-level `columns` array of `{name, params, category}` tables, in display order.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from ml_signals.chart_indicator_config import IndicatorEntry


DEFAULT_BAR_SECONDS = 3600


@dataclass(frozen=True)
class ColumnEntry(IndicatorEntry):
    """
    A column is an indicator plus the bar size it is computed on -- a field of its own, not a
    param: params feed the indicator constructor and its series id.
    """

    bar_seconds: int = DEFAULT_BAR_SECONDS


def load_config(path: Path) -> list[ColumnEntry]:
    """A missing or empty file (nothing configured yet) is `[]`, not an error."""
    if not path.exists():
        return []
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return [
        ColumnEntry(
            name=e["name"],
            params=e.get("params", {}),
            category=e["category"],
            bar_seconds=e.get("bar_seconds", DEFAULT_BAR_SECONDS),
        )
        for e in raw.get("columns", [])
    ]


def save_config(entries: list[ColumnEntry], path: Path) -> None:
    raw = {
        "columns": [
            {
                "name": e.name,
                "params": e.params,
                "category": e.category,
                "bar_seconds": e.bar_seconds,
            }
            for e in entries
        ]
    }
    # Serialize first: a bad value must fail before the file is truncated. (Not a temp-file
    # rename -- the docker single-file bind mount can't be renamed over.)
    path.write_bytes(tomli_w.dumps(raw).encode())
