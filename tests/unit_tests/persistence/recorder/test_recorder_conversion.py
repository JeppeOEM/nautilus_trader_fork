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

from nautilus_trader.common.component import TestClock
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from nautilus_trader.persistence.writer import StreamingFeatherWriter
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.data import TestDataStubs


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


def test_double_conversion_same_day_behavior(catalog_dir, sample_trade_ticks):
    # Documents the partial-day re-conversion question (Pitfall 2 / A2):
    # converting the same in-progress feather file twice in one day, without any
    # new data being appended in between, is IDEMPOTENT — the second call observes
    # the same start/end timestamps, derives the same target parquet filename
    # ({start}_{end}.parquet), finds it already exists, prints
    # "already exists, skipping write", and returns without raising (parquet.py
    # ~2602-2604). This is the case that matters for the recorder's in-process
    # conversion timer: re-running convert_stream_to_data on an un-rotated (open)
    # feather file between daily rotations is safe and a no-op on the second+ call.
    #
    # A2 RESULT: (a) idempotent skip with stable reloaded row count — NOT a raise.
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

    # Act: convert the same un-rotated feather file twice in a row.
    catalog.convert_stream_to_data(
        instance_id=RECORDER_INSTANCE_ID,
        data_cls=TradeTick,
        subdirectory="live",
    )
    trades_after_first = catalog.trade_ticks(
        instrument_ids=[str(sample_trade_ticks[0].instrument_id)],
    )

    catalog.convert_stream_to_data(
        instance_id=RECORDER_INSTANCE_ID,
        data_cls=TradeTick,
        subdirectory="live",
    )
    trades_after_second = catalog.trade_ticks(
        instrument_ids=[str(sample_trade_ticks[0].instrument_id)],
    )

    # Assert: second conversion is a no-op — same stable row count, no exception.
    assert len(trades_after_first) > 0
    assert len(trades_after_second) == len(trades_after_first)
    assert all(isinstance(t, TradeTick) for t in trades_after_second)


def test_repeated_conversion_with_new_appended_rows_does_not_raise(
    catalog_dir,
    sample_trade_ticks,
):
    # Regression test for the "non-disjoint intervals" ValueError raised by a
    # long-running recorder's periodic `_convert_stream` timer (REL-01). Unlike
    # `test_double_conversion_same_day_behavior` (no new data between calls), this
    # test appends NEW rows to the still-open feather file between two
    # `convert_stream_to_data` calls -- the scenario that previously raised
    # `ValueError: ... would create non-disjoint intervals` because the second
    # conversion re-read the whole feather file and recomputed an (start, end)
    # interval that overlapped the first conversion's already-written parquet
    # file (same start, later end).
    #
    # FIX: `_convert_feather_table_to_parquet` now trims the re-read feather table
    # down to rows newer than the latest already-converted interval before
    # computing (start, end), so the second conversion only persists the new tail
    # and the two parquet files have disjoint intervals.
    # Arrange
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
    )

    for tick in sample_trade_ticks:
        writer.write(tick)
    writer.flush()

    # Act: first conversion cycle.
    catalog.convert_stream_to_data(
        instance_id=RECORDER_INSTANCE_ID,
        data_cls=TradeTick,
        subdirectory="live",
    )
    trades_after_first = catalog.trade_ticks(
        instrument_ids=[str(sample_trade_ticks[0].instrument_id)],
    )

    # New rows arrive and are appended to the SAME (un-rotated) feather file.
    more_ticks = [
        TestDataStubs.trade_tick(
            instrument=instrument,
            price=51_000.0 + i,
            size=0.01,
            ts_event=1_000_000_000 * (i + 10),
            ts_init=1_000_000_000 * (i + 10),
        )
        for i in range(3)
    ]
    for tick in more_ticks:
        writer.write(tick)
    writer.flush()

    # Act: second conversion cycle -- must NOT raise.
    catalog.convert_stream_to_data(
        instance_id=RECORDER_INSTANCE_ID,
        data_cls=TradeTick,
        subdirectory="live",
    )
    trades_after_second = catalog.trade_ticks(
        instrument_ids=[str(sample_trade_ticks[0].instrument_id)],
    )

    # Assert: no data loss, and the new rows are now present too.
    assert len(trades_after_first) == len(sample_trade_ticks)
    assert len(trades_after_second) == len(sample_trade_ticks) + len(more_ticks)


def test_convert_stream_to_data_roundtrips_quote_ticks(catalog_dir, sample_quote_ticks):
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
        include_types=[QuoteTick],
    )
    for quote in sample_quote_ticks:
        writer.write(quote)
    writer.close()

    # Act
    catalog.convert_stream_to_data(
        instance_id=RECORDER_INSTANCE_ID,
        data_cls=QuoteTick,
        subdirectory="live",
    )
    quotes = catalog.quote_ticks(instrument_ids=[str(sample_quote_ticks[0].instrument_id)])

    # Assert
    assert len(quotes) > 0
    assert all(isinstance(q, QuoteTick) for q in quotes)


def test_convert_stream_to_data_roundtrips_order_book_deltas(catalog_dir, sample_order_book_deltas):
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
        # NOTE: the writer's include_types filter compares against
        # `obj.__class__`, which is `OrderBookDeltas` for the deltas wrapper
        # object actually published on the message bus (the per-instrument
        # writer schema is then mapped internally to OrderBookDelta).
        include_types=[OrderBookDeltas],
    )
    for deltas in sample_order_book_deltas:
        writer.write(deltas)
    writer.close()

    # Act
    catalog.convert_stream_to_data(
        instance_id=RECORDER_INSTANCE_ID,
        data_cls=OrderBookDelta,
        subdirectory="live",
    )
    deltas_read = catalog.order_book_deltas(
        instrument_ids=[str(sample_order_book_deltas[0].instrument_id)],
    )

    # Assert
    assert len(deltas_read) > 0
    assert all(isinstance(d, OrderBookDelta) for d in deltas_read)


def test_convert_stream_to_data_roundtrips_bars(catalog_dir, sample_bars):
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
        include_types=[Bar],
    )
    for bar in sample_bars:
        writer.write(bar)
    writer.close()

    # Act
    catalog.convert_stream_to_data(
        instance_id=RECORDER_INSTANCE_ID,
        data_cls=Bar,
        subdirectory="live",
    )
    bars = catalog.bars(bar_types=[str(sample_bars[0].bar_type)])

    # Assert
    assert len(bars) > 0
    assert all(isinstance(b, Bar) for b in bars)


def test_convert_stream_to_data_roundtrips_mark_prices(catalog_dir, sample_mark_prices):
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
        include_types=[MarkPriceUpdate],
    )
    for mark_price in sample_mark_prices:
        writer.write(mark_price)
    writer.close()

    # Act
    catalog.convert_stream_to_data(
        instance_id=RECORDER_INSTANCE_ID,
        data_cls=MarkPriceUpdate,
        subdirectory="live",
    )
    marks = catalog.query(
        data_cls=MarkPriceUpdate,
        identifiers=[str(sample_mark_prices[0].instrument_id)],
    )

    # Assert
    assert len(marks) > 0
    assert all(isinstance(m, MarkPriceUpdate) for m in marks)


# RESOLVED PERSISTENCE PATH (Open Question 1, Task 1 empirical spike):
#
# `FundingRateUpdate` is intentionally EXCLUDED from the kernel's "*" writer
# `include_types` (Plan 01 / D-01) — auto-writing the ~100ms funding ticker would
# flood the catalog. The deduped funding row is therefore persisted via a
# STRATEGY-OWNED `StreamingFeatherWriter` whose `include_types` DOES include
# `FundingRateUpdate` (a second writer, separate from the kernel's "*" writer).
# `class_to_filename(FundingRateUpdate)` resolves to the NATIVE
# `funding_rate_update` table (no `custom_` prefix — verified empirically and via
# nautilus_trader/persistence/funcs.py), so `catalog.convert_stream_to_data(...)`
# converts it like any other native type, and it reads back natively through
# `catalog.funding_rates(instrument_ids=[...])` as `FundingRateUpdate` instances.
# This is the contract Task 2's `on_funding_rate` dedup-and-persist path implements.
def test_convert_stream_to_data_roundtrips_funding_rates(catalog_dir, sample_funding_rates):
    # Arrange
    catalog = ParquetDataCatalog(str(catalog_dir))
    cache = TestComponentStubs.cache()
    cache.add_instrument(TestInstrumentProvider.btcusdt_perp_binance())
    clock = TestClock()

    # A strategy-owned writer (separate from the kernel's "*" writer, which
    # excludes FundingRateUpdate per D-01) that DOES include FundingRateUpdate.
    writer = StreamingFeatherWriter(
        path=f"{catalog_dir}/live/{RECORDER_INSTANCE_ID}",
        cache=cache,
        clock=clock,
        fs_protocol="file",
        include_types=[FundingRateUpdate],
    )
    for funding_rate in sample_funding_rates:
        writer.write(funding_rate)
    writer.close()

    # Act
    catalog.convert_stream_to_data(
        instance_id=RECORDER_INSTANCE_ID,
        data_cls=FundingRateUpdate,
        subdirectory="live",
    )
    funding_rates = catalog.funding_rates(
        instrument_ids=[str(sample_funding_rates[0].instrument_id)],
    )

    # Assert
    assert len(funding_rates) > 0
    assert all(isinstance(f, FundingRateUpdate) for f in funding_rates)


def test_convert_stream_to_data_roundtrips_index_prices(catalog_dir, sample_index_prices):
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
        include_types=[IndexPriceUpdate],
    )
    for index_price in sample_index_prices:
        writer.write(index_price)
    writer.close()

    # Act
    catalog.convert_stream_to_data(
        instance_id=RECORDER_INSTANCE_ID,
        data_cls=IndexPriceUpdate,
        subdirectory="live",
    )
    indices = catalog.query(
        data_cls=IndexPriceUpdate,
        identifiers=[str(sample_index_prices[0].instrument_id)],
    )

    # Assert
    assert len(indices) > 0
    assert all(isinstance(i, IndexPriceUpdate) for i in indices)
