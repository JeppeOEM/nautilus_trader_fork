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
Where the cross-cutting static tests read the source tree, and the sprint board they expire on.

`make test` runs inside the collector image, whose `/app` holds only the packages that image
ships (no `live_paper`, no `docker-compose.yml`) and is not even named `platform`: reading it
would silently check a subset. So the static tests read the whole checkout, mounted read-only
at `PLATFORM_SOURCE_DIR` (`make test` sets `/src/platform`); run on a host from the checkout,
it defaults to this directory's parent. A tree without `docker-compose.yml` fails loudly
instead of skipping, because a skipped guard is a loosened guard.
"""

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path


def _platform_dir() -> Path:
    configured = os.environ.get("PLATFORM_SOURCE_DIR")
    path = Path(configured) if configured else Path(__file__).resolve().parents[1]
    if not (path / "docker-compose.yml").is_file():
        raise RuntimeError(
            f"{path} is not the platform/ source tree (no docker-compose.yml). Mount the checkout "
            "read-only and set PLATFORM_SOURCE_DIR, as `make test` does "
            "(-v $(REPO_ROOT):/src:ro -e PLATFORM_SOURCE_DIR=/src/platform)."
        )
    return path


PLATFORM_DIR = _platform_dir()
REPO_ROOT = PLATFORM_DIR.parent
SPRINT_STATUS = REPO_ROOT / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"

_STATUS_ROW = re.compile(r"^\s{2}([0-9a-z-]+):\s*([a-z-]+)")


def story_statuses() -> dict[str, str]:
    """
    Every `development_status` row of the sprint board, story key -> status. Parsed with a line
    regex (no YAML dependency in the image); the board's layout is two-space-indented rows.
    """
    if not SPRINT_STATUS.is_file():
        raise RuntimeError(f"{SPRINT_STATUS} is missing: legacy-entry expiry cannot be judged")
    statuses: dict[str, str] = {}
    in_block = False
    for line in SPRINT_STATUS.read_text().splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            in_block = line.startswith("development_status:")
            continue
        match = _STATUS_ROW.match(line) if in_block else None
        if match:
            statuses[match.group(1)] = match.group(2)
    return statuses


def unknown_or_done(story_key: str, statuses: dict[str, str]) -> str | None:
    """
    Return why a legacy entry keyed on `story_key` is no longer honoured, or None. `superseded`
    is the board's other terminal status ("will never ship"): the entry's retirement then needs
    a new owner, so it is not honoured either.
    """
    status = statuses.get(story_key)
    if status is None:
        return f"story {story_key!r} is not on the sprint board (typo, or renamed)"
    if status == "done":
        return f"story {story_key!r} is done"
    if status == "superseded":
        return f"story {story_key!r} is superseded: re-key the entry to the story that retires it"
    return None


# Never Python sources of the platform: the SPA and its dependencies, the durable stores (the
# catalog is huge), environments, build output and tool caches. Pruned during the walk, so
# `make test` never descends into them. The first set is matched only directly under
# `platform/`: a package may well have a subdirectory called `data` or `build`, and pruning it
# would hide its modules from every guard.
_EXCLUDED_TOP_DIRS = frozenset({"frontend", "data", ".planning", "build"})
_EXCLUDED_DIRS_ANYWHERE = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "site-packages",
    }
)


def python_modules(root: Path = PLATFORM_DIR) -> dict[str, Path]:
    """
    Every Python module under `root` (the platform), dotted name (relative to `root`, a
    package's `__init__` named after the package) -> path. Sorted, so every test built on it is
    stable. Two files for one name (`a/b.py` beside `a/b/__init__.py`) fail: one of them would
    be hidden from every guard, and Python itself imports only the package.
    """
    modules: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        excluded = _EXCLUDED_DIRS_ANYWHERE | (
            _EXCLUDED_TOP_DIRS if Path(dirpath) == root else frozenset()
        )
        dirnames[:] = sorted(name for name in dirnames if name not in excluded)
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = Path(dirpath) / filename
            parts = path.relative_to(root).with_suffix("").parts
            dotted = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            if dotted in modules:
                raise RuntimeError(f"{path} and {modules[dotted]} are both module {dotted}")
            modules[dotted] = path
    return dict(sorted(modules.items()))


@dataclass(frozen=True)
class ImportRef:
    """
    One thing a module takes from another: `name` of module `target`, or the module itself when
    `name` is None. A module bound by an import (`from a import b`, `import a.b as c`) is resolved
    to the attributes read from it (`b.x`), so a split module's symbols and a `_private` name
    reached through a module alias are both seen.
    """

    target: str
    name: str | None
    line: int


def _absolute(module: str, is_package: bool, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    base = module.split(".") if is_package else module.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else None
    return None


def _module_attrs(tree: ast.Module, binding: str, module: str, known: set[str]) -> list[ImportRef]:
    """Attributes read from `module` through the local name `binding` (`binding` may be a prefix)."""
    refs: list[ImportRef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Load):
            continue
        chain = _dotted(node)
        if chain is None or not chain.startswith(binding + "."):
            continue
        full = module + chain[len(binding) :]
        head, _, attr = full.rpartition(".")
        # Only the innermost module in the chain: `a.b.c.x` with `a.b.c` a known module.
        if head == module or (head in known and head.startswith(module + ".")):
            refs.append(ImportRef(head, attr, node.lineno))
    return refs


def imports_of(module: str, path: Path, known: set[str]) -> list[ImportRef]:
    """
    Every import in `path` (module `module`), anywhere in the file: a function-level import
    runs too. `known` is the set of in-repo modules, used to tell `from a import b` of a module
    from that of a name and to resolve module aliases. External imports keep their dotted name.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    is_package = path.name == "__init__.py"
    refs: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                binding = alias.asname or alias.name.split(".")[0]
                bound = alias.name if alias.asname else alias.name.split(".")[0]
                refs.extend(_bound_module(tree, alias.name, binding, bound, node.lineno, known))
        elif isinstance(node, ast.ImportFrom):
            source = _absolute(module, is_package, node)
            for alias in node.names:
                submodule = f"{source}.{alias.name}"
                if submodule in known:
                    binding = alias.asname or alias.name
                    refs.extend(
                        _bound_module(tree, submodule, binding, submodule, node.lineno, known)
                    )
                else:
                    refs.append(ImportRef(source, alias.name, node.lineno))
    return refs


def _bound_module(
    tree: ast.Module, module: str, binding: str, bound: str, line: int, known: set[str]
) -> list[ImportRef]:
    if module not in known:
        return [ImportRef(module, None, line)]
    attrs = _module_attrs(tree, binding, bound, known)
    used = [ref for ref in attrs if ref.target == module or ref.target.startswith(module + ".")]
    return used or [ImportRef(module, None, line)]
