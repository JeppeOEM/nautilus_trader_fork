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
Self-check: bucketing per-second rows produces correct OHLC per time bucket.

Restated over the single fold in Story 24.1. The claims these tests carry were written against
the former candles module's `aggregate_ohlc`/`candle_dicts_from_snapshots`; both are retired, and
`bars_from_rows`/`forming_bar` answer the same questions with `candles.domain.fold.fold_arrays`.
`build_candles` (a flat list of trade prices -> bars) has no successor here at all: trades -> second
is `kernel.fold.fold_trades` (tested in `kernel/tests/test_fold.py`) and second -> bars is this
fold, so no path folds raw trades straight into a wide bar any more.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from kernel.catalog_files import query_second_ohlc
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import LegacySnapshotLayoutError
from kernel.tests.snapshot_factory import make_snapshot

from candles.application.forming import bars_from_rows
from candles.application.forming import forming_bar
from candles.application.queries import candle_dicts_for_window
from candles.domain.candle import is_valid_candle
from candles.domain.fold import MAX_BAR_SECONDS
from candles.domain.fold import bucket_start_ms
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


IID = "BTC-USD-PERP.DYDX"
_SECOND = 1_000_000_000


def _snap(
    ts_event: int, close_price: float | None, buy_volume: float = 1.0, sell_volume: float = 0.5
) -> SimpleNamespace:
    """Build a DydxSecondSnapshot-shaped stand-in (attribute access, as the real class has)."""
    return SimpleNamespace(
        ts_event=ts_event,
        open_price=close_price,
        high_price=close_price,
        low_price=close_price,
        close_price=close_price,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
    )


def _ohlc_second(
    ts_event: int, o: float, h: float, low: float, c: float, volume: float
) -> SimpleNamespace:
    return SimpleNamespace(
        ts_event=ts_event,
        open_price=o,
        high_price=h,
        low_price=low,
        close_price=c,
        buy_volume=volume,
        sell_volume=0.0,
    )


def test_buckets_two_periods_with_correct_ohlc() -> None:
    """Open of the first traded second, high/low across the bucket, close of the last, volume summed."""
    rows = [
        _ohlc_second(0, 100.0, 105.0, 100.0, 105.0, 1.0),
        _ohlc_second(10 * _SECOND, 105.0, 105.0, 95.0, 95.0, 2.0),
        _ohlc_second(30 * _SECOND, 95.0, 102.0, 95.0, 102.0, 7.0),
        _ohlc_second(61 * _SECOND, 200.0, 200.0, 200.0, 200.0, 5.0),  # bucket 1 starts at 60s
        _ohlc_second(90 * _SECOND, 200.0, 200.0, 190.0, 190.0, 6.0),
    ]

    first, second = bars_from_rows(rows, 60)

    assert first["t"] == 0
    assert (first["o"], first["h"], first["l"], first["c"], first["v"]) == (
        100.0,
        105.0,
        95.0,
        102.0,
        10.0,
    )
    assert second["t"] == 60_000
    assert (second["o"], second["h"], second["l"], second["c"], second["v"]) == (
        200.0,
        200.0,
        190.0,
        190.0,
        11.0,
    )


def test_a_bar_width_outside_the_int64_bucket_limit_is_refused() -> None:
    """
    An oversized width must raise here, not deep inside numpy.

    `bucket = ts_ms // (bar * 1000)` is int64 arithmetic, so a `bar` whose millisecond form does not
    fit raises `OverflowError: Python int too large to convert to C long` from the fold's middle.
    `bar_seconds` reaches this from client-written text (`/ws/live`'s subscribe channel), so the
    bound belongs where the limit is, stated as the domain error a caller can act on.
    """
    rows = [_snap(0, 100.0)]
    with pytest.raises(ValueError, match="int64 bucket limit"):
        bars_from_rows(rows, MAX_BAR_SECONDS + 1)


def test_the_widest_representable_bar_width_still_folds() -> None:
    """The bound is inclusive: one below the raise must still produce the single bucket at t=0."""
    bars = bars_from_rows([_snap(0, 100.0)], MAX_BAR_SECONDS)
    assert [b["t"] for b in bars] == [0]


def test_a_non_positive_bar_width_is_refused() -> None:
    """0 was a numpy RuntimeWarning plus a bogus `t=0` bar; a warning is a failure (TEST-04)."""
    with pytest.raises(ValueError, match="must be positive"):
        bars_from_rows([_snap(0, 100.0)], 0)


@pytest.mark.parametrize(
    ("bar_seconds", "message"),
    [(0, "must be positive"), (MAX_BAR_SECONDS + 1, "int64 bucket limit")],
)
def test_a_bad_bar_width_is_refused_even_when_no_row_falls_in_the_window(
    bar_seconds: int, message: str
) -> None:
    """
    An empty batch must not launder an out-of-range width.

    The fold short-circuits on no rows, so validating inside the array path alone would accept a bad
    `/ws/live` channel for a quiet instrument and raise only once its first trade arrived -- the
    same width answered two different ways depending on market activity.
    """
    with pytest.raises(ValueError, match=message):
        bars_from_rows([], bar_seconds)


def test_empty_input_produces_no_candles() -> None:
    assert bars_from_rows([], 60) == []
    assert forming_bar([], 60) is None


def test_rows_out_of_order_fold_into_the_same_bars() -> None:
    """The fold sorts by ts_event, so a reordered batch (a Redis reconnect) is not a new bar."""
    rows = [
        _ohlc_second(0, 100.0, 103.0, 99.0, 101.0, 1.0),
        _ohlc_second(10 * _SECOND, 101.0, 108.0, 100.0, 105.0, 2.0),
    ]
    assert bars_from_rows(list(reversed(rows)), 60) == bars_from_rows(rows, 60)


def test_no_trade_seconds_contribute_coverage_but_no_bar() -> None:
    """
    A second with no trade (close_price None) must not shape the bar, and a bucket in which
    nothing traded has no bar at all -- the store's `o IS NOT NULL` read, restated on the fold.
    """
    snapshots = [
        _snap(0, close_price=100.0),
        _snap(10 * _SECOND, close_price=None),  # no trade this second -- contributes nothing
        _snap(30 * _SECOND, close_price=102.0),
    ]

    (c,) = bars_from_rows(snapshots, 60)

    assert c["t"] == 0
    assert (c["o"], c["h"], c["l"], c["c"]) == (100.0, 102.0, 100.0, 102.0)
    assert c["v"] == 3.0  # two contributing seconds' buy_volume(1.0)+sell_volume(0.5) each


def test_a_bucket_where_nothing_traded_has_no_bar() -> None:
    snapshots = [_snap(0, close_price=None), _snap(_SECOND, close_price=None)]
    assert bars_from_rows(snapshots, 60) == []
    assert forming_bar(snapshots, 60) is None


def test_forming_bar_is_the_newest_traded_bucket_and_only_the_wire_keys() -> None:
    rows = [_snap(0, 100.0), _snap(30 * _SECOND, 101.0), _snap(70 * _SECOND, 102.0)]
    bar = forming_bar(rows, 60)
    assert bar is not None
    assert sorted(bar) == ["c", "h", "l", "o", "t", "v"]  # the frozen /ws/live payload
    assert (bar["t"], bar["o"], bar["c"]) == (60_000, 102.0, 102.0)


def test_forming_bar_folds_a_width_the_store_never_keeps() -> None:
    """600 s is not in BAR_SECONDS; the chart offers it, and one fold serves it."""
    rows = [_snap(0, 100.0), _snap(500 * _SECOND, 108.0), _snap(700 * _SECOND, 95.0)]
    bar = forming_bar(rows, 600)
    assert bar is not None
    assert (bar["t"], bar["o"], bar["h"], bar["l"], bar["c"]) == (600_000, 95.0, 95.0, 95.0, 95.0)
    assert forming_bar(rows[:2], 600) == {
        "t": 0,
        "o": 100.0,
        "h": 108.0,
        "l": 100.0,
        "c": 108.0,
        "v": 3.0,
    }


def test_candle_dicts_for_window_serves_raw_seconds_with_a_source_tag() -> None:
    rows = [_snap(_SECOND, 100.0), _snap(2 * _SECOND, 101.0)]
    (c,) = candle_dicts_for_window(IID, 0, 3 * _SECOND, 60, lambda _i, _a, _b: rows)
    assert (c["o"], c["c"], c["source"]) == (100.0, 101.0, "raw_1s")


def test_a_raw_1s_bar_is_partial_exactly_when_observed_under_90_percent() -> None:
    """
    Story 31.8: a bar folded from raw seconds carries `partial` like a stored one, from the fold's
    `seconds_observed` -- an untraded second still counts as observed. 54 of 60 is 90 %, not
    partial; 53 is.
    """
    whole = [_snap(s * _SECOND, 100.0 if s == 0 else None) for s in range(54)]
    under = [_snap((60 + s) * _SECOND, 101.0 if s == 0 else None) for s in range(53)]
    rows = whole + under
    bars = candle_dicts_for_window(IID, 0, 120 * _SECOND, 60, lambda _i, _a, _b: rows)
    assert [(c["t"], c["partial"]) for c in bars] == [(0, False), (60_000, True)]


def test_the_forming_bar_payload_carries_no_partial_flag() -> None:
    """The `/ws/live` forming bar `{t,o,h,l,c,v}` is frozen: the flag is the read path's only."""
    bar = forming_bar([_snap(_SECOND, 100.0)], 60)
    assert bar is not None
    assert list(bar) == ["t", "o", "h", "l", "c", "v"]


def test_is_valid_candle_rejects_inverted_negative_and_nonfinite() -> None:
    ok = {"o": 10.0, "h": 12.0, "l": 9.0, "c": 11.0, "v": 0.0}
    assert is_valid_candle(ok)
    assert not is_valid_candle({**ok, "h": 10.5})  # close above high
    assert not is_valid_candle({**ok, "l": 10.5})  # open below... low above open
    assert not is_valid_candle({**ok, "v": -1.0})
    assert not is_valid_candle({**ok, "c": float("nan")})
    assert not is_valid_candle({**ok, "h": float("inf")})
    assert not is_valid_candle({"o": 1.0})  # missing keys


def _write_ohlc_snapshots(catalog_path: str, base: int, n: int) -> list[DydxSecondSnapshot]:
    snaps = [
        make_snapshot(
            instrument_id=InstrumentId.from_str(IID),
            bid_prices=[99.0],
            bid_sizes=[1.0],
            ask_prices=[101.0],
            ask_sizes=[1.0],
            buy_volume=0.5 * i,
            sell_volume=0.25,
            buy_count=1,
            sell_count=1,
            open_price=100.0 + i,
            high_price=101.0 + i,
            low_price=99.5 + i,
            close_price=100.5 + i,
            ts_event=base + i * _SECOND,
            ts_init=base + i * _SECOND,
        )
        for i in range(n)
    ]
    ParquetDataCatalog(catalog_path).write_data(snaps)
    return snaps


def _decoded_snapshots(catalog_path: str, lo: int, hi: int) -> list[DydxSecondSnapshot]:
    """Return the rows the catalog's own decoder gives for [lo, hi] (CustomData-unwrapped)."""
    results = ParquetDataCatalog(catalog_path).query(
        data_cls=DydxSecondSnapshot,
        identifiers=[IID],
        start=lo,
        end=hi + READ_SPAN_MARGIN_NS,
    )
    rows = [r.data if hasattr(r, "data") else r for r in results]
    return [r for r in rows if lo <= r.ts_event <= hi]


def test_query_second_ohlc_matches_catalog_decoder(tmp_path: Path) -> None:
    """The column-projection read must fold to exactly what the catalog decoder does (Story 21.5)."""
    base = 1_800_000_000_000_000_000
    _write_ohlc_snapshots(str(tmp_path), base, 120)
    lo, hi = base + 10 * _SECOND, base + 90 * _SECOND
    old = bars_from_rows(_decoded_snapshots(str(tmp_path), lo, hi), 60)
    new = bars_from_rows(query_second_ohlc(str(tmp_path), IID, lo, hi), 60)
    assert new == old
    assert len(new) == 2
    assert all(lo <= r.ts_event <= hi for r in query_second_ohlc(str(tmp_path), IID, lo, hi))


def test_query_second_ohlc_refuses_a_float_layout_file(tmp_path: Path) -> None:
    """
    A pre-30.2 file (float layout; a pre-OHLC one lacks the OHLC columns too) is refused loudly,
    never read as None or floats: `archive.tools.migrate_snapshot_ints` rewrites it first (it fills
    a pre-OHLC file's OHLC with nulls, what the reader used to substitute).
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    d = tmp_path / "data" / "custom_dydx_second_snapshot" / IID
    d.mkdir(parents=True)
    ts = 1_800_000_000_000_000_000
    name = "2027-01-15T08-00-00-000000000Z_2027-01-15T08-00-01-000000000Z.parquet"
    pq.write_table(
        pa.table({"ts_event": pa.array([ts], pa.uint64()), "buy_volume": [1.0]}), d / name
    )
    with pytest.raises(LegacySnapshotLayoutError, match="migrate_snapshot_ints"):
        query_second_ohlc(str(tmp_path), IID, ts - 1, ts + 1)


_MONDAY_2024_01_01_MS = 1_704_067_200_000  # 2024-01-01 00:00 UTC, a Monday
_DAY_MS = 86_400_000


def test_a_week_bucket_starts_on_monday_utc_and_a_day_divisor_on_utc_midnight() -> None:
    """Story 31.3: 1W is Monday-anchored (the epoch is a Thursday); day divisors stay epoch-aligned."""
    wednesday_noon = _MONDAY_2024_01_01_MS + 2 * _DAY_MS + _DAY_MS // 2
    sunday_last_ms = _MONDAY_2024_01_01_MS - 1
    assert bucket_start_ms(wednesday_noon, 604_800) == _MONDAY_2024_01_01_MS
    assert bucket_start_ms(_MONDAY_2024_01_01_MS, 604_800) == _MONDAY_2024_01_01_MS
    assert bucket_start_ms(sunday_last_ms, 604_800) == _MONDAY_2024_01_01_MS - 7 * _DAY_MS
    assert bucket_start_ms(wednesday_noon, 86_400) == _MONDAY_2024_01_01_MS + 2 * _DAY_MS
    stamps = np.array([wednesday_noon, sunday_last_ms], dtype=np.int64)
    assert bucket_start_ms(stamps, 604_800).tolist() == [
        _MONDAY_2024_01_01_MS,
        _MONDAY_2024_01_01_MS - 7 * _DAY_MS,
    ]


def test_the_weekly_fold_groups_monday_to_sunday() -> None:
    rows = [
        make_snapshot(
            ts_event=(_MONDAY_2024_01_01_MS - _DAY_MS) * 1_000_000,
            close_price=1.0,
            open_price=1.0,
            high_price=1.0,
            low_price=1.0,
        ),
        make_snapshot(
            ts_event=_MONDAY_2024_01_01_MS * 1_000_000,
            close_price=2.0,
            open_price=2.0,
            high_price=2.0,
            low_price=2.0,
        ),
        make_snapshot(
            ts_event=(_MONDAY_2024_01_01_MS + 6 * _DAY_MS) * 1_000_000,
            close_price=3.0,
            open_price=3.0,
            high_price=3.0,
            low_price=3.0,
        ),
    ]
    bars = bars_from_rows(rows, 604_800)
    assert [(b["t"], b["o"], b["c"]) for b in bars] == [
        (_MONDAY_2024_01_01_MS - 7 * _DAY_MS, 1.0, 1.0),
        (_MONDAY_2024_01_01_MS, 2.0, 3.0),
    ]
