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
`research.application.frames.CatalogFrames` over a real `write_data` catalog and a real SQLite
candle store (Story 27.1, TEST-03): every read is bounded to `[start, end)`, gaps stay gaps and the
derived columns are `kernel.indicators`' own values.
"""

import math
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from candles.infrastructure.sqlite_store import CandleStore
from candles.infrastructure.sqlite_store import db_path_for_venue
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_MS
from kernel.clocks import NS_PER_S
from kernel.indicators import MultiLevelOBI
from kernel.indicators import microprice
from kernel.indicators import mid_price
from kernel.indicators import spread
from kernel.open_interest import OpenInterest
from kernel.second_snapshot import DydxSecondSnapshot

from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from research.application.frames import OBI_LEVELS
from research.application.frames import SECONDS_COLUMNS
from research.application.frames import CatalogFrames


_IID = "BTC-USD-PERP.DYDX"
_DAY0 = 20_000 * NS_PER_DAY
_EMPTY_ASK_SECOND = 4


def _at(second: int) -> int:
    return _DAY0 + second * NS_PER_S


def _snapshot(second: int) -> DydxSecondSnapshot:
    """Three levels a side; second 4 has no asks; a trade in minutes 0 and 2 only."""
    traded = second // 60 in (0, 2)
    close = 100.0 + second / 100 if traded else None
    return DydxSecondSnapshot(
        InstrumentId.from_str(_IID),
        [99.9, 99.8, 99.7],
        [1.0 + second % 3, 2.0, 3.0],
        [] if second == _EMPTY_ASK_SECOND else [100.1, 100.2, 100.3],
        [] if second == _EMPTY_ASK_SECOND else [2.0, 1.0, 0.5],
        1.0 if traded else 0.0,
        0.5 if traded else 0.0,
        1 if traded else 0,
        1 if traded else 0,
        _at(second),
        _at(second),
        close,
        close,
        close,
        close,
    )


def _write_catalog(root: Path) -> list[DydxSecondSnapshot]:
    iid = InstrumentId.from_str(_IID)
    snapshots = [_snapshot(s) for s in range(180)]
    catalog = ParquetDataCatalog(str(root))
    catalog.write_data(snapshots)
    catalog.write_data(
        [
            TradeTick(
                iid,
                Price.from_str("100.1"),
                Quantity.from_str("0.5"),
                side,
                TradeId(str(s)),
                _at(s),
                _at(s),
            )
            for s, side in (
                (1, AggressorSide.BUYER),
                (2, AggressorSide.SELLER),
                (3, AggressorSide.BUYER),
            )
        ]
    )
    catalog.write_data([OpenInterest(iid, Decimal(f"12{s}.5"), _at(s), _at(s)) for s in (0, 2, 5)])
    catalog.write_data([FundingRateUpdate(iid, Decimal("0.0001"), _at(s), _at(s)) for s in (0, 3)])
    catalog.write_data(
        [MarkPriceUpdate(iid, Price.from_str("100.05"), _at(s), _at(s)) for s in (1, 2)]
    )
    catalog.write_data(
        [IndexPriceUpdate(iid, Price.from_str("100.01"), _at(s), _at(s)) for s in (2, 3)]
    )
    return snapshots


@pytest.fixture
def frames(tmp_path: Path) -> CatalogFrames:
    snapshots = _write_catalog(tmp_path / "catalog")
    store = CandleStore(db_path_for_venue(tmp_path / "candles", "DYDX"))
    store.apply(_IID, snapshots)
    store.close()
    return CatalogFrames(str(tmp_path / "catalog"), str(tmp_path / "candles"))


def test_seconds_window_is_half_open_and_indexed_by_ts(frames: CatalogFrames) -> None:
    df = frames.seconds(_IID, start=_at(2), end=_at(5))
    assert df["ts_event"].tolist() == [_at(2), _at(3), _at(4)]
    assert df.index.name == "ts"
    assert str(df.index.tz) == "UTC"
    assert df.index[0] == pd.Timestamp(_at(2), unit="ns", tz="UTC")
    assert tuple(df.columns) == SECONDS_COLUMNS


def test_seconds_derived_columns_are_the_kernel_functions(frames: CatalogFrames) -> None:
    df = frames.seconds(_IID, start=_at(0), end=_at(4))
    for second, row in zip(range(4), df.itertuples(), strict=True):
        fields = DydxSecondSnapshot.to_dict(_snapshot(second))
        assert row.mid == mid_price(fields)
        assert row.spread == spread(fields)
        assert row.microprice == microprice(fields)
        for n in OBI_LEVELS:
            obi = MultiLevelOBI(levels=n)
            obi.update_raw(fields["bid_sizes"], fields["ask_sizes"])
            assert getattr(row, f"obi_{n}") == obi.value


def test_an_empty_side_is_a_gap_not_a_value(frames: CatalogFrames) -> None:
    row = frames.seconds(_IID, start=_at(4), end=_at(5)).iloc[0]
    assert math.isnan(row["mid"])
    assert math.isnan(row["spread"])
    assert math.isnan(row["obi_1"])  # a one-sided book has no imbalance, like no mid
    assert row["ask_prices"] == []


def test_an_empty_window_has_the_columns(frames: CatalogFrames) -> None:
    df = frames.seconds(_IID, start=_DAY0 - NS_PER_DAY, end=_DAY0)
    assert df.empty
    assert tuple(df.columns) == SECONDS_COLUMNS


def test_unbounded_reads_are_type_errors(frames: CatalogFrames) -> None:
    with pytest.raises(TypeError):
        frames.seconds(_IID)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        frames.bars(_IID, 60, start=_DAY0)  # type: ignore[call-arg]


def test_a_reversed_window_raises(frames: CatalogFrames) -> None:
    with pytest.raises(ValueError, match="after"):
        frames.trades(_IID, start=_at(5), end=_at(5))


def test_trades_bounded(frames: CatalogFrames) -> None:
    df = frames.trades(_IID, start=_at(2), end=_at(4))
    assert df["ts_event"].tolist() == [_at(2), _at(3)]
    assert df["aggressor_side"].tolist() == ["SELLER", "BUYER"]
    assert df["price"].tolist() == [100.1, 100.1]
    assert df["trade_id"].tolist() == ["2", "3"]


def test_open_interest_funding_and_mark_index_bounded(frames: CatalogFrames) -> None:
    oi = frames.open_interest(_IID, start=_at(1), end=_at(5))
    assert oi["open_interest"].tolist() == [122.5]
    funding = frames.funding(_IID, start=_at(0), end=_at(3))
    assert funding["rate"].tolist() == [0.0001]
    marks = frames.mark_index(_IID, start=_at(1), end=_at(3))
    assert marks["ts_event"].tolist() == [_at(1), _at(2), _at(2)]
    assert np.array_equal(marks["mark"], [100.05, 100.05, math.nan], equal_nan=True)
    assert np.array_equal(marks["index"], [math.nan, math.nan, 100.01], equal_nan=True)


def test_iso_string_windows_parse_like_the_catalog(frames: CatalogFrames) -> None:
    start = pd.Timestamp(_at(2), unit="ns", tz="UTC").isoformat()
    end = pd.Timestamp(_at(4), unit="ns", tz="UTC").isoformat()
    assert frames.trades(_IID, start=start, end=end)["ts_event"].tolist() == [_at(2), _at(3)]


def test_bars_come_from_the_candle_store_bounded_by_open_time(frames: CatalogFrames) -> None:
    day0_ms = _DAY0 // NS_PER_MS
    all_bars = frames.bars(_IID, 60, start=_DAY0, end=_at(180))
    assert all_bars["t"].tolist() == [day0_ms, day0_ms + 120_000]  # minute 1 had no trade
    assert all_bars["ts_event"].tolist() == [_DAY0, _at(120)]
    first = all_bars.iloc[0]
    assert (first["o"], first["c"], first["v"]) == (100.0, 100.59, 90.0)
    assert frames.bars(_IID, 60, start=_at(60), end=_at(180))["t"].tolist() == [day0_ms + 120_000]
    assert frames.bars(_IID, 60, start=_DAY0, end=_at(120))["t"].tolist() == [day0_ms]


def test_bars_lower_bound_is_the_exact_start_not_a_floored_millisecond(
    frames: CatalogFrames,
) -> None:
    day0_ms = _DAY0 // NS_PER_MS
    assert frames.bars(_IID, 60, start=_DAY0 + 1, end=_at(180))["t"].tolist() == [day0_ms + 120_000]


def test_a_bar_straddling_end_is_left_out(frames: CatalogFrames) -> None:
    """Minute 2 runs to second 180; a window ending at 150 must not hold its trades after 150."""
    day0_ms = _DAY0 // NS_PER_MS
    assert frames.bars(_IID, 60, start=_DAY0, end=_at(150))["t"].tolist() == [day0_ms]


def test_bars_refuse_a_window_across_a_bucket_never_observed(tmp_path: Path) -> None:
    """Minute 1 has no snapshot at all (an outage), unlike an untraded minute that was observed."""
    snapshots = _write_catalog(tmp_path / "catalog")
    outage = [s for s in snapshots if not _at(60) <= s.ts_event < _at(120)]
    store = CandleStore(db_path_for_venue(tmp_path / "candles", "DYDX"))
    store.apply(_IID, outage)
    store.close()
    frames = CatalogFrames(str(tmp_path / "catalog"), str(tmp_path / "candles"))
    with pytest.raises(ValueError, match=rf"never observed .* first at {_at(60) // NS_PER_MS} ms"):
        frames.bars(_IID, 60, start=_DAY0, end=_at(180))
    assert len(frames.bars(_IID, 60, start=_DAY0, end=_at(60))) == 1


def test_bars_refuse_a_window_reaching_past_the_stores_coverage(frames: CatalogFrames) -> None:
    with pytest.raises(ValueError, match="covers from"):
        frames.bars(_IID, 60, start=_DAY0 - 60 * NS_PER_S, end=_at(180))
    with pytest.raises(ValueError, match="None = nothing stored"):
        frames.bars("ETH-USD-PERP.DYDX", 60, start=_DAY0, end=_at(180))
    with pytest.raises(ValueError, match="covers until"):
        frames.bars(_IID, 60, start=_DAY0, end=_at(180) + 1)


def test_the_window_is_on_ts_event_even_when_ts_init_disagrees(tmp_path: Path) -> None:
    """The catalog bounds on `ts_init`; rows straddling an edge are judged by `ts_event`."""
    iid = InstrumentId.from_str(_IID)
    catalog = ParquetDataCatalog(str(tmp_path))
    start, end = _at(100), _at(200)
    # (ts_event, ts_init): late-received rows just before each edge, an early-stamped one at `end`.
    stamps = [
        (start - NS_PER_S, start + NS_PER_S),  # out: ts_event before start, ts_init inside
        (start + 5 * NS_PER_S, start - 2 * NS_PER_S),  # in: ts_init before start
        (end - NS_PER_S, end + 100 * NS_PER_S),  # in: ts_init after end
        (end, end - 3 * NS_PER_S),  # out: ts_event at end, ts_init inside
    ]
    for k, (ts_event, ts_init) in enumerate(stamps):
        catalog.write_data(
            [
                TradeTick(
                    iid,
                    Price.from_str("1.0"),
                    Quantity.from_str("1"),
                    AggressorSide.BUYER,
                    TradeId(str(k)),
                    ts_event,
                    ts_init,
                )
            ]
        )
    df = CatalogFrames(str(tmp_path), str(tmp_path)).trades(_IID, start=start, end=end)
    assert df["ts_event"].tolist() == [start + 5 * NS_PER_S, end - NS_PER_S]
    assert df.index.is_monotonic_increasing


def test_bars_reject_a_size_the_store_does_not_keep(frames: CatalogFrames) -> None:
    with pytest.raises(ValueError, match="keeps"):
        frames.bars(_IID, 7, start=_DAY0, end=_at(180))


def test_a_missing_candle_store_names_its_path(tmp_path: Path) -> None:
    frames = CatalogFrames(str(tmp_path), str(tmp_path / "candles"))
    with pytest.raises(FileNotFoundError, match=r"candles_dydx\.db"):
        frames.bars(_IID, 60, start=_DAY0, end=_at(60))
