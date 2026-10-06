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
Story 33.3: the archive-side candle page (`_parquet_page`) folds an instrument's archived
liquidations plus the live tail, each venue event once, null for a bucket starting before the
feed start -- the earliest of the first archived liquidation and the tail's (audit D-160, review
loop 2) -- and leaves a no-feed instrument's null; so does the technicals fallback.
A real catalog, no candle store (so every bar comes from Parquet).
"""

from pathlib import Path

import pytest
from candles.infrastructure import sqlite_store
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.tests.snapshot_factory import make_snapshot

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views import catalog_reads
from views import chart_series
from views import ranking_columns


_LINEAR = "BTCUSDT-LINEAR.BYBIT"
_SPOT = "BTCUSDT-SPOT.BYBIT"
_S = 1_000_000_000
_T0 = 20_000 * 86_400 * _S  # a UTC midnight long closed


def _write(root: str, iid: str, liquidations: list[Liquidation]) -> None:
    catalog = ParquetDataCatalog(root)
    catalog.write_data(
        [
            make_snapshot(
                iid,
                bid_prices=[99.0],
                bid_sizes=[1.0],
                ask_prices=[101.0],
                ask_sizes=[1.0],
                buy_volume=0.002,
                sell_volume=0.001,
                buy_count=1,
                sell_count=1,
                open_price=100.0,
                high_price=100.0,
                low_price=100.0,
                close_price=100.0,
                ts_event=_T0 + s * _S,
                price_precision=1,
                size_precision=3,
            )
            for s in (1, 61)
        ]
    )
    if liquidations:
        catalog.write_data(liquidations)


def _liq(key: str, s: int, size: int, side: LiquidatedSide = LiquidatedSide.LONG) -> Liquidation:
    ts = _T0 + s * _S
    return Liquidation(InstrumentId.from_str(_LINEAR), side, size, 1000, 1, 3, key, ts, ts)


def _page(root: Path, iid: str, tail: list[Liquidation]) -> list[dict]:
    kept, _ = chart_series.candle_page(
        iid,
        _T0 + 120 * _S,
        10,
        60,
        catalog_path=str(root),
        candles_dir=str(root / "no-candle-store"),
        recent_rows=lambda *_: [],
        recent_liquidations=lambda _iid, a, b: [r for r in tail if a <= r.ts_event <= b],
    )
    return kept


def test_the_archive_page_folds_the_archived_liquidations_plus_the_tail_once(
    tmp_path: Path,
) -> None:
    archived = _liq("a", 0, 4)  # the feed start, exactly minute 0's start: every bar known
    _write(str(tmp_path), _LINEAR, [archived])
    tail = [archived, _liq("b", 62, 6, LiquidatedSide.SHORT)]  # "a" flushed already: once
    first, second = _page(tmp_path, _LINEAR, tail)
    assert (first["liq_long_v"], first["liq_short_v"], first["liq_n"]) == (4, 0, 1)
    assert (second["liq_long_v"], second["liq_short_v"], second["liq_n"]) == (0, 6, 1)
    assert (first["buy_v"], first["sell_v"], first["pv"]) == (2, 1, 1000 * 3)
    assert first["source"] == "raw_1s"


def test_the_archive_page_of_an_instrument_without_the_feed_has_null_liquidations(
    tmp_path: Path,
) -> None:
    _write(str(tmp_path), _SPOT, [])
    bars = _page(tmp_path, _SPOT, [])
    assert [b["liq_n"] for b in bars] == [None, None]
    assert [b["buy_v"] for b in bars] == [2, 2]


def test_the_archive_page_is_null_before_the_feeds_first_archived_liquidation(
    tmp_path: Path,
) -> None:
    """
    The first archived liquidation is at +60 s: minute 0 starts before it and is unknown, null,
    never 0; minute 1 starts at it and counts it. The flow is known in both.
    """
    _write(str(tmp_path), _LINEAR, [_liq("first", 60, 6)])
    first, second = _page(tmp_path, _LINEAR, [])
    assert (first["liq_long_v"], first["liq_short_v"], first["liq_n"]) == (None, None, None)
    assert (second["liq_long_v"], second["liq_n"]) == (6, 1)
    assert first["buy_v"] == second["buy_v"] == 2


def test_the_archive_page_of_a_feed_with_nothing_archived_or_in_the_tail_is_null(
    tmp_path: Path,
) -> None:
    _write(str(tmp_path), _LINEAR, [])
    bars = _page(tmp_path, _LINEAR, [])
    assert [b["liq_n"] for b in bars] == [None, None]


def test_a_tail_liquidation_dates_the_feed_and_a_straddled_minute_stays_null(
    tmp_path: Path,
) -> None:
    """Nothing archived, the tail's liquidation at +62 s: minute 1 straddles it, so both null."""
    _write(str(tmp_path), _LINEAR, [])
    bars = _page(tmp_path, _LINEAR, [_liq("tail", 62, 6)])
    assert [b["liq_n"] for b in bars] == [None, None]


def test_a_tail_row_older_than_the_archive_bound_moves_it_without_failing(
    tmp_path: Path,
) -> None:
    """
    Archived first liquidation +61 s (minute 1 would straddle it), a not yet flushed one in the
    tail at +60 s: the start moves to +60 s, no error, and minute 1 counts both.
    """
    _write(str(tmp_path), _LINEAR, [_liq("archived", 61, 4)])
    first, second = _page(tmp_path, _LINEAR, [_liq("tail", 60, 6)])
    assert first["liq_n"] is None
    assert (second["liq_long_v"], second["liq_n"]) == (10, 2)


def test_the_technicals_fallback_folds_the_bounded_liquidations(tmp_path: Path) -> None:
    """
    `ranking_columns._read_candles` (a coin the store does not hold) carries the same `liq_*` as
    the page: null before the first archived liquidation (+60 s), counted from it.
    """
    _write(str(tmp_path), _LINEAR, [_liq("first", 60, 6)])
    bars = ranking_columns._read_candles(
        _LINEAR, 60, _T0 + 120 * _S, str(tmp_path), str(tmp_path / "no-candle-store")
    )
    assert [(b["t"], b["liq_n"]) for b in bars] == [
        (_T0 // 1_000_000, None),
        (_T0 // 1_000_000 + 60_000, 1),
    ]


def _store_feed_since(candles_dir: Path, since_ns: int) -> None:
    """Write a candle store holding only `_LINEAR`'s persisted feed start (no candles)."""
    db = sqlite_store.connect_rw(sqlite_store.db_path_for_venue(candles_dir, "BYBIT"))
    db.execute("INSERT INTO liquidation_feed_since VALUES(?, ?)", (_LINEAR, since_ns))
    db.commit()
    db.close()


def _no_archive_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    def scan(catalog_path: str, iid: str) -> int | None:
        raise AssertionError("the archive was scanned although the store has a feed start")

    monkeypatch.setattr(catalog_reads, "liquidation_feed_since_ns", scan)


def test_the_page_takes_the_stores_feed_start_without_scanning_the_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The archive's first liquidation is at +60 s (alone it would leave minute 0 null), but the
    store's persisted start is minute 0's start (the live sink saw the feed then): minute 0 reads a
    known 0, minute 1 counts the liquidation, and the archive is never scanned.
    """
    _write(str(tmp_path), _LINEAR, [_liq("first", 60, 6)])
    _store_feed_since(tmp_path / "candles", _T0)
    _no_archive_scan(monkeypatch)
    kept, _ = chart_series.candle_page(
        _LINEAR,
        _T0 + 120 * _S,
        10,
        60,
        catalog_path=str(tmp_path),
        candles_dir=str(tmp_path / "candles"),
        recent_rows=lambda *_: [],
        recent_liquidations=lambda *_: [],
    )
    assert [(b["liq_long_v"], b["liq_n"]) for b in kept] == [(0, 0), (6, 1)]


def test_the_technicals_fallback_takes_the_stores_feed_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(str(tmp_path), _LINEAR, [_liq("first", 60, 6)])
    _store_feed_since(tmp_path / "candles", _T0)
    _no_archive_scan(monkeypatch)
    bars = ranking_columns._read_candles(
        _LINEAR, 60, _T0 + 120 * _S, str(tmp_path), str(tmp_path / "candles")
    )
    assert [b["liq_n"] for b in bars] == [0, 1]


def test_with_no_store_row_the_archive_scan_is_the_fallback(tmp_path: Path) -> None:
    """A store without the id's row: the archive's first liquidation (+60 s) bounds the page."""
    _write(str(tmp_path), _LINEAR, [_liq("first", 60, 6)])
    _store_feed_since(tmp_path / "candles", _T0)
    assert (
        catalog_reads.liquidation_feed_start(
            str(tmp_path), str(tmp_path / "candles"), "ETHUSDT-LINEAR.BYBIT"
        )
        is None
    )  # no row and nothing archived for it
    assert (
        catalog_reads.liquidation_feed_start(
            str(tmp_path), str(tmp_path / "no-candle-store"), _LINEAR
        )
        == _T0 + 60 * _S
    )
