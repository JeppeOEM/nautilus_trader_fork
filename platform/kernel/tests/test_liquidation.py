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
"""`Liquidation` (Story 33.1): exact units from wire text, the catalog round trip, the side mapping."""

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from kernel.catalog_files import query_liquidations
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.liquidation import has_liquidation_feed
from kernel.second_snapshot import SnapshotEncodingError
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_BTC = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")
_PUMP = InstrumentId.from_str("PUMPFUNUSDT-LINEAR.BYBIT")
_T_NS = 1_791_214_346_487_000_000


def _row(
    size: str = "0.041",
    price: str = "85138.50",
    side: LiquidatedSide = LiquidatedSide.LONG,
    iid: InstrumentId = _BTC,
    precisions: tuple[int, int] = (2, 3),
    ts: int = _T_NS,
) -> Liquidation:
    key = f"{ts // 1_000_000}:Buy:{size}:{price}"
    return Liquidation.from_wire_text(iid, side, size, price, precisions, key, ts, ts + 5)


def test_wire_text_becomes_exact_units_at_the_definition_precisions() -> None:
    row = _row("0.010", "60000.10")
    assert (row.price_units, row.size_units) == (6_000_010, 10)
    assert (row.price_precision, row.size_precision) == (2, 3)


def test_units_at_precision_seven_hold_a_small_price_exactly() -> None:
    # PUMPFUNUSDT (definition read 2026-10-05): tickSize "0.0000010" (precision 7, the text's own
    # digits), qtyStep "100" (precision 0); the entry is a recorded one.
    row = _row("513700", "0.0064280", LiquidatedSide.SHORT, _PUMP, (7, 0))
    assert (row.price_units, row.size_units) == (64_280, 513_700)
    assert row.price == Price.from_str("0.0064280")
    assert row.size == Quantity.from_str("513700")


def test_price_and_size_are_real_nautilus_values() -> None:
    row = _row("0.483", "85123.20")
    assert row.price == Price.from_str("85123.20")
    assert row.price.precision == 2
    assert row.size == Quantity.from_str("0.483")
    assert row.size.precision == 3


def test_notional_is_size_times_bankruptcy_price_in_combined_units() -> None:
    row = _row("0.010", "60000.10")
    assert row.notional_units() == 10 * 6_000_010  # 600.0010 USDT at 10^-5


def test_a_value_finer_than_the_definition_is_refused_never_rounded() -> None:
    with pytest.raises(SnapshotEncodingError, match="not exact at precision 2"):
        _row(price="85138.505")
    with pytest.raises(SnapshotEncodingError, match="not exact at precision 3"):
        _row(size="0.0415")


def test_trailing_zeros_beyond_the_precision_are_exact_not_finer() -> None:
    # A definition at precision 6 holds "0.0064280" exactly: the 7th decimal is a 0.
    assert _row("1", "0.0064280", precisions=(6, 0)).price_units == 6428


@pytest.mark.parametrize("text", ["", "abc", "NaN", "Infinity", "-1", "0"])
def test_text_that_is_not_a_positive_decimal_is_refused(text: str) -> None:
    with pytest.raises(SnapshotEncodingError):
        _row(price=text)


def test_wire_buy_is_a_liquidated_long_whose_forced_order_is_a_sell() -> None:
    # The mapping lives in the venue parser; the kernel names the semantics: LONG = forced sell.
    assert LiquidatedSide.LONG.value == "long"
    assert LiquidatedSide.SHORT.value == "short"
    assert "forced sell" in (LiquidatedSide.__doc__ or "")


def test_a_batch_round_trips_through_the_catalog_unchanged(tmp_path: Path) -> None:
    rows = [
        _row("0.041", "85138.50"),
        _row("0.483", "85123.20", LiquidatedSide.SHORT, ts=_T_NS + 114_000_000),
        _row("513700", "0.0064280", LiquidatedSide.SHORT, _PUMP, (7, 0), _T_NS + 1),
    ]
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data(rows)
    assert sorted(p.name for p in (tmp_path / "data" / "custom_liquidation").iterdir()) == [
        str(_BTC),
        str(_PUMP),
    ]
    back = [
        Liquidation.to_dict(item.data)
        for iid in (_BTC, _PUMP)
        for item in catalog.query(Liquidation, identifiers=[str(iid)])
    ]
    assert back == [Liquidation.to_dict(row) for row in rows]


def test_the_stored_columns_are_the_documented_schema(tmp_path: Path) -> None:
    ParquetDataCatalog(str(tmp_path)).write_data([_row()])
    (path,) = (tmp_path / "data" / "custom_liquidation" / str(_BTC)).glob("*.parquet")
    schema = pq.read_schema(path)
    assert schema.names == list(Liquidation.schema().names)
    table = pq.read_table(path)
    assert table.column("side").to_pylist() == ["long"]
    assert table.column("price_units").to_pylist() == [8_513_850]


# -- the feed predicate and the catalog read (Story 33.3) -----------------------------------------


@pytest.mark.parametrize(
    ("iid", "expected"),
    [
        ("BTCUSDT-LINEAR.BYBIT", True),
        ("BTCUSDT-SPOT.BYBIT", False),
        ("BTCUSD-INVERSE.BYBIT", False),
        ("BTC-USD-PERP.HYPERLIQUID", False),
        ("BTC-USD-PERP.DYDX", False),
        ("BTCUSDT-LINEAR.OTHER", False),
        ("not-an-id", False),
    ],
)
def test_only_bybit_linear_has_a_liquidation_feed(iid: str, expected: bool) -> None:
    assert has_liquidation_feed(iid) is expected


def test_query_liquidations_reads_the_inclusive_window_sorted(tmp_path: Path) -> None:
    early = _row("0.041", ts=_T_NS)
    late = _row("0.483", side=LiquidatedSide.SHORT, ts=_T_NS + 2_000_000_000)
    outside = _row("0.100", ts=_T_NS + 3_000_000_000)
    ParquetDataCatalog(str(tmp_path)).write_data([early, late, outside])
    rows = query_liquidations(str(tmp_path), str(_BTC), _T_NS, _T_NS + 2_000_000_000)
    assert [Liquidation.to_dict(r) for r in rows] == [
        Liquidation.to_dict(early),
        Liquidation.to_dict(late),
    ]


def test_query_liquidations_keeps_one_copy_of_a_venue_event(tmp_path: Path) -> None:
    """The same event in two files (a minute file and its day file) is one liquidation."""
    catalog = ParquetDataCatalog(str(tmp_path))
    row = _row()
    catalog.write_data([row])
    filler = _row("0.500", ts=_T_NS + 1_000_000_000)  # a wider file span: a second file
    catalog.write_data([_row(ts=_T_NS), filler], skip_disjoint_check=True)
    assert len(list((tmp_path / "data" / "custom_liquidation" / str(_BTC)).glob("*"))) == 2
    rows = query_liquidations(str(tmp_path), str(_BTC), _T_NS - 1, _T_NS + 1)
    assert [Liquidation.to_dict(r) for r in rows] == [Liquidation.to_dict(row)]


def test_query_liquidations_refuses_two_copies_that_disagree(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([_row("0.041")])
    other = _row("0.050")
    forged = Liquidation.from_dict(
        {**Liquidation.to_dict(other), "venue_event_id": _row().venue_event_id}
    )
    filler = _row("0.500", ts=_T_NS + 1_000_000_000)
    catalog.write_data([forged, filler], skip_disjoint_check=True)
    with pytest.raises(ValueError, match="stored twice with different values"):
        query_liquidations(str(tmp_path), str(_BTC), _T_NS - 1, _T_NS + 1)


def test_query_liquidations_without_a_directory_is_empty(tmp_path: Path) -> None:
    assert query_liquidations(str(tmp_path), "BTC-USD-PERP.HYPERLIQUID", 0, 2**62) == []
