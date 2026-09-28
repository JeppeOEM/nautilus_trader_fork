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
ships (no `bots`, no `docker-compose.yml`) and is not even named `platform`: reading it
would silently check a subset. So the static tests read the whole checkout, mounted read-only
at `PLATFORM_SOURCE_DIR` (`make test` sets `/src/platform`); run on a host from the checkout,
it defaults to this directory's parent. A tree without `docker-compose.yml` fails loudly
instead of skipping, because a skipped guard is a loosened guard.
"""

import ast
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
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


# The checkout's text files, for the source sweeps (`test_legacy_names`, `test_notebook_rules`;
# moved here from the former in Story 27.9). A sweep reads the tracked files with `git grep`, the
# acceptance greps' own tool. Where no git can read the checkout -- `make test`'s collector image
# ships no git binary, and git's safe.directory refuses a mount owned by another uid -- it walks the
# checkout's text files instead, so each sweep runs in the canonical test run too.

# A sweep never reads the history notes (`docs/`) or the planning files: a note that records a
# retired name is history, not a stale reference.
HISTORY_DIRS = ("docs", ".planning")

# What the walk skips: git-ignored or generated trees and the history directories. `data/` is
# runtime state except its committed top-level `.toml` configs, which the walk reads. Every tracked
# text file must be walked: `test_legacy_names.test_walk_reads_every_tracked_text_file` fails the
# day a new suffix or directory is committed.
# Git-ignored trees, skipped by every no-git fallback as `git ls-files --exclude-standard` does.
IGNORED_DIRS = frozenset(
    {"data", "node_modules", "dist", "__pycache__", ".git", ".venv"}
    | {".pytest_cache", ".mypy_cache", ".ruff_cache", ".ipynb_checkpoints"}
)
_WALK_SKIP_DIRS = IGNORED_DIRS | frozenset(HISTORY_DIRS)
_WALK_SUFFIXES = frozenset(
    {".py", ".md", ".toml", ".yml", ".yaml", ".ts", ".tsx", ".css", ".json", ".txt", ".sh"}
    | {".ipynb", ".dockerfile", ".html", ".go", ".mjs", ".sql", ".jsonl", ".svg"}
)
_WALK_NAMES = frozenset({"Makefile", ".gitignore", ".env-example"})


def walked_files(top: Path = PLATFORM_DIR) -> Iterator[Path]:
    """Every text file the walk reads under `top` (the platform), in sorted order."""
    yield from _walk(top, top)


def _walk(top: Path, directory: Path) -> Iterator[Path]:
    for path in sorted(directory.iterdir()):
        if path.is_dir():
            # Only the top-level `docs/` is a history-notes exclusion; `frontend/.../docs/` is copy.
            nested_docs = path.name == "docs" and path.parent != top
            if path.name not in _WALK_SKIP_DIRS or nested_docs:
                yield from _walk(top, path)
            elif path == top / "data":
                yield from sorted(path.glob("*.toml"))
        elif path.suffix in _WALK_SUFFIXES or path.name in _WALK_NAMES:
            yield path


def walk_hits(
    pattern: re.Pattern[str],
    skip: frozenset[str] = frozenset(),
    top: Path = PLATFORM_DIR,
) -> list[str]:
    """
    `git grep -n` output (`path:line:text`, paths relative to `top`) rebuilt by walking `top`'s
    text files; `skip` holds relative paths never read (a guard's own file).
    """
    hits = []
    for path in walked_files(top):
        relative = path.relative_to(top).as_posix()
        if relative in skip:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # `\n` only, as git numbers lines: `splitlines()` also splits on `\f`, `\x1c`, U+2028...
        for number, line in enumerate(text.split("\n"), start=1):
            if pattern.search(line):
                hits.append(f"{relative}:{number}:{line}")
    return hits


def _git(*args: str) -> subprocess.CompletedProcess[str] | None:
    """`git <args>` run in the platform checkout, or None where no git can read it."""
    if shutil.which("git") is None:
        return None
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],  # noqa: S607
        cwd=PLATFORM_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return None  # e.g. git's safe.directory refusing a mount owned by another uid
    # `core.quotePath=false`: a non-ASCII path prints as itself, as the walk spells it.
    return subprocess.run(  # noqa: S603 (git with the caller's fixed arguments)
        ["git", "-c", "core.quotePath=false", *args],  # noqa: S607
        cwd=PLATFORM_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


def _sweep_pathspec(skip: frozenset[str]) -> list[str]:
    excluded = [*HISTORY_DIRS, *sorted(skip)]
    return ["--", ".", *(f":(exclude){path}" for path in excluded)]


def grep_hits(pattern: re.Pattern[str], skip: frozenset[str] = frozenset()) -> list[str]:
    r"""
    Every `path:line:text` of a tracked text file under the platform, outside the history
    directories and `skip`, that `pattern` matches. `pattern` is handed to `git grep -E` as is, so
    it must mean the same in POSIX ERE and Python `re` (no `\d`, lookarounds or lazy repeats).
    """
    found = _git("grep", "-n", "-I", "-E", pattern.pattern, *_sweep_pathspec(skip))
    if found is None:
        return walk_hits(pattern, skip)
    assert found.returncode in (0, 1), found.stderr  # 1: no hit at all
    return found.stdout.splitlines()


def tracked_text_files(skip: frozenset[str] = frozenset()) -> set[str] | None:
    """
    Return the tracked text files `grep_hits` reads (a binary file prints no line to judge), or
    None where no git can list them.
    """
    listed = _git("grep", "-I", "-l", "-e", "", *_sweep_pathspec(skip))
    if listed is None:
        return None
    # A git that runs but fails fails the guard, never skips it: a skipped guard is loosened.
    assert listed.returncode in (0, 1), listed.stderr
    return set(listed.stdout.splitlines())


def listed_files(pattern: str) -> list[str] | None:
    """
    Every file under the platform, history directories included, whose path matches the git
    pathspec `pattern` and that is tracked or untracked-but-not-ignored (a stray file is caught
    before its commit), sorted; None where no git can list them.
    """
    listed = _git("ls-files", "--cached", "--others", "--exclude-standard", "--", pattern)
    if listed is None:
        return None
    assert listed.returncode == 0, listed.stderr
    return sorted(set(listed.stdout.splitlines()))
