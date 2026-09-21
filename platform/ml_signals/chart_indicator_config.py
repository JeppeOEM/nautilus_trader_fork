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
Per-instrument chart indicator configuration: persisted `_activeIndicators` selections
(Story 10.5).

Mirrors dydx_collector/config.py's load_config()/save_config() shape -- tomllib to
read, tomli_w to write, full rewrite (not a patch). Schema is a table keyed by
instrument_id, each holding a list of indicator entries; `id` is never persisted --
it's a client-side sequence counter for the multi-instance picker UI, regenerated
fresh on every load.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli_w


@dataclass(frozen=True)
class IndicatorEntry:
    name: str
    params: dict[str, Any]
    category: str


def load_config(path: Path) -> dict[str, list[IndicatorEntry]]:
    """Load persisted per-instrument indicator selections.

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


def save_config(config: dict[str, list[IndicatorEntry]], path: Path) -> None:
    """
    Persist `config` back to `path` as TOML.

    Full rewrite, not a patch -- `tomli_w` has no comment-preservation support, so any
    hand-written comments in the file are lost on a Save-button-triggered write. Same
    accepted, deliberate tradeoff as `dydx_collector/config.py`'s `save_config`
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
