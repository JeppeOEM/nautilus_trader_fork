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
The `platform/` directory guard (DDD spine AD-D13), the one-Arrow-registration check for the
kernel's persisted `Data` classes, and the permanent no-shim rule.

`platform` is also a Python standard-library module. A directory without `__init__.py` is only a
namespace *portion*, which never shadows a regular module found later on `sys.path`; a
`platform/__init__.py` would turn it into a regular package and break `nautilus_trader`'s own
`import platform`. Both halves are asserted here, with the repo root first on `sys.path` exactly as
a script run from the repo root would see it.

The DDD migration's re-export shims (Stories 23.1-26.2) were all deleted by Story 26.3, and with
them the machinery that checked them. A moved module now moves with every caller in the same
commit; a re-export shim (a module that warns `DeprecationWarning` on import) or a moved-name
table is never added again (the spine's AD-D12 migration rule is closed), which
`test_no_module_is_a_migration_shim` holds.
"""

import ast
import importlib
import subprocess
import sys

from _source_tree import PLATFORM_DIR
from _source_tree import REPO_ROOT
from _source_tree import python_modules


# The module-level tables that served a moved or replaced name from a module that stayed (AD-D12).
_NAME_TABLES = frozenset({"_MOVED_NAMES", "_REPLACED_NAMES"})


# The categories a moved-name shim warns with; `warnings.warn` anywhere in a module (at import, or
# inside a lazy `__getattr__`) with one of them is the mark.
_DEPRECATION_CATEGORIES = frozenset(
    {"DeprecationWarning", "PendingDeprecationWarning", "FutureWarning"}
)


def _warns_deprecation(node: ast.AST) -> bool:
    """Tell a `warnings.warn(..., DeprecationWarning)` call, every re-export shim's mark."""
    if not isinstance(node, ast.Call):
        return False
    callee = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
    operands = [*node.args, *(keyword.value for keyword in node.keywords)]
    names = {n.id for operand in operands for n in ast.walk(operand) if isinstance(n, ast.Name)}
    return callee == "warn" and bool(names & _DEPRECATION_CATEGORIES)


def _shim_markers(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found |= {t.id for t in targets if isinstance(t, ast.Name)} & _NAME_TABLES
    found |= {
        f"DeprecationWarning at line {node.lineno}"
        for node in ast.walk(tree)
        if _warns_deprecation(node)
    }
    return found


def test_platform_dir_is_a_namespace_directory() -> None:
    assert PLATFORM_DIR.name == "platform"
    assert not (PLATFORM_DIR / "__init__.py").exists()


def test_stdlib_platform_wins_with_repo_root_on_sys_path() -> None:
    code = (
        "import importlib.util, sys; sys.path.insert(0, sys.argv[1]); "
        "print(importlib.util.find_spec('platform').origin)"
    )
    origin = subprocess.run(  # noqa: S603 (this interpreter, a fixed snippet)
        [sys.executable, "-c", code, str(REPO_ROOT)],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    ).stdout.strip()
    assert origin.endswith("platform.py"), origin
    assert not origin.startswith(str(REPO_ROOT)), origin


def test_no_module_is_a_migration_shim() -> None:
    shims = {
        module: sorted(found)
        for module, path in python_modules().items()
        if (found := _shim_markers(ast.parse(path.read_text())))
    }
    assert shims == {}, "move the callers with the module in one commit; never a shim (Story 26.3)"


def test_shim_marker_scan_sees_each_marker_shape() -> None:
    tree = ast.parse(
        "import warnings\n_MOVED_NAMES: dict[str, str] = {}\nOTHER = 1\n"
        "warnings.warn('moved', DeprecationWarning, stacklevel=2)\n"
        "warnings.warn('moved', category=DeprecationWarning)\n"
        "warnings.filterwarnings('ignore', category=DeprecationWarning)\n"
        "_REPLACED_NAMES = {}\n"
        "def __getattr__(name):\n"
        "    warnings.warn(f'{name} moved', FutureWarning, stacklevel=2)\n"
    )
    assert _shim_markers(tree) == {
        "_MOVED_NAMES",
        "DeprecationWarning at line 4",
        "DeprecationWarning at line 5",
        "_REPLACED_NAMES",
        "DeprecationWarning at line 9",
    }


# Kernel `Data` classes whose `__name__` is a persistence identifier (catalog directory name).
_KERNEL_DATA_CLASSES = ("DydxSecondSnapshot", "OpenInterest")


def test_each_kernel_data_class_is_registered_for_arrow_exactly_once() -> None:
    """
    Story 23.2: a second class registered under a persisted name (a copied class body anywhere)
    would get its own catalog directory and break `is` dispatch. Import the kernel modules that
    define them, then count the serializer's registrations.
    """
    from nautilus_trader.serialization.arrow.serializer import _SCHEMAS

    for module in ("kernel.second_snapshot", "kernel.open_interest"):
        importlib.import_module(module)
    names = [cls.__name__ for cls in _SCHEMAS]
    counts = {name: names.count(name) for name in _KERNEL_DATA_CLASSES}
    assert counts == dict.fromkeys(_KERNEL_DATA_CLASSES, 1)
