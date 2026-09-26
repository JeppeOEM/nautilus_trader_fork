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
Architecture AD-8 inside the bots context: `bots` is the one sanctioned `TradingNode`/`Strategy`
runtime, and within it the runtime stays behind the anti-corruption layer -- only
`infrastructure/nautilus_host.py` imports `TradingNode`/`TradingNodeConfig`/`nautilus_trader.live`,
no module imports `DataEngine`, and a `Strategy` is imported only by the infrastructure (the ACL)
and `strategies/` (framework code), never by `domain/` or `application/`. Reads the import
statements with `ast` (a docstring may name any of them). The platform-wide rule is
`platform/tests/test_boundaries.py`'s `TradingNode` check.
"""

import ast
from pathlib import Path


_BOTS_DIR = Path(__file__).resolve().parent.parent
_HOST = _BOTS_DIR / "infrastructure" / "nautilus_host.py"


def _imported(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {alias.name for alias in node.names}
    return found


def _sources() -> list[Path]:
    return [p for p in _BOTS_DIR.rglob("*.py") if "tests" not in p.relative_to(_BOTS_DIR).parts]


def test_only_the_nautilus_host_imports_the_live_runtime() -> None:
    sources = _sources()
    assert _HOST in sources, "the Nautilus host module was not found"
    assert len(sources) > 10, "the bots modules were not found"
    runtime = {"TradingNode", "TradingNodeConfig", "DataEngine"}
    offenders = {
        str(path.relative_to(_BOTS_DIR)): sorted(names)
        for path in sources
        if path != _HOST
        and (
            names := {
                n for n in _imported(path) if n in runtime or n.startswith("nautilus_trader.live")
            }
        )
    }
    assert offenders == {}
    assert "DataEngine" not in _imported(_HOST)


def test_domain_and_application_never_import_a_strategy() -> None:
    offenders = [
        str(path.relative_to(_BOTS_DIR))
        for path in _sources()
        if path.relative_to(_BOTS_DIR).parts[0] in ("domain", "application")
        and ({"Strategy", "nautilus_trader.trading.strategy"} & _imported(path))
    ]
    assert offenders == []
