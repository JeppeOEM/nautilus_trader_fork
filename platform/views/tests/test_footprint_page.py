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
`views.chart_series.footprint_page` (Story 32.8): the raw trade archive bucketed into integer price
rows per closed bar of the chart's own `candle_page`, on a real `ParquetDataCatalog`.
"""

from collections import defaultdict
from pathlib import Path

import pytest
from kernel.catalog_files import TradeDecodeError
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_S
from kernel.fold import fold_trades
from kernel.second_snapshot import SecondOHLC
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views import chart_series
from views.chart_series import FOOTPRINT_MAX_ROWS_FIXED
from views.chart_series import FOOTPRINT_SETTLE_SECONDS
from views.chart_series import MAX_FOOTPRINT_BARS
from views.chart_series import MAX_QUERY_SPAN_SECONDS
from views.chart_series import FootprintOverflow
from views.chart_series import footprint_bar


_IID = "BTCUSDT-LINEAR.BYBIT"
_NS = 1_000_000_000
_DAY0 = 20_000 * 86_400 * _NS  # a UTC midnight
_BUY = AggressorSide.BUYER
_SELL = AggressorSide.SELLER


def _trade(sec: int, price: str, size: str, side: AggressorSide, n: int) -> TradeTick:
    ts = _DAY0 + sec * _NS + n  # distinct ts_event inside the second, arrival order kept
    return TradeTick(
        InstrumentId.from_str(_IID),
        Price.from_str(price),
        Quantity.from_str(size),
        side,
        TradeId(str(n)),
        ts,
        ts,
    )


def _no_tail(_iid: str, _start: int, _end: int) -> list[SecondOHLC]:
    return []


def _archive(root: Path, trades: list[TradeTick], pp: int, sp: int, *, store: bool = True) -> str:
    """Write each traded second's snapshot (folded from its trades) and, with `store`, the trades."""
    by_second: dict[int, list[TradeTick]] = defaultdict(list)
    for trade in trades:
        by_second[trade.ts_event // _NS * _NS].append(trade)
    catalog = ParquetDataCatalog(str(root))
    catalog.write_data(
        [
            make_snapshot(
                _IID,
                ts_event=second,
                price_precision=pp,
                size_precision=sp,
                trades=fold_trades(group).snapshot_units(pp, sp),
            )
            for second, group in sorted(by_second.items())
        ]
    )
    if store:
        catalog.write_data(sorted(trades, key=lambda t: t.ts_init))
    return str(root)


def _page(
    catalog: str,
    before_ns: int,
    *,
    pp: int,
    sp: int,
    limit: int = 120,
    bar_seconds: int = 60,
    row_ticks: int | None = None,
    now_ns: int | None = None,
) -> tuple[list[dict], bool]:
    return chart_series.footprint_page(
        _IID,
        before_ns,
        limit,
        bar_seconds,
        row_ticks,
        catalog_path=catalog,
        candles_dir=f"{catalog}-no-candle-store",  # the Parquet fold: the snapshots' trade units
        recent_rows=_no_tail,
        recent_liquidations=lambda *_: [],
        price_precision=pp,
        size_precision=sp,
        now_ns=before_ns if now_ns is None else now_ns,
    )


def _settled(bar_end_sec: int) -> int:
    """Return a `before_ns`/now late enough that the bar ending at `bar_end_sec` has settled."""
    return _DAY0 + (bar_end_sec + FOOTPRINT_SETTLE_SECONDS) * _NS


def test_one_bar_two_rows_buy_and_sell_share_a_row(tmp_path: Path) -> None:
    trades = [
        _trade(10, "100.0", "3", _BUY, 1),
        _trade(11, "100.0", "1", _SELL, 2),
        _trade(12, "100.5", "2", _BUY, 3),
    ]
    catalog = _archive(tmp_path, trades, 1, 0)
    bars, _ = _page(catalog, _settled(60), pp=1, sp=0, row_ticks=5)
    assert bars == [
        {
            "t": _DAY0 // 1_000_000,
            "row_ticks": 5,
            "rows": [{"p": 1000, "b": 3, "s": 1}, {"p": 1005, "b": 2, "s": 0}],
            "delta": 4,
            "total": 6,
            "poc_row": 1000,
            "no_trades": False,
        }
    ]


def test_auto_row_size_gives_at_most_24_rows() -> None:
    levels = {1000 + k: [1, 0] for k in range(480)}
    bar = footprint_bar(_IID, 0, levels, None)
    assert bar.row_ticks == 20
    assert len(bar.rows) == 24
    assert bar.total == 480


def test_a_fixed_row_size_of_one_tick_keeps_every_level() -> None:
    levels = {1000 + k: [0, 2] for k in range(480)}
    bar = footprint_bar(_IID, 0, levels, 1)
    assert len(bar.rows) == 480
    assert bar.delta == -960


def test_a_fixed_row_size_past_the_row_cap_is_coarsened_to_a_multiple_and_says_so() -> None:
    levels = {k: [1, 0] for k in range(0, 3 * FOOTPRINT_MAX_ROWS_FIXED, 3)}  # 3000 tick levels
    bar = footprint_bar(_IID, 0, levels, 2)
    assert bar.row_ticks == 4  # 2 gives 1500 rows; 4 (the next multiple) gives 750
    assert len(bar.rows) <= FOOTPRINT_MAX_ROWS_FIXED
    assert bar.total == FOOTPRINT_MAX_ROWS_FIXED


def test_the_poc_ties_to_the_lower_row() -> None:
    bar = footprint_bar(_IID, 0, {10: [1, 1], 20: [2, 0], 30: [1, 0]}, 10)
    assert bar.poc_row == 10


def test_no_aggressor_counts_as_a_sell(tmp_path: Path) -> None:
    trades = [_trade(5, "100.0", "2", AggressorSide.NO_AGGRESSOR, 1)]
    catalog = _archive(tmp_path, trades, 1, 0)
    (bar,), _ = _page(catalog, _settled(60), pp=1, sp=0, row_ticks=1)
    assert bar["rows"] == [{"p": 1000, "b": 0, "s": 2}]


def test_a_bar_without_archived_trades_is_a_gap_never_zeros(tmp_path: Path) -> None:
    held = [_trade(5, "100.0", "1", _BUY, 1)]
    released = [_trade(65, "101.0", "1", _BUY, 2)]  # the snapshot remains, the trades do not
    catalog = _archive(tmp_path, held, 1, 0)
    _archive(tmp_path, released, 1, 0, store=False)
    bars, _ = _page(catalog, _settled(120), pp=1, sp=0)
    assert bars[1] == {
        "t": (_DAY0 + 60 * _NS) // 1_000_000,
        "row_ticks": None,
        "rows": [],
        "delta": None,
        "total": None,
        "poc_row": None,
        "no_trades": True,
    }
    assert bars[0]["no_trades"] is False


def test_limit_is_clamped_to_max_footprint_bars(tmp_path: Path) -> None:
    trades = [_trade(60 * m, "100.0", "1", _BUY, m) for m in range(MAX_FOOTPRINT_BARS + 20)]
    catalog = _archive(tmp_path, trades, 1, 0)
    bars, has_more = _page(
        catalog, _settled(60 * (MAX_FOOTPRINT_BARS + 20)), pp=1, sp=0, limit=1000
    )
    assert len(bars) == MAX_FOOTPRINT_BARS
    assert has_more is True


def test_the_forming_and_unsettled_bars_are_absent(tmp_path: Path) -> None:
    trades = [_trade(60 * m + 1, "100.0", "1", _BUY, m) for m in range(3)]  # minutes 0, 1, 2
    catalog = _archive(tmp_path, trades, 1, 0)
    now = _DAY0 + (120 + FOOTPRINT_SETTLE_SECONDS - 1) * _NS  # minute 1 is 1 s short of settled
    bars, _ = _page(catalog, now, pp=1, sp=0)
    assert [b["t"] for b in bars] == [_DAY0 // 1_000_000]


def test_a_cursor_inside_a_bar_never_serves_that_bar(tmp_path: Path) -> None:
    trades = [_trade(1, "100.0", "1", _BUY, 1), _trade(61, "100.0", "1", _BUY, 2)]
    catalog = _archive(tmp_path, trades, 1, 0)
    bars, _ = _page(catalog, _DAY0 + 90 * _NS, pp=1, sp=0, now_ns=_settled(600))
    assert [b["t"] for b in bars] == [_DAY0 // 1_000_000]


def test_limit_counts_closed_bars_when_the_cursor_is_inside_a_bar(tmp_path: Path) -> None:
    """The bar holding the cursor is not closed: the page still holds `limit` closed bars."""
    trades = [_trade(60 * m + 1, "100.0", "1", _BUY, m) for m in range(4)]  # minutes 0..3
    catalog = _archive(tmp_path, trades, 1, 0)
    inside = _DAY0 + 210 * _NS  # inside minute 3
    bars, has_more = _page(catalog, inside, pp=1, sp=0, limit=1, now_ns=_settled(600))
    assert [b["t"] for b in bars] == [_DAY0 // 1_000_000 + 120_000]
    assert has_more is True
    bars, _ = _page(catalog, inside, pp=1, sp=0, limit=3, now_ns=_settled(600))
    assert [b["t"] for b in bars] == [_DAY0 // 1_000_000 + 60_000 * m for m in range(3)]


def test_limit_counts_closed_bars_when_the_cursor_is_a_bar_boundary(tmp_path: Path) -> None:
    trades = [_trade(60 * m + 1, "100.0", "1", _BUY, m) for m in range(4)]
    catalog = _archive(tmp_path, trades, 1, 0)
    bars, has_more = _page(catalog, _DAY0 + 180 * _NS, pp=1, sp=0, limit=2, now_ns=_settled(600))
    assert [b["t"] for b in bars] == [_DAY0 // 1_000_000 + 60_000, _DAY0 // 1_000_000 + 120_000]
    assert has_more is True


def test_the_settle_outlasts_the_trade_backfill_skew() -> None:
    assert FOOTPRINT_SETTLE_SECONDS * NS_PER_S > MAX_TS_INIT_SKEW_NS


def test_a_span_over_the_cap_is_trimmed_to_the_newest_and_sets_has_more() -> None:
    day_ms = 86_400_000
    candles = [{"t": k * day_ms} for k in range(10)]
    starts, trimmed = chart_series._settled_bars(candles, 86_400, 10 * day_ms * 1_000_000)
    assert len(starts) == MAX_QUERY_SPAN_SECONDS // 86_400
    assert starts[-1] == 9 * day_ms
    assert trimmed is True


def test_a_page_across_utc_midnight_folds_each_days_trades_into_its_own_bar(
    tmp_path: Path,
) -> None:
    """Hourly bars either side of midnight: two day slices read, each trade in its own bar."""
    day = 86_400
    trades = [
        _trade(day - 1_800, "100.0", "2", _BUY, 1),  # 23:30 on day 0
        _trade(day + 600, "101.0", "3", _SELL, 2),  # 00:10 on day 1
    ]
    catalog = _archive(tmp_path, trades, 1, 0)
    bars, _ = _page(catalog, _settled(day + 3_600), pp=1, sp=0, bar_seconds=3_600, row_ticks=1)
    assert [(b["t"], b["rows"]) for b in bars] == [
        ((_DAY0 + (day - 3_600) * _NS) // 1_000_000, [{"p": 1000, "b": 2, "s": 0}]),
        ((_DAY0 + day * _NS) // 1_000_000, [{"p": 1010, "b": 0, "s": 3}]),
    ]


def test_row_ticks_below_one_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="row_ticks"):
        _page(str(tmp_path), _settled(60), pp=1, sp=0, row_ticks=0)


def test_an_inexact_trade_is_ledgered_and_raised(tmp_path: Path) -> None:
    catalog = _archive(tmp_path, [_trade(5, "100.05", "1", _BUY, 1)], 2, 0)
    error_ledger.reset()
    with pytest.raises(TradeDecodeError):
        _page(catalog, _settled(60), pp=1, sp=0)  # a definition coarser than the trade
    assert error_ledger.counts() == {"views.footprint_trade_decode": 1}
    error_ledger.reset()


def test_a_value_past_the_json_safe_range_is_ledgered_and_raised() -> None:
    error_ledger.reset()
    with pytest.raises(FootprintOverflow, match="total"):
        footprint_bar(_IID, 0, {1: [2**52, 2**52]}, 1)
    assert error_ledger.counts() == {"views.footprint_overflow": 1}
    error_ledger.reset()


def _fixture_day() -> list[TradeTick]:
    """Five busy minutes: mixed sides (NO_AGGRESSOR included), repeated prices, sub-tick sizes."""
    sides = (_BUY, _SELL, _BUY, AggressorSide.NO_AGGRESSOR, _SELL, _BUY, _SELL)
    trades = []
    for n in range(700):
        sec = 3_600 + n * 300 // 700  # spread over 5 minutes, several trades per second
        price = f"{50_000 + (n * 37) % 41 * 0.5:.2f}"
        size = f"{(n * 13) % 97 / 1000 + 0.001:.3f}"
        trades.append(_trade(sec, price, size, sides[n % len(sides)], n))
    return trades


def test_each_bars_rows_sum_to_its_candle_volume(tmp_path: Path) -> None:
    """Parity on a fixture day: Σ rows == the same window's `candle_page` volume (31.8's fold)."""
    catalog = _archive(tmp_path, _fixture_day(), 2, 3)
    before = _settled(3_600 + 300)
    bars, _ = _page(catalog, before, pp=2, sp=3, row_ticks=10)
    candles, _ = chart_series.candle_page(
        _IID,
        before,
        120,
        60,
        catalog_path=catalog,
        candles_dir=f"{catalog}-no-candle-store",
        recent_rows=_no_tail,
        recent_liquidations=lambda *_: [],
    )
    assert len(bars) == len(candles) == 5
    for bar, candle in zip(bars, candles, strict=True):
        assert bar["t"] == candle["t"]
        rows_total = sum(r["b"] + r["s"] for r in bar["rows"])
        assert rows_total == bar["total"] == round(candle["v"] * 1_000), f"bar t={bar['t']}"


def test_a_buy_and_a_sell_at_one_price_land_in_one_row_on_their_sides(tmp_path: Path) -> None:
    trades = [_trade(7, "50000.50", "0.250", _BUY, 1), _trade(8, "50000.50", "0.125", _SELL, 2)]
    catalog = _archive(tmp_path, trades, 2, 3)
    (bar,), _ = _page(catalog, _settled(60), pp=2, sp=3, row_ticks=1)
    assert bar["rows"] == [{"p": 5_000_050, "b": 250, "s": 125}]


def test_precision_6_units_are_exact(tmp_path: Path) -> None:
    trades = [_trade(3, "0.123457", "10", _SELL, 1), _trade(4, "0.123458", "5", _BUY, 2)]
    catalog = _archive(tmp_path, trades, 6, 0)
    (bar,), _ = _page(catalog, _settled(60), pp=6, sp=0, row_ticks=1)
    assert bar["rows"] == [{"p": 123457, "b": 0, "s": 10}, {"p": 123458, "b": 5, "s": 0}]
    assert (bar["delta"], bar["total"], bar["poc_row"]) == (-5, 15, 123457)
