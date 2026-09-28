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
`03_correlation` over the session fixture archive (Story 27.4), read from the notebook's own
namespace: a 2 x 2 matrix per venue, the BTC cross-venue group on all three venues with Bybit's
planted 2 s lead stated in words, a single-venue cluster fed to a `RunSpec`, and a notebook source
with no formula, resample or id split in any cell.
"""

import ast

import pytest
from kernel.venues import venue_of

from research.tests.fixture_catalog import FixturePaths
from research.tests.test_notebooks import NOTEBOOKS_DIR
from research.tests.test_notebooks import _run


NOTEBOOK = NOTEBOOKS_DIR / "03_correlation.py"


@pytest.fixture(scope="module")
def shown(fixture_archive: FixturePaths) -> dict:
    """Run the notebook headless against the fixture once and return its namespace."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        return _run(NOTEBOOK, fixture_archive, monkeypatch)


def test_each_venues_one_minute_matrix_is_two_by_two(
    shown: dict, fixture_archive: FixturePaths
) -> None:
    per_venue = shown["venue_matrices"][60]
    assert sorted(per_venue) == ["BYBIT", "DYDX", "HYPERLIQUID"]
    for venue, matrix in per_venue.items():
        expected = tuple(i for i in fixture_archive.instruments if venue_of(i) == venue)
        assert matrix.ids == expected


def test_the_btc_cross_venue_group_covers_all_three_venues(
    shown: dict, fixture_archive: FixturePaths
) -> None:
    btc = shown["cross"]["BTC-USD-PERP.DYDX"]
    assert btc.collected == fixture_archive.same_asset["BTC-USD-PERP.DYDX"]
    assert btc.missing == ()
    assert {venue_of(iid) for iid in btc.collected} == {"DYDX", "BYBIT", "HYPERLIQUID"}


def test_the_notebook_states_the_planted_lead(shown: dict, fixture_archive: FixturePaths) -> None:
    defects = fixture_archive.defects
    btc = shown["cross"]["BTC-USD-PERP.DYDX"]
    pair = next(
        p for p in btc.pairs if (p.a, p.b) == (defects.lead_instrument, "BTC-USD-PERP.DYDX")
    )
    assert pair.peak is not None
    assert pair.peak[0] == defects.lead_seconds
    assert pair.sentence.startswith(f"BYBIT leads DYDX by {defects.lead_seconds} s")


def test_each_asset_is_compared_once(shown: dict) -> None:
    assert list(shown["cross"]) == ["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"]


def test_the_backtest_spec_is_one_venues_cluster(shown: dict) -> None:
    spec = shown["spec"]
    assert spec.venue == venue_of(shown["ANCHOR"])
    assert shown["ANCHOR"] in spec.instrument_ids


# Calls a cell may not make: a resample (bars come from the candle store) or an id split (only
# `kernel.venues` parses ids).
_FORBIDDEN_CALLS = frozenset({"resample", "split", "rsplit", "partition", "rpartition"})


def test_no_cell_holds_a_formula_a_resample_or_an_id_split() -> None:
    """The percent-format `.py` is the notebook's code (markdown and raw cells are comments)."""
    arithmetic = []
    calls = []
    for node in ast.walk(ast.parse(NOTEBOOK.read_text())):
        if isinstance(node, ast.BinOp | ast.UnaryOp) and not isinstance(node.op, ast.USub):
            arithmetic.append(ast.unparse(node))
        called = node.func if isinstance(node, ast.Call) else None
        if isinstance(called, ast.Attribute) and called.attr in _FORBIDDEN_CALLS:
            calls.append(ast.unparse(node))
    assert arithmetic == []
    assert calls == []
