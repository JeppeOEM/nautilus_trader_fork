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

import os

import pytest

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
from scripts.bybit_recorder.strategy import RecorderStrategy
from scripts.bybit_recorder.strategy import RecorderStrategyConfig


# Hardcoded valid v4 UUID string (D-03) — same constant used by the recorder strategy.
RECORDER_INSTANCE_ID = "8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f"

# A realistic (19-digit) base wall-clock time so feather filenames (which embed
# `clock.timestamp_ns()` at creation) share a constant digit width and therefore
# sort chronologically under `_list_feather_data_files`'s lexical sort (mirrors
# `test_recorder_rotation_conversion.py`).
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


def test_restart_shaped_conversion_converts_finalized_file_without_raise(
    catalog_dir,
    sample_trade_ticks,
):
    # REL-02 restart-shaped regression (03-CONTEXT.md D-02/D-03 + the resolved
    # non-disjoint-intervals debug note, Approach B): model TWO PROCESS STARTS
    # against the SAME instance_id path. Process 1 writes a feather file; process 2
    # (a NEW StreamingFeatherWriter with a strictly-later creation timestamp)
    # finalizes process 1's file by creating its own active file. On process 2's
    # FIRST conversion cycle, `_convert_finalized_feather_files` (files[:-1] per
    # identifier) must convert process 1's now-finalized file IN FULL while
    # correctly SKIPPING process 2's still-active file — no raise, no gap, no
    # overlap, no "non-disjoint intervals" ValueError. parquet.py is NOT modified
    # (Pitfall 2: do not re-introduce interval bookkeeping).
    catalog = ParquetDataCatalog(str(catalog_dir))
    cache = TestComponentStubs.cache()
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    cache.add_instrument(instrument)

    # --- Process 1: write sample_trade_ticks, flush -> process 1's active file. ---
    clock_p1 = TestClock()
    clock_p1.set_time(_BASE_TIME_NS)
    writer_p1 = StreamingFeatherWriter(
        path=f"{catalog_dir}/live/{RECORDER_INSTANCE_ID}",
        cache=cache,
        clock=clock_p1,
        fs_protocol="file",
        include_types=[TradeTick],
        flush_interval_ms=0,
    )
    for tick in sample_trade_ticks:
        writer_p1.write(tick)
    writer_p1.flush()

    # --- Process 2: a NEW writer against the SAME path with a strictly-later
    # creation timestamp. Creating it stamps a new filename, finalizing process
    # 1's file. Write 3 more strictly-later ticks to process 2's active file. ---
    clock_p2 = TestClock()
    clock_p2.set_time(_BASE_TIME_NS + 70_000_000_000)
    writer_p2 = StreamingFeatherWriter(
        path=f"{catalog_dir}/live/{RECORDER_INSTANCE_ID}",
        cache=cache,
        clock=clock_p2,
        fs_protocol="file",
        include_types=[TradeTick],
        flush_interval_ms=0,
    )
    base_ts = sample_trade_ticks[-1].ts_init
    for i in range(3):
        ts = base_ts + 1_000_000_000 * (i + 1)
        writer_p2.write(
            TestDataStubs.trade_tick(
                instrument=instrument,
                price=60_000.0 + i,
                size=0.01,
                ts_event=ts,
                ts_init=ts,
            ),
        )
    writer_p2.flush()

    # Snapshot the on-disk feather files BEFORE conversion (real filesystem) so the
    # MEM-06 deletion assertions below operate on actual paths the catalog will list.
    listed_before = list(
        catalog._list_feather_data_files(
            kind="live",
            instance_id=RECORDER_INSTANCE_ID,
            data_cls=TradeTick,
        ),
    )
    paths_before = sorted(f.path for f in listed_before)
    assert len(paths_before) == 2  # process-1 finalized + process-2 active
    finalized_path, active_path = paths_before[0], paths_before[1]
    assert os.path.exists(finalized_path)
    assert os.path.exists(active_path)

    # --- Process 2's first conversion cycle. ---
    strategy = _build_strategy(catalog_dir)
    strategy._convert_finalized_feather_files(catalog, TradeTick)

    # Assert: process 1's finalized file converted in full (exactly its row count);
    # process 2's still-active file correctly skipped (no premature conversion, no
    # data loss for the finalized file, no non-disjoint raise).
    trades = catalog.trade_ticks(instrument_ids=[str(sample_trade_ticks[0].instrument_id)])
    assert len(trades) == len(sample_trade_ticks)
    assert all(isinstance(t, TradeTick) for t in trades)

    # MEM-06 (real-filesystem): the finalized feather source is now DELETED (never to
    # be re-read on a future pass), while the still-active file is UNTOUCHED on disk.
    assert not os.path.exists(finalized_path)
    assert os.path.exists(active_path)


# =====================================================================================
# MEM-06 — feather source deletion after conversion (cycle-5 memory-leak fix)
# =====================================================================================
#
# Root cause (code-confirmed): `_convert_finalized_feather_files` re-read EVERY
# already-converted feather file into an in-memory pyarrow Table on EVERY conversion
# pass (periodic + every Ctrl+C/on_stop) because feather sources were never deleted.
# The "already exists, skipping write" parquet skip lives INSIDE
# `_convert_feather_table_to_parquet`, AFTER the expensive read, so it never spared
# the read. Backlog (and per-pass RAM) therefore grew with total recorder uptime.
# FIX: delete each finalized feather source after a successful read+convert so it is
# never listed/re-read again. The tests below lock the four safety properties.


class _FakeFeatherFile:
    # Mirrors the `.path` attribute `_list_feather_data_files` yields (the only
    # attribute `_convert_finalized_feather_files` touches).
    def __init__(self, path: str) -> None:
        self.path = path


class _FakeCatalogFs:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def rm(self, path: str) -> None:
        self.removed.append(path)


class _FakeCatalog:
    # Minimal stand-in for ParquetDataCatalog exposing only the methods
    # `_convert_finalized_feather_files` calls, so deletion-vs-no-deletion can be
    # asserted deterministically without a real filesystem write.
    def __init__(
        self,
        feather_files: list[_FakeFeatherFile],
        *,
        read_returns_none_for: set[str] | None = None,
        convert_raises_for: set[str] | None = None,
    ) -> None:
        self._feather_files = feather_files
        self._read_returns_none_for = read_returns_none_for or set()
        self._convert_raises_for = convert_raises_for or set()
        self.fs = _FakeCatalogFs()
        self.read_calls: list[str] = []
        self.convert_calls: list[str] = []

    def _list_feather_data_files(self, **kwargs) -> list[_FakeFeatherFile]:
        return list(self._feather_files)

    def _read_feather_file(self, path: str):
        self.read_calls.append(path)
        if path in self._read_returns_none_for:
            return None
        return object()  # non-None sentinel "table"

    def _convert_feather_table_to_parquet(self, *, feather_path, **kwargs) -> None:
        self.convert_calls.append(feather_path)
        if feather_path in self._convert_raises_for:
            raise RuntimeError(f"convert boom for {feather_path}")
        # NOTE: the already-exists/skip-write case ALSO returns normally (no raise)
        # in real parquet.py — so "returns normally" is the success signal the
        # deletion keys off, exactly as in production.


def test_mem06_deletes_feather_source_after_successful_conversion(catalog_dir):
    # (a) A finalized feather file IS deleted once read+convert succeeds, so it is
    # never re-listed/re-read on a future pass.
    finalized = _FakeFeatherFile("/cat/data/trade_tick/BTC/file_finalized.feather")
    active = _FakeFeatherFile("/cat/data/trade_tick/BTC/file_active.feather")
    catalog = _FakeCatalog([finalized, active])
    strategy = _build_strategy(catalog_dir)

    strategy._convert_finalized_feather_files(catalog, TradeTick)

    assert catalog.convert_calls == [finalized.path]  # active never converted
    assert catalog.fs.removed == [finalized.path]  # finalized deleted


def test_mem06_deletes_feather_source_even_when_parquet_write_skipped(catalog_dir):
    # (b) The USER'S EXACT OBSERVED SCENARIO: the parquet destination already exists,
    # so `_convert_feather_table_to_parquet` prints "already exists, skipping write"
    # and returns WITHOUT raising. The data is durably in parquet either way, so the
    # feather source must STILL be deleted — otherwise it is re-read forever.
    # The _FakeCatalog convert returns normally (no raise) for this file, exactly
    # mirroring the already-exists early-return in real parquet.py (line ~2603).
    finalized = _FakeFeatherFile("/cat/data/quote_tick/ETH/file_already_converted.feather")
    active = _FakeFeatherFile("/cat/data/quote_tick/ETH/file_active.feather")
    catalog = _FakeCatalog([finalized, active])  # convert never raises -> skip-write success
    strategy = _build_strategy(catalog_dir)

    strategy._convert_finalized_feather_files(catalog, QuoteTick)

    assert catalog.read_calls == [finalized.path]
    assert catalog.fs.removed == [finalized.path]


def test_mem06_does_not_delete_when_conversion_raises(catalog_dir):
    # (c) If `_convert_feather_table_to_parquet` raises, the data is NOT confirmed
    # durable in parquet — the source must be KEPT (the per-type try/except in
    # `_run_conversion` swallows the raise upstream; the rm must never run for it).
    finalized = _FakeFeatherFile("/cat/data/trade_tick/BTC/file_finalized.feather")
    active = _FakeFeatherFile("/cat/data/trade_tick/BTC/file_active.feather")
    catalog = _FakeCatalog([finalized, active], convert_raises_for={finalized.path})
    strategy = _build_strategy(catalog_dir)

    with pytest.raises(RuntimeError, match="convert boom"):
        strategy._convert_finalized_feather_files(catalog, TradeTick)

    assert catalog.fs.removed == []  # nothing deleted on failed convert


def test_mem06_does_not_delete_when_read_returns_none(catalog_dir):
    # (c, variant) A missing/corrupt read (`_read_feather_file` -> None) is `continue`d
    # BEFORE convert; the source must be KEPT for a retry next pass (never deleted, and
    # never even handed to convert).
    finalized = _FakeFeatherFile("/cat/data/trade_tick/BTC/file_corrupt.feather")
    active = _FakeFeatherFile("/cat/data/trade_tick/BTC/file_active.feather")
    catalog = _FakeCatalog([finalized, active], read_returns_none_for={finalized.path})
    strategy = _build_strategy(catalog_dir)

    strategy._convert_finalized_feather_files(catalog, TradeTick)

    assert catalog.convert_calls == []  # None read never reaches convert
    assert catalog.fs.removed == []  # and is never deleted


def test_mem06_never_touches_the_still_active_file(catalog_dir):
    # (d) The most-recent (still actively-written) file per directory — excluded via
    # `files[:-1]` — must NEVER be read, converted, OR deleted. Two directories, so the
    # last file in EACH is protected.
    btc_old = _FakeFeatherFile("/cat/data/trade_tick/BTC/file_old.feather")
    btc_active = _FakeFeatherFile("/cat/data/trade_tick/BTC/file_active.feather")
    eth_active = _FakeFeatherFile("/cat/data/trade_tick/ETH/file_active.feather")
    catalog = _FakeCatalog([btc_old, btc_active, eth_active])
    strategy = _build_strategy(catalog_dir)

    strategy._convert_finalized_feather_files(catalog, TradeTick)

    # Only the BTC old file is finalized; both active files are untouched entirely.
    assert catalog.read_calls == [btc_old.path]
    assert catalog.convert_calls == [btc_old.path]
    assert catalog.fs.removed == [btc_old.path]
    assert btc_active.path not in catalog.fs.removed
    assert eth_active.path not in catalog.fs.removed


def test_mem06_deletion_failure_does_not_crash_the_pass(catalog_dir):
    # (e) A deletion failure (e.g. permissions / vanished file) must be logged via
    # self.log (MEM-05 pyo3) but NEVER crash the conversion pass — the data is already
    # durably in parquet, so the pass must continue and still process later files.
    file_a = _FakeFeatherFile("/cat/data/trade_tick/BTC/file_a.feather")
    file_b = _FakeFeatherFile("/cat/data/trade_tick/BTC/file_b.feather")
    active = _FakeFeatherFile("/cat/data/trade_tick/BTC/file_active.feather")
    catalog = _FakeCatalog([file_a, file_b, active])

    # rm raises for file_a only; the pass must still convert+delete file_b after.
    original_rm = catalog.fs.rm

    def _rm(path: str) -> None:
        if path == file_a.path:
            raise PermissionError("cannot remove")
        original_rm(path)

    catalog.fs.rm = _rm  # type: ignore[method-assign]
    strategy = _build_strategy(catalog_dir)

    # Must not raise despite the rm failure on file_a.
    strategy._convert_finalized_feather_files(catalog, TradeTick)

    # Both finalized files were converted; file_b was still deleted after file_a's
    # rm failure (the failure did not abort the remaining work).
    assert catalog.convert_calls == [file_a.path, file_b.path]
    assert catalog.fs.removed == [file_b.path]
