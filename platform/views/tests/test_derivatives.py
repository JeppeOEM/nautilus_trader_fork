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
Story 33.4: the derivatives and liquidations read model (`views.derivatives`), every row of the
spec's I/O matrix it owns, over a real `ParquetDataCatalog` and a real candle store. Every
expected value is computed by hand in its comment (TEST-01).
"""

import shutil
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from candles.infrastructure import sqlite_store
from kernel import catalog_files
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.open_interest import OpenInterest
from kernel.second_snapshot import SecondOHLC
from observability import error_ledger

from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views import derivatives
from views.chart_series import stored_bar


_LINEAR = "BTCUSDT-LINEAR.BYBIT"
_SPOT = "BTCUSDT-SPOT.BYBIT"
_HL = "BTC-USD-PERP.HYPERLIQUID"
_S = 1_000_000_000
_MIN = 60 * _S
_DAY_MS = 86_400_000
_DAY0_MS = 20_000 * _DAY_MS  # a Friday's UTC midnight (day 0 was a Thursday), long closed
_T0 = _DAY0_MS * 1_000_000
_TEN = _T0 + 10 * 3600 * _S  # 10:00:00
_TEN_MS = _TEN // 1_000_000


def _iid(iid: str = _LINEAR) -> InstrumentId:
    return InstrumentId.from_str(iid)


def _catalog(root: Path) -> ParquetDataCatalog:
    return ParquetDataCatalog(str(root))


def _oi(value: str, ts: int, iid: str = _LINEAR) -> OpenInterest:
    return OpenInterest(_iid(iid), Decimal(value), ts, ts)


def _funding(rate: str, ts: int, interval: int | None = 480) -> FundingRateUpdate:
    return FundingRateUpdate(
        _iid(), Decimal(rate), ts, ts, interval=interval, next_funding_ns=ts + 8 * 3600 * _S
    )


def _liq(
    key: str, ts: int, size: int, sp: int = 3, side: LiquidatedSide = LiquidatedSide.LONG
) -> Liquidation:
    # Bankruptcy price 100000.0 (1_000_000 units at price precision 1).
    return Liquidation(_iid(), side, size, 1_000_000, 1, sp, key, ts, ts)


def _second(ts: int, close_units: int) -> SecondOHLC:
    """One traded second at price precision 1 and size precision 3: 0.002 bought."""
    close = close_units / 10
    return SecondOHLC(ts, close, close, close, close, 0.002, 0.0, 1, 3, close_units, 2, 0, 1, 0)


def _store(root: Path) -> sqlite_store.sqlite3.Connection:
    return sqlite_store.connect_rw(sqlite_store.db_path_for_venue(root / "candles", "BYBIT"))


def _no_tail(_iid: str, _start: int, _end: int) -> list[Liquidation]:
    return []


# -- funding -------------------------------------------------------------------------------------


def test_funding_page_returns_the_newest_events_before_the_cursor_oldest_first(
    tmp_path: Path,
) -> None:
    """
    Five updates a minute apart, the fifth exactly at the cursor (not before it, so never served):
    the three newest before the cursor are #2..#4, and #1 is older (has_more). 480 min = 28 800 s;
    annualised 0.0001 x 31 536 000 / 28 800 = 0.1095.
    """
    _catalog(tmp_path).write_data([_funding("0.0001", _TEN + k * _MIN) for k in range(5)])
    items, has_more = derivatives.funding_page(
        _LINEAR, _TEN + 4 * _MIN, 3, catalog_path=str(tmp_path)
    )
    assert [item["t"] for item in items] == [_TEN + k * _MIN for k in (1, 2, 3)]
    assert items[0] == {
        "t": _TEN + _MIN,
        "rate": "0.0001",
        "interval": 28_800,
        "next_funding_ns": _TEN + _MIN + 8 * 3600 * _S,
        "annualised": pytest.approx(0.1095, abs=1e-15),
    }
    assert has_more is True


def test_funding_page_walks_back_across_days_and_has_no_annualised_without_an_interval(
    tmp_path: Path,
) -> None:
    """Updates three days apart (each in its own day window): a limit of 2 reaches both."""
    catalog = _catalog(tmp_path)
    catalog.write_data([_funding("-0.0002", _T0 - 2 * 86_400 * _S, interval=None)])
    catalog.write_data([_funding("0.0003", _TEN)])
    items, has_more = derivatives.funding_page(_LINEAR, _TEN + _S, 2, catalog_path=str(tmp_path))
    assert [(item["rate"], item["interval"], item["annualised"]) for item in items] == [
        ("-0.0002", None, None),
        ("0.0003", 28_800, pytest.approx(0.0003 * 31_536_000 / 28_800)),
    ]
    assert has_more is False


def test_a_funding_row_stored_twice_is_served_once(tmp_path: Path) -> None:
    """
    10:00 written to two files (a minute file and its day file both hold it): one item for it, the
    10:01 row beside it in the second file the only other one.
    """
    catalog = _catalog(tmp_path)
    catalog.write_data([_funding("0.0001", _TEN)])
    catalog.write_data(
        [_funding("0.0001", _TEN), _funding("0.0002", _TEN + _MIN)], skip_disjoint_check=True
    )
    items, _ = derivatives.funding_page(_LINEAR, _TEN + 10 * _MIN, 10, catalog_path=str(tmp_path))
    assert [(item["t"], item["rate"]) for item in items] == [
        (_TEN, "0.0001"),
        (_TEN + _MIN, "0.0002"),
    ]


def test_a_funding_row_stored_twice_with_different_values_is_ledgered_and_raised(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    catalog.write_data([_funding("0.0001", _TEN)])
    catalog.write_data(
        [_funding("0.0009", _TEN), _funding("0.0002", _TEN + _MIN)], skip_disjoint_check=True
    )
    before = error_ledger.counts().get(derivatives.READ_SITE, 0)
    with pytest.raises(derivatives.DerivativesReadError, match="stored twice"):
        derivatives.funding_page(_LINEAR, _TEN + 10 * _MIN, 10, catalog_path=str(tmp_path))
    assert error_ledger.counts()[derivatives.READ_SITE] == before + 1


# -- open interest -------------------------------------------------------------------------------


def test_open_interest_takes_each_buckets_last_value_and_changes_against_the_previous(
    tmp_path: Path,
) -> None:
    """
    10:00:05 100, 10:00:50 120, 10:01:30 90 at 1m: 10:00 -> 120 (nothing earlier in the bounded
    read: change None), 10:01 -> 90 (90 - 120 = -30).
    """
    _catalog(tmp_path).write_data(
        [_oi("100", _TEN + 5 * _S), _oi("120", _TEN + 50 * _S), _oi("90", _TEN + 90 * _S)]
    )
    items, has_more = derivatives.open_interest_page(
        _LINEAR, _TEN + 10 * _MIN, 100, 60, catalog_path=str(tmp_path)
    )
    assert items == [
        {"t": _TEN_MS, "oi": "120", "oi_change": None},
        {"t": _TEN_MS + 60_000, "oi": "90", "oi_change": "-30"},
    ]
    assert has_more is False


def test_open_interest_gap_buckets_are_marked_and_the_change_spans_them(tmp_path: Path) -> None:
    """10:00 (100) and 10:03 (130): gap rows at 10:01 and 10:02, 10:03's change 130 - 100 = 30."""
    _catalog(tmp_path).write_data([_oi("100", _TEN + _S), _oi("130", _TEN + 3 * _MIN + _S)])
    items, _ = derivatives.open_interest_page(
        _LINEAR, _TEN + 10 * _MIN, 100, 60, catalog_path=str(tmp_path)
    )
    assert items == [
        {"t": _TEN_MS, "oi": "100", "oi_change": None},
        {"t": _TEN_MS + 60_000},
        {"t": _TEN_MS + 120_000},
        {"t": _TEN_MS + 180_000, "oi": "130", "oi_change": "30"},
    ]


def test_open_interest_first_kept_bucket_compares_against_the_dropped_one(tmp_path: Path) -> None:
    """Limit 1 over 10:00 (100) and 10:01 (90): 10:01 alone, its change -10, and has_more."""
    _catalog(tmp_path).write_data([_oi("100", _TEN + _S), _oi("90", _TEN + _MIN + _S)])
    items, has_more = derivatives.open_interest_page(
        _LINEAR, _TEN + 10 * _MIN, 1, 60, catalog_path=str(tmp_path)
    )
    assert items == [{"t": _TEN_MS + 60_000, "oi": "90", "oi_change": "-10"}]
    assert has_more is True


def test_open_interest_reads_one_bucket_before_the_window_for_the_first_change(
    tmp_path: Path,
) -> None:
    """
    At 1W one window is one bucket (the span cap is a week), so the previous week's 100 is the one
    extra bucket read: the kept week's 125 changes by +25. Weeks start Monday 00:00 UTC; day 20 000
    is a Friday (20 000 = 7 x 2 857 + 1, day 0 a Thursday), so its week started on day 19 996.
    """
    this_week = (_DAY0_MS - 4 * _DAY_MS) * 1_000_000
    last_week = this_week - 7 * 86_400 * _S
    _catalog(tmp_path).write_data([_oi("100", last_week + _S), _oi("125", this_week + _S)])
    items, has_more = derivatives.open_interest_page(
        _LINEAR, _T0, 10, 604_800, catalog_path=str(tmp_path)
    )
    assert items == [{"t": this_week // 1_000_000, "oi": "125", "oi_change": "25"}]
    assert has_more is True


def test_a_buckets_change_does_not_depend_on_where_the_page_was_cut(tmp_path: Path) -> None:
    """
    10:00 (100) then a four-minute gap, 10:05 (130), 10:06 (140), in two files. 10:05's change is
    130 - 100 = 30 mid-page (limit 10) and first-of-page (limit 2: the page is 10:05, 10:06, and
    the bucket right before it, 10:04, holds nothing), found by walking back over the gap.
    """
    catalog = _catalog(tmp_path)
    catalog.write_data([_oi("100", _TEN + _S)])
    catalog.write_data([_oi("130", _TEN + 5 * _MIN + _S), _oi("140", _TEN + 6 * _MIN + _S)])

    def change_at_1005(limit: int) -> str | None:
        items, _ = derivatives.open_interest_page(
            _LINEAR, _TEN + 10 * _MIN, limit, 60, catalog_path=str(tmp_path)
        )
        (item,) = (item for item in items if item["t"] == _TEN_MS + 5 * 60_000)
        return item["oi_change"]

    assert change_at_1005(10) == "30"
    assert change_at_1005(2) == "30"


def test_a_walk_back_window_is_sized_from_the_request() -> None:
    """
    `limit` buckets, at most a day of them, at least one: 1 s x 120 is two minutes (never a day of
    ticks), 1m x 10 000 is capped at a day, 1W x 120 is one whole week.
    """
    assert derivatives._bucket_window(1, 120)[0] == 120 * _S
    assert derivatives._bucket_window(60, 10_000)[0] == 86_400 * _S
    assert derivatives._bucket_window(604_800, 120)[0] == 604_800 * _S


def test_a_page_reads_at_most_the_span_cap(tmp_path: Path) -> None:
    """
    One OI a day for 10 days at 1D: a window is one day, the cap one week, so 7 buckets of a limit
    of 10 are served, and the 3 older ones are left for the next page.
    """
    catalog = _catalog(tmp_path)
    for day in range(10):
        catalog.write_data([_oi(str(100 + day), _T0 + day * 86_400 * _S + _S)])
    items, has_more = derivatives.open_interest_page(
        _LINEAR, _T0 + 10 * 86_400 * _S, 10, 86_400, catalog_path=str(tmp_path)
    )
    assert [item["oi"] for item in items] == [str(100 + day) for day in range(3, 10)]
    assert items[0]["oi_change"] == "1"  # day 3 (103) against day 2 (102), the extra read
    assert has_more is True


def test_a_failed_read_is_ledgered_and_raised(tmp_path: Path) -> None:
    """One OI row stored twice with different values: refused, counted, never one copy picked."""
    catalog = _catalog(tmp_path)
    catalog.write_data([_oi("100", _TEN)])
    catalog.write_data([_oi("101", _TEN), _oi("110", _TEN + _S)], skip_disjoint_check=True)
    before = error_ledger.counts().get(derivatives.READ_SITE, 0)
    with pytest.raises(derivatives.DerivativesReadError, match="stored twice"):
        derivatives.open_interest_page(_LINEAR, _TEN + _MIN, 10, 60, catalog_path=str(tmp_path))
    assert error_ledger.counts()[derivatives.READ_SITE] == before + 1


# -- mark, index and basis -----------------------------------------------------------------------


def _mark(value: str, ts: int) -> MarkPriceUpdate:
    return MarkPriceUpdate(_iid(), Price.from_str(value), ts, ts)


def _index(value: str, ts: int) -> IndexPriceUpdate:
    return IndexPriceUpdate(_iid(), Price.from_str(value), ts, ts)


def test_mark_index_basis_against_index_and_the_stores_close(tmp_path: Path) -> None:
    """
    10:00: mark 100.5 (the last of 100.2, 100.5), index 100.0, the store's close 100.4:
    basis_mi = (100.5 - 100.0) / 100.0 x 10^4 = 50; basis_ml = 0.1 / 100.4 x 10^4 = 9.9601593...
    """
    catalog = _catalog(tmp_path)
    catalog.write_data([_mark("100.2", _TEN + _S), _mark("100.5", _TEN + 30 * _S)])
    catalog.write_data([_index("100.0", _TEN + 20 * _S)])
    store = _store(tmp_path)
    sqlite_store.apply_seconds(store, _LINEAR, [_second(_TEN + 10 * _S, 1004)])
    store.close()
    items, has_more = derivatives.mark_index_page(
        _LINEAR,
        _TEN + _MIN,
        10,
        60,
        catalog_path=str(tmp_path),
        candles_dir=str(tmp_path / "candles"),
    )
    assert items == [
        {
            "t": _TEN_MS,
            "mark": "100.5",
            "index": "100.0",
            "basis_mi_bps": 50.0,
            "basis_ml_bps": pytest.approx(9.960159362549801, rel=1e-15),
        }
    ]
    assert has_more is False


def test_mark_index_basis_is_null_without_an_input(tmp_path: Path) -> None:
    """
    10:00 a mark without an index (and no candle store at all); 10:01 an index without a mark;
    10:03 the gap's far side, with 10:02 a gap row. No input: no basis, never 0.
    """
    catalog = _catalog(tmp_path)
    catalog.write_data([_mark("100.5", _TEN + _S), _mark("101.0", _TEN + 3 * _MIN + _S)])
    catalog.write_data([_index("100.0", _TEN + _MIN + _S), _index("100.0", _TEN + 3 * _MIN + _S)])
    items, _ = derivatives.mark_index_page(
        _LINEAR,
        _TEN + 10 * _MIN,
        10,
        60,
        catalog_path=str(tmp_path),
        candles_dir=str(tmp_path / "no-store"),
    )
    assert items == [
        {"t": _TEN_MS, "mark": "100.5", "index": None, "basis_mi_bps": None, "basis_ml_bps": None},
        {
            "t": _TEN_MS + 60_000,
            "mark": None,
            "index": "100.0",
            "basis_mi_bps": None,
            "basis_ml_bps": None,
        },
        {"t": _TEN_MS + 120_000},
        {
            "t": _TEN_MS + 180_000,
            "mark": "101.0",
            "index": "100.0",
            "basis_mi_bps": 100.0,  # (101 - 100) / 100 x 10^4
            "basis_ml_bps": None,
        },
    ]


def test_a_failed_candle_store_read_is_ledgered_and_raised(tmp_path: Path) -> None:
    """A store file that is not a database: `sqlite3.DatabaseError`, ledgered, never a 500 unnamed."""
    _catalog(tmp_path).write_data([_mark("100.5", _TEN + _S)])
    (tmp_path / "candles").mkdir()
    (tmp_path / "candles" / "candles_bybit.db").write_bytes(b"not a sqlite file " * 64)
    before = error_ledger.counts().get(derivatives.READ_SITE, 0)
    with pytest.raises(derivatives.DerivativesReadError, match="not a database"):
        derivatives.mark_index_page(
            _LINEAR,
            _TEN + _MIN,
            10,
            60,
            catalog_path=str(tmp_path),
            candles_dir=str(tmp_path / "candles"),
        )
    assert error_ledger.counts()[derivatives.READ_SITE] == before + 1


def test_mark_index_at_a_width_no_stored_width_tiles_has_a_null_mark_last_basis(
    tmp_path: Path,
) -> None:
    """
    At 1 s the store (which holds 10:00:10's close 100.4) has no 1 s bars and none tile one: the
    10:00:10 bucket serves mark 100.5 and index 100.0 (basis_mi 50) with basis_ml null, not a 400.
    """
    catalog = _catalog(tmp_path)
    catalog.write_data([_mark("100.5", _TEN + 10 * _S)])
    catalog.write_data([_index("100.0", _TEN + 10 * _S)])
    store = _store(tmp_path)
    sqlite_store.apply_seconds(store, _LINEAR, [_second(_TEN + 10 * _S, 1004)])
    store.close()
    items, _ = derivatives.mark_index_page(
        _LINEAR,
        _TEN + _MIN,
        10,
        1,
        catalog_path=str(tmp_path),
        candles_dir=str(tmp_path / "candles"),
    )
    assert items == [
        {
            "t": _TEN_MS + 10_000,
            "mark": "100.5",
            "index": "100.0",
            "basis_mi_bps": 50.0,
            "basis_ml_bps": None,
        }
    ]


# -- liquidations --------------------------------------------------------------------------------


def test_liquidations_page_serves_the_stored_rows_with_their_price_kind(tmp_path: Path) -> None:
    """Three liquidations, limit 2: the two newest before the cursor, the archive plus the tail."""
    _catalog(tmp_path).write_data([_liq("a", _TEN, 4), _liq("b", _TEN + _S, 6)])
    tail = [_liq("b", _TEN + _S, 6), _liq("c", _TEN + 2 * _S, 5, sp=4, side=LiquidatedSide.SHORT)]
    items, has_more = derivatives.liquidations_page(
        _LINEAR,
        _TEN + _MIN,
        2,
        catalog_path=str(tmp_path),
        recent_liquidations=lambda _i, a, b: [r for r in tail if a <= r.ts_event <= b],
    )
    assert items == [
        {
            "side": "long",
            "size_units": 6,
            "price_units": 1_000_000,
            "price_precision": 1,
            "size_precision": 3,
            "venue_event_id": "b",
            "ts_event": _TEN + _S,
            "ts_init": _TEN + _S,
            "price_kind": "bankruptcy",
            # 6 x 1 000 000 at 10^-(1 + 3)
            "notional_units": 6_000_000,
            "notional_precision": 4,
        },
        {
            "side": "short",
            "size_units": 5,
            "price_units": 1_000_000,
            "price_precision": 1,
            "size_precision": 4,
            "venue_event_id": "c",
            "ts_event": _TEN + 2 * _S,
            "ts_init": _TEN + 2 * _S,
            "price_kind": "bankruptcy",
            # 5 x 1 000 000 at 10^-(1 + 4)
            "notional_units": 5_000_000,
            "notional_precision": 5,
        },
    ]
    assert has_more is True


def test_liquidations_sharing_a_time_are_never_split_across_pages(tmp_path: Path) -> None:
    """
    "a" at 10:00:00, then a cascade "b", "c", "d" all at 10:00:01. Limit 2 would cut the cascade
    and the next page (`ts_event < 10:00:01`) could never reach the cut-off one, so the first page
    keeps all three (one over the limit) and the second holds "a": all four, each once.
    """
    _catalog(tmp_path).write_data(
        [
            _liq("a", _TEN, 1),
            _liq("b", _TEN + _S, 1),
            _liq("c", _TEN + _S, 1),
            _liq("d", _TEN + _S, 1),
        ]
    )

    def page(before_ns: int) -> tuple[list[str], bool]:
        items, has_more = derivatives.liquidations_page(
            _LINEAR, before_ns, 2, catalog_path=str(tmp_path), recent_liquidations=_no_tail
        )
        return [item["venue_event_id"] for item in items], has_more

    assert page(_TEN + _MIN) == (["b", "c", "d"], True)
    assert page(_TEN + _S) == (["a"], False)


def _bars(root: Path, iid: str) -> list[dict]:
    items, _ = derivatives.liquidation_bars(
        iid,
        _TEN + 10 * _MIN,
        10,
        60,
        catalog_path=str(root),
        candles_dir=str(root / "candles"),
    )
    return items


def test_liquidation_bars_serve_an_untraded_bucket_and_sum_the_notional(tmp_path: Path) -> None:
    """
    The feed starts at 10:00 (liquidation "a"): 10:00 traded and liquidated 0.004 at 100000.0
    (notional 4 x 1 000 000 = 4 000 000 units of 10^-4 = 400 USDT); 10:01 only liquidations, no
    trade (D-162: served): "b" 0.006 (6 x 10^6 at 10^-4) and "c" 0.0005 (5 x 10^6 at 10^-5), at
    the finest 10^-5: 60 000 000 + 5 000 000 = 65 000 000 (650 USDT), long 0.0065 = 65 at size
    precision 4; 10:02 traded, none landed: a known 0.
    """
    liqs = [_liq("a", _TEN, 4), _liq("b", _TEN + 61 * _S, 6), _liq("c", _TEN + 62 * _S, 5, sp=4)]
    _catalog(tmp_path).write_data(liqs)
    store = _store(tmp_path)
    sqlite_store.apply_liquidations(store, _LINEAR, liqs)
    sqlite_store.apply_seconds(
        store, _LINEAR, [_second(_TEN + _S, 1004), _second(_TEN + 121 * _S, 1004)]
    )
    store.close()
    assert _bars(tmp_path, _LINEAR) == [
        {
            "t": _TEN_MS,
            "long_v": 4,
            "short_v": 0,
            "n": 1,
            "size_precision": 3,
            "notional_units": 4_000_000,
            "notional_precision": 4,
            "long_notional_units": 4_000_000,
            "short_notional_units": 0,
        },
        {
            "t": _TEN_MS + 60_000,
            "long_v": 65,
            "short_v": 0,
            "n": 2,
            "size_precision": 4,
            "notional_units": 65_000_000,
            "notional_precision": 5,
            "long_notional_units": 65_000_000,
            "short_notional_units": 0,
        },
        {
            "t": _TEN_MS + 120_000,
            "long_v": 0,
            "short_v": 0,
            "n": 0,
            "size_precision": 3,
            "notional_units": 0,
            "notional_precision": 4,
            "long_notional_units": 0,
            "short_notional_units": 0,
        },
    ]


def test_the_live_edge_notional_matches_the_stores_count_or_is_null(tmp_path: Path) -> None:
    """
    Capture archives a flush before the candle store applies it, so the store's count is the
    archive's rows of the bucket. The store counted "a" (10:00) and "b" (10:01); the archive also
    holds "c" (10:01:30), flushed but not applied yet (the live-edge lag). 10:00: the archive's 1
    row is the counted one, 4 x 1 000 000 = 4 000 000. 10:01: 2 archived rows against n = 1, so
    which one was counted is unknown: None, never a partial sum. The live bus's unflushed tail is
    not consulted: the store never counts a row the archive does not hold.
    """
    counted = [_liq("a", _TEN, 4), _liq("b", _TEN + 61 * _S, 6)]
    _catalog(tmp_path).write_data([*counted, _liq("c", _TEN + 90 * _S, 5)])
    store = _store(tmp_path)
    sqlite_store.apply_liquidations(store, _LINEAR, counted)
    store.close()
    bars = _bars(tmp_path, _LINEAR)
    assert [(bar["n"], bar["notional_units"]) for bar in bars] == [(1, 4_000_000), (1, None)]
    assert [(bar["long_notional_units"], bar["short_notional_units"]) for bar in bars] == [
        (4_000_000, 0),
        (None, None),  # null exactly when the total is
    ]


def test_a_partially_null_stored_liquidation_group_is_ledgered_and_raised(tmp_path: Path) -> None:
    """
    10:00's row has `liq_n` 1 but a NULL `liq_long_v`: corrupt, so ledgered and raised as
    `DerivativesReadError` (the route's 500), never an unledgered `TypeError`.
    """
    liq = _liq("a", _TEN, 4)
    _catalog(tmp_path).write_data([liq])
    store = _store(tmp_path)
    sqlite_store.apply_liquidations(store, _LINEAR, [liq])
    store.execute("UPDATE candles SET liq_long_v = NULL WHERE bar_seconds = 60")
    store.commit()
    store.close()
    before = error_ledger.counts().get(derivatives.READ_SITE, 0)
    with pytest.raises(derivatives.DerivativesReadError, match="partially null"):
        _bars(tmp_path, _LINEAR)
    assert error_ledger.counts()[derivatives.READ_SITE] == before + 1


def test_a_known_zero_count_beside_raw_rows_has_no_notional(tmp_path: Path) -> None:
    """
    The store's 10:00 is a known 0 (the feed started at 09:00 with "p", 10:00 traded), but the
    archive holds "x" at 10:00:30: store and archive disagree, so the notional is None, never 0.
    """
    store = _store(tmp_path)
    sqlite_store.apply_liquidations(store, _LINEAR, [_liq("p", _TEN - 3600 * _S, 1)])
    sqlite_store.apply_seconds(store, _LINEAR, [_second(_TEN + _S, 1004)])
    store.close()
    _catalog(tmp_path).write_data([_liq("x", _TEN + 30 * _S, 2)])
    (bar,) = _bars(tmp_path, _LINEAR)
    notionals = (
        "notional_units",
        "notional_precision",
        "long_notional_units",
        "short_notional_units",
    )
    assert (bar["n"], *(bar[key] for key in notionals)) == (0, None, None, None, None)


# -- liquidation bars of a width the store does not fold (1W from 1D) -----------------------------

_WEEK = 604_800
_W2_MS = _DAY0_MS + 3 * _DAY_MS  # day 20 003, a Monday: the week after day 20 000's
_W2 = _W2_MS * 1_000_000
_DAY = 86_400 * _S


def _weekly_store(root: Path, traded_days: range = range(7)) -> None:
    """
    Store a feed starting at 00:00 on the Wednesday of week 1 ("p"), and week 2's "b" on Tuesday
    (0.004 long at size precision 3), "c" on Thursday (0.0005 short at size precision 4) and a
    trade on each of `traded_days` (0 = Monday) of week 2, so every day of it is observed by
    default (a day without a liquidation a known 0). The store folds them into 1D rows; nothing at
    1W.
    """
    liqs = [
        _liq("p", _W2 - 5 * _DAY, 1),  # at midnight, so its day is known (not straddled)
        _liq("b", _W2 + _DAY + _S, 4),
        _liq("c", _W2 + 3 * _DAY + _S, 5, sp=4, side=LiquidatedSide.SHORT),
    ]
    _catalog(root).write_data(liqs)
    store = _store(root)
    sqlite_store.apply_liquidations(store, _LINEAR, liqs)
    seconds = [_second(_W2 + day * _DAY + 2 * _S, 1004) for day in traded_days]
    sqlite_store.apply_seconds(store, _LINEAR, seconds)
    store.close()


def _weekly_bars(root: Path, before_ns: int) -> tuple[list[dict], bool]:
    return derivatives.liquidation_bars(
        _LINEAR,
        before_ns,
        10,
        _WEEK,
        catalog_path=str(root),
        candles_dir=str(root / "candles"),
    )


def test_a_weekly_liquidation_bar_is_the_sum_of_its_stored_days(tmp_path: Path) -> None:
    """
    Week 2 from its seven 1D rows (Tuesday's and Thursday's liquidations, five known-0 days), sizes
    at the finest precision 4: long 4 x 10 = 40, short 5, n 2. Notional at the finest 10^-5: "b" 4 x 1 000 000 x 10 = 40 000 000, "c" 5 x 1 000 000 =
    5 000 000, 45 000 000 in all: long ("b") 40 000 000, short ("c") 5 000 000. The 1W span cap is one week, so the page is that one bar, and
    week 1's rows are older (has_more).
    """
    _weekly_store(tmp_path)
    items, has_more = _weekly_bars(tmp_path, _W2 + 7 * _DAY)
    assert items == [
        {
            "t": _W2_MS,
            "long_v": 40,
            "short_v": 5,
            "n": 2,
            "size_precision": 4,
            "notional_units": 45_000_000,
            "notional_precision": 5,
            "long_notional_units": 40_000_000,
            "short_notional_units": 5_000_000,
        }
    ]
    assert has_more is True


def test_a_weekly_bar_straddling_the_feed_start_is_null(tmp_path: Path) -> None:
    """
    Week 1 with every day stored and known (each traded; Monday's and Tuesday's `liq_*` set to a
    0 by hand, so neither a missing nor a null day nulls it): the feed started that Wednesday
    ("p"), so the week straddles it and is unknown. The control moves the stored feed start to
    the Monday: the same rows then sum to "p" alone, so the feed-start rule is what nulled it.
    """
    week1 = _W2 - 7 * _DAY
    feed_start = _liq("p", _W2 - 5 * _DAY, 1)  # Wednesday 00:00, so that day is known
    _catalog(tmp_path).write_data([feed_start])
    store = _store(tmp_path)
    sqlite_store.apply_liquidations(store, _LINEAR, [feed_start])
    days = [week1 + day * _DAY for day in range(14)]  # both weeks, ascending (the watermark)
    sqlite_store.apply_seconds(store, _LINEAR, [_second(day + 2 * _S, 1004) for day in days])
    store.execute(
        "UPDATE candles SET liq_long_v = 0, liq_short_v = 0, liq_n = 0 "
        "WHERE bar_seconds = 86400 AND liq_n IS NULL AND t >= ? AND t < ?",
        (week1 // 1_000_000, _W2_MS),
    )
    store.commit()
    store.close()
    (bar,), _ = _weekly_bars(tmp_path, _W2)
    assert (bar["t"], bar["long_v"], bar["short_v"], bar["n"], bar["notional_units"]) == (
        _W2_MS - 7 * _DAY_MS,
        None,
        None,
        None,
        None,
    )
    store = _store(tmp_path)
    store.execute("UPDATE liquidation_feed_since SET since_ns = ?", (week1,))
    store.commit()
    store.close()
    (bar,), _ = _weekly_bars(tmp_path, _W2)
    assert bar["n"] == 1


def test_a_weekly_bar_with_one_unknown_day_is_null(tmp_path: Path) -> None:
    """Week 2's Wednesday 1D row unknown (a file not migrated, null `liq_*`): the week is unknown."""
    _weekly_store(tmp_path)
    store = _store(tmp_path)
    store.execute(
        "UPDATE candles SET liq_long_v = NULL, liq_short_v = NULL, liq_n = NULL "
        "WHERE bar_seconds = 86400 AND t = ?",
        (_W2_MS + 2 * _DAY_MS,),
    )
    store.commit()
    store.close()
    (bar,), _ = _weekly_bars(tmp_path, _W2 + 7 * _DAY)
    assert (bar["long_v"], bar["short_v"], bar["n"], bar["notional_units"]) == (None,) * 4


def test_a_weekly_bar_missing_one_stored_day_is_null(tmp_path: Path) -> None:
    """
    Week 2 with Friday never observed (no 1D row: a collector outage) while Saturday and Sunday
    are: summing the other six would read the outage as 0 liquidations, so the week is unknown.
    """
    _weekly_store(tmp_path)
    store = _store(tmp_path)
    store.execute(
        "DELETE FROM candles WHERE bar_seconds = 86400 AND t = ?", (_W2_MS + 4 * _DAY_MS,)
    )
    store.commit()
    store.close()
    (bar,), _ = _weekly_bars(tmp_path, _W2 + 7 * _DAY)
    assert (bar["t"], bar["long_v"], bar["short_v"], bar["n"], bar["notional_units"]) == (
        _W2_MS,
        None,
        None,
        None,
        None,
    )


def test_the_forming_week_sums_its_days_up_to_the_stores_live_edge(tmp_path: Path) -> None:
    """
    The store's newest 1D row is week 2's Thursday (Friday..Sunday have not happened yet): the
    forming week is complete so far, Monday..Thursday summed as in the full week (40, 5, 2).
    """
    _weekly_store(tmp_path, traded_days=range(4))
    (bar,), _ = _weekly_bars(tmp_path, _W2 + 7 * _DAY)
    assert (bar["long_v"], bar["short_v"], bar["n"], bar["notional_units"]) == (
        40,
        5,
        2,
        45_000_000,
    )


@pytest.mark.parametrize("bar_seconds", [1, 59, 90])
def test_a_width_no_stored_width_tiles_is_refused(tmp_path: Path, bar_seconds: int) -> None:
    with pytest.raises(derivatives.UnsupportedBarSeconds, match="not composable"):
        derivatives.liquidation_bars(
            _LINEAR,
            _TEN,
            10,
            bar_seconds,
            catalog_path=str(tmp_path),
            candles_dir=str(tmp_path),
        )


def test_the_stored_width_of_each_offered_width() -> None:
    """Stored widths are their own; 1W tiles from 1D (Monday is a day boundary), 10m from 5m."""
    assert [stored_bar(b) for b in (60, 3600, 86_400, 604_800, 600, 90, 1)] == [
        60,
        3600,
        86_400,
        86_400,
        300,
        None,
        None,
    ]


def test_liquidation_bars_of_an_instrument_without_the_feed_are_null_never_zero(
    tmp_path: Path,
) -> None:
    """A Hyperliquid perp's traded minute: every liquidation field null; no event row at all."""
    store = sqlite_store.connect_rw(
        sqlite_store.db_path_for_venue(tmp_path / "candles", "HYPERLIQUID")
    )
    sqlite_store.apply_seconds(store, _HL, [_second(_TEN + _S, 1004)])
    store.close()
    (bar,) = _bars(tmp_path, _HL)
    keys = (
        "long_v",
        "short_v",
        "n",
        "notional_units",
        "long_notional_units",
        "short_notional_units",
    )
    assert {k: bar[k] for k in keys} == dict.fromkeys(keys)
    assert derivatives.liquidations_page(
        _HL, _TEN + _MIN, 10, catalog_path=str(tmp_path), recent_liquidations=_no_tail
    ) == ([], False)


def test_liquidation_bars_jump_a_gap_wider_than_the_window(tmp_path: Path) -> None:
    """
    Limit 2 at 1m is a 2-minute window: the only rows (10:00, 10:01) are 9 min before the cursor,
    so the page jumps to them; one older row (09:59) is left: has_more.
    """
    store = _store(tmp_path)
    sqlite_store.apply_seconds(
        store, _LINEAR, [_second(_TEN + k * _MIN + _S, 1004) for k in (-1, 0, 1)]
    )
    store.close()
    items, has_more = derivatives.liquidation_bars(
        _LINEAR,
        _TEN + 10 * _MIN,
        2,
        60,
        catalog_path=str(tmp_path),
        candles_dir=str(tmp_path / "candles"),
    )
    assert [item["t"] for item in items] == [_TEN_MS, _TEN_MS + 60_000]
    assert has_more is True


# -- spot ----------------------------------------------------------------------------------------


def _refuse(*_args: object) -> list[Liquidation]:
    raise AssertionError("a spot page read something")


def test_every_page_of_a_spot_id_is_empty_and_reads_nothing(tmp_path: Path) -> None:
    missing = str(tmp_path / "nothing-here")
    pages = [
        derivatives.funding_page(_SPOT, _TEN, 10, catalog_path=missing),
        derivatives.open_interest_page(_SPOT, _TEN, 10, 60, catalog_path=missing),
        derivatives.mark_index_page(_SPOT, _TEN, 10, 60, catalog_path=missing, candles_dir=missing),
        derivatives.liquidations_page(
            _SPOT, _TEN, 10, catalog_path=missing, recent_liquidations=_refuse
        ),
        derivatives.liquidation_bars(
            _SPOT,
            _TEN,
            10,
            60,
            catalog_path=missing,
            candles_dir=missing,
        ),
    ]
    assert pages == [([], False)] * 5


# -- review findings: sparse walks, exact text, mark/index copies ----------------------------------


def test_a_sparse_series_jumps_a_gap_inside_one_file_in_a_bounded_number_of_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Open interest at 10:00:00 (100) and 14:00:00 (130) in one file, a 1 s page of 1 bucket before
    14:00:01: the page is 14:00:00 (130), its change against 10:00:00 is 30. The change's walk
    back crosses four hours inside that one file: window by window it would read the file 14 400
    times; it reads it three times (the page, the empty window before it, the window it jumps to).
    """
    _catalog(tmp_path).write_data([_oi("100", _TEN), _oi("130", _TEN + 4 * 3600 * _S)])
    reads: list[tuple[int, int]] = []
    real = derivatives.catalog_files.query_open_interest

    def counted(
        path: str, iid: str, start_ns: int, end_ns: int, *, on_foreign: object
    ) -> list[OpenInterest]:
        assert on_foreign is error_ledger.record  # a stray file name is ledgered, then skipped
        reads.append((start_ns, end_ns))
        return real(path, iid, start_ns, end_ns)

    monkeypatch.setattr(derivatives.catalog_files, "query_open_interest", counted)
    items, has_more = derivatives.open_interest_page(
        _LINEAR, _TEN + 4 * 3600 * _S + _S, 1, 1, catalog_path=str(tmp_path)
    )
    assert items == [{"t": _TEN_MS + 4 * 3_600_000, "oi": "130", "oi_change": "30"}]
    assert has_more is True
    assert len(reads) == 3


def test_a_foreign_file_name_in_a_derivatives_leaf_is_ledgered_and_the_page_served(
    tmp_path: Path,
) -> None:
    """DW-181's policy on Story 33.4's readers: a stray `*.parquet` costs a ledger line, no page."""
    _catalog(tmp_path).write_data([_oi("100", _TEN), _oi("130", _TEN + 4 * 3600 * _S)])
    before_ns = _TEN + 4 * 3600 * _S + _S
    expected = derivatives.open_interest_page(_LINEAR, before_ns, 1, 1, catalog_path=str(tmp_path))
    leaf = tmp_path / "data" / catalog_files.OPEN_INTEREST_DIRNAME / _LINEAR
    (real,) = leaf.glob("*.parquet")
    shutil.copy(real, leaf / "notes.parquet")
    error_ledger.reset()
    page = derivatives.open_interest_page(_LINEAR, before_ns, 1, 1, catalog_path=str(tmp_path))
    assert page == expected
    assert set(error_ledger.counts()) == {"catalog.foreign_file"}
    error_ledger.reset()


def test_open_interest_and_funding_text_is_positional_never_scientific(tmp_path: Path) -> None:
    """
    `str(Decimal("0.00000012"))` is `1.2E-7` and a zero change `0E-8`: served as `0.00000012` and
    `0.00000000`. The stored funding rate is `rust_decimal`'s `"1.2E-7"` text: served positional.
    """
    catalog = _catalog(tmp_path)
    catalog.write_data([_oi("0.00000012", _TEN + _S), _oi("0.00000012", _TEN + _MIN + _S)])
    catalog.write_data([_funding("0.00000012", _TEN)])
    items, _ = derivatives.open_interest_page(
        _LINEAR, _TEN + 10 * _MIN, 10, 60, catalog_path=str(tmp_path)
    )
    assert items == [
        {"t": _TEN_MS, "oi": "0.00000012", "oi_change": None},
        {"t": _TEN_MS + 60_000, "oi": "0.00000012", "oi_change": "0.00000000"},
    ]
    (funding,), _ = derivatives.funding_page(_LINEAR, _TEN + _MIN, 10, catalog_path=str(tmp_path))
    assert funding["rate"] == "0.00000012"


def _mark_index_items(root: Path) -> list[dict]:
    items, _ = derivatives.mark_index_page(
        _LINEAR,
        _TEN + _MIN,
        10,
        60,
        catalog_path=str(root),
        candles_dir=str(root / "no-store"),
    )
    return items


_PriceRow = Callable[[str, int], MarkPriceUpdate | IndexPriceUpdate]


@pytest.mark.parametrize("make", [_mark, _index])
def test_a_mark_or_index_row_stored_twice_is_read_once_at_its_own_precision(
    tmp_path: Path, make: _PriceRow
) -> None:
    """
    10:00:01 at 100.50 in a minute file and again in a second file beside 10:00:30's 100.70: the
    bucket's last is 100.70, its trailing zero kept (the file's precision label, 2).
    """
    catalog = _catalog(tmp_path)
    catalog.write_data([make("100.50", _TEN + _S)])
    catalog.write_data(
        [make("100.50", _TEN + _S), make("100.70", _TEN + 30 * _S)],
        skip_disjoint_check=True,
    )
    (item,) = _mark_index_items(tmp_path)
    key = "mark" if make is _mark else "index"
    assert item[key] == "100.70"


@pytest.mark.parametrize("make", [_mark, _index])
def test_a_mark_or_index_row_stored_twice_with_different_values_is_ledgered_and_raised(
    tmp_path: Path, make: _PriceRow
) -> None:
    catalog = _catalog(tmp_path)
    catalog.write_data([make("100.50", _TEN + _S)])
    catalog.write_data(
        [make("100.90", _TEN + _S), make("100.70", _TEN + 30 * _S)],
        skip_disjoint_check=True,
    )
    before = error_ledger.counts().get(derivatives.READ_SITE, 0)
    with pytest.raises(derivatives.DerivativesReadError, match="stored twice"):
        _mark_index_items(tmp_path)
    assert error_ledger.counts()[derivatives.READ_SITE] == before + 1
