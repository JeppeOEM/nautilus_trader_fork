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
"""Bybit-specific pieces only: open-interest parse + catalog round-trip, config (core tests live in collector_core)."""

from decimal import Decimal
from pathlib import Path

import pytest

from bybit_collector.config import load_config
from bybit_collector.open_interest import BybitOpenInterest
from bybit_collector.open_interest import parse_open_interest
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTCUSDT-LINEAR.BYBIT"


def test_open_interest_round_trips_through_catalog(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([BybitOpenInterest(InstrumentId.from_str(_IID), Decimal("123.456"), 1, 1)])
    (oi,) = catalog.query(BybitOpenInterest, identifiers=[_IID])
    assert oi.data.open_interest == Decimal("123.456")


def test_parse_open_interest() -> None:
    payload = {
        "result": {
            "list": [
                {"symbol": "BTCUSDT", "openInterest": "1234.5"},
                {"symbol": "ETHUSDT", "openInterest": ""},
                {"openInterest": "1"},
            ]
        }
    }
    (item,) = parse_open_interest(payload, ts=7)
    assert str(item.instrument_id) == _IID
    assert item.open_interest == Decimal("1234.5")


def test_config_defaults_and_validation(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('instruments = ["BTCUSDT-LINEAR.BYBIT"]\nopen_interest_poll_seconds = 300\n')
    cfg = load_config(path)
    assert (cfg.environment, cfg.snapshot_interval_seconds, cfg.instruments) == (
        "mainnet",
        1.0,
        (_IID,),
    )
    assert (cfg.open_interest_poll_seconds, cfg.stale_book_seconds) == (300, 5.0)
    path.write_text('environment = "prod"\n')
    with pytest.raises(ValueError, match="environment"):
        load_config(path)
    path.write_text("open_interest_poll_seconds = 0\n")
    with pytest.raises(ValueError, match="open_interest_poll_seconds"):
        load_config(path)
