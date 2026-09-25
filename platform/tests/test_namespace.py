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
The `platform/` directory guard (DDD spine AD-D13) and the migration shims' contract (AD-D12).

`platform` is also a Python standard-library module. A directory without `__init__.py` is only a
namespace *portion*, which never shadows a regular module found later on `sys.path`; a
`platform/__init__.py` would turn it into a regular package and break `nautilus_trader`'s own
`import platform`. Both halves are asserted here, with the repo root first on `sys.path` exactly as
a script run from the repo root would see it.

Shims: a moved module leaves a pure re-export at its old path (`REMOVE_AFTER = "<story key>"`); a
name moved out of a module that stays is served by the module's `__getattr__` from a
`_MOVED_NAMES` table (`MOVED_NAMES_REMOVE_AFTER`). Every old name must be the *same object* as
its successor (a copy would register a second Arrow class and break `is` dispatch), must warn,
and must be gone once its story is `done` on the sprint board. Shims are found statically in the
source tree, so a new one is checked without editing this file. No in-repo module may import an
old path: a shim's `DeprecationWarning` is attributed to the caller, which the test run only
lists, never fails on, so the stale caller is caught here statically instead.
"""

import ast
import contextlib
import importlib
import importlib.util
import re
import subprocess
import sys
from typing import NamedTuple

import pytest
from _source_tree import PLATFORM_DIR
from _source_tree import REPO_ROOT
from _source_tree import imports_of
from _source_tree import python_modules
from _source_tree import story_statuses
from _source_tree import unknown_or_done


class _ShimName(NamedTuple):
    shim: str  # the module serving the old name
    name: str
    target: str  # dotted path of the successor object
    whole_module: bool  # a re-export module (warns on import) vs a moved name (warns on access)


def _constant(module: str, tree: ast.Module, name: str) -> object:
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets) and node.value:
                try:
                    return ast.literal_eval(node.value)
                except ValueError as e:
                    pytest.fail(f"{module}.{name} must be a literal the shim scanner can read: {e}")
    return None


def _shim_names(module: str, tree: ast.Module) -> list[_ShimName]:
    if _constant(module, tree, "REMOVE_AFTER") is not None:
        # A re-export shim binds every old name with `from <new> import <name>`: an `import a.b
        # as c` would bind a name this scanner never checks for identity or its warning.
        plain = [
            alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name != "warnings"
        ]
        if plain:
            pytest.fail(f"{module}: a re-export shim binds names only with `from`: {plain}")
        return [
            _ShimName(module, alias.asname or alias.name, f"{node.module}.{alias.name}", True)
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module and node.module != "warnings"
            for alias in node.names
        ]
    moved = _constant(module, tree, "_MOVED_NAMES")
    if isinstance(moved, dict) and _constant(module, tree, "MOVED_NAMES_REMOVE_AFTER") is not None:
        return [_ShimName(module, name, target, False) for name, target in moved.items()]
    return []


def _trees() -> dict[str, ast.Module]:
    return {name: ast.parse(path.read_text()) for name, path in _MODULES.items()}


_MODULES = python_modules()
_TREES = _trees()
_SHIM_NAMES = [shim for module, tree in _TREES.items() for shim in _shim_names(module, tree)]


def _expiry(module: str, tree: ast.Module) -> str | None:
    found = _constant(module, tree, "REMOVE_AFTER") or _constant(
        module, tree, "MOVED_NAMES_REMOVE_AFTER"
    )
    return str(found) if found else None


def _replaced_names(module: str, tree: ast.Module) -> list[tuple[str, str, str]]:
    """Return (module, old name, successor) for every name its `_REPLACED_NAMES` makes raise."""
    replaced = _constant(module, tree, "_REPLACED_NAMES")
    if not isinstance(replaced, dict):
        return []
    return [(module, name, successor) for name, successor in replaced.items()]


_EXPIRIES = {module: expiry for module, tree in _TREES.items() if (expiry := _expiry(module, tree))}
_REPLACED = [entry for module, tree in _TREES.items() for entry in _replaced_names(module, tree)]


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


def _warns_deprecation(tree: ast.Module) -> bool:
    """Return whether the module itself issues a `DeprecationWarning` via `warnings.warn`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        callee = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else getattr(node.func, "id", None)
        )
        if callee != "warn":
            continue
        operands = [*node.args, *(keyword.value for keyword in node.keywords)]
        names = {n.id for operand in operands for n in ast.walk(operand) if isinstance(n, ast.Name)}
        if "DeprecationWarning" in names:
            return True
    return False


def test_every_deprecation_shim_declares_its_expiry() -> None:
    """A module warning `DeprecationWarning` is a shim, and a shim without an expiry never dies."""
    unexpiring = [
        module
        for module, tree in _TREES.items()
        if _warns_deprecation(tree) and module not in _EXPIRIES and ".tests." not in f".{module}."
    ]
    assert unexpiring == [], "declare REMOVE_AFTER or MOVED_NAMES_REMOVE_AFTER in each"
    assert _SHIM_NAMES, "shims exist (Story 23.1) but none were parsed: the scanner is broken"


def test_scanner_rejects_a_shim_that_binds_a_name_with_a_plain_import() -> None:
    tree = ast.parse(
        'REMOVE_AFTER = "x"\nimport warnings\nimport observability.error_ledger as error_ledger\n'
    )
    with pytest.raises(pytest.fail.Exception, match="binds names only with `from`"):
        _shim_names("old.path", tree)


def test_scanner_reads_only_a_warn_call_as_a_shim() -> None:
    shim = ast.parse("import warnings\nwarnings.warn('gone', DeprecationWarning, stacklevel=2)\n")
    keyword = ast.parse("import warnings\nwarnings.warn('gone', category=DeprecationWarning)\n")
    filtered = ast.parse(
        "import warnings\nwarnings.filterwarnings('ignore', category=DeprecationWarning)\n"
    )
    assert _warns_deprecation(shim)
    assert _warns_deprecation(keyword)
    assert not _warns_deprecation(filtered)


def test_no_module_imports_an_old_path() -> None:
    """
    Story 23.1's second acceptance criterion, mechanised: the only mentions of a moved module, a
    moved name or a replaced name are the shims that serve them. A stale caller would only warn,
    and the test run lists warnings without failing on them.
    """
    whole = {shim.shim for shim in _SHIM_NAMES if shim.whole_module}
    served = {(shim.shim, shim.name) for shim in _SHIM_NAMES if not shim.whole_module}
    replaced = {(module, name) for module, name, _ in _REPLACED}
    known = set(_MODULES)
    stale = [
        f"{module}:{ref.line} takes {ref.name or 'the module'} from {ref.target}"
        for module, path in _MODULES.items()
        if module not in whole
        for ref in imports_of(module, path, known)
        if ref.target in whole or (ref.target, ref.name) in served | replaced
    ]
    assert stale == [], "repoint these callers to the successor (the shim's docstring names it)"


@pytest.mark.parametrize(("module", "story"), sorted(_EXPIRIES.items()))
def test_shim_expiry_story_is_not_done(module: str, story: str) -> None:
    reason = unknown_or_done(story, story_statuses())
    assert reason is None, f"{module}: {reason} -- delete the shim, its callers are migrated"


def _resolve(dotted: str) -> object:
    """Import the longest importable prefix of `dotted`, then walk the rest as attributes."""
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            obj: object = importlib.import_module(".".join(parts[:cut]))
        except ModuleNotFoundError:
            continue
        for attr in parts[cut:]:
            obj = getattr(obj, attr)
        return obj
    raise ModuleNotFoundError(dotted)


def _shipped(module: str) -> bool:
    """
    Whether this image ships `module`, found by its full dotted name: a stranger package that
    merely shares the top-level name (`common`) must not make a shim look shipped.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:  # an absent parent package
        return False


def _require_shipped(module: str) -> None:
    """
    Skip, with the reason shown, a shim whose package this image does not ship.

    The live_paper image deliberately ships no collector (AD-8): there a shim of an absent
    package cannot be imported. `make test` (collector image) checks every one.
    """
    if not _shipped(module):
        pytest.skip(f"{module} is not shipped in this image; checked by `make test`")


def _import_fresh(module: str, monkeypatch: pytest.MonkeyPatch) -> object:
    """Re-import a re-export shim so its import-time warning fires again; restored afterwards."""
    parent_name, _, child = module.rpartition(".")
    parent = importlib.import_module(parent_name)
    if hasattr(parent, child):
        monkeypatch.setattr(parent, child, getattr(parent, child))
    monkeypatch.delitem(sys.modules, module, raising=False)
    return importlib.import_module(module)


def _old_name(shim: _ShimName, monkeypatch: pytest.MonkeyPatch) -> object:
    """Read the old name the way a stale caller would: import the shim, or access the name."""
    if shim.whole_module:
        return getattr(_import_fresh(shim.shim, monkeypatch), shim.name)
    return getattr(importlib.import_module(shim.shim), shim.name)


@pytest.mark.parametrize("shim", _SHIM_NAMES, ids=lambda s: f"{s.shim}.{s.name}")
def test_old_name_warns_and_is_the_new_object(
    shim: _ShimName, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_shipped(shim.shim)
    with pytest.warns(DeprecationWarning, match=_EXPIRIES[shim.shim]):
        old = _old_name(shim, monkeypatch)
    assert old is _resolve(shim.target), f"{shim.shim}.{shim.name} is a copy of {shim.target}"


@pytest.mark.parametrize("entry", _REPLACED, ids=lambda e: f"{e[0]}.{e[1]}")
def test_replaced_name_raises_naming_its_successor(entry: tuple[str, str, str]) -> None:
    """A name whose successor changed shape is never served: a stale caller fails loudly."""
    module, name, successor = entry
    _require_shipped(module)
    with pytest.raises(AttributeError, match=re.escape(successor)):
        getattr(importlib.import_module(module), name)


def test_replaced_names_are_not_also_served() -> None:
    assert _REPLACED, "replaced names exist (Story 23.1) but none were parsed"
    served = {(shim.shim, shim.name) for shim in _SHIM_NAMES}
    assert {(module, name) for module, name, _ in _REPLACED} & served == set()


def test_scanner_reads_both_shim_shapes() -> None:
    whole = ast.parse(
        "import warnings\nfrom new.mod import a\nREMOVE_AFTER = 'k'\nwarnings.warn('x')\n"
    )
    moved = ast.parse("MOVED_NAMES_REMOVE_AFTER = 'k'\n_MOVED_NAMES = {'_old': 'new.mod.b'}\n")
    assert _shim_names("old.mod", whole) == [_ShimName("old.mod", "a", "new.mod.a", True)]
    assert _shim_names("old.mod", moved) == [_ShimName("old.mod", "_old", "new.mod.b", False)]


# Kernel `Data` classes whose `__name__` is a persistence identifier (catalog directory name).
_KERNEL_DATA_CLASSES = ("DydxSecondSnapshot", "OpenInterest")


def test_each_kernel_data_class_is_registered_for_arrow_exactly_once() -> None:
    """
    Story 23.2: a shim that copied a class body would register a second class under the same name
    (and its catalog directory). Import the kernel modules and every shim of them, then count.
    """
    from nautilus_trader.serialization.arrow.serializer import _SCHEMAS

    for module in ("kernel.second_snapshot", "kernel.open_interest"):
        importlib.import_module(module)
    whole = {s.shim for s in _SHIM_NAMES if s.whole_module and s.target.startswith("kernel.")}
    for shim in sorted(whole):
        if not _shipped(shim):
            continue  # not shipped in this image (live_paper); `make test` imports every one
        fresh = shim not in sys.modules
        with (
            pytest.warns(DeprecationWarning, match="Story 23.2")
            if fresh
            else contextlib.nullcontext()
        ):
            importlib.import_module(shim)
    names = [cls.__name__ for cls in _SCHEMAS]
    counts = {name: names.count(name) for name in _KERNEL_DATA_CLASSES}
    assert counts == dict.fromkeys(_KERNEL_DATA_CLASSES, 1)
