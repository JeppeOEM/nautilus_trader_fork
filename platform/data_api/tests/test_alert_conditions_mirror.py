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
Story 33.8: the alert forms' hand-declared kinds and fields (`frontend/src/lib/alertConditions.ts`)
mirror `alerting.domain.conditions`' `CONDITION_KINDS`/`CONDITION_FIELDS`, so a kind or a field
added on one side and not the other fails here, not as a 422 the operator meets in the form.
"""

import os
import re
from pathlib import Path

from alerting.domain.conditions import CONDITION_FIELDS
from alerting.domain.conditions import CONDITION_KINDS
from alerting.domain.conditions import LEVEL_KINDS


# `frontend/` is source, never shipped in an image: `make test` mounts the checkout at
# PLATFORM_SOURCE_DIR; a host run finds it two levels up (as in test_ranking_columns_mirror.py).
_SOURCE = os.environ.get("PLATFORM_SOURCE_DIR")
_PLATFORM_DIR = Path(_SOURCE) if _SOURCE else Path(__file__).resolve().parents[2]
_TS = _PLATFORM_DIR / "frontend" / "src" / "lib" / "alertConditions.ts"
_TS_KINDS = re.compile(r"export const CONDITION_KINDS = \[(.*?)\] as const;", re.DOTALL)
_TS_FIELDS = re.compile(
    r"export const CONDITION_FIELDS: Record<ConditionKind, readonly string\[\]> = \{(.*?)\n\};",
    re.DOTALL,
)
_TS_LEVEL_KINDS = re.compile(
    r"export const LEVEL_KINDS: readonly ConditionKind\[\] = \[(.*?)\];", re.DOTALL
)
_TS_FIELD_ENTRY = re.compile(r"(\w+): \[(.*?)\]")


def _block(pattern: re.Pattern[str]) -> str:
    found = pattern.search(_TS.read_text())
    assert found is not None, f"{pattern.pattern[:40]}... not found in {_TS}"
    return found.group(1)


def test_ts_kinds_mirror_the_domain_kinds_in_order() -> None:
    assert tuple(re.findall(r'"([^"]+)"', _block(_TS_KINDS))) == CONDITION_KINDS


def test_ts_fields_mirror_the_domain_fields_in_order() -> None:
    fields = {
        kind: tuple(re.findall(r'"([^"]+)"', names))
        for kind, names in _TS_FIELD_ENTRY.findall(_block(_TS_FIELDS))
    }
    assert fields == CONDITION_FIELDS


def test_ts_level_kinds_mirror_the_domain_level_kinds() -> None:
    assert tuple(re.findall(r'"([^"]+)"', _block(_TS_LEVEL_KINDS))) == LEVEL_KINDS
