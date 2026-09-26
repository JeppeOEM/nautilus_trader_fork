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
The archive context's structural rules, read with `ast` over its non-test modules (Story 25.1):
only `infrastructure/catalog_files.py` writes, renames or removes files; only
`application/prune.py` calls the deleter; `archive.infrastructure`/`candles.infrastructure` are
imported only by the composition roots; and no venue collector keeps a prune loop.
"""

import ast
from pathlib import Path

import pytest


_ARCHIVE = Path(__file__).resolve().parents[1]
_PLATFORM = _ARCHIVE.parent
_REWRITER = _ARCHIVE / "infrastructure" / "catalog_files.py"
_DELETE_CALLER = _ARCHIVE / "application" / "prune.py"
# File-mutating calls: a Parquet write, a rename over a file, a file or directory removal. The
# names another API shares (`datetime.replace`, `list.remove`, `str.rename`...) count only on the
# module that makes them file operations.
_MUTATIONS = frozenset(
    {
        "write_table",
        "ParquetWriter",
        "replace",
        "rename",
        "unlink",
        "remove",
        "rmdir",
        "rmtree",
        "move",
    }
)
_OWNED = {"replace": "os", "remove": "os", "rename": "os", "move": "shutil"}
# AC2: the only offline `ParquetDataCatalog.write_data()` callers (and repair's range delete).
_CATALOG_WRITES = frozenset({"write_data", "delete_data_range"})
_CATALOG_WRITERS = {
    _ARCHIVE / "application" / "backfill_bars.py",
    _ARCHIVE / "application" / "repair.py",
}
_INFRASTRUCTURE = ("archive.infrastructure", "candles.infrastructure")


def _sources() -> dict[Path, ast.Module]:
    return {
        path: ast.parse(path.read_text())
        for path in sorted(_ARCHIVE.rglob("*.py"))
        if "tests" not in path.relative_to(_ARCHIVE).parts
    }


_SOURCES = _sources()


def _called(tree: ast.Module, names: frozenset[str]) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        owner = getattr(func.value, "id", None) if isinstance(func, ast.Attribute) else None
        if name in names and (name not in _OWNED or owner == _OWNED[name]):
            found.append(f"{name}:{node.lineno}")
    return found


def _is_composition_root(path: Path) -> bool:
    relative = path.relative_to(_ARCHIVE)
    top_level_cli = len(relative.parts) == 1 and relative.name != "__init__.py"
    return top_level_cli or relative.parts[0] == "tools"


def _imports(tree: ast.Module) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
    return found


def test_the_scan_sees_the_whole_context() -> None:
    names = {path.relative_to(_ARCHIVE).as_posix() for path in _SOURCES}
    assert {"infrastructure/catalog_files.py", "application/prune.py", "nightly.py"} <= names


def test_only_catalog_files_writes_renames_or_removes_a_file() -> None:
    offenders = {
        str(path.relative_to(_ARCHIVE)): calls
        for path, tree in _SOURCES.items()
        if path != _REWRITER and (calls := _called(tree, _MUTATIONS))
    }
    assert offenders == {}, "go through CatalogWriter (archive.infrastructure.catalog_files)"
    assert _called(_SOURCES[_REWRITER], _MUTATIONS), "the rewriter itself must still be seen"


def test_only_backfill_and_repair_write_through_the_nautilus_catalog() -> None:
    callers = {path for path, tree in _SOURCES.items() if _called(tree, _CATALOG_WRITES)}
    assert callers == _CATALOG_WRITERS


def test_the_scan_sees_every_spelling_of_a_file_mutation() -> None:
    tree = ast.parse(
        "os.rename(a, b)\nshutil.move(a, b)\npq.ParquetWriter(p, s)\nParquetWriter(p, s)\n"
        "t.replace(tzinfo=None)\nname.rename('x')\nitems.remove(1)\n"
    )
    assert _called(tree, _MUTATIONS) == [
        "rename:1",
        "move:2",
        "ParquetWriter:3",
        "ParquetWriter:4",
    ]


def test_only_the_prune_service_calls_the_deleter() -> None:
    callers = {
        str(path.relative_to(_ARCHIVE))
        for path, tree in _SOURCES.items()
        if path != _REWRITER and _called(tree, frozenset({"delete"}))
    }
    assert callers == {str(_DELETE_CALLER.relative_to(_ARCHIVE))}


def test_infrastructure_is_imported_only_by_the_composition_roots() -> None:
    offenders = {
        str(path.relative_to(_ARCHIVE)): targets
        for path, tree in _SOURCES.items()
        if not _is_composition_root(path) and "infrastructure" not in path.parts
        if (targets := [t for t in _imports(tree) if t.startswith(_INFRASTRUCTURE)])
    }
    assert offenders == {}, "application/domain code declares a port; the CLI wires the adapter"


def test_no_module_level_mutable_state_in_the_context() -> None:
    mutable = (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)
    offenders = [
        f"{path.relative_to(_ARCHIVE)}:{node.lineno}"
        for path, tree in _SOURCES.items()
        for node in tree.body
        if isinstance(node, ast.Assign | ast.AnnAssign) and isinstance(node.value, mutable)
    ]
    assert offenders == []


@pytest.mark.parametrize("venue", ["dydx_collector", "bybit_collector", "hyperliquid_collector"])
def test_no_venue_collector_keeps_a_prune_loop(venue: str) -> None:
    tree = ast.parse((_PLATFORM / venue / "collector.py").read_text())
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert not {name for name in defined if name.startswith("_prune")}, "retention is archive's"
    assert "prune_instrument" not in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
