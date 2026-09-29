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
The stateful and series-level derived values against the independent reference (Story 31.3,
`docs/DATA_DICTIONARY.md` §2.13): the ranking board's live fields, its three volatilities and its
pct changes (`ranking`), the research series functions (`research`) and the picker's candle ->
indicator feed (`views.indicator_picker`). Same rules as `test_reference_signals.py`: seeded
generators, the real soak rows, golden cases, a planted defect per comparator, and no DIFFERENT.

A declared composition root (`tests/test_boundaries.py`): it imports what it compares.
"""

import math
import random
import statistics
from decimal import Decimal
from decimal import localcontext
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from candles.domain.fold import fold_rows
from kernel import indicators as kernel
from kernel.indicators import OFI_GAP_NS
from kernel.second_snapshot import DydxSecondSnapshot
from ranking.domain.board import ROLLING_WINDOW
from ranking.domain.board import VOLUME_DELTA_WINDOW
from ranking.domain.board import RankingBoard
from ranking.domain.board import RankingsPublisher
from ranking.domain.metrics import PCT_MAX_SHORTFALL_NS
from ranking.domain.metrics import VOLATILITY_WINDOW_NS
from ranking.domain.metrics import pct_change_from
from ranking.domain.metrics import price_stats_from_series
from ranking.domain.values import RankingMode
from ranking.domain.volatility import VolatilityTracker
from ranking.infrastructure import metrics_store
from ranking.infrastructure.metrics_store import SqliteMetricsStore
from research.application.aligned import funding_per_hour
from research.application.aligned import oi_changes
from research.application.microstructure import microprice_edge
from research.application.microstructure import spread_frame
from research.application.microstructure import trade_flow
from research.domain.correlation import basis_bps
from research.domain.correlation import correlation_of
from research.domain.correlation import lead_lag
from research.domain.correlation import rolling_correlation
from research.domain.returns import ReturnSeries
from views.indicator_picker import replay_native

from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.indicators import SimpleMovingAverage
from verification.domain import reference_signals as ref
from verification.domain.reference_signals import RefBook
from verification.domain.signal_compare import Agreement
from verification.domain.signal_compare import Tally
from verification.domain.signal_compare import at_places
from verification.domain.signal_compare import equal
from verification.domain.signal_compare import relative
from verification.domain.signal_compare import within_ulps
from verification.tests.signal_cases import FIXTURE_IDS
from verification.tests.signal_cases import NS_PER_S
from verification.tests.signal_cases import BookCase
from verification.tests.signal_cases import book_series
from verification.tests.signal_cases import both
from verification.tests.signal_cases import carried
from verification.tests.signal_cases import float_series
from verification.tests.signal_cases import load_fixture
from verification.tests.signal_cases import pinned_zero
from verification.tests.signal_cases import price_path
from verification.tests.signal_cases import seeded
from verification.tests.signal_cases import stored_row


SEEDS = range(8)
HOUR_NS = 3_600 * NS_PER_S
LOOKBACK_S = 3_600


def _assert_agrees(tally: Tally) -> None:
    print("\n" + "\n".join(tally.report()))
    assert tally.signals(), "nothing was compared"
    assert tally.failures() == {}


def _book_inputs(rows: int = 360) -> list[list[dict[str, Any]]]:
    generated = [[stored_row(c) for c in book_series(seeded(seed), rows)] for seed in SEEDS]
    return generated + [load_fixture(iid) for iid in FIXTURE_IDS]


def _dec(value: float | None) -> Decimal | None:
    """Return the exact decimal of a float input (research takes floats; the reference reads them exactly)."""
    return None if value is None or math.isnan(value) else Decimal(value)


# --- §3.2/§3.3 the ranking board's live fields ----------------------------------------------


def _board() -> RankingBoard:
    board = RankingBoard(RankingsPublisher(5, lambda: 0.0), LOOKBACK_S, 10**18)
    board.switch_mode(RankingMode.VOLATILITY)  # every fresh row is ranked, volume or not
    return board


def _is_fed(book: RefBook) -> bool:
    """§3.2: the board feeds its trackers only from a two-sided book with a positive mid."""
    centre = ref.mid(book)
    return centre is not None and centre > 0


def _mid(book: RefBook) -> Decimal:
    centre = ref.mid(book)
    assert centre is not None, "a fed book is two-sided"
    return centre


def _expected_live(fed: list[RefBook]) -> dict[str, Any]:
    """Return the rank entry's live fields over what the board was fed (none for an empty window)."""
    if not fed:
        empty: dict[str, Any] = dict.fromkeys(_LIVE_NONE_WHEN_EMPTY)
        return {**empty, "buy_count": 0, "sell_count": 0}
    window, latest = fed[-ROLLING_WINDOW:], fed[-1]
    micro, centre = ref.microprice(latest), _mid(latest)
    return {
        "cvd": ref.cvd(window),
        "volume_delta": ref.cvd(fed[-VOLUME_DELTA_WINDOW:]),
        "avg_trade_size": ref.avg_trade_size(window),
        "volatility_fast": ref.vol_fast([_mid(b) for b in window], ROLLING_WINDOW),
        "volatility_score": ref.vol_score_1h(
            [(b.ts_event, _mid(b)) for b in fed], LOOKBACK_S * NS_PER_S
        ),
        "price": centre,
        "microprice": micro,
        "microprice_lean": None if micro is None else micro - centre,
        "spread": ref.spread(latest),
        "buy_count": sum(b.buy_count for b in window),
        "sell_count": sum(b.sell_count for b in window),
    }


_LIVE_NONE_WHEN_EMPTY = (
    "cvd",
    "volume_delta",
    "avg_trade_size",
    "volatility_fast",
    "volatility_score",
    "price",
    "microprice",
    "microprice_lean",
    "spread",
)
_LIVE_AT_SIZE_PLACES = ("cvd", "volume_delta")
_LIVE_RELATIVE = ("avg_trade_size", "volatility_fast", "volatility_score", "microprice")
_LIVE_EQUAL = ("buy_count", "sell_count")


def _compare_live(tally: Tally, row: dict, want: dict[str, Any], book: RefBook) -> None:
    p, s = book.price_precision, book.size_precision
    for name in _LIVE_AT_SIZE_PLACES:
        tally.record(f"board.{name}", at_places(row[name], want[name], s))
    for name in _LIVE_RELATIVE:
        tally.record(f"board.{name}", relative(row[name], want[name]))
    for name in _LIVE_EQUAL:
        tally.record(f"board.{name}", equal(row[name], want[name]))
    tally.record("board.price", at_places(row["price"], want["price"], p + 1))
    tally.record("board.spread", at_places(row["spread"], want["spread"], p))
    magnitude = 0.0 if want["price"] is None else float(want["price"])
    lean = within_ulps(row["microprice_lean"], want["microprice_lean"], magnitude)
    tally.record("board.microprice_lean", lean)


def _compare_book_trackers(tally: Tally, rows: list[dict], fed: list[RefBook]) -> None:
    """Compare the board's OFI 3/5/10 (window 300), OFI10z and carried OBI with the reference."""
    s = fed[0].size_precision
    for n in (3, 5, 10):
        ofi = ref.rolling_ofi(fed, n, 300, False, OFI_GAP_NS)
        obi = carried([ref.obi(b, n) for b in fed])
        for row, o, b in zip(rows, ofi, obi, strict=True):
            tally.record(f"board.ofi_{n}", at_places(row[f"ofi_{n}"], o, s))
            tally.record(f"board.obi_{n}", relative(row[f"obi_{n}"], b))
    z = ref.rolling_ofi_z(fed, 10, 50, False, OFI_GAP_NS, 3600)
    for row, expected in zip(rows, z, strict=True):
        tally.record("board.ofi_10_z", pinned_zero(row["ofi_10_z"], expected))


def _one_sided(row: dict[str, Any], ts_event: int) -> dict[str, Any]:
    """Return a copy of a stored row with an empty ask side at `ts_event`: it feeds the board nothing."""
    return {**row, "ask_prices": [], "ask_sizes": [], "ts_event": ts_event, "ts_init": ts_event}


def test_the_ranking_boards_live_fields_match_the_reference() -> None:
    """
    Every published row, the unfed and one-sided ones included: each input starts with a one-sided
    row, so the first rank entry has an empty window (`cvd`, `price` None, counts 0).
    """
    tally = Tally()
    for rows in _book_inputs():
        prods, books = both([_one_sided(rows[0], rows[0]["ts_event"] - NS_PER_S), *rows])
        board = _board()
        fed: list[RefBook] = []
        published_fed: list[dict] = []
        for prod, book in zip(prods, books, strict=True):
            board.ingest(prod, received_ns=prod.ts_event)
            if _is_fed(book):
                fed.append(book)
            row = board.current_ranks(prod.ts_event)[0]
            _compare_live(tally, row, _expected_live(fed), fed[-1] if fed else book)
            if _is_fed(book):
                published_fed.append(row)
        _compare_book_trackers(tally, published_fed, fed)
    _assert_agrees(tally)
    assert tally.count("board.cvd", Agreement.BOTH_UNDEFINED) >= len(_book_inputs())
    assert tally.count("board.price", Agreement.BOTH_UNDEFINED) >= len(_book_inputs())


# --- §3.2 VolatilityTracker, §3.3 price stats ------------------------------------------------


def _price_points(seed: int, points: int = 700) -> list[tuple[int, Decimal]]:
    """Return a trade-close series spanning ~26 h with irregular spacing and some long gaps."""
    rng = seeded(seed)
    precision, units = rng.randint(0, 6), rng.randint(10**4, 10**6)
    ts, out = 10**18, []
    for _ in range(points):
        ts += rng.choice((1, 5, 30, 60, 120, 120, 200, 400, 900, 1800)) * NS_PER_S
        units = max(units + rng.randint(-300, 300), 1_000)
        out.append((ts, Decimal(units).scaleb(-precision)))
    return out


def _as_floats(points: list[tuple[int, Decimal]]) -> list[tuple[int, float]]:
    return [(ts, float(p)) for ts, p in points]


def _stats_tally(base_at_latest: bool = False) -> Tally:
    tally = Tally()
    for seed in range(40):
        points = _price_points(seed)
        for end in range(2, len(points) + 1, 37):
            series = points[:end]
            stats = price_stats_from_series(_as_floats(series))
            for hours, name in ((1, "pct_change_1h"), (24, "pct_change_24h")):
                expected = ref.pct_change(series, hours * HOUR_NS, PCT_MAX_SHORTFALL_NS)
                if base_at_latest and expected is not None:
                    expected = ref.pct_change_from(series[-1][1], series[-1][1])
                tally.record(name, relative(stats[name], expected))
            expected_vol = ref.vol_catalog_24h(series, VOLATILITY_WINDOW_NS)
            tally.record("volatility", relative(stats["volatility"], expected_vol))
    return tally


def test_price_stats_match_the_reference_pct_bound_and_24h_volatility() -> None:
    tally = _stats_tally()
    _assert_agrees(tally)
    assert tally.count("pct_change_1h", Agreement.BOTH_UNDEFINED) > 0  # short and gapped bases


def test_planted_pct_taking_the_latest_point_as_its_base_is_caught() -> None:
    tally = _stats_tally(base_at_latest=True)
    assert tally.count("pct_change_24h", Agreement.DIFFERENT) > 0


def _vol_score_tally(swapped_ddof: bool) -> Tally:
    tally = Tally()
    for seed in range(20):
        tracker = VolatilityTracker(lookback_seconds=LOOKBACK_S)
        points = _price_points(seed, 300)
        for i, (ts, price) in enumerate(points):
            tracker.update("X", ts, float(price))
            window = [(t, p) for t, p in points[: i + 1]]
            expected = ref.vol_score_1h(window, LOOKBACK_S * NS_PER_S)
            if swapped_ddof and expected is not None:
                cutoff = ts - LOOKBACK_S * NS_PER_S
                kept = [p for t, p in window if t >= cutoff]
                expected = _pstdev(ref.pct_returns(kept))
            tally.record("volatility_score", relative(tracker.score("X"), expected))
    return tally


def _pstdev(returns: list[Decimal]) -> Decimal:
    return statistics.pstdev(returns)


def test_the_volatility_tracker_matches_the_reference_sample_stdev() -> None:
    _assert_agrees(_vol_score_tally(swapped_ddof=False))


def test_planted_reference_with_ddof_swapped_is_caught() -> None:
    assert _vol_score_tally(swapped_ddof=True).count("volatility_score", Agreement.DIFFERENT) > 0


_TOLERANCE_NS = 3_600 * NS_PER_S
# Offsets from the target (ns): the exact edges of `(target - tolerance, target]` and one ns either
# side of each, so `>` vs `>=` at either end, or a moved tolerance, changes the answer.
_EDGE_OFFSETS = (0, 1, -1, -_TOLERANCE_NS, -_TOLERANCE_NS + 1, -_TOLERANCE_NS - 1)


def _offset(rng: random.Random) -> int:
    if rng.random() < 0.5:
        return rng.choice(_EDGE_OFFSETS)
    return rng.choice((-1, 1)) * rng.randint(1, 5_000) * NS_PER_S


def _seed_week_ago(store: SqliteMetricsStore, target: int) -> dict[str, list[tuple[int, Decimal]]]:
    """Write 0-4 distinct-stamp prices per instrument around `target`; return them per instrument."""
    rng = seeded(31)
    series: dict[str, list[tuple[int, Decimal]]] = {}
    for k in range(60):
        offsets = sorted({_offset(rng) for _ in range(rng.randint(0, 4))})
        iid = f"I{k}-USD-PERP.HYPERLIQUID"
        series[iid] = [(target + o, Decimal(rng.randint(1, 10**6)).scaleb(-2)) for o in offsets]
        store.write([{"ts": ts, "instrument_id": iid, "price": float(p)} for ts, p in series[iid]])
    return series


def test_the_1w_base_is_the_stored_price_at_or_before_the_target_within_the_tolerance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    `price_near_days_ago` (a real tmp store) and `pct_change_from` against §3.3's as-of rule, with
    the store's clock pinned so rows sit exactly on and one ns off both edges of the window.
    """
    now = 1_900_000_000 * NS_PER_S
    monkeypatch.setattr(metrics_store.time, "time_ns", lambda: now)
    tally = Tally()
    store = SqliteMetricsStore(str(tmp_path / "metrics.db"))
    target = now - 7 * 86_400 * NS_PER_S
    series = _seed_week_ago(store, target)
    found = store.price_near_days_ago(7)
    store.close()
    current = Decimal(12345).scaleb(-2)
    for iid, points in series.items():
        expected = ref.price_as_of(points, target, _TOLERANCE_NS)
        tally.record("price_near_days_ago", relative(found.get(iid), expected))
        got_pct = pct_change_from(float(current), found.get(iid))
        tally.record("pct_1w", relative(got_pct, ref.pct_change_from(current, expected)))
    _assert_agrees(tally)
    assert tally.count("price_near_days_ago", Agreement.EXACT) > 0


# --- §2.12 research series ---------------------------------------------------------------------


def _grid(seed: int, points: int, period_s: int) -> tuple[list[float | None], list[int]]:
    """Prices on a period grid with missing buckets and stamps inside each bucket."""
    rng = seeded(seed)
    prices = price_path(rng, points)
    ts, stamps = 10**18 - 10**18 % (86_400 * NS_PER_S), []
    for _ in prices:
        ts += rng.choice((1, 1, 1, 2, 5)) * period_s * NS_PER_S
        stamps.append(ts + rng.randrange(period_s) * NS_PER_S)
    return prices, stamps


def _compare_returns(
    tally: Tally, series: ReturnSeries, expected: list[ref.Return], name: str
) -> None:
    tally.record(f"{name}.length", equal(len(series), len(expected)))
    values = series.values.tolist()  # noqa: PD011 -- a numpy array on ReturnSeries, not pandas
    pairs = zip(series.ts_ns.tolist(), values, expected, strict=False)
    for got_ts, got, (want_ts, want) in pairs:
        tally.record(f"{name}.ts", equal(got_ts, want_ts))
        tally.record(name, relative(got, want))


def test_return_series_and_resample_match_the_reference() -> None:
    tally = Tally()
    for seed in SEEDS:
        for period_s, coarse_s in ((60, 300), (300, 3600), (3600, 86_400)):
            prices, stamps = _grid(seed, 400, period_s)
            series = ReturnSeries.from_prices(prices, stamps, period_s)
            expected = ref.simple_returns([_dec(p) for p in prices], stamps, period_s * NS_PER_S)
            _compare_returns(tally, series, expected, "returns")
            coarse = ref.resample(expected, period_s * NS_PER_S, coarse_s * NS_PER_S)
            _compare_returns(tally, series.resample(coarse_s), coarse, "resample")
    _assert_agrees(tally)


def test_correlation_rolling_correlation_and_lead_lag_match_the_reference() -> None:
    tally = Tally()
    for seed in SEEDS:
        rng = seeded(seed)
        columns = [float_series(rng, 240) for _ in range(4)]
        columns.append([math.nan if math.isnan(v) else 2.0 * v + 1e-4 for v in columns[0]])
        columns.append([0.25] * 240)  # exactly constant: undefined
        matrix = correlation_of([f"c{i}" for i in range(6)], np.array(columns).T)
        rho = matrix.values  # noqa: PD011 -- a numpy array on CorrelationMatrix, not pandas
        for i in range(6):
            for j in range(6):
                tally.record("pearson", relative(rho[i, j], ref.pearson(columns[i], columns[j])))
        a, b = columns[0], columns[1]
        for window in (5, 30):
            got = rolling_correlation(np.array(a), np.array(b), window)
            want = ref.rolling_pearson(a, b, window, max(2, window // 2))
            for g, w in zip(got.tolist(), want, strict=True):
                tally.record("rolling_pearson", relative(g, w))
        for (lag, g), (want_lag, w) in zip(
            lead_lag(np.array(a), np.array(columns[4]), 5, 3),
            ref.lead_lag(a, columns[4], 5, 3),
            strict=True,
        ):
            tally.record("lead_lag.lag", equal(lag, want_lag))
            tally.record("lead_lag", relative(g, w))
    _assert_agrees(tally)
    assert tally.count("pearson", Agreement.BOTH_UNDEFINED) > 0  # the constant column


def test_basis_funding_per_hour_spread_frame_and_trade_flow_match_the_reference() -> None:
    tally = Tally()
    rng = seeded(7)
    a, b = price_path(rng, 300), price_path(rng, 300)
    got = basis_bps(
        np.array([math.nan if v is None else v for v in a]),
        np.array([math.nan if v is None else v for v in b]),
    )
    for g, x, y in zip(got.tolist(), a, b, strict=True):
        tally.record("basis_bps", relative(g, ref.basis_bps(_dec(x), _dec(y))))
    rates = [rng.gauss(0, 1e-4) for _ in range(60)]
    intervals = [rng.choice((60, 240, 480, None, 0, -60)) for _ in rates]
    per_hour = funding_per_hour(pd.DataFrame({"rate": rates, "interval": intervals}))
    for g, rate, interval in zip(per_hour.tolist(), rates, intervals, strict=True):
        expected = ref.funding_per_hour(
            Decimal(rate), None if interval is None else Decimal(interval)
        )
        tally.record("funding_per_hour", relative(g, expected))
    for rows in _book_inputs(200):
        _compare_microstructure(tally, *both(rows))
    _assert_agrees(tally)


def _gapped(values: list[float | None], present: list[bool]) -> list[float]:
    return [
        v if keep and v is not None else math.nan for v, keep in zip(values, present, strict=True)
    ]


def _grid_frame(prods: list[DydxSecondSnapshot], present: list[bool]) -> pd.DataFrame:
    """Return the research grid's columns for these seconds, NaN on a missing one."""
    floats = [p.as_floats() for p in prods]
    columns: dict[str, list[float | None]] = {
        "spread": [kernel.spread(f) for f in floats],
        "mid": [kernel.mid_price(f) for f in floats],
        "buy_volume": [p.buy_volume for p in prods],
        "sell_volume": [p.sell_volume for p in prods],
        "buy_count": [float(p.buy_count) for p in prods],
        "sell_count": [float(p.sell_count) for p in prods],
        "volume_delta": [kernel.volume_delta(f) for f in floats],
    }
    return pd.DataFrame({k: _gapped(v, present) for k, v in columns.items()}).astype("float64")


def _compare_microstructure(
    tally: Tally, prods: list[DydxSecondSnapshot], books: list[RefBook]
) -> None:
    """`spread_frame` and `trade_flow` over a grid with every 9th second missing (a gap)."""
    present = [i % 9 != 4 for i in range(len(prods))]
    frame = _grid_frame(prods, present)
    p, s = books[0].price_precision, books[0].size_precision
    spreads = spread_frame(frame, 10.0**-p)
    tick = Decimal(1).scaleb(-p)
    for i, (book, keep) in enumerate(zip(books, present, strict=True)):
        spread, mid = (ref.spread(book), ref.mid(book)) if keep else (None, None)
        got_ticks, got_bps = spreads["spread_ticks"].iloc[i], spreads["spread_bps"].iloc[i]
        tally.record("spread_ticks", relative(got_ticks, ref.spread_ticks(spread, tick)))
        tally.record("spread_bps", relative(got_bps, ref.spread_bps(spread, mid)))
    deltas = [ref.volume_delta(b) if keep else None for b, keep in zip(books, present, strict=True)]
    for got, want in zip(trade_flow(frame)["cvd"].tolist(), ref.run_cvd(deltas), strict=True):
        tally.record("trade_flow.cvd", at_places(got, want, s))


# --- the picker's candle -> indicator feed (SMA, EMA) ------------------------------------------


def _fed_price(candle: ref.RefCandle, feed_open: bool) -> Decimal:
    price = candle.open if feed_open else candle.close
    assert price is not None, "a traded bucket has its open and close"
    return price


def _picker_tally(feed_open: bool) -> Tally:
    """
    `replay_native` against the same Nautilus indicator fed the reference fold's closes in order:
    only the feed (which field, which order, the warm-up None) is checked, never the TA math.
    """
    tally = Tally()
    for rows in _book_inputs(300):
        prods, books = both(rows)
        folded = sorted(
            ((t, v) for (_w, t), v in fold_rows(prods, bars=(60,)).items() if v[0] is not None)
        )
        candles = [
            {"t": t, "o": o, "h": h, "l": low, "c": c, "v": v}
            for t, (o, h, low, c, v, _n) in folded
        ]
        closes = [
            _fed_price(c, feed_open)
            for _, c in sorted(ref.fold_candles(books, 60).items())
            if c.close is not None
        ]
        for cls, name in (
            (SimpleMovingAverage, "SimpleMovingAverage"),
            (ExponentialMovingAverage, "ExponentialMovingAverage"),
        ):
            got = replay_native(candles, name, {"period": 5})["value"]
            indicator = cls(5)
            for g, close in zip(got, closes, strict=True):
                indicator.update_raw(float(close))
                expected = indicator.value if indicator.initialized else None
                tally.record(
                    f"picker.{name}", relative(g, None if expected is None else Decimal(expected))
                )
    return tally


def test_the_picker_feeds_sma_and_ema_the_folded_closes_in_order() -> None:
    _assert_agrees(_picker_tally(feed_open=False))


def test_planted_picker_feed_of_opens_is_caught() -> None:
    tally = _picker_tally(feed_open=True)
    assert tally.count("picker.SimpleMovingAverage", Agreement.DIFFERENT) > 0


# --- golden cases -------------------------------------------------------------------------------


def test_golden_pct_bound_and_volatilities_are_hand_checked() -> None:
    hour = HOUR_NS
    series = [(0, Decimal(90)), (hour + 300 * NS_PER_S, Decimal(100)), (2 * hour, Decimal(110))]
    assert ref.pct_change(series, hour, PCT_MAX_SHORTFALL_NS) == Decimal(10)
    shortened = [(0, Decimal(90)), (hour + 301 * NS_PER_S, Decimal(100)), (2 * hour, Decimal(110))]
    assert ref.pct_change(shortened, hour, PCT_MAX_SHORTFALL_NS) is None
    assert price_stats_from_series(_as_floats(shortened))["pct_change_1h"] is None
    returns = [Decimal("0.1"), Decimal("-0.1")]  # 100 -> 110 -> 99
    points = [(0, Decimal(100)), (NS_PER_S, Decimal(110)), (2 * NS_PER_S, Decimal(99))]
    with localcontext(prec=ref.DIGITS):
        assert ref.vol_fast([p for _, p in points], 300) == Decimal(2).sqrt() / 10
    assert ref.vol_catalog_24h(points, VOLATILITY_WINDOW_NS) == Decimal("0.1")
    assert ref.pct_returns([p for _, p in points]) == returns


@pytest.mark.parametrize("iid", FIXTURE_IDS)
def test_the_real_fixture_feeds_the_board_with_no_mid_substitute(iid: str) -> None:
    """
    D-78, on the deleted path: a traded but one-sided second puts a close into the price series
    while feeding no book; after a slow-loop pass the slow metrics hold that close, and the rank
    entry's `price` must still be None -- restoring the slow-close fallback fails here. Once the
    real two-sided rows arrive, `price` is the last row's own mid.
    """
    rows = load_fixture(iid)
    traded = next(r for r in rows if r["close_price"] is not None)
    first = _one_sided(traded, rows[0]["ts_event"] - NS_PER_S)
    prods, books = both([first, *rows])
    board = _board()
    board.ingest(prods[0], received_ns=prods[0].ts_event)
    slow = board.slow_rows(prods[0].ts_event, {}, {})
    assert slow[0]["price"] is not None  # the close the fallback used to publish
    assert board.current_ranks(prods[0].ts_event)[0]["price"] is None
    for prod in prods[1:]:
        board.ingest(prod, received_ns=prod.ts_event)
    row = board.current_ranks(prods[-1].ts_event)[0]
    assert at_places(row["price"], ref.mid(books[-1]), books[-1].price_precision + 1) in (
        Agreement.EXACT,
        Agreement.FLOAT_NOISE,
    )


# --- pinned Known limits: substitutions kept on purpose (DATA_DICTIONARY §2.12, §2.13) ----------


def test_microprice_edge_reads_a_lean_within_four_ulps_of_the_mid_as_flat_known_limit() -> None:
    """
    Pinned: `microstructure.microprice_edge` sets a predictor within `_ROUNDING_ULPS` (4) ulps of
    the mid to 0 -- it cannot tell rounding from a real lean that small. The reference lean here is
    genuinely non-zero (2.5e-12), yet it reads flat. Upgrade path: judge the lean on the exact
    decimals (the reference's) instead of two independently rounded floats.
    """
    case = stored_row(BookCase(0, 2, 0, (1343729,), (10**9 + 1,), (1343730,), (10**9,)))
    (prod,), (book,) = both([case])
    lean = ref.microprice(book) - ref.mid(book)  # type: ignore[operator]
    frame = pd.DataFrame(
        {
            "mid": [kernel.mid_price(prod.as_floats())],
            "microprice": [kernel.microprice(prod.as_floats())],
        }
    )
    assert lean != 0
    assert microprice_edge(frame)["predictor"].iloc[0] == 0.0


def test_oi_changes_read_a_move_to_zero_as_undefined_known_limit() -> None:
    """
    Pinned: `aligned.oi_changes` treats an open interest of 0 as NaN, so a real drop to 0 (a -100%
    change the reference defines) is undefined there, as is the change from 0. Upgrade path: keep
    the -100% and leave only the change *from* 0 undefined.
    """
    oi = pd.DataFrame({"ts_event": [0, 60 * NS_PER_S], "open_interest": [100.0, 0.0]})
    got = oi_changes({"x": oi}, 60, 0, 120 * NS_PER_S).column("x")
    ((_ts, expected),) = ref.simple_returns(
        [Decimal(100), Decimal(0)], [0, 60 * NS_PER_S], 60 * NS_PER_S
    )
    assert expected == Decimal(-1)
    assert math.isnan(got[0])
