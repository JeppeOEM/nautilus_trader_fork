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
`05_monte_carlo` over the session fixture archive (Story 27.6), read from the notebook's own
namespace: both bootstraps drew the harness's 200 paths with the notebook's seed, every figure's
title states its seed and path count, the ruin table has its three levels, the sweep ran the 2 x 2
grid, the single run traded, `SWEEP=false` prints a sentence instead of the deflated Sharpe table, and no
code cell builds a backtest node, sums, averages or does arithmetic of its own.
"""

import ast
import json
import re

import plotly.graph_objects as go
import pytest

from research.tests.fixture_catalog import FixturePaths
from research.tests.test_notebooks import NOTEBOOKS_DIR
from research.tests.test_notebooks import _run


NOTEBOOK = NOTEBOOKS_DIR / "05_monte_carlo.py"
_HARNESS_PATHS = 200


@pytest.fixture(scope="module")
def shown(fixture_archive: FixturePaths) -> dict:
    """Run the notebook headless against the fixture once and return its namespace."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        return _run(NOTEBOOK, fixture_archive, monkeypatch)


def test_both_bootstraps_record_the_harness_paths_and_the_seed(shown: dict) -> None:
    for name in ("trade_mc", "return_mc"):
        result = shown[name]
        assert (result.n_paths, result.seed) == (_HARNESS_PATHS, shown["SEED"]), name
        assert result.paths.shape[0] == _HARNESS_PATHS, name


def test_every_figure_title_states_its_seed_and_paths(shown: dict) -> None:
    figures = {name: v for name, v in shown.items() if isinstance(v, go.Figure)}
    assert figures, "the notebook drew no figure"
    for name, figure in figures.items():
        title = figure.layout.title.text or ""
        assert f"seed={shown['SEED']}" in title, name
        assert f"paths={_HARNESS_PATHS}" in title, name


def test_the_ruin_frame_has_the_three_levels(shown: dict) -> None:
    assert list(shown["ruin"].index) == [0.9, 0.75, 0.5]
    assert len(shown["return_ruin"]) == 3


def test_the_sweep_ran_the_two_by_two_grid(shown: dict) -> None:
    assert len(shown["sweep"].results) == 4
    assert shown["check"].n_trials == 4, "the fixture straddles UTC midnight: a Sharpe is defined"


def test_the_fixture_draws_the_sharpe_interval_and_deflates_the_best_point(shown: dict) -> None:
    """The fixture straddles UTC midnight, so §7 and §8 show values rather than their sentences."""
    assert shown["interval"] is not None
    assert isinstance(shown["interval_fig"], go.Figure)
    assert shown["check"].psr is not None
    assert shown["check"].dsr is not None
    assert list(shown["deflated"].index) == ["PSR vs 0", "DSR, 4 trials"]


def test_the_single_run_traded(shown: dict) -> None:
    assert len(shown["result"].trades) >= 1
    assert shown["trade_mc"].n_steps == len(shown["result"].trades) + 1


def test_sweep_off_prints_a_sentence_and_shows_no_deflated_table(
    fixture_archive: FixturePaths, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("NOTEBOOK_SWEEP", json.dumps(False))
        namespace = _run(NOTEBOOK, fixture_archive, monkeypatch)
    assert namespace["sweep"] is None
    assert namespace["check"] is None
    assert "deflated" not in namespace
    assert "SWEEP is off" in capsys.readouterr().out


# A percent-format cell header: `# %%` opens a code cell, with or without cell metadata after it;
# `# %% [markdown]`/`[raw]` do not.
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
