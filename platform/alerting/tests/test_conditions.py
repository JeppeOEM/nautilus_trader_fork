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
Story 33.8: `alerting.domain.conditions` -- every kind's validation (one table, each
`ConditionError.field`), its pure `evaluate` over hand-computed observations (one table per kind,
the story's I/O matrix included), `resolve_condition`'s back-compat rule, `describe`, and the
trendline geometry over the fixture the frontend's `trendlinePriceAt` test reads too.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from alerting.domain.conditions import CONDITION_FIELDS
from alerting.domain.conditions import CONDITION_KINDS
from alerting.domain.conditions import ConditionError
from alerting.domain.conditions import Observation
from alerting.domain.conditions import describe
from alerting.domain.conditions import evaluate
from alerting.domain.conditions import resolve_condition
from alerting.domain.conditions import validate_condition
from alerting.domain.geometry import trendline_price_at


_FIXTURES = Path(__file__).parent / "fixtures"

_VALID: dict[str, dict[str, Any]] = {
    "price_cross": {"kind": "price_cross", "level": 100},
    "price_cross_up": {"kind": "price_cross_up", "level": 100.0},
    "price_cross_down": {"kind": "price_cross_down", "level": 100.0},
    "price_above": {"kind": "price_above", "level": 100.0},
    "price_below": {"kind": "price_below", "level": 100.0},
    "pct_move": {"kind": "pct_move", "pct": 10, "bars": 2},
    "channel_exit": {"kind": "channel_exit", "upper": 110, "lower": 90},
    "indicator": {
        "kind": "indicator",
        "name": "RelativeStrengthIndex",
        "params": {"period": 14},
        "output": "value",
        "op": "crosses_up",
        "value": 70,
    },
    "trendline_cross": {"kind": "trendline_cross", "drawing_id": "d1"},
    "funding_above": {"kind": "funding_above", "rate": 0.0003},
    "funding_below": {"kind": "funding_below", "rate": -0.0001},
    "oi_change": {"kind": "oi_change", "pct": 5, "window_s": 3600},
    "liquidation_notional": {
        "kind": "liquidation_notional",
        "notional": 100000,
        "window_s": 300,
        "side": "long",
    },
    "forced_share": {"kind": "forced_share", "share": 0.15, "window_s": 300},
}


def test_every_kind_has_a_valid_example_and_a_field_spec() -> None:
    assert set(_VALID) == set(CONDITION_KINDS) == set(CONDITION_FIELDS)


@pytest.mark.parametrize("kind", CONDITION_KINDS)
def test_a_valid_condition_normalises_in_field_order(kind: str) -> None:
    normalised = validate_condition(_VALID[kind])
    assert normalised["kind"] == kind
    assert list(normalised)[1:] == [f for f in CONDITION_FIELDS[kind] if f in normalised]
    assert validate_condition(normalised) == normalised  # idempotent: the stored form reloads


def test_normalisation_types_and_defaults() -> None:
    assert validate_condition(_VALID["price_cross"]) == {"kind": "price_cross", "level": 100.0}
    assert type(validate_condition(_VALID["price_cross"])["level"]) is float
    pct = validate_condition({"kind": "pct_move", "pct": 10, "bars": 2.0})
    assert pct == {"kind": "pct_move", "pct": 10.0, "bars": 2}
    assert type(pct["bars"]) is int
    indicator = validate_condition(_VALID["indicator"])
    assert indicator["source"] == "close"
    assert indicator["value"] == 70.0
    both_sides = validate_condition({**_VALID["liquidation_notional"], "side": None})
    assert "side" not in both_sides


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ([1], "condition"),
        ({"kind": "nope"}, "kind"),
        ({"level": 1}, "kind"),
        ({"kind": "price_cross"}, "level"),
        ({"kind": "price_cross", "level": 1, "upper": 2}, "upper"),
        ({"kind": "price_above", "level": float("nan")}, "level"),
        ({"kind": "price_above", "level": float("inf")}, "level"),
        ({"kind": "price_above", "level": True}, "level"),
        ({"kind": "price_above", "level": "100"}, "level"),
        ({"kind": "pct_move", "pct": 0, "bars": 2}, "pct"),
        ({"kind": "pct_move", "pct": 1000.5, "bars": 2}, "pct"),
        ({"kind": "pct_move", "pct": -1001, "bars": 2}, "pct"),
        ({"kind": "pct_move", "pct": 5, "bars": 0}, "bars"),
        ({"kind": "pct_move", "pct": 5, "bars": 501}, "bars"),
        ({"kind": "pct_move", "pct": 5, "bars": 2.5}, "bars"),
        ({"kind": "pct_move", "pct": 5}, "bars"),
        ({"kind": "channel_exit", "upper": 90, "lower": 90}, "upper"),
        ({"kind": "channel_exit", "upper": 80, "lower": 90}, "upper"),
        ({"kind": "channel_exit", "upper": 110}, "lower"),
        ({**_VALID["indicator"], "name": ""}, "name"),
        ({**_VALID["indicator"], "params": [14]}, "params"),
        ({**_VALID["indicator"], "params": {"period": None}}, "params.period"),
        ({**_VALID["indicator"], "params": {"period": [1]}}, "params.period"),
        ({**_VALID["indicator"], "params": {"k": float("nan")}}, "params.k"),
        ({**_VALID["indicator"], "source": ""}, "source"),
        ({**_VALID["indicator"], "output": ""}, "output"),
        ({**_VALID["indicator"], "output": "x" * 65}, "output"),
        ({**_VALID["indicator"], "op": ">="}, "op"),
        ({**_VALID["indicator"], "value": float("nan")}, "value"),
        ({"kind": "trendline_cross", "drawing_id": ""}, "drawing_id"),
        ({"kind": "trendline_cross", "drawing_id": "d" * 129}, "drawing_id"),
        ({"kind": "funding_above", "rate": float("inf")}, "rate"),
        ({"kind": "funding_below"}, "rate"),
        ({"kind": "oi_change", "pct": 5, "window_s": 0}, "window_s"),
        ({"kind": "oi_change", "pct": 5, "window_s": 86401}, "window_s"),
        ({"kind": "oi_change", "pct": 0, "window_s": 60}, "pct"),
        ({**_VALID["liquidation_notional"], "notional": 0}, "notional"),
        ({**_VALID["liquidation_notional"], "notional": -5}, "notional"),
        ({**_VALID["liquidation_notional"], "side": "both"}, "side"),
        ({"kind": "liquidation_notional", "notional": 1}, "window_s"),
        ({**_VALID["forced_share"], "share": 0}, "share"),
        ({**_VALID["forced_share"], "share": 10.5}, "share"),
    ],
)
def test_a_bad_condition_names_its_field(raw: Any, field: str) -> None:
    with pytest.raises(ConditionError) as caught:
        validate_condition(raw)
    assert caught.value.field == field
    assert str(caught.value).startswith(field)


def test_the_largest_share_and_bounds_are_accepted() -> None:
    assert validate_condition({**_VALID["forced_share"], "share": 10})["share"] == 10.0
    assert validate_condition({"kind": "pct_move", "pct": -1000, "bars": 500})["bars"] == 500
    assert (
        validate_condition({"kind": "oi_change", "pct": 1, "window_s": 86400})["window_s"] == 86400
    )


# --- evaluate: one table per kind -----------------------------------------------------------------


def _met(kind: str, prev: Any, cur: Any, **override: Any) -> bool:
    return evaluate(validate_condition({**_VALID[kind], **override}), Observation(prev, cur))


@pytest.mark.parametrize(
    ("prev", "cur", "met"),
    [
        (99, 101, True),
        (101, 99, True),
        (99, 100, True),
        (100, 100, False),
        (99, 99.5, False),
        (None, 101, False),
    ],
)
def test_price_cross_either_direction(prev: Any, cur: Any, met: bool) -> None:
    assert _met("price_cross", prev, cur) is met


@pytest.mark.parametrize(
    ("kind", "prev", "cur", "met"),
    [
        ("price_cross_up", 99, 100, True),  # matrix: 99 -> 100 fires
        ("price_cross_up", 101, 99, False),  # matrix: 101 -> 99 does not
        ("price_cross_up", 100, 101, False),  # starting on the level is no cross up
        ("price_cross_down", 101, 100, True),
        ("price_cross_down", 99, 101, False),
    ],
)
def test_directional_crosses(kind: str, prev: Any, cur: Any, met: bool) -> None:
    assert _met(kind, prev, cur) is met


@pytest.mark.parametrize(
    ("kind", "cur", "met"),
    [
        ("price_above", 101, True),
        ("price_above", 100, False),  # strict
        ("price_below", 99, True),
        ("price_below", 100, False),
        ("price_above", None, False),
    ],
)
def test_above_and_below_are_strict(kind: str, cur: Any, met: bool) -> None:
    assert _met(kind, None, cur) is met


@pytest.mark.parametrize(
    ("pct", "change", "met"),
    [
        (10, Decimal(10), True),  # matrix: 100 -> 110 is 10 % >= 10
        (10, Decimal("9.99"), False),
        (-5, Decimal(-5), True),  # a negative pct fires at a change <= it
        (-5, Decimal(-4), False),
        (-5, Decimal(6), False),
    ],
)
def test_pct_move_reaches_its_signed_threshold(pct: float, change: Decimal, met: bool) -> None:
    assert _met("pct_move", None, change, pct=pct) is met


@pytest.mark.parametrize(
    ("prev", "cur", "met"),
    [(105, 111, True), (95, 89, True), (105, 109, False), (111, 112, False), (89, 95, False)],
)
def test_channel_exit_crosses_up_through_upper_or_down_through_lower(
    prev: float, cur: float, met: bool
) -> None:
    assert _met("channel_exit", prev, cur) is met


@pytest.mark.parametrize(
    ("op", "prev", "cur", "met"),
    [
        ("crosses_up", 69, 71, True),  # matrix: RSI 69 -> 71 crosses up 70
        ("crosses_up", 71, 72, False),
        ("crosses_down", 71, 69, True),
        ("crosses_down", None, 69, False),
        (">", None, 71, True),
        (">", None, 70, False),
        ("<", None, 69, True),
    ],
)
def test_indicator_ops(op: str, prev: Any, cur: Any, met: bool) -> None:
    assert _met("indicator", prev, cur, op=op) is met


@pytest.mark.parametrize(
    ("prev", "cur", "met"),
    [(-10.0, 10.0, True), (10.0, -10.0, True), (-10.0, -1.0, False), (None, 10.0, False)],
)
def test_trendline_cross_is_a_cross_of_close_minus_line_at_zero(
    prev: Any, cur: Any, met: bool
) -> None:
    # The matrix: closes 290 -> 310 against line(3000) = 300.
    assert _met("trendline_cross", prev, cur) is met


@pytest.mark.parametrize(
    ("kind", "cur", "met"),
    [
        ("funding_above", Decimal("0.0002"), False),  # matrix: 0.0002 does not fire
        ("funding_above", Decimal("0.0004"), True),  # 0.0004 does
        ("funding_above", Decimal("0.0003"), False),  # exactly the stored rate: not above
        ("funding_below", Decimal("-0.0002"), True),
        ("funding_below", Decimal("-0.0001"), False),
    ],
)
def test_funding_compares_the_exact_decimal(kind: str, cur: Decimal, met: bool) -> None:
    assert _met(kind, None, cur) is met


@pytest.mark.parametrize(
    ("change", "met"), [(Decimal(5), True), (Decimal("4.99"), False), (None, False)]
)
def test_oi_change(change: Decimal | None, met: bool) -> None:
    assert _met("oi_change", None, change) is met


@pytest.mark.parametrize(
    ("total", "met"), [(Decimal(60000), False), (Decimal(110000), True), (Decimal(100000), True)]
)
def test_liquidation_notional_fires_at_or_above(total: Decimal, met: bool) -> None:
    assert _met("liquidation_notional", None, total) is met


@pytest.mark.parametrize(("share", "met"), [(0.2, True), (0.15, True), (0.1, False), (None, False)])
def test_forced_share_fires_at_or_above(share: float | None, met: bool) -> None:
    assert _met("forced_share", None, share) is met


# --- resolve_condition: the create request's level/condition rule ---------------------------------


def test_level_alone_is_a_price_cross() -> None:
    assert resolve_condition(100, None) == {"kind": "price_cross", "level": 100.0}


def test_neither_level_nor_condition_is_refused() -> None:
    with pytest.raises(ConditionError) as caught:
        resolve_condition(None, None)
    assert caught.value.field == "level"


def test_both_given_must_agree_on_a_level_kind() -> None:
    assert resolve_condition(6, {"kind": "price_above", "level": 6}) == {
        "kind": "price_above",
        "level": 6.0,
    }
    with pytest.raises(ConditionError) as disagree:  # matrix: level 5 with price_above 6
        resolve_condition(5, {"kind": "price_above", "level": 6})
    assert disagree.value.field == "level"
    with pytest.raises(ConditionError) as not_level:
        resolve_condition(5, _VALID["funding_above"])
    assert not_level.value.field == "level"


def test_a_bad_condition_field_is_named_under_condition() -> None:
    with pytest.raises(ConditionError) as caught:
        resolve_condition(None, {"kind": "channel_exit", "upper": 1, "lower": 2})
    assert caught.value.field == "condition.upper"


# --- describe -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("price_cross", "close crosses 100 on 3600s bars"),
        ("price_cross_up", "close crosses up 100 on 3600s bars"),
        ("price_cross_down", "close crosses down 100 on 3600s bars"),
        ("price_above", "close > 100 on 3600s bars"),
        ("price_below", "close < 100 on 3600s bars"),
        ("pct_move", "close change >= +10% over 2 bars on 3600s bars"),
        ("channel_exit", "close exits 90..110 on 3600s bars"),
        ("indicator", "RelativeStrengthIndex(period=14) value crosses up 70 on 3600s bars"),
        ("trendline_cross", "close crosses trendline d1 on 3600s bars"),
        ("funding_above", "funding rate > 0.0003 on 3600s bars"),
        ("funding_below", "funding rate < -0.0001 on 3600s bars"),
        ("oi_change", "open interest change >= +5% over 3600s on 3600s bars"),
        ("liquidation_notional", "long liquidation notional >= 100000 over 300s on 3600s bars"),
        ("forced_share", "forced share >= 0.15 over 300s on 3600s bars"),
    ],
)
def test_describe_every_kind(kind: str, text: str) -> None:
    assert describe(validate_condition(_VALID[kind]), 3600) == text


def test_describe_names_a_non_close_source_and_a_falling_pct() -> None:
    hl2 = validate_condition({**_VALID["indicator"], "source": "hl2", "op": ">"})
    assert (
        describe(hl2, 60) == "RelativeStrengthIndex(period=14, source=hl2) value > 70 on 60s bars"
    )
    fall = validate_condition({"kind": "pct_move", "pct": -2.5, "bars": 3})
    assert describe(fall, 60) == "close change <= -2.5% over 3 bars on 60s bars"
    both = validate_condition({"kind": "liquidation_notional", "notional": 5e4, "window_s": 60})
    assert describe(both, 60) == "liquidation notional >= 50000 over 60s on 60s bars"


# --- geometry: the fixture shared with `frontend/src/lib/drawings.ts` -----------------------------


def _trendline_cases() -> list[dict[str, Any]]:
    return json.loads((_FIXTURES / "trendline_cases.json").read_text())["cases"]


@pytest.mark.parametrize("case", _trendline_cases(), ids=lambda case: case["name"])
def test_trendline_price_at_matches_the_shared_fixture(case: dict[str, Any]) -> None:
    assert trendline_price_at(case["anchors"], case["t"]) == case["expected"]
