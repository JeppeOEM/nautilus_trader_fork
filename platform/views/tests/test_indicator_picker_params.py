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
`views.indicator_picker.check_params`: the one save-time rule both indicator-list PUTs and the
layout seed/reset apply, refusing exactly what the replay would refuse (no Nautilus mocks: the
native half is judged by constructing the real indicator class).
"""

import time
from typing import Any

import pytest

from views.indicator_picker import CUSTOM_INDICATOR_CATALOG
from views.indicator_picker import MAX_ABS_FLOAT_PARAM
from views.indicator_picker import MAX_INT_PARAM
from views.indicator_picker import check_params
from views.indicator_picker import merged_catalog


def test_every_catalog_default_passes() -> None:
    for name, entry in merged_catalog().items():
        check_params(name, entry["params"])


def test_every_custom_default_passes_listed_or_not() -> None:
    """
    `merged_catalog` lists only listed entries; the unlisted `AnchoredStoredVWAP` (Story 33.6) keeps
    a default that passes too (`anchor_t` "0", the epoch, a digit string), so every catalog default
    is one its own check accepts.
    """
    for name, spec in CUSTOM_INDICATOR_CATALOG.items():
        check_params(name, spec.params)


def test_partial_params_and_valid_enum_names_pass() -> None:
    check_params("SimpleMovingAverage", {"period": 50})
    check_params("BollingerBands", {"ma_type": "EXPONENTIAL", "k": 3})
    check_params("CandlePattern", {"pattern": "HAMMER"})
    check_params("OrderFlowImbalance", {"window": 5})
    check_params("CumulativeVolumeDelta", {})


def test_on_balance_volume_period_zero_is_its_valid_default() -> None:
    check_params("OnBalanceVolume", {"period": 0})


@pytest.mark.parametrize("period", [0, -1])
def test_non_positive_period_is_refused_naming_the_param(period: int) -> None:
    with pytest.raises(ValueError, match="period"):
        check_params("SimpleMovingAverage", {"period": period})


@pytest.mark.parametrize(
    ("name", "params", "choice"),
    [
        ("CandlePattern", {"pattern": "hammer"}, "HAMMER"),
        ("BollingerBands", {"ma_type": "NOPE"}, "SIMPLE"),
        ("BollingerBands", {"ma_type": "ADAPTIVE"}, "SIMPLE"),
        ("BollingerBands", {"ma_type": 1}, "SIMPLE"),
    ],
)
def test_bad_enum_value_is_refused_listing_the_choices(
    name: str, params: dict[str, Any], choice: str
) -> None:
    with pytest.raises(ValueError, match="choices") as excinfo:
        check_params(name, params)
    assert choice in str(excinfo.value)
    assert "ADAPTIVE" not in str(excinfo.value).split("choices")[1]


def test_unknown_param_key_is_refused_naming_the_key() -> None:
    with pytest.raises(ValueError, match="perod"):
        check_params("SimpleMovingAverage", {"perod": 20})


def test_oversized_integer_is_refused_before_any_construction() -> None:
    started = time.monotonic()
    with pytest.raises(ValueError, match=str(MAX_INT_PARAM)):
        check_params("HullMovingAverage", {"period": 10**9})
    assert time.monotonic() - started < 1.0  # HullMovingAverage(10**9) itself takes ~16 s


@pytest.mark.parametrize(
    ("name", "params", "param"),
    [
        # A Cython `int` param truncates a float: 2.5 would silently replay as 2, 1e9 as 10**9.
        ("SimpleMovingAverage", {"period": 2.5}, "period"),
        ("HullMovingAverage", {"period": 1e9}, "period"),
        ("SimpleMovingAverage", {"period": True}, "period"),
        ("SimpleMovingAverage", {"period": "x"}, "period"),
        ("KeltnerChannel", {"use_previous": "no"}, "use_previous"),
        ("BollingerBands", {"k": float("nan")}, "k"),
        # Out of float range: must be a 400, never `math.isfinite`'s `OverflowError` (a 500).
        ("BollingerBands", {"k": 10**400}, "k"),
        # Finite but replays to `inf`, which the values JSON cannot carry.
        ("BollingerBands", {"k": 1e308}, "k"),
    ],
)
def test_wrongly_typed_value_is_refused_naming_the_param(
    name: str, params: dict[str, Any], param: str
) -> None:
    with pytest.raises(ValueError, match=param):
        check_params(name, params)


def test_float_param_sent_as_an_int_is_bounded_by_the_float_ceiling_not_the_int_cap() -> None:
    check_params("BollingerBands", {"k": MAX_INT_PARAM * 2})  # 20000 and 20000.0 alike
    with pytest.raises(ValueError, match="k="):
        check_params("BollingerBands", {"k": int(MAX_ABS_FLOAT_PARAM) + 1})


def test_constructor_refusal_names_the_param_without_echoing_the_params() -> None:
    with pytest.raises(ValueError, match="'period' not a positive") as excinfo:
        check_params("SimpleMovingAverage", {"period": 0})
    assert "{" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("name", "window"),
    [("OrderFlowImbalance", 0), ("CancelPressure", -1), ("OrderFlowImbalance", True)],
)
def test_bad_custom_window_is_refused(name: str, window: Any) -> None:
    with pytest.raises(ValueError, match="window"):
        check_params(name, {"window": window})


def test_constructor_refusal_names_the_indicator() -> None:
    with pytest.raises(ValueError, match="CandlePattern"):
        check_params("CandlePattern", {"trend_bars": -5})


def test_non_object_params_are_refused() -> None:
    with pytest.raises(ValueError, match="object"):
        check_params("SimpleMovingAverage", [])


def test_unknown_indicator_is_refused() -> None:
    with pytest.raises(ValueError, match="NoSuchIndicator"):
        check_params("NoSuchIndicator", {})
