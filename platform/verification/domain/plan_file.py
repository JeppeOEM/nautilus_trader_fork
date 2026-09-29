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
The recording plan: which instruments the reference recorder subscribes, read from the same venue
`config.toml` the collector reads (`BYBIT_COLLECTOR_CONFIG` / `HYPERLIQUID_COLLECTOR_CONFIG`), never
from a copied list.

Invariant: the recorded set is exactly the collector's collected set -- the flat `instruments`
list deduped in order, minus the optional flat `exclude` list (the semantics of
`capture.infrastructure.config`'s flat plan, restated here from the file format because the
reference side never imports the code it checks). Only the keys the recorder needs are read;
the collector's loader owns the rest of the file and its unknown-key refusal.

Known limit: this parser is looser than the collector's loader
(`capture.infrastructure.config.load_venue_config`), which also refuses unknown keys and bad
thresholds, so a file the collector refuses (it keeps its last plan, or fails to start) can still
be recorded here -- the two sides then record different sets until the file is fixed. And both
re-read the file every 30 s on independent phases, so after an edit the two sets can disagree for
up to ~60 s. Both are visible: every plan change writes `plan_changed` connection lines with their
own timestamps, and the collector publishes its plan on `collector:status`. Upgrade path: a
comparator that trims each window to the instruments both sides held.
"""

import tomllib
from dataclasses import dataclass
from typing import Any

from kernel.venues import has_venue


DEFAULT_ENVIRONMENT = "mainnet"


@dataclass(frozen=True)
class RecordingPlan:
    """One venue's recorded instruments and the network (`environment`) they are recorded from."""

    venue: str
    environment: str
    instruments: tuple[str, ...]


def _string_list(raw: dict[str, Any], key: str) -> list[str]:
    values = raw.get(key, [])
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ValueError(f"`{key}` must be a list of instrument id strings, got {values!r}")
    return values


def parse_plan(text: str, venue: str) -> RecordingPlan:
    """
    Parse a venue `config.toml`'s text into the recorded plan. Raises `ValueError` (or
    `tomllib.TOMLDecodeError`, itself a `ValueError`) for a malformed file, a non-list
    `instruments`/`exclude`, a non-string `environment`, an id of another venue, or a file with
    no `instruments` key at all -- what a read landing mid-save sees (`collection_control`'s plan
    store rewrites the file in place, so it is briefly empty), which must keep the last good plan,
    not become an empty one. An explicit `instruments = []` is a plan that records nothing.
    """
    raw = tomllib.loads(text)
    if "instruments" not in raw:
        raise ValueError("no `instruments` key: an empty or partly written config file")
    environment = raw.get("environment", DEFAULT_ENVIRONMENT)
    if not isinstance(environment, str):
        raise ValueError(f"`environment` must be a string, got {environment!r}")
    environment = environment.lower()  # as the collector's `core_config_from_dict` reads it
    excluded = set(_string_list(raw, "exclude"))
    ids = tuple(i for i in dict.fromkeys(_string_list(raw, "instruments")) if i not in excluded)
    foreign = [i for i in ids if not has_venue(i, venue)]
    if foreign:
        raise ValueError(f"instruments of another venue than {venue}: {foreign}")
    return RecordingPlan(venue=venue, environment=environment, instruments=ids)
