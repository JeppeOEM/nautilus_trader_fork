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
`kernel.ta` (Story 33.11): per indicator a hand-computed fixture (every step written out below,
with Nautilus's seeding: a Wilder average starts at its first input, `DirectionalMovement` feeds 0
on the first bar, the first true range is `h - l`), cross-checked by a naive batch computation over
seeded random walks; then the params each constructor refuses, `reset`, constant state over 10,000
bars and `handle_bar` on a real `Bar`.
"""

import random
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import pytest

from kernel.ta import PIVOT_LEVELS
from kernel.ta import AverageDirectionalIndex
from kernel.ta import AwesomeOscillator
from kernel.ta import ChaikinMoneyFlow
from kernel.ta import MoneyFlowIndex
from kernel.ta import ParabolicSAR
from kernel.ta import PivotPoints
from kernel.ta import Supertrend
from kernel.ta import WilliamsPercentR
from kernel.ta import ZigZag
from kernel.ta import _RollingSum
from kernel.ta import pivot_levels
from nautilus_trader.indicators import Indicator
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


# (high, low, close, volume)
HLCV = tuple[float, float, float, float]


def _walk(seed: int, count: int) -> list[HLCV]:
    """Seeded random-walk bars, `l <= c <= h`, positive volume, with some flat and zero-volume ones."""
    rng = random.Random(seed)  # noqa: S311 -- a reproducible walk, not a secret
    close = 100.0
    bars: list[HLCV] = []
    for _ in range(count):
        close = max(1.0, close + rng.gauss(0, 1.0))
        high = close + abs(rng.gauss(0, 0.6))
        low = max(0.5, close - abs(rng.gauss(0, 0.6)))
        flat = rng.random() < 0.03
        volume = 0.0 if rng.random() < 0.03 else rng.uniform(0.1, 5.0)
        bars.append((close, close, close, volume) if flat else (high, low, close, volume))
    return bars


def _approx(values: list[float | None]) -> list[Any]:
    return [None if v is None else pytest.approx(v, rel=1e-12, abs=1e-9) for v in values]


# --- the naive batch references: whole-series recomputations, no streaming state ----------------


def _wilder(xs: list[float], period: int) -> list[float]:
    """Nautilus's Wilder average at every index: seeded at the first input, alpha 1/period."""
    out = [xs[0]]
    for x in xs[1:]:
        out.append(out[-1] + (x - out[-1]) / period)
    return out


def _true_ranges(bars: list[HLCV]) -> list[float]:
    prev_closes = [bars[0][2]] + [b[2] for b in bars[:-1]]
    return [max(pc, h) - min(l, pc) for (h, l, _c, _v), pc in zip(bars, prev_closes, strict=True)]


def _batch_supertrend(bars: list[HLCV], period: int, mult: float) -> list[float | None]:
    atr = _wilder(_true_ranges(bars), period)
    out: list[float | None] = [None] * (period - 1)
    upper = lower = 0.0
    trend = -1  # TradingView's start: the down trend, value the upper band
    for i in range(period - 1, len(bars)):
        h, l, c, _ = bars[i]
        bu, bl = (h + l) / 2 + mult * atr[i], (h + l) / 2 - mult * atr[i]
        if i == period - 1:
            upper, lower = bu, bl
        else:
            pc = bars[i - 1][2]
            upper = bu if bu < upper or pc > upper else upper
            lower = bl if bl > lower or pc < lower else lower
            trend = (-1 if c < lower else 1) if trend == 1 else (1 if c > upper else -1)
        out.append(lower if trend == 1 else upper)
    return out


def _batch_sar(bars: list[HLCV], step: float, cap: float) -> list[float | None]:
    """TA-Lib's `TA_SAR` loop, transcribed over the whole list."""
    highs, lows = [b[0] for b in bars], [b[1] for b in bars]
    down, up = lows[0] - lows[1], highs[1] - highs[0]
    long = not (down > 0 and down > up)
    sar, ep, af = (lows[0], highs[1], step) if long else (highs[0], lows[1], step)
    out: list[float | None] = [None]
    for i in range(1, len(bars)):
        ph, pl = highs[max(i - 1, 1)], lows[max(i - 1, 1)]
        h, l = highs[i], lows[i]
        if long and l <= sar:
            long, sar = False, max(ep, ph, h)
            out.append(sar)
            af, ep = step, l
            sar = max(sar + af * (ep - sar), ph, h)
        elif not long and h >= sar:
            long, sar = True, min(ep, pl, l)
            out.append(sar)
            af, ep = step, h
            sar = min(sar + af * (ep - sar), pl, l)
        elif long:
            out.append(sar)
            if h > ep:
                ep, af = h, min(af + step, cap)
            sar = min(sar + af * (ep - sar), pl, l)
        else:
            out.append(sar)
            if l < ep:
                ep, af = l, min(af + step, cap)
            sar = max(sar + af * (ep - sar), ph, h)
    return out


def _batch_adx(bars: list[HLCV], period: int) -> list[float | None]:
    highs, lows = [b[0] for b in bars], [b[1] for b in bars]
    plus_dm, minus_dm = [0.0], [0.0]
    for i in range(1, len(bars)):
        up, dn = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus_dm.append(up if up > dn and up > 0 else 0.0)
        minus_dm.append(dn if dn > up and dn > 0 else 0.0)
    pos, neg, atr = (
        _wilder(plus_dm, period),
        _wilder(minus_dm, period),
        _wilder(_true_ranges(bars), period),
    )
    dx: list[float] = []
    for p, n, a in list(zip(pos, neg, atr, strict=True))[period - 1 :]:
        pdi, mdi = (100 * p / a, 100 * n / a) if a > 0 else (0.0, 0.0)
        dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi > 0 else 0.0)
    adx = _wilder(dx, period)
    return [None] * (2 * period - 2) + adx[period - 1 :]


def _batch_williams(bars: list[HLCV], period: int) -> list[float | None]:
    out: list[float | None] = [None] * (period - 1)
    for i in range(period - 1, len(bars)):
        window = bars[i - period + 1 : i + 1]
        hh, ll = max(b[0] for b in window), min(b[1] for b in window)
        out.append(-50.0 if hh == ll else -100 * (hh - bars[i][2]) / (hh - ll))
    return out


def _batch_mfi(bars: list[HLCV], period: int) -> list[float | None]:
    tps = [(h + l + c) / 3 for h, l, c, _ in bars]
    positive, negative = [0.0], [0.0]  # bar 0 has no previous typical price: no flow
    for i in range(1, len(bars)):
        flow = tps[i] * bars[i][3]
        positive.append(flow if tps[i] > tps[i - 1] else 0.0)
        negative.append(flow if tps[i] < tps[i - 1] else 0.0)
    out: list[float | None] = [None] * period
    for i in range(period, len(bars)):
        pos, neg = sum(positive[i - period + 1 : i + 1]), sum(negative[i - period + 1 : i + 1])
        out.append(50.0 if pos + neg == 0 else 100 * pos / (pos + neg))
    return out


def _batch_cmf(bars: list[HLCV], period: int) -> list[float | None]:
    mfv = [0.0 if h == l else ((c - l) - (h - c)) / (h - l) * v for h, l, c, v in bars]
    out: list[float | None] = [None] * (period - 1)
    for i in range(period - 1, len(bars)):
        volume = sum(b[3] for b in bars[i - period + 1 : i + 1])
        out.append(0.0 if volume == 0 else sum(mfv[i - period + 1 : i + 1]) / volume)
    return out


def _batch_ao(bars: list[HLCV], fast: int, slow: int) -> list[float | None]:
    hl2 = [(h + l) / 2 for h, l, _c, _v in bars]
    return [None] * (slow - 1) + [
        sum(hl2[i - fast + 1 : i + 1]) / fast - sum(hl2[i - slow + 1 : i + 1]) / slow
        for i in range(slow - 1, len(bars))
    ]


def _replay(
    indicator: Indicator, bars: list[HLCV], feed: Callable[[HLCV], tuple], read: str = "value"
) -> list[float | None]:
    out: list[float | None] = []
    for bar in bars:
        indicator.update_raw(*feed(bar))
        out.append(float(getattr(indicator, read)) if indicator.initialized else None)
    return out


def _hlc(bar: HLCV) -> tuple[float, float, float]:
    return bar[:3]


def _hl(bar: HLCV) -> tuple[float, float]:
    return bar[:2]


# --- Supertrend ---------------------------------------------------------------------------------

# period 2, multiplier 1. ATR (Wilder, alpha 1/2): TR0 = 10 - 8 = 2 -> ATR 2; TR1 = 11 - 9 = 2 ->
# ATR 2 (initialized). b1: hl2 10, bands 12 / 8, direction starts at -1 (down, TradingView's
#     `ta.supertrend` start), value 12.
# b2: TR 2 -> ATR 2; hl2 11, basic 13 / 9; upper stays 12 (13 > 12, prev close 10 <= 12); lower 9
#     (9 > 8); close 11.5 <= 12: -1, value 12.
# b3: TR max(11.5, 11) - min(7, 11.5) = 4.5 -> ATR 3.25; hl2 9, basic 12.25 / 5.75; upper stays 12;
#     lower stays 9 (5.75 < 9, prev close 11.5 >= 9); close 7.5 <= 12: -1, value 12.
# b4: TR 9 - 6 = 3 -> ATR 3.125; hl2 7.5, basic 10.625 / 4.375; upper 10.625 (< 12); lower 4.375
#     (prev close 7.5 < 9); close 8.5 <= 10.625: -1, value 10.625.
# b5: TR 12 - 8.5 = 3.5 -> ATR 3.3125; hl2 10.5, basic 13.8125 / 7.1875; upper stays 10.625; lower
#     7.1875 (> 4.375); close 11.8 > 10.625: +1, value 7.1875.
# b6: TR 11.8 - 6 = 5.8 -> ATR 4.55625; hl2 8, basic 12.55625 / 3.44375; upper 12.55625 (prev close
#     11.8 > 10.625); lower stays 7.1875; close 6.5 < 7.1875: -1, value 12.55625.
_SUPERTREND_BARS: list[HLCV] = [
    (10, 8, 9, 1),
    (11, 9, 10, 1),
    (12, 10, 11.5, 1),
    (11, 7, 7.5, 1),
    (9, 6, 8.5, 1),
    (12, 9, 11.8, 1),
    (10, 6, 6.5, 1),
]


def test_supertrend_hand_computed_fixture() -> None:
    indicator = Supertrend(period=2, multiplier=1.0)
    values = _replay(indicator, _SUPERTREND_BARS, _hlc)
    directions = _replay(Supertrend(2, 1.0), _SUPERTREND_BARS, _hlc, "direction")
    assert values == _approx([None, 12.0, 12.0, 12.0, 10.625, 7.1875, 12.55625])
    assert directions == [None, -1, -1, -1, -1, 1, -1]
    assert values == _approx(_batch_supertrend(_SUPERTREND_BARS, 2, 1.0))


def test_supertrend_matches_the_batch_reference_on_a_walk() -> None:
    bars = _walk(1, 600)
    got = _replay(Supertrend(10, 3.0), bars, _hlc)
    assert got == _approx(_batch_supertrend(bars, 10, 3.0))


# --- Parabolic SAR ------------------------------------------------------------------------------

# step 0.1, max 0.2. Seed at b1: down move 9 - 10 < 0 -> long, SAR = l0 = 9, EP = h1 = 11, AF 0.1.
# b1: 10 > 9, value 9; 11 is not above EP; next SAR min(9 + 0.1 * 2, 10, 10) = 9.2.
# b2: value 9.2; new high 12 -> EP 12, AF 0.2; next min(9.2 + 0.2 * 2.8, 10, 10.5) = 9.76.
# b3: value 9.76; new high 13 -> EP 13, AF stays 0.2 (cap); next min(9.76 + 0.2 * 3.24, 10.5, 11)
#     = 10.408.
# b4: low 10 <= 10.408 -> short; value max(EP 13, 13, 12.5) = 13; EP 10, AF 0.1; next
#     max(13 + 0.1 * -3, 13, 12.5) = 13.
# b5: high 11.5 < 13, value 13; new low 9 -> EP 9, AF 0.2; next max(13 - 0.8, 12.5, 11.5) = 12.5.
# b6: high 12.6 >= 12.5 -> long; value min(EP 9, 9, 10) = 9 (10.408, b3's next SAR, is never a
#     bar's value: b4 reversed through it).
_SAR_BARS: list[HLCV] = [
    (10, 9, 0, 0),
    (11, 10, 0, 0),
    (12, 10.5, 0, 0),
    (13, 11, 0, 0),
    (12.5, 10, 0, 0),
    (11.5, 9, 0, 0),
    (12.6, 10, 0, 0),
]


def test_parabolic_sar_hand_computed_fixture() -> None:
    sar = ParabolicSAR(step=0.1, max_step=0.2)
    values = _replay(sar, _SAR_BARS, _hl)
    assert values == _approx([None, 9.0, 9.2, 9.76, 13.0, 13.0, 9.0])
    assert values == _approx(_batch_sar(_SAR_BARS, 0.1, 0.2))
    assert sar.is_long


def test_parabolic_sar_matches_the_batch_reference_on_a_walk() -> None:
    bars = _walk(2, 600)
    assert _replay(ParabolicSAR(), bars, _hl) == _approx(_batch_sar(bars, 0.02, 0.2))


def test_parabolic_sar_seeds_short_when_the_second_bar_moves_down_more() -> None:
    sar = ParabolicSAR()
    sar.update_raw(10, 9)
    sar.update_raw(9.8, 8)  # down move 1 > up move -0.2: short, SAR = h0 = 10, EP 8
    assert (sar.is_long, sar.value) == (False, 10.0)


# --- ADX ----------------------------------------------------------------------------------------

# period 2. b0: +DM = -DM = 0 (Nautilus's first bar); TR 2. Averages seeded: pos 0, neg 0, ATR 2.
# b1: up 2, down -1 -> +DM 2: pos 1, neg 0; TR 3 -> ATR 2.5 (both initialized). +DI 40, -DI 0,
#     DX 100 -> the DX average starts at 100 (not yet initialized).
# b2: up 1, down -2 -> +DM 1: pos 1, neg 0; TR 2 -> ATR 2.25; +DI 44.44.., DX 100 -> ADX 100.
# b3: up -1, down 2 -> -DM 2: pos 0.5, neg 1; TR 3 -> ATR 2.625; +DI 19.047.., -DI 38.095..,
#     DX 100 * 0.5 / 1.5 = 33.33.. -> ADX (33.33.. + 100) / 2 = 66.66...
_ADX_BARS: list[HLCV] = [(10, 8, 9, 0), (12, 9, 11, 0), (13, 11, 12, 0), (12, 9, 10, 0)]


def test_adx_hand_computed_fixture() -> None:
    adx = AverageDirectionalIndex(period=2)
    values = _replay(adx, _ADX_BARS, _hlc, "adx")
    assert values == _approx([None, None, 100.0, 200.0 / 3])
    assert [adx.plus_di, adx.minus_di] == _approx([50 / 2.625, 100 / 2.625])
    assert values == _approx(_batch_adx(_ADX_BARS, 2))


def test_adx_matches_the_batch_reference_on_a_walk() -> None:
    bars = _walk(3, 600)
    assert _replay(AverageDirectionalIndex(14), bars, _hlc, "adx") == _approx(_batch_adx(bars, 14))


def test_adx_of_a_flat_market_is_zero_not_a_division_error() -> None:
    adx = AverageDirectionalIndex(period=2)
    for _ in range(4):
        adx.update_raw(10, 10, 10)
    assert (adx.adx, adx.plus_di, adx.minus_di) == (0.0, 0.0, 0.0)


# --- Williams %R --------------------------------------------------------------------------------

# period 3. b2: HH 12, LL 7, c 8 -> -100 * 4 / 5 = -80. b3: HH 13, LL 7, c 12 -> -100 / 6.
# b4: HH 13, LL 6, c 7 -> -100 * 6 / 7.
_WILLIAMS_BARS: list[HLCV] = [
    (10, 8, 9, 0),
    (12, 9, 11, 0),
    (11, 7, 8, 0),
    (13, 10, 12, 0),
    (9, 6, 7, 0),
]


def test_williams_percent_r_hand_computed_fixture() -> None:
    values = _replay(WilliamsPercentR(3), _WILLIAMS_BARS, _hlc)
    assert values == _approx([None, None, -80.0, -100 / 6, -600 / 7])
    assert values == _approx(_batch_williams(_WILLIAMS_BARS, 3))


def test_williams_percent_r_matches_the_batch_reference_on_a_walk() -> None:
    bars = _walk(4, 600)
    assert _replay(WilliamsPercentR(14), bars, _hlc) == _approx(_batch_williams(bars, 14))


def test_williams_percent_r_of_a_flat_window_is_minus_fifty() -> None:
    assert _replay(WilliamsPercentR(2), [(5, 5, 5, 0)] * 3, _hlc) == [None, -50.0, -50.0]


# --- Pivot points -------------------------------------------------------------------------------


_NO_R4 = {"r4": None, "s4": None}


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        # PP = 300 / 3 = 100; R1 = 200 - 90, S1 = 200 - 110, R2/S2 = 100 +- 20, R3 = 110 + 2 * 10,
        # S3 = 90 - 2 * 10.
        (
            "standard",
            {"pp": 100, "r1": 110, "s1": 90, "r2": 120, "s2": 80, "r3": 130, "s3": 70, **_NO_R4},
        ),
        # 100 +- 0.382 * 20, 0.618 * 20, 1.0 * 20.
        (
            "fibonacci",
            {
                "pp": 100,
                "r1": 107.64,
                "s1": 92.36,
                "r2": 112.36,
                "s2": 87.64,
                "r3": 120,
                "s3": 80,
                **_NO_R4,
            },
        ),
        # C +- 20 * 1.1 / 12, / 6, / 4, / 2.
        (
            "camarilla",
            {
                "pp": 100,
                "r1": 100 + 22 / 12,
                "s1": 100 - 22 / 12,
                "r2": 100 + 22 / 6,
                "s2": 100 - 22 / 6,
                "r3": 105.5,
                "s3": 94.5,
                "r4": 111,
                "s4": 89,
            },
        ),
    ],
)
def test_pivot_levels_of_the_spec_matrix(kind: str, expected: dict[str, float | None]) -> None:
    assert pivot_levels(kind, 110.0, 90.0, 100.0) == _approx_dict(expected)


def _approx_dict(expected: dict[str, float | None]) -> dict[str, Any]:
    return {k: None if v is None else pytest.approx(v, rel=1e-12) for k, v in expected.items()}


def test_pivot_points_take_the_completed_sessions_high_low_and_last_close() -> None:
    pivots = PivotPoints("standard")
    for high, low, close in [(105, 95, 101), (110, 92, 99), (104, 90, 100)]:
        pivots.update_raw(high, low, close, session=0)
    assert not pivots.initialized
    assert pivots.levels() == dict.fromkeys(PIVOT_LEVELS)
    pivots.update_raw(101, 99, 100, session=1)  # session 0 completes: H 110, L 90, C 100
    assert pivots.initialized
    assert pivots.levels() == pivot_levels("standard", 110, 90, 100)
    assert (pivots.r4, pivots.s4) == (None, None)
    pivots.update_raw(130, 60, 70, session=1)  # inside session 1: the levels do not move
    assert pivots.pp == 100.0


def test_pivot_points_match_a_batch_grouping_on_a_walk() -> None:
    bars = _walk(5, 400)
    sessions = [i // 24 for i in range(len(bars))]
    pivots = PivotPoints("camarilla")
    grouped: dict[int, list[HLCV]] = defaultdict(list)
    for bar, session in zip(bars, sessions, strict=True):
        pivots.update_raw(bar[0], bar[1], bar[2], session)
        if session > 0:
            prev = grouped[session - 1]
            hlc = (max(b[0] for b in prev), min(b[1] for b in prev), prev[-1][2])
            assert pivots.levels() == pivot_levels("camarilla", *hlc)
        grouped[session].append(bar)


def test_pivot_points_refuse_an_inverted_bar() -> None:
    with pytest.raises(ValueError, match="needs l <= h"):
        PivotPoints().update_raw(9, 10, 9.5, 0)


def test_pivot_points_refuse_a_session_key_going_back() -> None:
    """A lower key would complete the running session out of order: refused, state untouched."""
    pivots = PivotPoints()
    pivots.update_raw(110, 90, 100, 5)
    pivots.update_raw(105, 95, 101, 6)
    levels = pivots.levels()
    with pytest.raises(ValueError, match="before the running session 6"):
        pivots.update_raw(120, 80, 99, 5)
    assert (pivots.session, pivots.levels()) == (6, levels)


# --- Money Flow Index ---------------------------------------------------------------------------

# period 2. b0 seeds tp 9. b1: tp 11 > 9 -> +22 (11 * 2): pos [22], 100 (not initialized).
# b2: tp 9 < 11 -> -27 (9 * 3): pos 22, neg 27 -> 100 * 22 / 49. b3: tp 9 unchanged -> neither:
# pos 0, neg 27 -> 0. b4: unchanged again: both sums 0 -> 50.
_MFI_BARS: list[HLCV] = [
    (10, 8, 9, 1),
    (12, 9, 12, 2),
    (11, 8, 8, 3),
    (9, 9, 9, 4),
    (9, 9, 9, 1),
]


def test_money_flow_index_hand_computed_fixture() -> None:
    values = _replay(MoneyFlowIndex(2), _MFI_BARS, lambda b: b)
    assert values == _approx([None, None, 2200 / 49, 0.0, 50.0])
    assert values == _approx(_batch_mfi(_MFI_BARS, 2))


def test_money_flow_index_matches_the_batch_reference_on_a_walk() -> None:
    bars = _walk(6, 600)
    assert _replay(MoneyFlowIndex(14), bars, lambda b: b) == _approx(_batch_mfi(bars, 14))


# --- Chaikin Money Flow -------------------------------------------------------------------------

# period 2. b0: CLV ((9.5 - 8) - (10 - 9.5)) / 2 = 0.5 -> 1. b1: CLV (0 - 2) / 2 = -1 -> -2:
# (1 - 2) / 4 = -0.25. b2: h == l -> CLV 0: (-2 + 0) / 6. b3, b4: no volume -> 0.
_CMF_BARS: list[HLCV] = [
    (10, 8, 9.5, 2),
    (12, 10, 10, 2),
    (11, 11, 11, 4),
    (11, 10, 10.5, 0),
    (12, 10, 11, 0),
]


def test_chaikin_money_flow_hand_computed_fixture() -> None:
    values = _replay(ChaikinMoneyFlow(2), _CMF_BARS, lambda b: b)
    assert values == _approx([None, -0.25, -1 / 3, 0.0, 0.0])
    assert values == _approx(_batch_cmf(_CMF_BARS, 2))


def test_rolling_sum_does_not_drift_over_a_long_history_of_large_values() -> None:
    """
    200 000 pushes up to 1e12, then a window of thirteen zeros and 1.0: the sum is 1.0, never the
    residue of adding and removing 1e12-sized values (a plain running sum read 1.0002).
    """
    rng = random.Random(13)  # noqa: S311 -- a reproducible stream, not a secret
    window = _RollingSum(14)
    for _ in range(200_000):
        window.push(rng.uniform(0.0, 1e12))
    for value in [0.0] * 13 + [1.0]:
        window.push(value)
    assert window.total == pytest.approx(1.0, abs=1e-9)


def test_chaikin_money_flow_after_huge_volumes_matches_a_fresh_batch() -> None:
    """100 000 bars of ~1e12 volume, then 20 small ones: the value is the 20 bars' own, exactly."""
    rng = random.Random(14)  # noqa: S311 -- a reproducible stream, not a secret
    huge = [
        (101.0, 99.0, rng.uniform(99.0, 101.0), rng.uniform(1e11, 1e12)) for _ in range(100_000)
    ]
    small = _walk(15, 20)
    indicator = ChaikinMoneyFlow(20)
    for bar in huge + small:
        indicator.update_raw(*bar)
    assert indicator.value == pytest.approx(_batch_cmf(small, 20)[-1], rel=1e-12, abs=1e-12)


def test_chaikin_money_flow_matches_the_batch_reference_on_a_walk() -> None:
    bars = _walk(7, 600)
    assert _replay(ChaikinMoneyFlow(20), bars, lambda b: b) == _approx(_batch_cmf(bars, 20))


# --- Awesome Oscillator -------------------------------------------------------------------------

# fast 2, slow 3 over hl2 9, 11, 13, 10. b2: (11 + 13) / 2 - (9 + 11 + 13) / 3 = 12 - 11 = 1.
# b3: (13 + 10) / 2 - (11 + 13 + 10) / 3 = 11.5 - 34 / 3.
_AO_BARS: list[HLCV] = [(10, 8, 0, 0), (12, 10, 0, 0), (14, 12, 0, 0), (11, 9, 0, 0)]


def test_awesome_oscillator_hand_computed_fixture() -> None:
    values = _replay(AwesomeOscillator(2, 3), _AO_BARS, _hl)
    assert values == _approx([None, None, 1.0, 11.5 - 34 / 3])
    assert values == _approx(_batch_ao(_AO_BARS, 2, 3))


def test_awesome_oscillator_matches_the_batch_reference_on_a_walk() -> None:
    bars = _walk(8, 600)
    assert _replay(AwesomeOscillator(), bars, _hl) == _approx(_batch_ao(bars, 5, 34))


# --- ZigZag -------------------------------------------------------------------------------------


def _earliest(indices: range, key: Callable[[int], float]) -> int:
    """Return the earliest index of the largest `key` (only a strict new extreme moves one)."""
    return max(indices, key=lambda k: (key(k), -k))


def _batch_zigzag(bars: list[HLCV], pct: float) -> list[tuple[int, float]]:
    """
    Every confirmed pivot `(bar, price)`, recomputed from the definition with each leg's extreme
    taken over its whole slice: a bar that does not extend the leg's extreme and reverses `pct` %
    from it confirms it; before the first pivot both extremes since bar 0 are candidates, the top
    first.
    """
    d = pct / 100
    highs, lows = [b[0] for b in bars], [b[1] for b in bars]
    pivots: list[tuple[int, float]] = []
    direction, start = 0, 0
    for i in range(1, len(bars)):
        top = _earliest(range(start, i), lambda k: highs[k])
        bottom = _earliest(range(start, i), lambda k: -lows[k])
        tops = direction == 1 or (direction == 0 and highs[i] <= highs[top])
        bottoms = direction == -1 or (direction == 0 and lows[i] >= lows[bottom])
        if tops and highs[i] <= highs[top] and lows[i] <= highs[top] * (1 - d):
            pivots.append((top, highs[top]))
            direction, start = -1, i
        elif bottoms and lows[i] >= lows[bottom] and highs[i] >= lows[bottom] * (1 + d):
            pivots.append((bottom, lows[bottom]))
            direction, start = 1, i
    return pivots


def _zigzag_pivots(bars: list[HLCV], pct: float) -> list[tuple[int, float]]:
    zigzag = ZigZag(pct)
    found: list[tuple[int, float]] = []
    for high, low, _c, _v in bars:
        zigzag.update_raw(high, low)
        if zigzag.confirmed:
            assert zigzag.pivot_bar is not None
            assert zigzag.pivot_price is not None
            found.append((zigzag.pivot_bar, zigzag.pivot_price))
    return found


# 5 %. b0: highest 100, lowest 99. b1: high 105 >= 99 * 1.05 = 103.95 and b1 did not set the low:
# a bottom at b0 (99); an up leg starts at 105. b2: 110 extends it. b3: 109 does not extend, low
# 104.4 <= 110 * 0.95 = 104.5: the top at b2 (110) is confirmed, a down leg starts at 104.4.
_ZIGZAG_BARS: list[HLCV] = [
    (100, 99, 0, 0),
    (105, 104, 0, 0),
    (110, 109, 0, 0),
    (109, 104.4, 0, 0),
]


def test_zigzag_confirms_the_top_of_the_spec_matrix() -> None:
    zigzag = ZigZag(5.0)
    for high, low, _c, _v in _ZIGZAG_BARS:
        zigzag.update_raw(high, low)
    assert (zigzag.confirmed, zigzag.pivot_bar, zigzag.pivot_price) == (True, 2, 110)
    assert (zigzag.extreme_bar, zigzag.extreme_price, zigzag.direction) == (3, 104.4, -1)
    assert _zigzag_pivots(_ZIGZAG_BARS, 5.0) == [(0, 99), (2, 110)]
    assert _zigzag_pivots(_ZIGZAG_BARS, 5.0) == _batch_zigzag(_ZIGZAG_BARS, 5.0)


def test_zigzag_last_leg_repaints_until_a_reversal_confirms_it() -> None:
    zigzag = ZigZag(5.0)
    for high, low, _c, _v in _ZIGZAG_BARS:
        zigzag.update_raw(high, low)
    zigzag.update_raw(104, 100)  # extends the down leg: its end moves, nothing is confirmed
    assert (zigzag.confirmed, zigzag.extreme_bar, zigzag.extreme_price) == (False, 4, 100)
    zigzag.update_raw(104.9, 101)  # 104.9 < 100 * 1.05: no reversal yet
    assert not zigzag.confirmed
    zigzag.update_raw(105, 103)  # 105 >= 105: the bottom at b4 is confirmed
    assert (zigzag.confirmed, zigzag.pivot_bar, zigzag.pivot_price) == (True, 4, 100)


def test_zigzag_checks_extension_before_reversal() -> None:
    zigzag = ZigZag(5.0)
    for high, low, _c, _v in _ZIGZAG_BARS[:3]:
        zigzag.update_raw(high, low)
    zigzag.update_raw(120, 100)  # a new high *and* a 5 % drop from 110: an extension only
    assert (zigzag.confirmed, zigzag.extreme_price, zigzag.direction) == (False, 120, 1)


def test_zigzag_matches_the_batch_reference_on_a_walk() -> None:
    bars = _walk(9, 1500)
    assert _zigzag_pivots(bars, 2.0) == _batch_zigzag(bars, 2.0)
    assert len(_zigzag_pivots(bars, 2.0)) > 10


# --- every class: params, reset, constant state, a real Bar -------------------------------------

_FEEDS: dict[type, Callable[[HLCV], tuple]] = {
    Supertrend: _hlc,
    ParabolicSAR: _hl,
    AverageDirectionalIndex: _hlc,
    WilliamsPercentR: _hlc,
    MoneyFlowIndex: lambda b: b,
    ChaikinMoneyFlow: lambda b: b,
    AwesomeOscillator: _hl,
    ZigZag: _hl,
}
_OUTPUT = {AverageDirectionalIndex: "adx", ZigZag: "extreme_price"}


@pytest.mark.parametrize(
    ("cls", "kwargs"),
    [
        (Supertrend, {"period": 0}),
        (Supertrend, {"multiplier": 0.0}),
        (ParabolicSAR, {"step": 0.0}),
        (ParabolicSAR, {"step": 0.3, "max_step": 0.2}),
        (ParabolicSAR, {"step": 0.8, "max_step": 1.5}),
        (AverageDirectionalIndex, {"period": 0}),
        (WilliamsPercentR, {"period": -1}),
        (PivotPoints, {"kind": "woodie"}),
        (MoneyFlowIndex, {"period": 0}),
        (ChaikinMoneyFlow, {"period": 0}),
        (AwesomeOscillator, {"fast": 0}),
        (AwesomeOscillator, {"fast": 34, "slow": 34}),
        (ZigZag, {"deviation_pct": 0.0}),
        (ZigZag, {"deviation_pct": 100.0}),
        (ZigZag, {"deviation_pct": -1.0}),
    ],
)
def test_each_constructor_refuses_a_bad_param(cls: type, kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        cls(**kwargs)


@pytest.mark.parametrize("cls", list(_FEEDS))
def test_a_non_finite_input_raises(cls: type) -> None:
    bar = (float("nan"), 1.0, 1.0, 1.0)
    with pytest.raises(ValueError, match="finite"):
        cls().update_raw(*_FEEDS[cls](bar))


@pytest.mark.parametrize("cls", [*_FEEDS, PivotPoints])
def test_an_inverted_bar_raises_before_any_state_changes(cls: type) -> None:
    feed = _FEEDS.get(cls, lambda b: (*b[:3], 0))
    indicator = cls()
    with pytest.raises(ValueError, match="needs l <= h"):
        indicator.update_raw(*feed((9.0, 10.0, 9.5, 1.0)))
    assert not indicator.has_inputs


@pytest.mark.parametrize("low", [0.0, -1.0])
def test_zigzag_refuses_a_non_positive_price_before_any_state_changes(low: float) -> None:
    zigzag = ZigZag(5.0)
    with pytest.raises(ValueError, match="positive prices"):
        zigzag.update_raw(10.0, low)
    assert not zigzag.has_inputs


@pytest.mark.parametrize("cls", [MoneyFlowIndex, ChaikinMoneyFlow])
def test_a_negative_volume_raises(cls: type) -> None:
    indicator = cls()
    with pytest.raises(ValueError, match="volume >= 0"):
        indicator.update_raw(10.0, 9.0, 9.5, -1.0)
    assert not indicator.has_inputs


@pytest.mark.parametrize("cls", list(_FEEDS))
def test_reset_forgets_the_stream(cls: type) -> None:
    bars = _walk(10, 120)
    indicator = cls()
    first = _replay(indicator, bars, _FEEDS[cls], _OUTPUT.get(cls, "value"))
    indicator.reset()
    assert not indicator.initialized
    assert not indicator.has_inputs
    assert _replay(indicator, bars, _FEEDS[cls], _OUTPUT.get(cls, "value")) == first


def test_pivot_points_reset_forgets_the_session() -> None:
    pivots = PivotPoints("fibonacci")
    pivots.update_raw(110, 90, 100, 0)
    pivots.update_raw(100, 100, 100, 1)
    pivots.reset()
    assert not pivots.initialized
    assert pivots.levels() == dict.fromkeys(PIVOT_LEVELS)
    pivots.update_raw(100, 100, 100, 1)
    assert not pivots.initialized


def _bounded_containers(indicator: Indicator) -> list[int]:
    """Return the size of every internal container, read through the helpers' own state."""
    sizes = []
    for value in vars(indicator).values():
        inner = getattr(value, "_entries", None) or getattr(value, "_values", None)
        if inner is not None:
            sizes.append(len(inner))
    return sizes


@pytest.mark.parametrize("cls", [*_FEEDS, PivotPoints])
def test_state_stays_constant_over_ten_thousand_bars(cls: type) -> None:
    indicator = cls()
    bars = _walk(11, 10_000)
    feed = _FEEDS.get(cls, lambda b: (*b[:3], 0))
    indicator.update_raw(*feed(bars[0]))
    attributes = set(vars(indicator))
    for i, bar in enumerate(bars):
        indicator.update_raw(*(feed(bar) if cls is not PivotPoints else (*bar[:3], i // 60)))
        assert set(vars(indicator)) == attributes
    assert all(size <= 34 for size in _bounded_containers(indicator))
    assert indicator.initialized


def _nautilus_bar(high: float, low: float, close: float, volume: float) -> Bar:
    bar_type = BarType.from_str("BTCUSDT-LINEAR.BYBIT-1-MINUTE-LAST-EXTERNAL")
    return Bar(
        bar_type,
        Price(close, 2),
        Price(high, 2),
        Price(low, 2),
        Price(close, 2),
        Quantity(volume, 3),
        0,
        0,
    )


@pytest.mark.parametrize("cls", list(_FEEDS))
def test_handle_bar_on_a_real_bar_equals_update_raw(cls: type) -> None:
    bars = [(round(h, 2), round(l, 2), round(c, 2), round(v, 3)) for h, l, c, v in _walk(12, 80)]
    by_bar, by_raw = cls(), cls()
    output = _OUTPUT.get(cls, "value")
    for high, low, close, volume in bars:
        by_bar.handle_bar(_nautilus_bar(high, low, close, volume))
        by_raw.update_raw(*_FEEDS[cls]((high, low, close, volume)))
        assert getattr(by_bar, output) == getattr(by_raw, output)
    assert by_bar.initialized
