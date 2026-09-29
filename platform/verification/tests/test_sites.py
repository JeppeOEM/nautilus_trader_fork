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
"""Every verification ledger site is named once in `verification/application/sites.py` and used."""

import ast
from pathlib import Path

from verification.application import sites


_CONTEXT = Path(__file__).resolve().parents[1]
_SITES_FILE = _CONTEXT / "application" / "sites.py"
_PREFIXES = (
    "verification.recorder.",
    "verification.conservation.",
    "verification.trades.",
    "verification.book.",
    "verification.derivs.",
    "verification.catalog.",
)


def _production_sources() -> list[Path]:
    return [p for p in _CONTEXT.rglob("*.py") if "tests" not in p.relative_to(_CONTEXT).parts]


def _declared() -> dict[str, str]:
    return {name: value for name, value in vars(sites).items() if name.isupper()}


def test_each_site_is_one_distinct_prefixed_constant() -> None:
    declared = _declared()
    assert declared, "no ledger site declared"
    assert all(value.startswith(_PREFIXES) for value in declared.values())
    assert len(set(declared.values())) == len(declared)


def test_no_module_spells_a_site_literally() -> None:
    literal = [
        f"{path.relative_to(_CONTEXT)}:{node.lineno}"
        for path in _production_sources()
        if path != _SITES_FILE
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(_PREFIXES)
    ]
    assert literal == [], "report through `verification.application.sites` constants"


def test_every_site_is_reported_somewhere() -> None:
    used = {
        node.attr
        for path in _production_sources()
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sites"
    }
    assert sorted(set(_declared()) - used) == [], "a site nothing reports: delete it"
