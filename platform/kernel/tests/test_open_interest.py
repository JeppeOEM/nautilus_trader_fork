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
"""OpenInterest catalog round-trip for every venue id, and the Hyperliquid pyo3 mapping."""

from decimal import Decimal
from pathlib import Path

import pytest

from kernel.catalog_files import query_open_interest
from kernel.open_interest import OpenInterest
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IIDS = ("BTC-USD-PERP.DYDX", "BTCUSDT-LINEAR.BYBIT", "BTC-USD-PERP.HYPERLIQUID")


def test_round_trips_every_venue_through_one_catalog_dir(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data(
        [OpenInterest(InstrumentId.from_str(i), Decimal("123.456"), 1, 1) for i in _IIDS]
    )
    assert (tmp_path / "data" / "custom_open_interest").is_dir()
    for iid in _IIDS:
        (oi,) = catalog.query(OpenInterest, identifiers=[iid])
        assert oi.data.open_interest == Decimal("123.456")


def test_from_pyo3_maps_real_hyperliquid_object() -> None:
    iid = "BTC-USD-PERP.HYPERLIQUID"
    raw = nautilus_pyo3.HyperliquidOpenInterest(
        nautilus_pyo3.InstrumentId.from_str(iid), "1234.5", 7, 7
    )
    oi = OpenInterest.from_pyo3(raw)
    assert (str(oi.instrument_id), oi.open_interest, oi.ts_event, oi.ts_init) == (
        iid,
        Decimal("1234.5"),
        7,
        7,
    )


def _oi(value: str, ts_event: int, ts_init: int) -> OpenInterest:
    return OpenInterest(InstrumentId.from_str(_IIDS[1]), Decimal(value), ts_event, ts_init)


def test_query_open_interest_reads_the_inclusive_window_sorted(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([_oi("120", 10, 11), _oi("90", 20, 21)])
    catalog.write_data([_oi("100", 30, 31), _oi("80", 40, 41)])
    rows = query_open_interest(str(tmp_path), _IIDS[1], 10, 30)
    assert [(r.ts_event, r.open_interest) for r in rows] == [
        (10, Decimal(120)),
        (20, Decimal(90)),
        (30, Decimal(100)),
    ]


def test_query_open_interest_keeps_one_copy_of_a_row_stored_twice(tmp_path: Path) -> None:
    """A minute file and its consolidated day file both hold the row (before the cleanup)."""
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([_oi("100", 10, 11)])
    catalog.write_data([_oi("100", 10, 11), _oi("110", 12, 13)], skip_disjoint_check=True)
    rows = query_open_interest(str(tmp_path), _IIDS[1], 0, 100)
    assert [(r.ts_event, r.open_interest) for r in rows] == [
        (10, Decimal(100)),
        (12, Decimal(110)),
    ]


def test_query_open_interest_refuses_two_copies_that_disagree(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([_oi("100", 10, 11)])
    catalog.write_data([_oi("101", 10, 11), _oi("110", 12, 13)], skip_disjoint_check=True)
    with pytest.raises(ValueError, match="stored twice"):
        query_open_interest(str(tmp_path), _IIDS[1], 0, 100)


def test_query_open_interest_of_an_id_without_rows_is_empty(tmp_path: Path) -> None:
    assert query_open_interest(str(tmp_path), "BTCUSDT-SPOT.BYBIT", 0, 100) == []
