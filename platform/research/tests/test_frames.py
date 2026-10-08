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
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.open_interest import OpenInterest
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger

from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from research.application import frames as frames_module
from research.application.frames import INSTRUMENT_ATTR
from research.application.frames import LIQUIDATION_DUPLICATE_SITE
from research.application.frames import LIQUIDATION_READ_SITE
from research.application.frames import LIQUIDATIONS_COLUMNS
from research.application.frames import OBI_LEVELS
from research.application.frames import SECONDS_COLUMNS
from research.application.frames import CatalogFrames
from research.tests.fixture_catalog import FixturePaths
from research.tests.test_ofi_strategy_forced_flow import _instrument as _bybit_instrument


_IID = "BTC-USD-PERP.DYDX"
_DAY0 = 20_000 * NS_PER_DAY
_EMPTY_ASK_SECOND = 4


def _at(second: int) -> int:
    return _DAY0 + second * NS_PER_S


def _snapshot(second: int) -> DydxSecondSnapshot:
    """Three levels a side; second 4 has no asks; a trade in minutes 0 and 2 only."""
    traded = second // 60 in (0, 2)
    close = 100.0 + second / 100 if traded else None
    return make_snapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=[99.9, 99.8, 99.7],
        bid_sizes=[1.0 + second % 3, 2.0, 3.0],
        ask_prices=[] if second == _EMPTY_ASK_SECOND else [100.1, 100.2, 100.3],
        ask_sizes=[] if second == _EMPTY_ASK_SECOND else [2.0, 1.0, 0.5],
        buy_volume=1.0 if traded else 0.0,
        sell_volume=0.5 if traded else 0.0,
        buy_count=1 if traded else 0,
        sell_count=1 if traded else 0,
        ts_event=_at(second),
        ts_init=_at(second),
        open_price=close,
        high_price=close,
        low_price=close,
        close_price=close,
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
        fields = _snapshot(second).as_floats()
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


def test_objects_are_the_typed_rows_of_the_half_open_window(frames: CatalogFrames) -> None:
    ticks = frames.objects(TradeTick, _IID, start=_at(2), end=_at(4))
    assert [t.ts_event for t in ticks] == [_at(2), _at(3)]
    assert all(isinstance(t, TradeTick) for t in ticks)
    assert ticks[0].price == Price.from_str("100.1")  # the exact value, precision label kept
    marks = frames.objects(MarkPriceUpdate, _IID, start=_at(0), end=_at(2))
    assert [(m.ts_event, m.value) for m in marks] == [(_at(1), Price.from_str("100.05"))]
    oi = frames.objects(OpenInterest, _IID, start=_at(1), end=_at(3))
    assert [row.open_interest for row in oi] == [Decimal("122.5")]


def test_objects_read_index_prices_through_the_kernel_reader(frames: CatalogFrames) -> None:
    """The pinned catalog cannot decode `IndexPriceUpdate`: `end` stays exclusive all the same."""
    rows = frames.objects(IndexPriceUpdate, _IID, start=_at(2), end=_at(3))
    assert [(r.ts_event, r.price) for r in rows] == [(_at(2), Price.from_str("100.01"))]
    assert rows[0].price.precision == 2


def test_objects_is_bounded_like_every_read(frames: CatalogFrames) -> None:
    with pytest.raises(TypeError):
        frames.objects(TradeTick, _IID)  # type: ignore[call-arg]


# --- Story 27.4: bar coverage and same-symbol discovery --------------------------------------


def test_bar_coverage_is_one_span_over_a_contiguous_store(frames: CatalogFrames) -> None:
    wide = frames.bar_coverage(_IID, 60, start=_DAY0 - NS_PER_DAY, end=_at(180) + NS_PER_DAY)
    assert wide == [(_DAY0, _at(180))]  # a window past the store is clipped to what it holds
    assert frames.bar_coverage(_IID, 60, start=_DAY0 + 1, end=_at(170)) == [(_at(60), _at(120))]


def test_bar_coverage_splits_at_a_bucket_never_observed(tmp_path: Path) -> None:
    snapshots = _write_catalog(tmp_path / "catalog")
    outage = [s for s in snapshots if not _at(60) <= s.ts_event < _at(120)]
    store = CandleStore(db_path_for_venue(tmp_path / "candles", "DYDX"))
    store.apply(_IID, outage)
    store.close()
    frames = CatalogFrames(str(tmp_path / "catalog"), str(tmp_path / "candles"))
    spans = frames.bar_coverage(_IID, 60, start=_DAY0 - NS_PER_DAY, end=_at(180) + NS_PER_DAY)
    assert spans == [(_DAY0, _at(60)), (_at(120), _at(180))]
    read = [frames.bars(_IID, 60, start=lo, end=hi)["t"].tolist() for lo, hi in spans]
    assert read == [[_DAY0 // NS_PER_MS], [_at(120) // NS_PER_MS]]  # no span raises


def test_bar_coverage_without_a_bucket_is_empty_and_without_a_store_raises(
    frames: CatalogFrames, tmp_path: Path
) -> None:
    no_store = CatalogFrames(str(tmp_path), str(tmp_path / "nothing"))
    with pytest.raises(FileNotFoundError, match="no candle store"):
        no_store.bar_coverage(_IID, 60, start=_DAY0, end=_at(180))
    assert frames.bar_coverage("ETH-USD-PERP.DYDX", 60, start=_DAY0, end=_at(180)) == []
    with pytest.raises(ValueError, match="keeps"):
        frames.bar_coverage(_IID, 7, start=_DAY0, end=_at(180))


def test_same_symbol_on_the_fixture_is_the_three_venues(fixture_archive: FixturePaths) -> None:
    frames = CatalogFrames(fixture_archive.catalog_path, fixture_archive.candles_dir)
    for iid, expected in fixture_archive.same_asset.items():
        assert frames.same_symbol(iid) == list(expected), iid
    assert frames.same_symbol("BTC-USD-PERP.DYDX") == [
        "BTCUSDT-LINEAR.BYBIT",
        "BTC-USD-PERP.DYDX",
        "BTC-USD-PERP.HYPERLIQUID",
    ]


def _spot(symbol: str) -> CurrencyPair:
    return CurrencyPair(
        instrument_id=InstrumentId.from_str(f"{symbol}.BYBIT"),
        raw_symbol=Symbol(symbol),
        base_currency=BTC,
        quote_currency=USDT,
        price_precision=2,
        size_precision=6,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.000001"),
        lot_size=None,
        max_quantity=None,
        min_quantity=None,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal(0),
        margin_maint=Decimal(0),
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        ts_event=0,
        ts_init=0,
    )


def test_same_symbol_keeps_spot_apart_and_refuses_an_unreadable_id(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([_spot("BTCUSDT-SPOT"), _spot("BTCUSDC-SPOT"), _spot("ETHBTC-SPOT")])
    frames = CatalogFrames(str(tmp_path), str(tmp_path))
    assert frames.same_symbol("BTCUSDT-SPOT.BYBIT") == ["BTCUSDC-SPOT.BYBIT", "BTCUSDT-SPOT.BYBIT"]
    assert frames.same_symbol("BTC-USD-PERP.DYDX") == ["BTC-USD-PERP.DYDX"]  # itself, no spot
    assert frames.same_symbol("ETHBTC-SPOT.BYBIT") == []


def test_seconds_carry_the_exact_integer_volumes(frames: CatalogFrames) -> None:
    # Second 0 traded 1.0 bought and 0.5 sold at the factory's size precision (Story 33.13).
    row = frames.seconds(_IID, start=_at(0), end=_at(1)).iloc[0]
    snapshot = _snapshot(0)
    assert (row["buy_volume_units"], row["sell_volume_units"]) == (
        snapshot.buy_volume_units,
        snapshot.sell_volume_units,
    )
    assert row["buy_volume_units"] / 10 ** row["size_precision"] == row["buy_volume"]


_LIQ_IID = "BTCUSDT-LINEAR.BYBIT"


def _liquidation(ts_event: int, side: LiquidatedSide, size_units: int) -> Liquidation:
    # Price 65 000.50 at precision 2, sizes at precision 3: a 10^-5 notional.
    return Liquidation(
        InstrumentId.from_str(_LIQ_IID),
        side,
        size_units,
        6_500_050,
        2,
        3,
        f"liq-{ts_event}",
        ts_event,
        ts_event + 50 * NS_PER_MS,
    )


@pytest.fixture
def liquidation_frames(tmp_path: Path) -> CatalogFrames:
    rows = [
        _liquidation(_at(1), LiquidatedSide.LONG, 1_250),
        _liquidation(_at(2), LiquidatedSide.SHORT, 3),
        _liquidation(_at(3), LiquidatedSide.LONG, 40),
        _liquidation(_DAY0 + NS_PER_DAY + NS_PER_S, LiquidatedSide.SHORT, 7),  # the next UTC day
    ]
    ParquetDataCatalog(str(tmp_path / "catalog")).write_data(rows)
    return CatalogFrames(str(tmp_path / "catalog"), str(tmp_path / "candles"))


def test_liquidations_are_decoded_once_from_their_units(
    liquidation_frames: CatalogFrames,
) -> None:
    df = liquidation_frames.liquidations(_LIQ_IID, start=_at(1), end=_at(2))
    assert tuple(df.columns) == LIQUIDATIONS_COLUMNS
    row = df.iloc[0]
    assert (row["side"], row["size_units"], row["price_units"]) == ("long", 1_250, 6_500_050)
    assert (row["size_precision"], row["price_precision"]) == (3, 2)
    assert (row["size"], row["price"]) == (1.25, 65_000.5)
    assert row["notional"] == 81_250.625  # 1.25 x 65 000.50, at the bankruptcy price
    assert row["venue_event_id"] == f"liq-{_at(1)}"
    assert row["ts_init"] == _at(1) + 50 * NS_PER_MS
    assert df.attrs[INSTRUMENT_ATTR] == _LIQ_IID


def test_liquidations_window_is_half_open_and_crosses_utc_days(
    liquidation_frames: CatalogFrames,
) -> None:
    df = liquidation_frames.liquidations(_LIQ_IID, start=_at(2), end=_at(3))
    assert df["ts_event"].tolist() == [_at(2)]
    both_days = liquidation_frames.liquidations(_LIQ_IID, start=_at(1), end=_DAY0 + 2 * NS_PER_DAY)
    assert both_days["ts_event"].tolist() == [
        _at(1),
        _at(2),
        _at(3),
        _DAY0 + NS_PER_DAY + NS_PER_S,
    ]
    assert both_days.index[0] == pd.Timestamp(_at(1), unit="ns", tz="UTC")


def test_every_liquidation_price_is_a_bankruptcy_price(liquidation_frames: CatalogFrames) -> None:
    df = liquidation_frames.liquidations(_LIQ_IID, start=_at(0), end=_at(10))
    assert set(df["price_kind"]) == {"bankruptcy"}


def test_an_id_without_the_feed_is_the_empty_frame_with_every_column(
    liquidation_frames: CatalogFrames,
) -> None:
    for iid in ("BTCUSDT-SPOT.BYBIT", "BTC-USD-PERP.HYPERLIQUID", _IID):
        df = liquidation_frames.liquidations(iid, start=_at(0), end=_at(10))
        assert df.empty, iid
        assert tuple(df.columns) == LIQUIDATIONS_COLUMNS, iid


def test_a_disagreeing_duplicate_is_ledgered_at_the_duplicate_site(tmp_path: Path) -> None:
    first = _liquidation(_at(1), LiquidatedSide.LONG, 5)
    forged = _liquidation(_at(1), LiquidatedSide.LONG, 6)  # the same venue event, another size
    filler = _liquidation(_at(2), LiquidatedSide.LONG, 1)  # a wider span: a second file
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([first])
    catalog.write_data([forged, filler], skip_disjoint_check=True)
    error_ledger.reset()
    try:
        with pytest.raises(ValueError, match="stored twice"):
            CatalogFrames(str(tmp_path), str(tmp_path)).liquidations(
                _LIQ_IID, start=_at(0), end=_at(10)
            )
        assert error_ledger.counts() == {LIQUIDATION_DUPLICATE_SITE: 1}
    finally:
        error_ledger.reset()


def test_any_other_refused_read_is_ledgered_at_the_read_site(
    liquidation_frames: CatalogFrames, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The kernel's other refusal: a listing that kept losing files during a consolidation.
    def refused(*_args: object) -> list[Liquidation]:
        raise ValueError("liquidations: files kept disappearing during the read (3 listings)")

    monkeypatch.setattr(frames_module, "query_liquidations", refused)
    error_ledger.reset()
    try:
        with pytest.raises(ValueError, match="kept disappearing"):
            liquidation_frames.liquidations(_LIQ_IID, start=_at(0), end=_at(10))
        assert error_ledger.counts() == {LIQUIDATION_READ_SITE: 1}
    finally:
        error_ledger.reset()


def test_definition_precisions_are_the_catalogs(tmp_path: Path) -> None:
    ParquetDataCatalog(str(tmp_path)).write_data([_bybit_instrument()])
    frames = CatalogFrames(str(tmp_path), str(tmp_path))
    assert frames.definition_precisions(_LIQ_IID) == (2, 3)
    assert frames.definition_precisions("ETHUSDT-LINEAR.BYBIT") is None
