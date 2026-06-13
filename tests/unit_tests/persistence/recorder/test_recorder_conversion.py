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

import pytest

from nautilus_trader.common.component import TestClock
from nautilus_trader.model.data import TradeTick
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from nautilus_trader.persistence.writer import StreamingFeatherWriter
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.component import TestComponentStubs


# Hardcoded valid v4 UUID string (D-03) — same constant used by the recorder strategy.
RECORDER_INSTANCE_ID = "8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f"


def test_convert_stream_to_data_roundtrips_trade_ticks(catalog_dir, sample_trade_ticks):
    # Arrange
    catalog = ParquetDataCatalog(str(catalog_dir))
    cache = TestComponentStubs.cache()
    cache.add_instrument(TestInstrumentProvider.btcusdt_perp_binance())
    clock = TestClock()

    writer = StreamingFeatherWriter(
        path=f"{catalog_dir}/live/{RECORDER_INSTANCE_ID}",
        cache=cache,
        clock=clock,
        fs_protocol="file",
        include_types=[TradeTick],
    )
    for tick in sample_trade_ticks:
        writer.write(tick)
    writer.close()

    # Act
    catalog.convert_stream_to_data(
        instance_id=RECORDER_INSTANCE_ID,
        data_cls=TradeTick,
        subdirectory="live",
    )
    trades = catalog.trade_ticks(instrument_ids=[str(sample_trade_ticks[0].instrument_id)])

    # Assert
    assert len(trades) > 0
    assert all(isinstance(t, TradeTick) for t in trades)


@pytest.mark.skip(reason="A2 — empirical: verified in plan 04")
def test_double_conversion_same_day_behavior():
    # Documents the partial-day re-conversion question (Pitfall 2 / A2):
    # converting the same in-progress feather file twice in one day may raise
    # ValueError on the second call (non-disjoint interval). This contract is
    # recorded here but not yet asserted; Plan 04 verifies the actual behavior.
    ...
