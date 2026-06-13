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

from pathlib import Path

import pytest

from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.data import TradeTick
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.data import TestDataStubs


@pytest.fixture
def catalog_dir(tmp_path) -> Path:
    """
    Provide a temp catalog root used as BOTH the streaming root and the conversion
    catalog root (Pitfall 5 — single shared root).
    """
    catalog_path = tmp_path / "catalog"
    catalog_path.mkdir(parents=True, exist_ok=True)
    return catalog_path


@pytest.fixture
def sample_toml() -> str:
    """
    Provide a sample recorder TOML configuration mirroring the CONTEXT `<specifics>`
    block: one linear instrument and one spot instrument.
    """
    return """
[recorder]
trader_id = "BYBIT-COLLECTOR-001"
catalog_path = "catalog"
streaming_path = "catalog/streaming"
conversion_interval_minutes = 60
environment = "mainnet"

[[instruments.linear]]
id = "BTCUSDT-LINEAR.BYBIT"
depth = 50
bar_intervals = ["1-MINUTE"]

[[instruments.spot]]
id = "ETHUSDT-SPOT.BYBIT"
depth = 50
bar_intervals = ["1-MINUTE"]
"""


@pytest.fixture
def mock_cache():
    """
    Provide a real `Cache` instance (required by `Portfolio`'s PyO3 `CacheFacade`
    type check). `cache.instrument(instrument_id)` naturally returns `None` for any
    instrument not registered via `cache.add_instrument(...)`, so tests configure
    "present" instruments by calling `add_instrument` and leave others unregistered
    to simulate "missing" (Cython `cdef class` methods cannot be monkeypatched).
    """
    return Cache(database=None)


@pytest.fixture
def sample_trade_ticks() -> list[TradeTick]:
    """
    Provide a short list of `TradeTick` objects with strictly monotonically
    increasing `ts_init` (required by the catalog's monotonic-ts_init contract).
    """
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    return [
        TestDataStubs.trade_tick(
            instrument=instrument,
            price=50_000.0 + i,
            size=0.01,
            ts_event=1_000_000_000 * (i + 1),
            ts_init=1_000_000_000 * (i + 1),
        )
        for i in range(3)
    ]
