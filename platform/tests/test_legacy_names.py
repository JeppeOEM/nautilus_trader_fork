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
on a host, `test_walk_reads_every_tracked_text_file` holds the walk to git's file set. Both scans
are `_source_tree`'s (`grep_hits`, `walk_hits`), shared with `test_notebook_rules` since Story 27.9.
"""

import re
import shutil
from typing import NamedTuple

import pytest
from _source_tree import PLATFORM_DIR
from _source_tree import grep_hits
from _source_tree import tracked_text_files
from _source_tree import walk_hits
from _source_tree import walked_files


LEGACY_NAMES = (
    r"ml_signals|collector_core|dydx_collector|bybit_collector|hyperliquid_collector"
    r"|ranking_engine|live_paper|common\.venues"
)
_LEGACY = re.compile(LEGACY_NAMES)

# The ranking process's error-ledger sites (`ranking/`): published language, read by
# `GET /api/errors`, `archive.crosscheck_errors --expect` and the durable `.jsonl` history. A site
# added later keeps the process's one prefix (Story 29.5's `markets`, the `markets:live` publish;
# Story 33.4's `derivs_entry`, `liquidation_entry` and `derivs_backfill`).
_RANKING_SITES = (
    "volume24h|message|snapshot_entry|publish|price_backfill|metrics_history|slow_loop|redis"
    "|markets|derivs_entry|liquidation_entry|derivs_backfill"
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
_SELF = frozenset({"tests/test_legacy_names.py"})


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


def _grep_hits() -> list[str]:
    return grep_hits(_LEGACY, _SELF)


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
    assert set(_grep_hits()) <= set(walk_hits(_LEGACY, _SELF))


def test_walk_reads_every_tracked_text_file() -> None:
    """The image's walk reads each file the host's `git grep` would, not only today's hits."""
    if shutil.which("git") is None:
        pytest.skip("no git here to list the tracked files (the walk itself ran above)")
    listed = tracked_text_files(_SELF)
    if listed is None:
        pytest.skip("git cannot read this checkout (the walk itself ran above)")
    walked = {path.relative_to(PLATFORM_DIR).as_posix() for path in walked_files()}
    assert listed - walked == set()
