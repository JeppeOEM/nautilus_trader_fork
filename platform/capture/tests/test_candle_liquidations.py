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
Story 33.3: capture hands each flush's archived liquidations to the second sink before its seconds
(they set the feed start the seconds are bounded by, review loop 2), under the same isolation and
one-line-per-flush ledgering, and re-applies the last day of them at start for every feed id of the
watermarks and the plan (the sink's `venue_event_id` dedup makes the overlap harmless).
"""

import asyncio
import logging
import time
from pathlib import Path

import pytest
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from observability import error_ledger

from capture.application import sites
from capture.tests.test_collector import _BYBIT
from capture.tests.test_collector import _collector
from capture.tests.test_collector import _RecordingSink
from nautilus_trader.model.identifiers import InstrumentId


_SPOT = "BTCUSDT-SPOT.BYBIT"


def _liquidation(key: str, iid: str = _BYBIT, ts_ns: int | None = None) -> Liquidation:
    ts = time.time_ns() if ts_ns is None else ts_ns
    return Liquidation.from_wire_text(
        InstrumentId.from_str(iid), LiquidatedSide.LONG, "0.010", "60000.10", (2, 3), key, ts, ts
    )


def test_flushed_liquidations_reach_the_sink(tmp_path: Path) -> None:
    sink = _RecordingSink()
    c = _collector(tmp_path, second_sink=sink)
    c.ingest_rows([_liquidation("k1"), _liquidation("k2")], sites.LIQUIDATION_FEED)
    asyncio.run(c._flush_once())
    assert sink.liquidations == {_BYBIT: ["k1", "k2"]}
    assert sink.calls == [f"{_BYBIT} (liquidations)"]


def test_a_failing_liquidation_apply_is_one_ledger_line_naming_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sink = _RecordingSink()
    sink.failing_liquidations.add(_BYBIT)
    c = _collector(tmp_path, second_sink=sink)
    c.ingest_rows([_liquidation("k1")], sites.LIQUIDATION_FEED)
    error_ledger.reset()
    with caplog.at_level(logging.ERROR):
        asyncio.run(c._flush_once())
    detail = "\n".join(r.getMessage() for r in caplog.records)
    assert f"failed for 1 instruments (liquidations: {_BYBIT})" in detail
    assert "OSError on 1 writes" in detail
    # Where the rows come back from: they are archived, never retried live.
    assert "next start's catch-up" in detail
    assert "nightly rebuild" in detail
    assert error_ledger.counts() == {"collector.candle_store": 1}
    error_ledger.reset()


def test_the_flush_line_counts_instruments_not_writes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    An instrument whose liquidations and seconds both failed is one instrument, named under both
    kinds: 2 instruments, 3 failed writes (BTC liquidations + seconds, spot seconds).
    """
    sink = _RecordingSink(failing_with={_BYBIT: OSError, _SPOT: OSError})
    sink.failing_liquidations.add(_BYBIT)
    c = _collector(tmp_path, second_sink=sink)
    error_ledger.reset()
    with caplog.at_level(logging.ERROR):
        c._apply_to_candle_store({_BYBIT: [], _SPOT: []}, {_BYBIT: [_liquidation("k1")]})
    (record,) = [r for r in caplog.records if "candle store write failed" in r.getMessage()]
    assert (
        f"failed for 2 instruments (seconds: {_BYBIT}, {_SPOT}; liquidations: {_BYBIT})"
        in record.getMessage()
    )
    assert "OSError on 3 writes" in record.getMessage()
    assert error_ledger.counts() == {"collector.candle_store": 1}
    error_ledger.reset()


def test_liquidations_of_an_instrument_without_the_feed_are_ledgered_not_folded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Spot has no feed, so its `liq_*` are null and the store refuses its rows: they never reach the
    sink, and the contradiction is one ledger line per flush naming the instrument and row count.
    """
    sink = _RecordingSink()
    c = _collector(tmp_path, second_sink=sink)
    rows = [_liquidation("k1", _SPOT), _liquidation("k2", _SPOT)]
    error_ledger.reset()
    with caplog.at_level(logging.ERROR):
        c._apply_to_candle_store({}, {_SPOT: rows, _BYBIT: [_liquidation("k3")]})
    assert sink.calls == [f"{_BYBIT} (liquidations)"]
    detail = "\n".join(r.getMessage() for r in caplog.records)
    assert f"{_SPOT} (2)" in detail
    assert error_ledger.counts() == {"collector.candle_store": 1}
    error_ledger.reset()


def test_the_catch_up_reapplies_the_last_days_archived_liquidations(tmp_path: Path) -> None:
    """A crash between the Parquet flush and the sink write: the next start fills it, once."""
    failing = _RecordingSink()
    failing.failing_liquidations.add(_BYBIT)
    first = _collector(tmp_path, second_sink=failing)
    first.ingest_rows([_liquidation("k1")], sites.LIQUIDATION_FEED)
    asyncio.run(first._flush_once())  # archived; the sink write failed
    assert failing.liquidations == {}
    error_ledger.reset()

    sink = _RecordingSink()
    sink._through[_BYBIT] = time.time_ns()  # a watermark: the instrument is in the store
    restarted = _collector(tmp_path, second_sink=sink)
    restarted._catch_up_candle_store()
    restarted._catch_up_candle_store()  # a second start: the dedup keeps it at one
    assert sink.liquidations == {_BYBIT: ["k1"]}
    assert error_ledger.counts() == {}


def test_a_flushs_liquidations_reach_the_sink_before_its_seconds(tmp_path: Path) -> None:
    """The liquidations set the store's feed start, so the same flush's seconds are bounded."""
    sink = _RecordingSink()
    c = _collector(tmp_path, second_sink=sink)
    c._apply_to_candle_store({_BYBIT: []}, {_BYBIT: [_liquidation("k1")]})
    assert sink.calls == [f"{_BYBIT} (liquidations)", _BYBIT]


def test_the_liquidation_catch_up_covers_a_planned_id_without_a_watermark(tmp_path: Path) -> None:
    """A fresh store (no watermark yet): the plan's feed id still gets its last day's rows."""
    first = _collector(tmp_path, second_sink=_RecordingSink())
    first.ingest_rows([_liquidation("k1")], sites.LIQUIDATION_FEED)
    asyncio.run(first._flush_once())
    sink = _RecordingSink()
    restarted = _collector(tmp_path, second_sink=sink)
    restarted._catch_up_candle_store()
    assert sink.liquidations == {_BYBIT: ["k1"]}


def test_the_liquidation_catch_up_runs_when_the_seconds_are_a_day_behind(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Seconds more than a day behind are the rebuild's; the last day's liquidations still apply."""
    first = _collector(tmp_path, second_sink=_RecordingSink())
    first.ingest_rows([_liquidation("k1")], sites.LIQUIDATION_FEED)
    asyncio.run(first._flush_once())
    sink = _RecordingSink()
    sink._through[_BYBIT] = time.time_ns() - 2 * 86_400 * 1_000_000_000
    restarted = _collector(tmp_path, second_sink=sink)
    error_ledger.reset()
    with caplog.at_level(logging.ERROR):
        restarted._catch_up_candle_store()
    assert sink.liquidations == {_BYBIT: ["k1"]}
    assert _BYBIT not in sink.calls  # no seconds applied
    detail = "\n".join(r.getMessage() for r in caplog.records)
    assert "liquidations only over the last day" in detail
    assert error_ledger.counts() == {"collector.candle_store_behind": 1}
    error_ledger.reset()
