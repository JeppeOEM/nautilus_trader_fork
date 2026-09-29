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
Story 31.3: the web docs may only name a frontend helper that exists. The Docs page claimed for
months that CVD and spread were normalised client-side by `usdFromTokens`/`bpsFromPriceUnits` --
functions no file under `frontend/src` ever defined, so the published units were misdescribed.
Sibling of `test_ranking_columns_mirror.py` (the same source-tree lookup).

The rule: every lowerCamelCase identifier written as code (`<code>name</code>` or `<code>name(`)
anywhere under `frontend/src` -- the frontend's own naming style; Python names are snake_case --
must be declared somewhere under `frontend/src` (a `function`, `const`, `let` or `var`). The same
holds for every lowerCamelCase name in a comment of `views/ranking_columns.py`, whose unit-contract
comment made the same claim (review P15).
"""

import os
import re
from pathlib import Path


_SOURCE = os.environ.get("PLATFORM_SOURCE_DIR")
_PLATFORM_DIR = Path(_SOURCE) if _SOURCE else Path(__file__).resolve().parents[2]
_FRONTEND_SRC = _PLATFORM_DIR / "frontend" / "src"
_NAMED_HELPER = re.compile(r"<code>([a-z][a-z0-9]*[A-Z][A-Za-z0-9]*)(?:\(|</code>)")
_RANKING_COLUMNS = _PLATFORM_DIR / "views" / "ranking_columns.py"
_CAMEL = re.compile(r"\b([a-z][a-z0-9]*[A-Z][A-Za-z0-9]*)\b")


def _sources() -> dict[str, str]:
    return {
        str(path.relative_to(_PLATFORM_DIR)): path.read_text(encoding="utf-8")
        for pattern in ("*.ts", "*.tsx")
        for path in _FRONTEND_SRC.rglob(pattern)
    }


def _declared(name: str, texts: list[str]) -> bool:
    declaration = re.compile(rf"\b(?:function|const|let|var)\s+{name}\b")
    return any(declaration.search(text) for text in texts)


def _undefined_helpers(sources: dict[str, str]) -> list[str]:
    """Return `file: name` for every helper named as code that no source declares."""
    texts = list(sources.values())
    return sorted(
        f"{where}: {name}"
        for where, text in sources.items()
        for name in set(_NAMED_HELPER.findall(text))
        if not _declared(name, texts)
    )


def test_the_frontend_names_no_helper_it_does_not_define() -> None:
    sources = _sources()
    assert len(sources) > 20, f"frontend sources not found under {_FRONTEND_SRC}"
    assert _undefined_helpers(sources) == []


def _comment_helpers(python_source: str) -> set[str]:
    """Every lowerCamelCase name in a `#` comment of a Python source (a frontend helper's style)."""
    comments = [line.split("#", 1)[1] for line in python_source.splitlines() if "#" in line]
    return {name for comment in comments for name in _CAMEL.findall(comment)}


def test_the_ranking_columns_comments_name_no_undefined_frontend_helper() -> None:
    texts = list(_sources().values())
    names = _comment_helpers(_RANKING_COLUMNS.read_text(encoding="utf-8"))
    assert sorted(name for name in names if not _declared(name, texts)) == []


def test_the_comment_rule_catches_the_old_ranking_columns_claim() -> None:
    old = "# normalize these client-side (see the inline JS's usdFromTokens/\n# bpsFromPriceUnits)"
    assert _comment_helpers(old) == {"usdFromTokens", "bpsFromPriceUnits"}


def test_the_rule_catches_the_helpers_the_docs_used_to_name() -> None:
    """The planted case: the pre-31.3 docs text is refused, a declared helper is accepted."""
    sources = {
        "docs.ts": "normalized via <code>usdFromTokens(raw, price)</code> and <code>fmtSigned</code>",
        "lib.ts": "function fmtSigned(v: number): string { return String(v); }",
    }
    assert _undefined_helpers(sources) == ["docs.ts: usdFromTokens"]
