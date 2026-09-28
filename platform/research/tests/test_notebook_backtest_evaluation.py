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
`04_backtest_evaluation` over the session fixture archive (Story 27.5), read from the notebook's own
namespace: the metric table is exactly `all_metrics`, the single run traded, the sweep ran the
2 x 2 grid into a 2 x 2 heatmap, the walk-forward has two folds, and no code cell builds a backtest
node, sums, averages or does arithmetic of its own.
"""

import ast
import re

import pytest
from kernel.performance_metrics import all_metrics

from research.tests.fixture_catalog import FixturePaths
from research.tests.test_notebooks import NOTEBOOKS_DIR
from research.tests.test_notebooks import _run


NOTEBOOK = NOTEBOOKS_DIR / "04_backtest_evaluation.py"


@pytest.fixture(scope="module")
def shown(fixture_archive: FixturePaths) -> dict:
    """Run the notebook headless against the fixture once and return its namespace."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        return _run(NOTEBOOK, fixture_archive, monkeypatch)


def test_the_metric_table_is_exactly_all_metrics(shown: dict) -> None:
    assert tuple(shown["metric_table"].index) == tuple(all_metrics([], [], 1.0))


def test_the_single_run_traded(shown: dict) -> None:
    assert len(shown["result"].trades) >= 1
    assert len(shown["trades"]) == len(shown["result"].trades)


def test_the_sweep_ran_the_two_by_two_grid(shown: dict) -> None:
    assert len(shown["sweep"].results) == 4
    assert shown["heat"].shape == (2, 2)
    assert shown["heat_note"] is None, "the fixture's grid metric should be defined"


def test_the_walk_forward_has_two_folds(shown: dict) -> None:
    assert len(shown["wf"].folds) == 2


# A percent-format cell header: `# %%` opens a code cell, with or without cell metadata after it
# (`# %% tags=["parameters"]`); `# %% [markdown]`/`[raw]` do not.
_CELL = re.compile(r"^# %%(.*)$", re.MULTILINE)
_FORBIDDEN_TOKENS = ("BacktestNode", "sum(", "np.", ".mean(")


def _code_cells() -> list[str]:
    parts = _CELL.split(NOTEBOOK.read_text())
    # `split` alternates [before the first header, header suffix, body, header suffix, body, ...].
    return [
        body
        for suffix, body in zip(parts[1::2], parts[2::2], strict=True)
        if not suffix.strip().startswith("[")
    ]


def test_no_code_cell_builds_a_node_sums_or_averages() -> None:
    cells = _code_cells()
    assert any("runner.run(spec)" in cell for cell in cells), "the code cells were not found"
    found = [
        (token, i) for i, cell in enumerate(cells) for token in _FORBIDDEN_TOKENS if token in cell
    ]
    assert found == []


def test_no_cell_does_arithmetic() -> None:
    """The `.py` is the notebook's code (markdown and raw cells are comments): no binary operator."""
    arithmetic = [
        ast.unparse(node)
        for node in ast.walk(ast.parse(NOTEBOOK.read_text()))
        if isinstance(node, ast.BinOp | ast.UnaryOp | ast.AugAssign)
        and not isinstance(getattr(node, "op", None), ast.USub)
    ]
    assert arithmetic == []
