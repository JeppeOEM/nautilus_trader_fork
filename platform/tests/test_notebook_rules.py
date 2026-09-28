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
The research notebook rules of `platform/CLAUDE.md` that a static scan can hold (Story 27.9).

- NB-01 (no analysis logic in a notebook): no code cell of a numbered notebook holds an array-math
  or reducer token -- the computation belongs in `research/domain`, `research/application`,
  `kernel` or `kernel.performance_metrics`. Known limit: a token list, not a parser -- plain
  arithmetic on two variables (`(a - b) / b`) and a builtin `min(`/`max(` pass, so review stays the gate NB-01 names; upgrade
  path: an AST check refusing any `BinOp` whose operands are not both literals.
- NB-03 (no runtime install, nothing outside `uv.lock`): no `.ipynb` in `research/notebooks/` has
  an install line (a `pip`/`conda`/`mamba`/`uv` magic or shell escape, `python -m pip`, or an
  install inside a `%%bash`/`%%sh` cell), and no text file under `platform/`
  outside the history notes names a legacy TA library, the IPython `pip` magic or the retired
  minute-bar directory -- exactly the epic's acceptance `git grep`, so a regression fails here
  before any reviewer greps. No `.ipynb` lives outside `research/notebooks/`.

NB-02 (paired, output-stripped, run against the fixture) is `research/tests/test_notebooks.py`'s;
NB-04 (every read bounded by `START`/`END`) is `research/tests/test_research_reads.py`'s.

Stdlib only, reading the checkout through `_source_tree` (git, else the walk), so it runs in every
image `make test` uses. Each token is spelled with a character class, which the token itself does
not match, and the matcher cases spell them with escapes: this file needs no exclusion from its own
sweep, and the literal acceptance grep stays empty.
"""

import json
import re
from pathlib import Path

import _source_tree
import pytest
from _source_tree import IGNORED_DIRS
from _source_tree import PLATFORM_DIR
from _source_tree import grep_hits
from _source_tree import listed_files
from _source_tree import walk_hits


# The acceptance grep's four tokens, as `git grep -E` and Python `re` both read them, plus the
# PyPI spelling of the legacy TA package (hyphen for underscore, as a requirements line names it).
LEGACY_TOKENS = re.compile(r"pandas[-_]ta|ta[l]ib|[%]pip|custom_dydx_minute[_]bar")
# A line of a code cell that installs a package at run time; leading whitespace allowed. Any magic
# or shell escape naming an installer counts (`!pip3 install`, `%mamba`, `!python -m pip`,
# `!{sys.executable} -m pip`), not only a line that starts with the installer's name.
INSTALL_LINE = re.compile(r"^\s*[%!].*\b(?:pip3?|conda|mamba|micromamba|uv)\b")
# A `%%bash`-style cell is shell throughout: any installer command in it counts.
_SHELL_CELL = re.compile(r"^\s*%%(?:bash|sh|script)\b")
_SHELL_INSTALL = re.compile(r"\b(?:pip3?|conda|mamba|micromamba|uv)\s+(?:pip\s+)?(?:install|add)\b")
# Array math and reducers in a notebook cell are analysis logic (NB-01); matched lower-cased.
# `\b` before a bare name, so `checksum(` is not `sum(`.
FORMULA_TOKENS = re.compile(
    r"\bnp\.|\bnumpy\b|\bmath\.|\bstatistics\.|\bsum\(|"
    r"\.(?:mean|sum|std|var|min|max|median|quantile|corr|cov|diff|pct_change|cumsum|cumprod"
    r"|rolling|ewm|expanding|resample|agg|aggregate|cummax|cummin|abs|clip|shift|apply|round"
    r"|nlargest|nsmallest)\("
)

# The four tokens for the matcher cases, spelled with an escape (`\x5f` is `_`, `\x6c` is `l`,
# `\x25` is `%`) so this file's source text never matches the acceptance grep it enforces.
_PANDAS_TA = "pandas\x5fta"
_TA_LIB = "ta\x6cib"
_PIP_MAGIC = "\x25pip"
_MINUTE_BAR_DIR = "custom_dydx_minute\x5fbar"

NOTEBOOKS_DIR = PLATFORM_DIR / "research" / "notebooks"
_NOTEBOOK_HOME = "research/notebooks/"
# A percent-format cell header: `# %%` opens a code cell, with or without cell metadata after it;
# `# %% [markdown]`/`[raw]` do not.
_CELL = re.compile(r"^# %%(.*)$", re.MULTILINE)


def numbered(suffix: str) -> list[Path]:
    """Return the numbered notebooks' files with `suffix` (`.py` source or `.ipynb` twin)."""
    return sorted(NOTEBOOKS_DIR.glob(f"[0-9]*_*{suffix}"))


def code_cells(source: str) -> list[str]:
    """Return the code cells of a jupytext percent-format notebook's source."""
    parts = _CELL.split(source)
    # `split` alternates [before the first header, header suffix, body, header suffix, body, ...].
    return [
        body
        for suffix, body in zip(parts[1::2], parts[2::2], strict=True)
        if not suffix.strip().startswith("[")
    ]


def formula_tokens(cell: str) -> list[str]:
    """Return every NB-01 array-math or reducer token in one code cell."""
    return FORMULA_TOKENS.findall(cell.lower())


def _source_lines(cell: dict) -> list[str]:
    # nbformat stores `source` as a list of lines or as one string; both are valid.
    source = cell["source"]
    return source.splitlines(keepends=True) if isinstance(source, str) else source


def _cell_installs(lines: list[str]) -> list[str]:
    shell = bool(lines) and _SHELL_CELL.match(lines[0]) is not None
    return [
        line.rstrip("\n")
        for line in lines
        if INSTALL_LINE.match(line) or (shell and _SHELL_INSTALL.search(line))
    ]


def install_lines(notebook: dict) -> list[str]:
    """Return every install line of a notebook's (`.ipynb` JSON) code cells."""
    return [
        line
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
        for line in _cell_installs(_source_lines(cell))
    ]


def _ignored(path: Path) -> bool:
    return any(part in IGNORED_DIRS for part in path.relative_to(PLATFORM_DIR).parts)


def _all_notebooks() -> list[str]:
    listed = listed_files("*.ipynb")
    if listed is None:  # no git: every notebook on disk, history directories included
        listed = [
            path.relative_to(PLATFORM_DIR).as_posix()
            for path in PLATFORM_DIR.rglob("*.ipynb")
            if not _ignored(path)
        ]
    return sorted(listed)


def _stray_notebooks() -> list[str]:
    # Directly in `research/notebooks/`, not in a subdirectory of it or of another tree.
    return [
        path
        for path in _all_notebooks()
        if not path.startswith(_NOTEBOOK_HOME) or "/" in path.removeprefix(_NOTEBOOK_HOME)
    ]


def test_no_legacy_token_outside_the_history_notes() -> None:
    hits = [":".join(hit.split(":", 2)[:2]) for hit in grep_hits(LEGACY_TOKENS)]
    assert hits == [], "re-word these (NB-03; the tokens live only in docs/ and .planning/)"


def test_no_notebook_outside_research_notebooks() -> None:
    assert _stray_notebooks() == [], "move these into research/notebooks/ or delete them"


def test_no_notebook_installs_a_package() -> None:
    notebooks = sorted(NOTEBOOKS_DIR.glob("*.ipynb"))  # numbered or not
    assert notebooks, f"no notebook found under {NOTEBOOKS_DIR}"
    found = {
        path.name: lines
        for path in notebooks
        if (lines := install_lines(json.loads(path.read_text())))
    }
    assert found == {}, "NB-03: a dependency goes through uv.lock, never a cell"


def test_no_numbered_notebook_code_cell_holds_a_formula() -> None:
    sources = numbered(".py")
    assert sources, f"no numbered notebook source found under {NOTEBOOKS_DIR}"
    found: dict[str, list[str]] = {}
    for path in sources:
        cells = code_cells(path.read_text())
        assert cells, f"{path.name}: no code cell found (is it still percent format?)"
        for index, cell in enumerate(cells):
            if tokens := formula_tokens(cell):
                found[f"{path.name} code cell {index}"] = tokens
    assert found == {}, "NB-01: move the computation into research/domain or application"


@pytest.mark.parametrize(
    "line",
    [
        f"import {_PANDAS_TA} as ta",
        f"import {_TA_LIB}",
        f"{_PIP_MAGIC} install x",
        f"data/{_MINUTE_BAR_DIR}/BTC",
        "pandas\x2dta==0.3.14b0",
    ],
)
def test_the_token_sweep_matches_each_legacy_token(line: str) -> None:
    assert LEGACY_TOKENS.search(line)


@pytest.mark.parametrize(
    "line",
    [
        "No third-party TA library (TA-Lib was the reference once)",
        "the retired minute-bar directory",
        "uv add plotly",
        "%load_ext x",
    ],
)
def test_the_token_sweep_passes_a_clean_line(line: str) -> None:
    assert LEGACY_TOKENS.search(line) is None


@pytest.mark.parametrize(
    "line",
    [
        f"{_PIP_MAGIC} install x\n",
        "  !pip install x",
        "%conda install x",
        "!uv pip install x",
        "!pip3 install x",
        "%mamba install x",
        "!python -m pip install x",
        "!{sys.executable} -m pip install x",
    ],
)
def test_an_install_line_is_found(line: str) -> None:
    notebook = {"cells": [{"cell_type": "code", "source": ["import x\n", line]}]}
    assert install_lines(notebook) == [line.rstrip("\n")]


@pytest.mark.parametrize(
    "line", ["%load_ext x", "!ls", "# pip is never run here", "piper = 1", "!echo uv_cache"]
)
def test_a_non_install_line_passes(line: str) -> None:
    notebook = {"cells": [{"cell_type": "code", "source": [line]}]}
    assert install_lines(notebook) == []


def test_an_install_in_a_shell_cell_is_found() -> None:
    cell = {"cell_type": "code", "source": ["%%bash\n", "cd /tmp\n", "pip install x\n"]}
    assert install_lines({"cells": [cell]}) == ["pip install x"]


def test_a_cell_source_stored_as_one_string_is_read_by_line() -> None:
    cell = {"cell_type": "code", "source": "import x\n!pip install y\n"}
    assert install_lines({"cells": [cell]}) == ["!pip install y"]


def test_an_install_line_in_a_markdown_cell_is_prose() -> None:
    notebook = {"cells": [{"cell_type": "markdown", "source": ["!pip install x"]}]}
    assert install_lines(notebook) == []


@pytest.mark.parametrize(
    "cell",
    [
        "x = np.log(prices)",
        "frame.close.Mean()",
        "total = sum(pnl)",
        "bars.resample('1min')",
        "mid.rolling(60)",
        "flow.cumsum()",
        "returns.std()",
        "mid.diff()",
        "close.pct_change()",
        "peak = equity.max()",
        "drift = math.log(ratio)",
        "vol = returns.ewm(span=20)",
        "drawdown = equity / equity.cummax() - 1",
        "moves = mid.shift(1).abs()",
        "import numpy",
    ],
)
def test_a_formula_token_is_found(cell: str) -> None:
    assert formula_tokens(cell) != []


@pytest.mark.parametrize(
    "cell",
    [
        "hits = patterns.scan_grids(bars, THRESHOLDS)",
        "report = result.metrics.as_table()",
        "seconds = frames.seconds(iid, start=START, end=END)",
        "digest = checksum(path)",
        "print(summary(frame))",
    ],
)
def test_a_service_call_is_not_a_formula(cell: str) -> None:
    assert formula_tokens(cell) == []


def test_code_cells_read_the_real_notebook() -> None:
    """The split finds the scanner's own service call, so an empty or wrong split cannot pass."""
    cells = code_cells((NOTEBOOKS_DIR / "06_candlestick_scanner.py").read_text())
    assert any("patterns.scan_grids(" in cell for cell in cells)


def test_code_cells_skip_markdown_and_raw_cells() -> None:
    source = "# %% [raw]\n# np.x\n# %% [markdown]\n# sum(x)\n# %%\nprint(1)\n# %% tags=[]\ny = 2\n"
    assert code_cells(source) == ["\nprint(1)\n", "\ny = 2\n"]


def test_the_walk_finds_a_planted_token_outside_the_history_notes(tmp_path: Path) -> None:
    """The image's no-git fallback reads the same files: a token in a module is found."""
    (tmp_path / "research").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "research" / "a.py").write_text(f"x = 1\nimport {_TA_LIB}\n")
    (tmp_path / "docs" / "HISTORY.md").write_text(f"import {_TA_LIB}\n")
    assert walk_hits(LEGACY_TOKENS, top=tmp_path) == [f"research/a.py:2:import {_TA_LIB}"]


# A pattern with hits today (the NB rule ids in `platform/CLAUDE.md` and this file's own docstring),
# so comparing the two paths is not a comparison of two empty sets.
_PROBE = re.compile(r"NB-0[1-4]")


def test_the_sweep_without_git_sees_what_git_sees(monkeypatch: pytest.MonkeyPatch) -> None:
    """Where no git binary exists the sweep walks instead, and sees every line git would."""
    with_git = set(grep_hits(_PROBE))
    assert with_git, "the probe found nothing: pick a pattern the checkout holds"
    monkeypatch.setattr(_source_tree.shutil, "which", lambda _name: None)
    assert with_git <= set(grep_hits(_PROBE))


def test_the_notebook_list_without_git_sees_what_git_sees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_git = set(_all_notebooks())
    assert with_git, "no notebook listed at all"
    monkeypatch.setattr(_source_tree.shutil, "which", lambda _name: None)
    assert with_git <= set(_all_notebooks())
