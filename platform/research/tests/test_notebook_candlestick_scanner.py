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
`06_candlestick_scanner` over the session fixture archive (Story 27.7), read from the notebook's own
namespace: the hits table has the scanner's columns and only kept timeframes, the unkept size is
skipped with a line, the forward table covers exactly the harness's horizons, both figures are
drawn, and a filter that matches nothing prints a sentence instead of a chart. That no code cell
holds array math or a TA library is `platform/tests/test_notebook_rules.py`'s, for every numbered
notebook (Story 27.9).
"""

import json

import plotly.graph_objects as go
import pytest
from kernel.candle_patterns import PatternName

from research.application import patterns
from research.tests.fixture_catalog import FixturePaths
from research.tests.test_notebooks import NOTEBOOK_ENV
from research.tests.test_notebooks import NOTEBOOKS_DIR
from research.tests.test_notebooks import _run


NOTEBOOK = NOTEBOOKS_DIR / "06_candlestick_scanner.py"
_ENV = NOTEBOOK_ENV[NOTEBOOK.name]


@pytest.fixture(scope="module")
def shown(fixture_archive: FixturePaths) -> dict:
    """Run the notebook headless against the fixture once and return its namespace."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        return _run(NOTEBOOK, fixture_archive, monkeypatch)


def test_the_hits_table_has_the_scanner_columns_and_kept_timeframes(shown: dict) -> None:
    hits = shown["hits"]
    assert list(hits.columns) == list(patterns.SCAN_COLUMNS)
    assert len(hits) > 0, "the fixture's minutes fire patterns"
    assert set(hits["timeframe"]) <= {60, 300}
    assert set(hits["pattern"]) <= {p.value for p in PatternName}
    assert set(hits["direction"]) <= {-100, 100}


def test_the_unkept_timeframe_is_skipped_with_a_line(shown: dict) -> None:
    assert shown["kept"] == [60, 300]
    assert [line.split()[0] for line in shown["skipped"]] == ["45"]
    assert {timeframe for _, timeframe in shown["grids"]} == {60, 300}


def test_the_forward_table_covers_the_harness_horizons(shown: dict) -> None:
    forward = shown["forward"]
    assert list(forward.columns) == list(patterns.FORWARD_COLUMNS)
    assert sorted(set(forward["horizon"])) == json.loads(_ENV["NOTEBOOK_HORIZONS"])
    assert (forward["n"] > 0).any(), "some hit is measurable on the fixture's ten minutes"


def test_both_figures_are_drawn(shown: dict) -> None:
    figures = {name for name, value in shown.items() if isinstance(value, go.Figure)}
    assert figures == {"hit_fig", "forward_fig"}
    assert shown["hit_fig"].layout.title.text.startswith(shown["hit"].instrument_id)


def test_the_chart_is_centred_on_the_chosen_hit(shown: dict) -> None:
    around, hit = shown["around"], shown["hit"]
    assert hit.timestamp in set(around["timestamp"])
    assert len(around) <= 2 * json.loads(_ENV["NOTEBOOK_WINDOW_BARS"]) + 1


def test_a_filter_matching_nothing_prints_a_sentence_instead_of_a_chart(
    fixture_archive: FixturePaths, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("NOTEBOOK_PATTERN_FILTER", json.dumps("MORNING_STAR"))
        namespace = _run(NOTEBOOK, fixture_archive, monkeypatch)
    assert namespace["shown"].empty
    assert list(namespace["shown"].columns) == list(patterns.SCAN_COLUMNS)
    assert "hit_fig" not in namespace
    assert "forward_fig" not in namespace
    out = capsys.readouterr().out
    assert "No hit in this window for pattern filter 'MORNING_STAR'" in out
    assert "the forward table is empty" in out
