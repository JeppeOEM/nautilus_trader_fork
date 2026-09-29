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
The DDD migration's legacy package names stay gone (Story 26.3, spine AD-D12).

Stories 23.1-26.2 moved every module out of `ml_signals`, `collector_core`, `common`, the three
`<venue>_collector` packages, `ranking_engine` and `live_paper`, and Story 26.3 deleted the last
re-export shims. Any mention of those names left in `platform/` (outside the `docs/` history notes
and `.planning/`) is a stale reference -- an import, a path, a comment pointing at code that no
longer exists -- except the published-language identifiers the migration froze on purpose:
compose service names, error-ledger file and site names, the dYdX plan's container mount path and
the bots' host store directory. Renaming those is a post-migration story with operator actions
(the DDD spine's Deferred list), so each is allowed here by one narrow pattern with its reason,
and nothing else is.

Reads the checkout with `git grep` (tracked files, exactly the story's acceptance grep). Where no
git can read it -- `make test`'s collector image ships no git binary -- it walks the mounted
checkout's text files instead, skipping what git ignores here (`data/` but its committed
`.toml` configs, build and cache directories), so the guard runs in the canonical test run too;
on a host, `test_walk_reads_every_tracked_text_file` holds the walk to git's file set.
"""

import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from _source_tree import PLATFORM_DIR


LEGACY_NAMES = (
    r"ml_signals|collector_core|dydx_collector|bybit_collector|hyperliquid_collector"
    r"|ranking_engine|live_paper|common\.venues"
)
_LEGACY = re.compile(LEGACY_NAMES)

# The ranking process's error-ledger sites (`ranking/`): published language, read by
# `GET /api/errors`, `archive.crosscheck_errors --expect` and the durable `.jsonl` history. A site
# added later keeps the process's one prefix (Story 29.5's `markets`, the `markets:live` publish).
_RANKING_SITES = (
    "volume24h|message|snapshot_entry|publish|price_backfill|metrics_history|slow_loop|redis"
    "|markets"
)


# A service name is never a module or a path: `import x`, `from x`, `-m x`, `import_module("x`,
# `<dir>/x` or `<dir>\x` in front of one is a stale package reference, whatever the allowances
# below would accept.
_STALE_CONTEXT = re.compile(
    r"(?:\bimport\s+|\bfrom\s+|-m\s+|(?:import_module|__import__)\(\s*[\"']|[/\\])"
    r"(?:bybit_collector|hyperliquid_collector|ranking_engine)\b"
)
# What may follow an allowed identifier: never a path or module continuation (`/x`, `\x`, `.x`),
# not even behind a closing quote or backtick (`` `ranking_engine`.engine ``).
_NO_CONTINUATION = r"(?![`'\"]?[./\\][\w.])"


class Allowance(NamedTuple):
    pattern: re.Pattern[str]
    reason: str


ALLOWANCES = (
    Allowance(
        re.compile(rf"(?<![\w./])/app/dydx_collector/config\.toml\b{_NO_CONTINUATION}"),
        "the dYdX plan's container bind-mount path (compose, DYDX_PLAN_PATH, make nightly)",
    ),
    Allowance(
        # A host data directory: any path beneath it (`fills.db`) is still that store.
        re.compile(r"\bdata/live_paper\b"),
        "the bots' fills.db store directory under platform/data/ (and its mirrored container path)",
    ),
    Allowance(
        # A bare name only: never followed by a package path (`/`) or a module path (`.<word>`).
        re.compile(
            rf"\b(?:bybit_collector|hyperliquid_collector|ranking_engine)\b{_NO_CONTINUATION}"
        ),
        "a compose service name, which is also its ERROR_LEDGER_SERVICE value",
    ),
    Allowance(
        re.compile(
            rf"\b(?:bybit_collector|hyperliquid_collector|ranking_engine)\.jsonl\b{_NO_CONTINUATION}"
        ),
        "a service's durable error-ledger file, named after its ERROR_LEDGER_SERVICE",
    ),
    Allowance(
        re.compile(rf"\branking_engine\.(?:{_RANKING_SITES})\b{_NO_CONTINUATION}"),
        "an error-ledger site name the ranking process kept when it moved to ranking/",
    ),
)

# The guard's own file names every token, in its patterns and its examples.
_GREP = (
    "git",
    "grep",
    "-n",
    "-I",  # a binary file prints no line to judge
    "-E",
    LEGACY_NAMES,
    "--",
    ".",
    ":(exclude)docs",
    ":(exclude).planning",
    ":(exclude)tests/test_legacy_names.py",
)


def stale_mentions(line: str) -> list[str]:
    """Return every legacy-name mention in `line` that no allowance covers."""
    stale = [m.span() for m in _STALE_CONTEXT.finditer(line)]
    allowed = [m.span() for a in ALLOWANCES for m in a.pattern.finditer(line)]
    allowed = [span for span in allowed if not any(s < span[1] and span[0] < e for s, e in stale)]
    return [
        found.group()
        for found in _LEGACY.finditer(line)
        if not any(start <= found.start() and found.end() <= end for start, end in allowed)
    ]


# What the walk skips where git cannot list the tracked files: git-ignored or generated trees, the
# story's own exclusions, and this file. `data/` is runtime state except its committed top-level
# `.toml` configs, which the walk reads. Every tracked text file must be walked:
# `test_walk_reads_every_tracked_text_file` fails the day a new suffix or directory is committed.
_WALK_SKIP_DIRS = frozenset(
    {"docs", ".planning", "data", "node_modules", "dist", "__pycache__", ".git", ".venv"}
    | {".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
_WALK_SUFFIXES = frozenset(
    {".py", ".md", ".toml", ".yml", ".yaml", ".ts", ".tsx", ".css", ".json", ".txt", ".sh"}
    | {".ipynb", ".dockerfile", ".html", ".go", ".mjs", ".sql", ".jsonl", ".svg"}
)
_WALK_NAMES = frozenset({"Makefile", ".gitignore", ".env-example"})


def _walked_files(top: Path) -> Iterator[Path]:
    for path in sorted(top.iterdir()):
        if path.is_dir():
            # Only the top-level `docs/` is a history-notes exclusion; `frontend/.../docs/` is copy.
            nested_docs = path.name == "docs" and path.parent != PLATFORM_DIR
            if path.name not in _WALK_SKIP_DIRS or nested_docs:
                yield from _walked_files(path)
            elif path == PLATFORM_DIR / "data":
                yield from sorted(path.glob("*.toml"))
        elif path.suffix in _WALK_SUFFIXES or path.name in _WALK_NAMES:
            yield path


def _walk_hits() -> list[str]:
    """`git grep -n` output, rebuilt by walking the checkout's text files."""
    hits = []
    for path in _walked_files(PLATFORM_DIR):
        relative = path.relative_to(PLATFORM_DIR).as_posix()
        if relative == "tests/test_legacy_names.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # `\n` only, as git numbers lines: `splitlines()` also splits on `\f`, `\x1c`, U+2028...
        for number, line in enumerate(text.split("\n"), start=1):
            if _LEGACY.search(line):
                hits.append(f"{relative}:{number}:{line}")
    return hits


def _grep_hits() -> list[str]:
    if shutil.which("git") is None:
        return _walk_hits()
    inside = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],  # noqa: S607
        cwd=PLATFORM_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return _walk_hits()  # e.g. git's safe.directory refusing a mount owned by another uid
    found = subprocess.run(  # noqa: S603 (fixed git arguments)
        _GREP,
        cwd=PLATFORM_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert found.returncode in (0, 1), found.stderr  # 1: no hit at all
    return found.stdout.splitlines()


def test_only_published_language_keeps_a_legacy_name() -> None:
    stale = [
        f"{hit.split(':', 2)[0]}:{hit.split(':', 2)[1]}: {mentions}"
        for hit in _grep_hits()
        if (mentions := stale_mentions(hit.split(":", 2)[2]))
    ]
    assert stale == [], "re-word or re-point these (the package is gone); see ALLOWANCES"


@pytest.mark.parametrize(
    "line",
    [
        "from collector_core.config import CoreConfig",
        "import dydx_collector.client",
        "moved from `ranking_engine/engine.py` in Story 25.2",
        "pre-move `ranking_engine.engine` output",
        "was ml_signals.catalog_stats",
        "COPY platform/live_paper ./live_paper",
        "/app/live_paper/strategy.py",
        "kernel.venues replaced common.venues",
        "the dydx_collector package",
        "bybit_collector/client.py",
        "ranking_engine.py",
        "ranking_engine.jsonl beside collector_core",
        "import bybit_collector",
        "from ranking_engine import engine",
        "import hyperliquid_collector as hl",
        "python3 -m ranking_engine",
        "COPY platform/ranking_engine ./ranking_engine",
        "ranking_engine.redis.client",
        "ranking_engine.jsonl.bak/x",
        "/app/dydx_collector/config.tomlx",
        "x/app/dydx_collector/config.toml",
        "/app/dydx_collector/config.toml/../client.py",
        "see `bybit_collector`.client",
        "platform\\ranking_engine\\engine.py",
        'importlib.import_module("ranking_engine")',
        "__import__('hyperliquid_collector')",
    ],
)
def test_matcher_rejects_a_module_or_package_mention(line: str) -> None:
    assert stale_mentions(line) != []


@pytest.mark.parametrize(
    "line",
    [
        "- ./data/dydx_config.toml:/app/dydx_collector/config.toml:rw",
        "- ./data/live_paper:/app/data/live_paper",
        "mkdir -p platform/data/live_paper",
        "  ranking_engine:",
        'ERROR_LEDGER_SERVICE: "hyperliquid_collector"',
        "`bybit_collector`'s ledger. The ranking_engine service.",
        "(ranking_engine, data_api)",
        '_write(tmp_path / "ranking_engine.jsonl", [])',
        '"ranking_engine.volume24h"',
        'error_ledger.record("ranking_engine.price_backfill", mismatch)',
    ],
)
def test_matcher_accepts_each_published_language_allowance(line: str) -> None:
    assert stale_mentions(line) == []


def test_every_allowance_states_its_reason() -> None:
    assert all(allowance.reason for allowance in ALLOWANCES)


def test_walk_finds_what_git_grep_finds() -> None:
    """The image's fallback sees every line the host's `git grep` does (plus untracked files)."""
    if shutil.which("git") is None:
        pytest.skip("no git here to compare the walk against (the walk itself ran above)")
    assert set(_grep_hits()) <= set(_walk_hits())


def test_walk_reads_every_tracked_text_file() -> None:
    """The image's walk reads each file the host's `git grep` would, not only today's hits."""
    if shutil.which("git") is None:
        pytest.skip("no git here to list the tracked files (the walk itself ran above)")
    listed = subprocess.run(  # noqa: S603 (fixed git arguments)
        ["git", "grep", "-I", "-l", "-e", "", "--", *_GREP[_GREP.index("--") + 1 :]],  # noqa: S607
        cwd=PLATFORM_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode not in (0, 1):
        pytest.skip(f"git cannot read this checkout: {listed.stderr.strip()}")
    walked = {path.relative_to(PLATFORM_DIR).as_posix() for path in _walked_files(PLATFORM_DIR)}
    assert set(listed.stdout.splitlines()) - walked == set()
