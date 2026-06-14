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

import pandas as pd

from nautilus_trader.common.component import TestClock
from nautilus_trader.model.data import TradeTick
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from nautilus_trader.persistence.writer import RotationMode
from nautilus_trader.persistence.writer import StreamingFeatherWriter
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.data import TestDataStubs
from scripts.bybit_recorder.strategy import RecorderStrategy
from scripts.bybit_recorder.strategy import RecorderStrategyConfig


# Hardcoded valid v4 UUID string (D-03) — same constant used by the recorder strategy.
RECORDER_INSTANCE_ID = "8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f"

# A realistic (19-digit) base wall-clock time. `_list_feather_data_files` sorts
# feather filenames (which embed `clock.timestamp_ns()` at creation) lexically,
# so all generated timestamps must share the same digit width for that sort to
# match chronological order -- a `TestClock` starting at epoch 0 would produce
# `0`, `70000000000`, `140000000000` (different widths, sorts incorrectly).
_BASE_TIME_NS = 1_700_000_000_000_000_000


def _build_strategy(catalog_dir) -> RecorderStrategy:
    config = RecorderStrategyConfig(
        instrument_ids=[],
        linear_instrument_ids=[],
        instrument_depths={},
        instrument_bar_intervals={},
        catalog_path=str(catalog_dir),
        instance_id_str=RECORDER_INSTANCE_ID,
        conversion_interval_minutes=1,
        rotation_interval_minutes=1,
    )
    return RecorderStrategy(config=config)


def test_convert_finalized_feather_files_skips_active_file(catalog_dir):
    # Regression test for the "non-disjoint intervals" ValueError raised by a
    # long-running recorder's periodic `_convert_stream` timer (REL-01/Pitfall 2):
    # `convert_stream_to_data` re-reads a feather file in full each cycle and
    # recomputes its (start, end) interval, so converting the still-open,
    # most-recently-created file on a later cycle (once it has grown) overlaps
    # the interval already written for it.
    #
    # `_convert_finalized_feather_files` avoids this by only converting feather
    # files that have already been rotated out -- the most-recently-created file
    # per identifier is always skipped, so a file's interval is computed exactly
    # once, after it can never change again.
    catalog = ParquetDataCatalog(str(catalog_dir))
    cache = TestComponentStubs.cache()
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    cache.add_instrument(instrument)
    clock = TestClock()

    writer = StreamingFeatherWriter(
        path=f"{catalog_dir}/live/{RECORDER_INSTANCE_ID}",
        cache=cache,
        clock=clock,
        fs_protocol="file",
        include_types=[TradeTick],
        flush_interval_ms=0,
        rotation_mode=RotationMode.INTERVAL,
        rotation_interval=pd.Timedelta(seconds=60),
    )
    strategy = _build_strategy(catalog_dir)

    def _tick(ts: int) -> TradeTick:
        return TestDataStubs.trade_tick(
            instrument=instrument,
            price=50_000.0,
            size=0.01,
            ts_event=ts,
            ts_init=ts,
        )

    # tick1 -> file A. No rotation yet (next_rotation_time just initialized).
    clock.set_time(_BASE_TIME_NS)
    writer.write(_tick(1_000_000_000))
    writer.flush()

    # tick2 -> file A (still active at write-time); rotation check AFTER this
    # write triggers (now=+70s >= next_rotation=+60s) -> file B created (empty,
    # active). File A is now finalized: [tick1, tick2].
    clock.set_time(_BASE_TIME_NS + 70_000_000_000)
    writer.write(_tick(2_000_000_000))
    writer.flush()

    # tick3 -> file B; rotation check AFTER this write triggers again
    # (now=+140s >= next_rotation=+130s) -> file C created (empty, active). File
    # B is now finalized: [tick3].
    clock.set_time(_BASE_TIME_NS + 140_000_000_000)
    writer.write(_tick(3_000_000_000))
    writer.flush()

    # Act: cycle 1 -- converts finalized files A and B, skips active file C.
    strategy._convert_finalized_feather_files(catalog, TradeTick)
    trades_after_first = catalog.trade_ticks(instrument_ids=[str(instrument.id)])

    # Assert: both finalized files converted, no raise.
    assert len(trades_after_first) == 3

    # tick4 -> file C; rotation check AFTER this write triggers again
    # (now=+200s >= next_rotation=+200s) -> file D created (empty, active). File
    # C is now finalized: [tick4].
    clock.set_time(_BASE_TIME_NS + 200_000_000_000)
    writer.write(_tick(4_000_000_000))
    writer.flush()

    # Act: cycle 2 -- re-converting A and B is idempotent (no raise); converts
    # newly-finalized file C; skips active file D.
    strategy._convert_finalized_feather_files(catalog, TradeTick)
    trades_after_second = catalog.trade_ticks(instrument_ids=[str(instrument.id)])

    # Assert: no data loss, no non-disjoint-interval raise.
    assert len(trades_after_second) == 4
