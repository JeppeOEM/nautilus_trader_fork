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

from collector_core.open_interest import OpenInterest
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IIDS = ("BTC-USD-PERP.DYDX", "BTCUSDT-LINEAR.BYBIT", "BTC-USD-PERP.HYPERLIQUID")


def test_round_trips_every_venue_through_one_catalog_dir(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([OpenInterest(InstrumentId.from_str(i), Decimal("123.456"), 1, 1) for i in _IIDS])
    assert (tmp_path / "data" / "custom_open_interest").is_dir()
    for iid in _IIDS:
        (oi,) = catalog.query(OpenInterest, identifiers=[iid])
        assert oi.data.open_interest == Decimal("123.456")


def test_from_pyo3_maps_real_hyperliquid_object() -> None:
    iid = "BTC-USD-PERP.HYPERLIQUID"
    raw = nautilus_pyo3.HyperliquidOpenInterest(nautilus_pyo3.InstrumentId.from_str(iid), "1234.5", 7, 7)
    oi = OpenInterest.from_pyo3(raw)
    assert (str(oi.instrument_id), oi.open_interest, oi.ts_event, oi.ts_init) == (iid, Decimal("1234.5"), 7, 7)
