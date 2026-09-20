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
"""Hyperliquid-specific pieces only: open interest + config (core tests live in collector_core)."""

from decimal import Decimal
from pathlib import Path

import pytest

from hyperliquid_collector.config import load_config
from hyperliquid_collector.open_interest import HyperliquidOpenInterest
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.HYPERLIQUID"


def test_open_interest_round_trips_through_catalog(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data(
        [HyperliquidOpenInterest(InstrumentId.from_str(_IID), Decimal("123.456"), 1, 1)]
    )
    (oi,) = catalog.query(HyperliquidOpenInterest, identifiers=[_IID])
    assert oi.data.open_interest == Decimal("123.456")


def test_open_interest_from_pyo3_shape() -> None:
    class Pyo3OI:
        instrument_id = _IID
        open_interest = "1234.5"
        ts_event = ts_init = 7

    oi = HyperliquidOpenInterest.from_pyo3(Pyo3OI())
    assert str(oi.instrument_id) == _IID
    assert oi.open_interest == Decimal("1234.5")


def test_config_defaults_and_validation(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('instruments = ["BTC-USD-PERP.HYPERLIQUID"]\n')
    cfg = load_config(path)  # venue default: 30s stale guard (l2Book pushes ~5s apart)
    assert (cfg.environment, cfg.snapshot_interval_seconds, cfg.stale_book_seconds) == (
        "mainnet",
        1.0,
        30.0,
    )
    path.write_text("stale_book_seconds = 12.0\n")
    assert load_config(path).stale_book_seconds == 12.0
    path.write_text('environment = "prod"\n')
    with pytest.raises(ValueError, match="environment"):
        load_config(path)
