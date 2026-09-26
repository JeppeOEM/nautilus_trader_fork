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
The two UI preference files, and their one loader/saver each (Story 24.2 merged
`ml_signals.chart_indicator_config` and `ml_signals.screener_columns_config` here, bodies verbatim):

- `chart_indicators.toml` (`CHART_INDICATOR_CONFIG_PATH`): per-instrument chart indicator
  selections (Story 10.5), a table keyed by instrument_id, each holding a list of
  `{name, params, category}` entries -- `load_chart_indicators`/`save_chart_indicators`. `id` is
  never persisted: it is a client-side sequence counter for the multi-instance picker UI,
  regenerated fresh on every load.
- `screener_columns.toml` (`SCREENER_COLUMNS_CONFIG_PATH`): the screener-wide Technicals column
  selection (Story 17.5), one flat top-level `columns` array of
  `{name, params, category, bar_seconds}` tables in display order, applied to every row of the
  Rankings table -- `load_screener_columns`/`save_screener_columns`.

Both are tomllib to read, tomli_w to write, and a full rewrite (not a patch). Key sets frozen
(AD-D12): both files are bind-mounted and hand-editable, so a renamed or dropped key would silently
lose a saved selection on the next deploy; `views/tests/test_preferences.py` pins the written text.
The paths themselves are the interface's (env vars read in `data_api`), passed in.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli_w


DEFAULT_BAR_SECONDS = 3600


@dataclass(frozen=True)
class IndicatorEntry:
    name: str
    params: dict[str, Any]
    category: str


@dataclass(frozen=True)
class ColumnEntry(IndicatorEntry):
    """
    A column is an indicator plus the bar size it is computed on -- a field of its own, not a
    param: params feed the indicator constructor and its series id.
    """

    bar_seconds: int = DEFAULT_BAR_SECONDS


def load_chart_indicators(path: Path) -> dict[str, list[IndicatorEntry]]:
    """
    Load persisted per-instrument indicator selections.

    A missing file (nothing saved yet) returns an empty dict, not an error -- same
    "nothing saved yet" treatment the rest of this codebase gives an absent data
    source, so a fresh install or a coin with no saved config just sees no entries.
    """
    if not path.exists():
        return {}
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return {
        instrument_id: [
            IndicatorEntry(name=e["name"], params=e.get("params", {}), category=e["category"])
            for e in entries
        ]
        for instrument_id, entries in raw.items()
    }


def save_chart_indicators(config: dict[str, list[IndicatorEntry]], path: Path) -> None:
    """
    Persist `config` back to `path` as TOML.

    Full rewrite, not a patch -- `tomli_w` has no comment-preservation support, so any
    hand-written comments in the file are lost on a Save-button-triggered write. Same
    accepted, deliberate tradeoff as `collection_control`'s `TomlPlanStore.save`
    (see its docstring); revisit only if it becomes a real complaint.
    """
    raw = {
        instrument_id: [
            {"name": e.name, "params": e.params, "category": e.category} for e in entries
        ]
        for instrument_id, entries in config.items()
    }
    with path.open("wb") as f:
        tomli_w.dump(raw, f)


def load_screener_columns(path: Path) -> list[ColumnEntry]:
    """Load the column list; a missing or empty file (nothing configured yet) is `[]`, not an error."""
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


def save_screener_columns(entries: list[ColumnEntry], path: Path) -> None:
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
