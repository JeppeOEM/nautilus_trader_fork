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
The per-snapshot and per-bar derived signals against the independent reference (Story 31.3,
`docs/DATA_DICTIONARY.md` §2.13): the two decoders, `kernel.indicators`, the chart's Lines mode
and per-bar replay (`views.chart_series`) and the one seconds -> bars fold -- each on seeded
generators, on the committed real soak rows and on hand-computed golden cases, each with a planted
defect the comparison must catch.

Every comparison records a verdict in a `Tally`; a test passes only with no DIFFERENT and no
UNDEFINED_MISMATCH. Float noise (a float that quantizes to the right decimal but is not its
nearest double) is counted as its own class and printed (`pytest -s`). The ranking, research and
picker comparisons are `test_reference_series.py`.

This module is a declared composition root (`tests/test_boundaries.py`): it imports the
production code it compares, the reference (`verification.domain`) never does.
"""

from collections.abc import Callable
from decimal import Decimal
from itertools import pairwise
from typing import Any

import pytest
from candles.domain.candle import TIMEFRAMES
from candles.domain.fold import BAR_SECONDS
from candles.domain.fold import fold_rows
from kernel import indicators as kernel
from kernel.indicators import OFI_GAP_NS
from kernel.indicators import MultiLevelOBI
from kernel.indicators import MultiLevelOFI
from kernel.second_snapshot import DydxSecondSnapshot
from views import chart_series

from verification.domain import reference_signals as ref
from verification.domain.reference_signals import RefBook
from verification.domain.signal_compare import FAILING
from verification.domain.signal_compare import Agreement
from verification.domain.signal_compare import Tally
from verification.domain.signal_compare import at_places
from verification.domain.signal_compare import equal
from verification.domain.signal_compare import relative
from verification.tests.signal_cases import FIXTURE_IDS
from verification.tests.signal_cases import BookCase
from verification.tests.signal_cases import book_series
from verification.tests.signal_cases import both
from verification.tests.signal_cases import carried
from verification.tests.signal_cases import load_fixture
from verification.tests.signal_cases import pinned_zero
from verification.tests.signal_cases import seeded
from verification.tests.signal_cases import sparse_series
from verification.tests.signal_cases import stored_row


SEEDS = range(12)
ROWS = 160
OFI_LEVELS = (3, 5, 10)
OFI_WINDOWS = (50, 300)
DEPTH_EDGES = (0.5, 1.0, 2.0, 5.0, 10.0, 25.0, 0.3, 7.77)
BAR_WIDTHS = tuple(sorted(set(BAR_SECONDS) | set(TIMEFRAMES.values())))


def _generated(seed: int) -> list[dict[str, Any]]:
    return [stored_row(c) for c in book_series(seeded(seed), ROWS)]


def _inputs() -> list[tuple[str, list[dict[str, Any]]]]:
    """Every row set compared: the seeded generators, then the five real soak instruments."""
    generated = [(f"seed-{seed}", _generated(seed)) for seed in SEEDS]
    return generated + [(iid, load_fixture(iid)) for iid in FIXTURE_IDS]


def _assert_agrees(tally: Tally) -> None:
    print("\n" + "\n".join(tally.report()))
    assert tally.signals(), "nothing was compared"
    assert tally.failures() == {}


def _two_sided_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["bid_prices"] and r["ask_prices"]]


# --- §1.7: the two decoders --------------------------------------------------------------------


def _compare_decoded(tally: Tally, prod: DydxSecondSnapshot, book: RefBook) -> None:
    exact = prod.exact
    pairs = (
        ("decode.bid_prices", exact.bid_prices, book.bid_prices),
        ("decode.bid_sizes", exact.bid_sizes, book.bid_sizes),
        ("decode.ask_prices", exact.ask_prices, book.ask_prices),
        ("decode.ask_sizes", exact.ask_sizes, book.ask_sizes),
    )
    for signal, prod_values, ref_values in pairs:
        tally.record(signal, equal(len(prod_values), len(ref_values)))
        for p, r in zip(prod_values, ref_values, strict=False):
            tally.record(signal, equal(p.as_decimal(), r))
    tally.record("decode.volume", equal(exact.buy_volume.as_decimal(), book.buy_volume))
    tally.record("decode.volume", equal(exact.sell_volume.as_decimal(), book.sell_volume))
    close = None if exact.close_price is None else exact.close_price.as_decimal()
    tally.record("decode.close", equal(close, book.close_price))


def test_the_two_decoders_read_the_same_stored_integers() -> None:
    tally = Tally()
    for _, rows in _inputs():
        for prod, book in zip(*both(rows), strict=True):
            _compare_decoded(tally, prod, book)
    _assert_agrees(tally)


# --- §2.1: top of book, flow ------------------------------------------------------------------


def _compare_top(tally: Tally, prod: DydxSecondSnapshot, book: RefBook) -> None:
    floats = prod.as_floats()
    p, s = book.price_precision, book.size_precision
    tally.record("mid", at_places(kernel.mid_price(floats), ref.mid(book), p + 1))
    tally.record("spread", at_places(kernel.spread(floats), ref.spread(book), p))
    tally.record("microprice", relative(kernel.microprice(floats), ref.microprice(book)))
    tally.record("volume_delta", at_places(kernel.volume_delta(floats), ref.volume_delta(book), s))


def test_top_of_book_and_volume_delta_match_the_reference() -> None:
    tally = Tally()
    for _, rows in _inputs():
        for prod, book in zip(*both(rows), strict=True):
            _compare_top(tally, prod, book)
    _assert_agrees(tally)
    assert tally.count("microprice", Agreement.BOTH_UNDEFINED) > 0  # zero tops were generated


def _compare_window(tally: Tally, prods: list[DydxSecondSnapshot], books: list[RefBook]) -> None:
    buy, sell, buy_count, sell_count = kernel.trade_aggregates([p.as_floats() for p in prods])
    count = buy_count + sell_count
    s = books[0].size_precision
    tally.record("cvd", at_places(buy - sell, ref.cvd(books), s))
    avg = (buy + sell) / count if count else None
    tally.record("avg_trade_size", relative(avg, ref.avg_trade_size(books)))


def test_trade_aggregates_give_the_reference_cvd_and_avg_trade_size() -> None:
    tally = Tally()
    for _, rows in _inputs():
        prods, books = both(rows)
        for end in range(1, len(rows) + 1, 7):
            for window in (1, 60, 300):
                start = max(0, end - window)
                _compare_window(tally, prods[start:end], books[start:end])
    _assert_agrees(tally)


# --- §2.3 OBI -----------------------------------------------------------------------------------


def _prod_obi(prod: DydxSecondSnapshot, levels: int, swapped: bool = False) -> float | None:
    obi = MultiLevelOBI(levels=levels)
    sides = (prod.ask_sizes, prod.bid_sizes) if swapped else (prod.bid_sizes, prod.ask_sizes)
    obi.update_raw(*sides)
    return obi.value if obi.initialized else None


def _obi_tally(swapped: bool) -> Tally:
    tally = Tally()
    for _, rows in _inputs():
        for prod, book in zip(*both(rows), strict=True):
            for levels in range(1, 21):
                verdict = relative(_prod_obi(prod, levels, swapped), ref.obi(book, levels))
                tally.record(f"obi_{levels}", verdict)
    return tally


def test_obi_at_every_level_count_matches_the_reference() -> None:
    _assert_agrees(_obi_tally(swapped=False))


def test_planted_obi_with_bid_and_ask_swapped_is_caught() -> None:
    tally = _obi_tally(swapped=True)
    assert sum(tally.count(f"obi_{n}", Agreement.DIFFERENT) for n in range(1, 21)) > 0


def test_multilevel_obi_keeps_its_previous_value_on_a_zero_total_known_limit() -> None:
    """
    Pinned Known limit (DATA_DICTIONARY §2.3): a zero total leaves `MultiLevelOBI.value` at the
    previous reading (the reference's value there is None). Stateful readers publish the carried
    value; the per-bar replay's docstring and the dictionary say so.
    """
    obi = MultiLevelOBI(levels=3)
    obi.update_raw([3.0], [1.0])
    obi.update_raw([0.0], [0.0])
    zero_total = both([stored_row(BookCase(0, 2, 2, (100,), (0,), (101,), (0,)))])[1][0]
    assert (obi.value, ref.obi(zero_total, 3)) == (0.75, None)


# --- §2.2 OFI -----------------------------------------------------------------------------------


def _prod_ofi(
    prods: list[DydxSecondSnapshot], levels: int, window: int, usd: bool, z: int | None = None
) -> list[float | None]:
    """`MultiLevelOFI` driven by §2.2's gap rule (clear the previous book past `OFI_GAP_NS`)."""
    ofi = MultiLevelOFI(levels=levels, window=window, usd_notional=usd, zscore_window=z)
    out: list[float | None] = []
    last: int | None = None
    for p in prods:
        if last is not None and p.ts_event - last > OFI_GAP_NS:
            ofi.clear_prev_state()
        last = p.ts_event
        ofi.update_raw(p.bid_prices, p.bid_sizes, p.ask_prices, p.ask_sizes)
        out.append(ofi.value if ofi.initialized else None)
    return out


def _ofi_verdict(prod: float | None, expected: Decimal | None, usd: bool, s: int) -> Agreement:
    return relative(prod, expected) if usd else at_places(prod, expected, s)


def _ofi_tally(level_offset: int = 0) -> Tally:
    tally = Tally()
    for _, rows in _inputs():
        prods, books = both(rows)
        s = books[0].size_precision
        for levels in OFI_LEVELS:
            for window in OFI_WINDOWS:
                for usd in (False, True):
                    prod = _prod_ofi(prods, levels - level_offset, window, usd)
                    expected = ref.rolling_ofi(books, levels, window, usd, OFI_GAP_NS)
                    signal = f"ofi_{levels}_w{window}_{'usd' if usd else 'count'}"
                    for p, e in zip(prod, expected, strict=True):
                        tally.record(signal, _ofi_verdict(p, e, usd, s))
    return tally


def test_multilevel_ofi_count_and_usd_match_the_reference() -> None:
    _assert_agrees(_ofi_tally())


def test_planted_ofi_using_one_level_too_few_is_caught() -> None:
    tally = _ofi_tally(level_offset=1)
    assert sum(tally.count(s, Agreement.DIFFERENT) for s in tally.signals()) > 0


def test_the_z_scored_ofi_matches_the_reference_with_zero_when_undefined_pinned() -> None:
    tally = Tally()
    for _, rows in _inputs():
        prods, books = both(rows)
        for z_window in (3600, 20):
            prod = _prod_ofi(prods, 10, 50, False, z_window)
            expected = ref.rolling_ofi_z(books, 10, 50, False, OFI_GAP_NS, z_window)
            for p, e in zip(prod, expected, strict=True):
                tally.record(f"ofi_10_z{z_window}", pinned_zero(p, e))
    _assert_agrees(tally)


# --- §2.12 depth --------------------------------------------------------------------------------


def _prod_depth(prod: DydxSecondSnapshot, levels: int) -> tuple[list[float], list[float]]:
    profile = kernel.snapshot_depth(prod.as_floats(), levels)
    if profile is None:
        nan = [float("nan")] * len(DEPTH_EDGES)
        return nan, list(nan)
    return kernel.depth_within_bps(profile, DEPTH_EDGES)


# Production's `kernel.indicators._BPS_EDGE_SLACK`: a level within this relative distance of an
# edge counts as on it. The reference is exact; a disagreement explained by this slack alone is the
# pinned Known limit EDGE_SLACK (DATA_DICTIONARY §2.13), anything else DIFFERENT.
_EDGE_SLACK = Decimal("1e-9")


def _near_edge(levels: ref.Distances, edge: Decimal) -> bool:
    return any(abs(distance - edge) <= edge * _EDGE_SLACK for distance, _ in levels)


def _slack_depth(levels: ref.Distances, edge: Decimal) -> Decimal | None:
    """Return the depth with production's slack applied to the exact distances."""
    slack = edge * _EDGE_SLACK
    if not levels or edge - slack > levels[-1][0]:
        return None
    return sum((size for distance, size in levels if distance <= edge + slack), Decimal(0))


def _depth_verdict(
    got: float, want: Decimal | None, levels: ref.Distances | None, edge: Decimal, s: int
) -> Agreement:
    verdict = at_places(got, want, s)
    if verdict not in FAILING or levels is None or not _near_edge(levels, edge):
        return verdict
    slack = at_places(got, _slack_depth(levels, edge), s)
    return Agreement.DIFFERENT if slack in FAILING else Agreement.EDGE_SLACK


def _compare_depth(tally: Tally, prod: DydxSecondSnapshot, book: RefBook, levels: int) -> None:
    edges = [Decimal(repr(e)) for e in DEPTH_EDGES]
    sides = ref.bps_distances(book, levels)
    exact = ref.depth_within_bps(book, levels, edges)
    for i, got_side in enumerate(_prod_depth(prod, levels)):
        side = None if sides is None else sides[i]
        for got, want, edge in zip(got_side, exact[i], edges, strict=True):
            verdict = _depth_verdict(got, want, side, edge, book.size_precision)
            tally.record("depth_within_bps", verdict)


def test_a_level_exactly_on_an_edge_differs_only_by_the_pinned_edge_slack() -> None:
    """
    Golden EDGE_SLACK case: bid 99.99 / ask 100.01 (mid 100) puts the best bid exactly 1 bp away.
    A distance a hair past 1 bp (inside 1e-9 relative) is on the edge for production, beyond it for
    the exact reference: the comparator names that EDGE_SLACK, and a real miss DIFFERENT.
    """
    edge = Decimal(1)
    near = [(Decimal("1.0000000001"), Decimal(5))]  # 1e-10 relative past the edge
    assert ref.depth_at(near, edge) == 0  # exactly: the level lies past the edge
    assert _depth_verdict(5.0, Decimal(0), near, edge, 0) is Agreement.EDGE_SLACK
    assert _depth_verdict(4.0, Decimal(0), near, edge, 0) is Agreement.DIFFERENT
    far = [(Decimal("1.1"), Decimal(5))]
    assert _depth_verdict(5.0, Decimal(0), far, edge, 0) is Agreement.DIFFERENT


def test_depth_within_bps_matches_the_reference_nan_beyond_the_stored_depth() -> None:
    tally = Tally()
    for _, rows in _inputs():
        for prod, book in zip(*both(rows), strict=True):
            for levels in (1, 5, 10, 20):
                _compare_depth(tally, prod, book, levels)
    _assert_agrees(tally)
    assert tally.count("depth_within_bps", Agreement.BOTH_UNDEFINED) > 0


# --- §2.7 views: Lines mode and the per-bar replay ---------------------------------------------


def test_price_series_rows_match_the_reference_mid_micro_and_cvd_weighted_price() -> None:
    tally = Tally()
    for _, rows in _inputs():
        prods, books = both(_two_sided_rows(rows))
        lines = [r for r in chart_series.price_series_rows(prods) if r["bid_units"] is not None]
        for line, book in zip(lines, books, strict=True):
            p = book.price_precision
            tally.record("lines.mid", at_places(line["mid"], ref.mid(book), p + 1))
            tally.record("lines.micro", relative(line["micro"], ref.microprice(book)))
            tally.record("lines.price", relative(line["price"], ref.cvd_weighted_price(book)))
    _assert_agrees(tally)
    assert tally.count("lines.micro", Agreement.BOTH_UNDEFINED) > 0  # no mid stands in


def _ref_bars(books: list[RefBook], bar: int) -> dict[int, dict[str, Any]]:
    ordered = sorted(books, key=lambda b: b.ts_event)
    ofi = ref.rolling_ofi(ordered, 10, 50, False, OFI_GAP_NS)
    obi = carried([ref.obi(b, 10) for b in ordered])
    bars: dict[int, dict[str, Any]] = {}
    for i, book in enumerate(ordered):
        values = {"ofi": ofi[i], "obi": obi[i], "micro": ref.microprice(book)}
        bars[ref.bucket_start(book.ts_event // 1_000_000, bar)] = {**values, "book": book}
    return bars


def _compare_bar(tally: Tally, got: dict[str, object], want: dict[str, object]) -> None:
    book = want["book"]
    assert isinstance(book, RefBook)
    s, p = book.size_precision, book.price_precision
    tally.record("replay.ofi", at_places(got["ofi"], want["ofi"], s))  # type: ignore[arg-type]
    tally.record("replay.obi", relative(got["obi"], want["obi"]))  # type: ignore[arg-type]
    tally.record("replay.microprice", relative(got["microprice"], want["micro"]))  # type: ignore[arg-type]
    tally.record("replay.spread", at_places(got["spread"], ref.spread(book), p))  # type: ignore[arg-type]


def test_the_per_bar_replay_matches_the_reference_ofi_obi_per_bucket() -> None:
    tally = Tally()
    for _, rows in _inputs():
        prods, books = both(rows)
        for bar in (1, 60, 300, 604_800):
            got = chart_series.replay_bucket_samples(prods, bar)
            want = _ref_bars(books, bar)
            tally.record("replay.buckets", equal(sorted(got), sorted(want)))
            # A bucket on one side only is already DIFFERENT above; the rest compare value by value.
            for key in sorted(want.keys() & got.keys()):
                _compare_bar(tally, got[key], want[key])
    _assert_agrees(tally)


# --- §2.5 candles -----------------------------------------------------------------------------


def _candle_rows() -> list[tuple[str, list[dict[str, Any]]]]:
    sparse = [
        (f"sparse-{seed}", [stored_row(c) for c in sparse_series(seeded(seed), 400, 40)])
        for seed in SEEDS
    ]
    return sparse + _inputs()


def _compare_candle(tally: Tally, got: list, want: ref.RefCandle, p: int, s: int) -> None:
    o, h, low, c, v, n = got
    for name, value, expected in (
        ("o", o, want.open),
        ("h", h, want.high),
        ("l", low, want.low),
        ("c", c, want.close),
    ):
        tally.record(f"candle.{name}", at_places(value, expected, p))
    tally.record("candle.v", at_places(v, want.volume, s))
    tally.record("candle.seconds_observed", equal(n, want.seconds_observed))


def _fold_tally(fold: Callable[[list[DydxSecondSnapshot], int], dict]) -> Tally:
    tally = Tally()
    for _, rows in _candle_rows():
        prods, books = both(rows)
        p, s = books[0].price_precision, books[0].size_precision
        for width in BAR_WIDTHS:
            got = fold(prods, width)
            want = ref.fold_candles(books, width)
            tally.record("candle.buckets", equal(sorted(got), sorted(want)))
            # A bucket on one side only is already DIFFERENT above; the rest compare value by value.
            for t in sorted(want.keys() & got.keys()):
                _compare_candle(tally, got[t], want[t], p, s)
    return tally


def _production_fold(prods: list[DydxSecondSnapshot], width: int) -> dict:
    return {t: values for (_bar, t), values in fold_rows(prods, bars=(width,)).items()}


def test_fold_arrays_matches_the_reference_at_every_stored_and_offered_width() -> None:
    tally = _fold_tally(_production_fold)
    _assert_agrees(tally)
    assert tally.total("candle.buckets") == len(BAR_WIDTHS) * (2 * len(SEEDS) + len(FIXTURE_IDS))


def _first_close_fold(prods: list[DydxSecondSnapshot], width: int) -> dict:
    """Fold with the planted defect: each bucket's close taken from its first traded second."""
    folded = _production_fold(prods, width)
    firsts: dict[int, float] = {}
    for prod in sorted(prods, key=lambda p: p.ts_event):
        t = ref.bucket_start(prod.ts_event // 1_000_000, width)
        if prod.close_price is not None:
            firsts.setdefault(t, prod.close_price)
    return {t: [*values[:3], firsts.get(t), *values[4:]] for t, values in folded.items()}


def test_planted_fold_taking_the_first_close_is_caught() -> None:
    tally = _fold_tally(_first_close_fold)
    assert tally.count("candle.c", Agreement.DIFFERENT) > 0


# --- golden cases: hand-computed values ------------------------------------------------------

# Bids 100.5 / 100.3 / 99.9 (sizes 1, 2, 3), asks 100.7 / 101.0 (sizes 4, 5) at p=1, s=0 --
# §1.7's own worked example, stored [1005, 2, 4] / [1007, 3].
_GOLDEN = BookCase(
    ts_event=1_790_553_600_500_000_000,
    price_precision=1,
    size_precision=0,
    bid_units=(1005, 1003, 999),
    bid_size_units=(1, 2, 3),
    ask_units=(1007, 1010),
    ask_size_units=(4, 5),
    buy_volume=6,
    sell_volume=2,
    buy_count=3,
    sell_count=1,
    ohlc=(1006, 1007, 1005, 1005),
)


def test_golden_book_values_are_the_hand_computed_ones_on_both_sides() -> None:
    row = stored_row(_GOLDEN)
    assert (row["bid_prices"], row["ask_prices"]) == ([1005, 2, 4], [1007, 3])
    (prod,), (book,) = both([row])
    assert ref.mid(book) == Decimal("100.6")
    assert ref.spread(book) == Decimal("0.2")
    assert ref.microprice(book) == Decimal("100.54")  # (100.5*4 + 100.7*1) / 5
    assert ref.obi(book, 2) == Decimal("0.25")  # (1+2) / (1+2+4+5)
    assert ref.cvd_weighted_price(book) == Decimal("100.65")  # 100.6 + (4/8) * 0.2 / 2
    floats = prod.as_floats()
    assert at_places(kernel.mid_price(floats), ref.mid(book), 2) is Agreement.EXACT
    assert relative(kernel.microprice(floats), ref.microprice(book)) in (
        Agreement.EXACT,
        Agreement.WITHIN_TOL,
    )


def test_golden_ofi_step_counts_each_cks_branch_and_values_a_withdrawal_at_its_price() -> None:
    """
    Level 0: bid up 100.5 -> 100.6 (+ new size 2), ask unchanged 100.7 size 4 -> 1 (-3); level 1:
    bid unchanged 100.3 size 2 -> 5 (+3), ask worse 101.0 -> 101.2 (a withdrawal of 5). Level 2
    exists on the bid side only, so it is not counted.
    """
    prev = both([stored_row(_GOLDEN)])[1][0]
    cur_case = BookCase(
        _GOLDEN.ts_event + 1_000_000_000, 1, 0, (1006, 1003, 999), (2, 5, 3), (1007, 1012), (1, 5)
    )
    cur = both([stored_row(cur_case)])[1][0]
    # count: bid (2) + (3); ask (1 - 4 = -3) and (-5) -> 5 - (-8) = 13
    assert ref.ofi_step(prev, cur, 10, usd=False) == Decimal(13)
    usd = (
        2 * Decimal("100.6") + 3 * Decimal("100.3") - (-3 * Decimal("100.7") - 5 * Decimal("101.0"))
    )
    assert ref.ofi_step(prev, cur, 10, usd=True) == usd
    ofi = MultiLevelOFI(levels=10, window=5, usd_notional=True)
    for prod in both([stored_row(_GOLDEN), stored_row(cur_case)])[0]:
        ofi.update_raw(prod.bid_prices, prod.bid_sizes, prod.ask_prices, prod.ask_sizes)
    assert relative(ofi.value, usd) in (Agreement.EXACT, Agreement.WITHIN_TOL)


def test_golden_week_bucket_starts_monday_and_the_fold_is_hand_checked() -> None:
    wednesday_ms = 1_790_553_600_000 + 2 * 86_400_000 + 12 * 3_600_000
    assert ref.bucket_start(wednesday_ms, 604_800) == 1_790_553_600_000  # 2026-09-28, a Monday
    early = _GOLDEN
    later = BookCase(
        _GOLDEN.ts_event + 60_000_000_000,
        1,
        0,
        (1005,),
        (1,),
        (1007,),
        (1,),
        1,
        0,
        1,
        0,
        (1004, 1009, 1001, 1008),
    )
    books = both([stored_row(early), stored_row(later)])[1]
    (candle,) = ref.fold_candles(books, 3600).values()
    assert (candle.open, candle.high, candle.low, candle.close) == (
        Decimal("100.6"),
        Decimal("100.9"),
        Decimal("100.1"),
        Decimal("100.8"),
    )
    assert (candle.volume, candle.seconds_observed) == (Decimal(9), 2)


@pytest.mark.parametrize("iid", FIXTURE_IDS)
def test_every_real_fixture_holds_300_consecutive_two_sided_rows(iid: str) -> None:
    """The fixtures are the soak's own stored rows: 300 of them, each with a top of book."""
    rows = load_fixture(iid)
    assert len(rows) == 300
    assert _two_sided_rows(rows) == rows
    stamps = [r["ts_event"] for r in rows]
    # Consecutive stored seconds: strictly 1 s apart, no duplicate and no gap (review P10).
    assert {b - a for a, b in pairwise(stamps)} == {1_000_000_000}


def test_the_compare_rules_judge_infinity_and_read_a_float_reference_as_written() -> None:
    """Review P6: an infinite production value is DIFFERENT (never a crash); `0.1` means 0.1."""
    assert relative(float("inf"), Decimal(1)) is Agreement.DIFFERENT
    assert at_places(float("-inf"), Decimal(1), 0) is Agreement.DIFFERENT
    assert relative(0.1, 0.1) is Agreement.EXACT
    assert at_places(0.30000000000000004, 0.3, 1) is Agreement.FLOAT_NOISE
