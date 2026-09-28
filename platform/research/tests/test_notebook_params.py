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
`research/notebooks/_params.setting` (Story 27.5): a notebook constant is the JSON in
`NOTEBOOK_<NAME>` when set, else its default, and never silently of another type.
"""

import json

import pytest

from research.notebooks._params import setting


def test_unset_or_empty_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTEBOOK_N_FOLDS", raising=False)
    assert setting("N_FOLDS", 3) == 3
    monkeypatch.setenv("NOTEBOOK_N_FOLDS", "")
    assert setting("N_FOLDS", 3) == 3


def test_the_json_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    grid = {"ofi_threshold": [0.5, 1.0], "ofi_window": [2, 3]}
    monkeypatch.setenv("NOTEBOOK_GRID", json.dumps(grid))
    assert setting("GRID", {"ofi_threshold": [1.5]}) == grid
    monkeypatch.setenv("NOTEBOOK_INSTRUMENT", json.dumps("BTC-USD-PERP.HYPERLIQUID"))
    assert setting("INSTRUMENT", "BTC-USD-PERP.DYDX") == "BTC-USD-PERP.HYPERLIQUID"


def test_an_int_is_accepted_for_a_float(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTEBOOK_FRACTION", "1")
    value = setting("FRACTION", 0.7)
    assert value == 1.0
    assert isinstance(value, float)


@pytest.mark.parametrize(
    ("raw", "default"),
    [("2.5", 3), ("true", 3), ("3", True), ('"3"', 3), ("[1]", {}), ('"x"', 0.7)],
)
def test_a_type_mismatch_raises(monkeypatch: pytest.MonkeyPatch, raw: str, default: object) -> None:
    monkeypatch.setenv("NOTEBOOK_X", raw)
    with pytest.raises(ValueError, match="NOTEBOOK_X="):
        setting("X", default)


def test_invalid_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTEBOOK_X", "{not json")
    with pytest.raises(ValueError, match="is not JSON"):
        setting("X", {})
    monkeypatch.setenv("NOTEBOOK_X", "BTC-USD-PERP.DYDX")
    with pytest.raises(ValueError, match="a string must be quoted"):
        setting("X", "ETH-USD-PERP.DYDX")


def test_a_blank_value_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTEBOOK_N_FOLDS", "  ")
    assert setting("N_FOLDS", 3) == 3


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "[1, NaN]"])
def test_a_non_finite_number_raises(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("NOTEBOOK_X", raw)
    with pytest.raises(ValueError, match=r"NOTEBOOK_X=.*not a finite JSON number"):
        setting("X", 0.7 if "[" not in raw else [1.0])
