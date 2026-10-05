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
Bybit-specific pieces only: open-interest parse + catalog round-trip, config (core tests live in
capture/tests).
"""

from decimal import Decimal
from pathlib import Path

import pytest
from kernel.open_interest import OpenInterest
from observability import error_ledger

from capture.infrastructure.config import load_venue_config
from capture.venues.bybit.config import BybitConfig
from capture.venues.bybit.open_interest import parse_open_interest
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTCUSDT-LINEAR.BYBIT"


def test_open_interest_round_trips_through_catalog(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([OpenInterest(InstrumentId.from_str(_IID), Decimal("123.456"), 1, 1)])
    (oi,) = catalog.query(OpenInterest, identifiers=[_IID])
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
    polled = parse_open_interest(payload, ts=7)
    (item,) = polled.rows
    assert str(item.instrument_id) == _IID
    assert item.open_interest == Decimal("1234.5")
    # Story 31.2: the rows it could not parse are named for the poll to ledger, never skipped.
    assert [iid for iid, _ in polled.malformed] == ["ETHUSDT-LINEAR.BYBIT", None]


def test_a_non_decimal_open_interest_is_malformed_not_raised() -> None:
    payload = {"result": {"list": [{"symbol": "BTCUSDT", "openInterest": "n/a"}]}}
    polled = parse_open_interest(payload, ts=7)
    assert polled.rows == []
    assert polled.malformed == [(_IID, "openInterest 'n/a' is not a finite decimal")]


def test_config_defaults_and_validation(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('instruments = ["BTCUSDT-LINEAR.BYBIT"]\nopen_interest_poll_seconds = 300\n')
    cfg, plan = load_venue_config(path, "BYBIT")
    assert (cfg.environment, cfg.snapshot_interval_seconds, plan.collected) == (
        "mainnet",
        1.0,
        (_IID,),
    )
    assert isinstance(cfg, BybitConfig)
    assert (cfg.open_interest_poll_seconds, cfg.stale_book_seconds) == (300, 5.0)
    path.write_text('environment = "prod"\n')
    with pytest.raises(ValueError, match="environment"):
        load_venue_config(path, "BYBIT")
    path.write_text("open_interest_poll_seconds = 0\n")
    with pytest.raises(ValueError, match="open_interest_poll_seconds"):
        load_venue_config(path, "BYBIT")


def test_spot_id_never_in_open_interest() -> None:
    payload = {"result": {"list": [{"symbol": "BTCUSDT", "openInterest": "1"}]}}
    ids = {str(item.instrument_id) for item in parse_open_interest(payload, ts=1).rows}
    assert ids == {"BTCUSDT-LINEAR.BYBIT"}
    assert not any("-SPOT" in i for i in ids)


def test_client_routes_by_product_type() -> None:
    from capture.venues.bybit.client import BybitClient
    from nautilus_trader.core.nautilus_pyo3 import BybitProductType

    client = BybitClient(on_data=lambda _: None, ledger=error_ledger.record)
    ws, pt = client._ws_for("BTCUSDT-SPOT.BYBIT")
    assert ws is client._ws_spot
    assert pt == BybitProductType.SPOT
    ws, pt = client._ws_for("BTCUSDT-LINEAR.BYBIT")
    assert ws is client._ws_linear
    assert pt == BybitProductType.LINEAR
    with pytest.raises(ValueError, match="unsupported"):
        client._ws_for("BTCUSD-INVERSE.BYBIT")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-inf", {"v": 1}, ["1"], True])
def test_a_non_finite_or_non_numeric_open_interest_is_malformed(value: object) -> None:
    payload = {"result": {"list": [{"symbol": "BTCUSDT", "openInterest": value}]}}
    polled = parse_open_interest(payload, ts=7)
    assert polled.rows == []
    assert [iid for iid, _ in polled.malformed] == [_IID]
