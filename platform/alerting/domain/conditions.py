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
An alert's condition (Story 33.8): the 14 kinds, their one validator, their one describer and the
pure rule that decides whether one observation meets them.

A condition is a plain `dict` (`{"kind": ..., <fields>}`), the stored `[alerts.condition]` table
and the `/api/alerts` `condition` object alike. `validate_condition` is the only way in: it refuses
an unknown kind, an unknown or missing field and an out-of-range value with a `ConditionError`
naming the field, and returns the normalised dict (fields in `CONDITION_FIELDS` order, `bars`/
`window_s` as ints, every other number a float, `source` defaulted, an absent `side` omitted).

`evaluate(condition, observation)` is pure: the engine (`application.engine.AlertEngine`) turns each
input into an `Observation` -- the previous and current value of the series the kind reads -- and
the firing policy (`domain.policy.step`) decides which observations reach it:

- the price kinds read the forming bar's close; `pct_move` reads `kernel.indicators.pct_change`
  from the close N closed bars before the newest closed one to the close; `trendline_cross` reads
  `close - line(t)` (`domain.geometry`), so its cross is at 0; `indicator` reads the replayed
  output at the newest closed bar and the bar before it;
- `funding_*` read each funding tick's exact `Decimal`; `oi_change` reads `pct_change` over the
  open-interest ticks; `liquidation_notional` the exact `Decimal` notional sum over its window;
  `forced_share` the `kernel.indicators.units_ratio` of liquidated to traded size over its window.

A cross is `prev < x <= cur` (up) or `prev > x >= cur` (down), the pre-33.8 `_crossed` rule; the
above/below kinds are strict comparisons. A `Decimal` observation is compared with its float
threshold read back as the shortest decimal that float prints as (`Decimal(repr(x))`), so a
stored `0.0003` compares as `0.0003`, never as the binary float's `0.000299999...`.
"""

import math
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


Value = float | Decimal

LEVEL_KINDS: tuple[str, ...] = (
    "price_cross",
    "price_cross_up",
    "price_cross_down",
    "price_above",
    "price_below",
)
CONDITION_KINDS: tuple[str, ...] = (
    *LEVEL_KINDS,
    "pct_move",
    "channel_exit",
    "indicator",
    "trendline_cross",
    "funding_above",
    "funding_below",
    "oi_change",
    "liquidation_notional",
    "forced_share",
)

# Every field of each kind, in the normalised (stored) order. Mirrored by the pydantic models of
# `data_api/routes/alerts.py` and by `frontend/src/lib/alertConditions.ts`, each held to it by a
# mirror test.
CONDITION_FIELDS: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(LEVEL_KINDS, ("level",)),
    "pct_move": ("pct", "bars"),
    "channel_exit": ("upper", "lower"),
    "indicator": ("name", "params", "source", "output", "op", "value"),
    "trendline_cross": ("drawing_id",),
    "funding_above": ("rate",),
    "funding_below": ("rate",),
    "oi_change": ("pct", "window_s"),
    "liquidation_notional": ("notional", "window_s", "side"),
    "forced_share": ("share", "window_s"),
}
# The optional fields and their defaults: `source` defaults to the close, an absent `side` sums
# both sides and is omitted from the normalised dict.
OPTIONAL_FIELDS: dict[str, dict[str, str | None]] = {
    "indicator": {"source": "close"},
    "liquidation_notional": {"side": None},
}

# The families: which input feeds a kind (`AlertEngine` routes on them).
BAR_KINDS: frozenset[str] = frozenset(
    {*LEVEL_KINDS, "pct_move", "channel_exit", "trendline_cross", "indicator", "forced_share"}
)
DERIVS_KINDS: frozenset[str] = frozenset({"funding_above", "funding_below", "oi_change"})
LIQUIDATION_KINDS: frozenset[str] = frozenset({"liquidation_notional", "forced_share"})
WINDOW_KINDS: frozenset[str] = frozenset({"oi_change", "liquidation_notional", "forced_share"})

INDICATOR_OPS: tuple[str, ...] = (">", "<", "crosses_up", "crosses_down")
LIQUIDATION_SIDES: tuple[str, ...] = ("long", "short")
MAX_ABS_PCT = 1000.0
MAX_BARS = 500
MAX_WINDOW_S = 86_400
MAX_SHARE = 10.0
MAX_OUTPUT_LEN = 64
MAX_DRAWING_ID_LEN = 128
MAX_NAME_LEN = 128
MAX_SOURCE_LEN = 16


class ConditionError(ValueError):
    """A condition that cannot be stored: `field` names the offending field (relative path)."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field} {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True)
class Observation:
    """The series value a condition reads: the one before (`prev`, None if unseen) and now."""

    prev: Value | None
    cur: Value | None


# --- validation ---------------------------------------------------------------------------------


def _number(field: str, raw: Any) -> float:
    # bool is an int subclass: `true` is no number.
    if not isinstance(raw, int | float) or isinstance(raw, bool):
        raise ConditionError(field, "must be a number")
    value = float(raw)
    if not math.isfinite(value):
        raise ConditionError(field, "must be finite")
    return value


def _whole(field: str, raw: Any, high: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise ConditionError(field, f"must be an integer in 1..{high}")
    if isinstance(raw, float) and not (math.isfinite(raw) and raw.is_integer()):
        raise ConditionError(field, f"must be an integer in 1..{high}")
    value = int(raw)
    if not 1 <= value <= high:
        raise ConditionError(field, f"must be an integer in 1..{high}")
    return value


def _text(field: str, raw: Any, high: int) -> str:
    if not isinstance(raw, str) or not 1 <= len(raw) <= high:
        raise ConditionError(field, f"must be a string of 1..{high} characters")
    return raw


def _pct(field: str, raw: Any) -> float:
    value = _number(field, raw)
    if value == 0 or abs(value) > MAX_ABS_PCT:
        raise ConditionError(field, f"must be non-zero and at most {MAX_ABS_PCT:g} in magnitude")
    return value


def _positive(field: str, raw: Any) -> float:
    value = _number(field, raw)
    if value <= 0:
        raise ConditionError(field, "must be greater than 0")
    return value


def _share(field: str, raw: Any) -> float:
    value = _number(field, raw)
    if not 0 < value <= MAX_SHARE:
        raise ConditionError(field, f"must be in (0, {MAX_SHARE:g}]")
    return value


def _choice(options: tuple[str, ...]) -> Callable[[str, Any], str]:
    def check(field: str, raw: Any) -> str:
        if raw not in options:
            raise ConditionError(field, f"must be one of {list(options)}")
        return str(raw)

    return check


def _params(field: str, raw: Any) -> dict[str, Any]:
    # Flat scalars only: the params are stored as a TOML table and replayed as constructor kwargs.
    if not isinstance(raw, dict):
        raise ConditionError(field, "must be an object")
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise ConditionError(field, "keys must be non-empty strings")
        if not isinstance(value, str | int | float | bool):
            raise ConditionError(f"{field}.{key}", "must be a string, number or boolean")
        if isinstance(value, float) and not math.isfinite(value):
            raise ConditionError(f"{field}.{key}", "must be finite")
    return dict(raw)


_CHECKS: dict[str, Callable[[str, Any], Any]] = {
    "level": _number,
    "upper": _number,
    "lower": _number,
    "value": _number,
    "rate": _number,
    "pct": _pct,
    "bars": lambda field, raw: _whole(field, raw, MAX_BARS),
    "window_s": lambda field, raw: _whole(field, raw, MAX_WINDOW_S),
    "notional": _positive,
    "share": _share,
    "name": lambda field, raw: _text(field, raw, MAX_NAME_LEN),
    "params": _params,
    "source": lambda field, raw: _text(field, raw, MAX_SOURCE_LEN),
    "output": lambda field, raw: _text(field, raw, MAX_OUTPUT_LEN),
    "op": _choice(INDICATOR_OPS),
    "drawing_id": lambda field, raw: _text(field, raw, MAX_DRAWING_ID_LEN),
    "side": _choice(LIQUIDATION_SIDES),
}


def _field_value(kind: str, field: str, raw: Mapping[str, Any]) -> Any:
    """Return the checked value of `field`, its default when optional and absent (None: omit)."""
    optional = OPTIONAL_FIELDS.get(kind, {})
    value = raw.get(field)
    if value is None:
        if field in optional:
            return optional[field]
        raise ConditionError(field, "is required")
    return _CHECKS[field](field, value)


def validate_condition(raw: Any) -> dict[str, Any]:
    """
    Return the normalised condition, or raise `ConditionError` naming the field: the one rule every
    writer (the route, the service, the store's load) applies. An explicit null for an optional
    field reads as absent.
    """
    if not isinstance(raw, Mapping):
        raise ConditionError("condition", "must be an object")
    kind = raw.get("kind")
    if kind not in CONDITION_FIELDS:
        raise ConditionError("kind", f"must be one of {list(CONDITION_KINDS)}")
    fields = CONDITION_FIELDS[kind]
    unknown = sorted(set(raw) - {"kind", *fields})
    if unknown:
        raise ConditionError(unknown[0], f"is not a field of a {kind} condition")
    out: dict[str, Any] = {"kind": kind}
    for field in fields:
        value = _field_value(kind, field, raw)
        if value is not None:
            out[field] = value
    if kind == "channel_exit" and out["upper"] <= out["lower"]:
        raise ConditionError("upper", "must be greater than lower")
    return out


def level_of(condition: Mapping[str, Any]) -> float | None:
    """Return the alert's `level` mirror: a price-level kind's level, else None."""
    return condition["level"] if condition["kind"] in LEVEL_KINDS else None


def resolve_condition(level: float | None, condition: Any) -> dict[str, Any]:
    """
    Return the normalised condition of a create request carrying `level`, `condition` or both
    (AD-D12 back-compat): `level` alone is a `price_cross`; both are refused unless `condition` is
    a level-bearing price kind at an equal level. Field paths are request-relative
    (`condition.upper`, `level`).
    """
    if condition is None:
        if level is None:
            raise ConditionError("level", "or condition is required")
        return validate_condition({"kind": "price_cross", "level": level})
    try:
        normalised = validate_condition(condition)
    except ConditionError as exc:
        raise ConditionError(f"condition.{exc.field}", exc.reason) from exc
    if level is not None and level_of(normalised) != _number("level", level):
        raise ConditionError(
            "level", "must be omitted, or equal the condition's level for a price-level kind"
        )
    return normalised


# --- evaluation ---------------------------------------------------------------------------------


def _threshold(x: float, like: Value) -> Value:
    return Decimal(repr(x)) if isinstance(like, Decimal) else x


def _crosses_up(obs: Observation, x: float) -> bool:
    prev, cur = obs.prev, obs.cur
    if prev is None or cur is None:
        return False
    level = _threshold(x, cur)
    return prev < level <= cur


def _crosses_down(obs: Observation, x: float) -> bool:
    prev, cur = obs.prev, obs.cur
    if prev is None or cur is None:
        return False
    level = _threshold(x, cur)
    return prev > level >= cur


def _crosses(obs: Observation, x: float) -> bool:
    return _crosses_up(obs, x) or _crosses_down(obs, x)


def _above(obs: Observation, x: float) -> bool:
    return obs.cur is not None and obs.cur > _threshold(x, obs.cur)


def _below(obs: Observation, x: float) -> bool:
    return obs.cur is not None and obs.cur < _threshold(x, obs.cur)


def _at_least(obs: Observation, x: float) -> bool:
    return obs.cur is not None and obs.cur >= _threshold(x, obs.cur)


def _reaches(obs: Observation, pct: float) -> bool:
    """Return whether a change reaches `pct`: >= a positive one, <= a negative one."""
    if obs.cur is None:
        return False
    target = _threshold(pct, obs.cur)
    return obs.cur >= target if pct > 0 else obs.cur <= target


_INDICATOR_OPS: dict[str, Callable[[Observation, float], bool]] = {
    ">": _above,
    "<": _below,
    "crosses_up": _crosses_up,
    "crosses_down": _crosses_down,
}

_EVALUATORS: dict[str, Callable[[Mapping[str, Any], Observation], bool]] = {
    "price_cross": lambda c, o: _crosses(o, c["level"]),
    "price_cross_up": lambda c, o: _crosses_up(o, c["level"]),
    "price_cross_down": lambda c, o: _crosses_down(o, c["level"]),
    "price_above": lambda c, o: _above(o, c["level"]),
    "price_below": lambda c, o: _below(o, c["level"]),
    "pct_move": lambda c, o: _reaches(o, c["pct"]),
    "channel_exit": lambda c, o: _crosses_up(o, c["upper"]) or _crosses_down(o, c["lower"]),
    "indicator": lambda c, o: _INDICATOR_OPS[c["op"]](o, c["value"]),
    "trendline_cross": lambda c, o: _crosses(o, 0.0),
    "funding_above": lambda c, o: _above(o, c["rate"]),
    "funding_below": lambda c, o: _below(o, c["rate"]),
    "oi_change": lambda c, o: _reaches(o, c["pct"]),
    "liquidation_notional": lambda c, o: _at_least(o, c["notional"]),
    "forced_share": lambda c, o: _at_least(o, c["share"]),
}


def evaluate(condition: Mapping[str, Any], observation: Observation) -> bool:
    """Whether `observation` meets the (normalised) `condition`; a missing value never does."""
    return _EVALUATORS[condition["kind"]](condition, observation)


# --- description --------------------------------------------------------------------------------


def _num(x: float) -> str:
    """Return a threshold as the operator typed it: `100`, not `100.0`; `0.0003`, not `3e-04`."""
    if float(x).is_integer() and abs(x) < 1e15:
        return str(int(x))
    return format(Decimal(repr(float(x))), "f")


def _signed_pct(pct: float) -> str:
    return f"{'>= +' if pct > 0 else '<= '}{_num(pct)}%"


def _indicator_text(c: Mapping[str, Any]) -> str:
    args = [f"{k}={v}" for k, v in sorted(c["params"].items())]
    if c["source"] != "close":
        args.append(f"source={c['source']}")
    op = {"crosses_up": "crosses up", "crosses_down": "crosses down"}.get(c["op"], c["op"])
    return f"{c['name']}({', '.join(args)}) {c['output']} {op} {_num(c['value'])}"


def _liquidation_text(c: Mapping[str, Any]) -> str:
    side = f"{c['side']} " if "side" in c else ""
    return f"{side}liquidation notional >= {_num(c['notional'])} over {c['window_s']}s"


_DESCRIBERS: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "price_cross": lambda c: f"close crosses {_num(c['level'])}",
    "price_cross_up": lambda c: f"close crosses up {_num(c['level'])}",
    "price_cross_down": lambda c: f"close crosses down {_num(c['level'])}",
    "price_above": lambda c: f"close > {_num(c['level'])}",
    "price_below": lambda c: f"close < {_num(c['level'])}",
    "pct_move": lambda c: f"close change {_signed_pct(c['pct'])} over {c['bars']} bars",
    "channel_exit": lambda c: f"close exits {_num(c['lower'])}..{_num(c['upper'])}",
    "indicator": _indicator_text,
    "trendline_cross": lambda c: f"close crosses trendline {c['drawing_id']}",
    "funding_above": lambda c: f"funding rate > {_num(c['rate'])}",
    "funding_below": lambda c: f"funding rate < {_num(c['rate'])}",
    "oi_change": lambda c: f"open interest change {_signed_pct(c['pct'])} over {c['window_s']}s",
    "liquidation_notional": _liquidation_text,
    "forced_share": lambda c: f"forced share >= {_num(c['share'])} over {c['window_s']}s",
}


def describe(condition: Mapping[str, Any], bar_seconds: int) -> str:
    """
    Return the one human text of a condition (`AlertResponse.condition_text`, the toast's
    `condition`, the `{{condition}}` placeholder), e.g.
    `RelativeStrengthIndex(period=14) value > 70 on 3600s bars`. The browser never composes its own.
    """
    return f"{_DESCRIBERS[condition['kind']](condition)} on {bar_seconds}s bars"
