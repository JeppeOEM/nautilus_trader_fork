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
The capture -> archive handoff, end to end across the two contexts (Story 25.1; cross-cutting, so
it lives in `platform/tests` and may import both): markers capture writes are honoured by archive's
rebuild, a late trade capture archived is rebuilt into its exchange second, and archive's
`capture_exclusive` sees a running capture's lock. Skipped where the image ships neither context.
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest


if (
    importlib.util.find_spec("collector_core") is None
    or importlib.util.find_spec("archive") is None
):
    pytest.skip("needs the collector image (capture and archive)", allow_module_level=True)

from archive.application.rebuild_day import DayReport
from archive.application.rebuild_day import rebuild_day
from archive.application.repair import second_snapshots
from archive.infrastructure.gap_markers import GapMarkerFiles
from archive.infrastructure.gap_markers import load_gaps
from archive.infrastructure.maintenance_lock import capture_exclusive
from archive.infrastructure.maintenance_lock import maintenance
from collector_core.capture_lock import acquire_capture_lock
from collector_core.tests.test_collector import _BYBIT
from collector_core.tests.test_collector import _D0 as _BYBIT_D0
from collector_core.tests.test_collector import _clocked_trade as _bybit_trade
from collector_core.tests.test_collector import _day_collector as _bybit_day_collector
from collector_core.tests.test_collector import _deltas as _bybit_deltas
from collector_core.tests.test_collector import _sample_at as _bybit_sample_at
from collector_core.tests.test_venue_time import _D0 as _VENUE_D0
from collector_core.tests.test_venue_time import _IID as _VENUE_IID
from collector_core.tests.test_venue_time import _SEC as _VENUE_SEC
from collector_core.tests.test_venue_time import _book as _venue_book
from collector_core.tests.test_venue_time import _close as _venue_close
from collector_core.tests.test_venue_time import _collector as _venue_collector
from collector_core.tests.test_venue_time import _trade as _venue_trade
from observability import error_ledger

from nautilus_trader.model.data import TradeTick


_S_NS = 1_000_000_000


def _rebuild(tmp_path: Path, iid: str, day_start_ns: int) -> DayReport:
    """Rebuild the day capture wrote, wired as `archive.rebuild_seconds` wires it."""
    with maintenance(tmp_path) as writer:
        assert writer is not None
        return rebuild_day(str(tmp_path), iid, day_start_ns, writer, GapMarkerFiles(tmp_path), True)


def test_a_failed_trade_write_marks_a_gap_and_the_rebuild_keeps_live_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _bybit_day_collector(tmp_path)
    c._process_data(_bybit_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    c._process_data(_bybit_trade(1, _BYBIT_D0 + 1000 * _S_NS, _BYBIT_D0 + 1000 * _S_NS))
    _bybit_sample_at(c, 1000.5)
    asyncio.run(c._flush_once(final=True))  # the archive exists from second 1000 on
    c._process_data(_bybit_trade(2, _BYBIT_D0 + 2000 * _S_NS, _BYBIT_D0 + 2000 * _S_NS))
    live = _bybit_sample_at(c, 2000.5)
    write = c._archive.catalog.write_data

    def trades_fail(items: list) -> None:
        if isinstance(items[0], TradeTick):
            raise OSError("disk full")
        write(items)

    monkeypatch.setattr(c._archive.catalog, "write_data", trades_fail)
    asyncio.run(c._flush_once(final=True))  # the snapshot lands, its trade does not

    (gap,) = load_gaps(str(tmp_path), _BYBIT)
    assert gap[0] == _BYBIT_D0 + 2000 * _S_NS
    report = _rebuild(tmp_path, _BYBIT, _BYBIT_D0)
    (row,) = [
        s
        for s in second_snapshots(str(tmp_path), _BYBIT, 0, 1 << 62)
        if s.ts_event == live.ts_event
    ]
    assert (row.close_price, row.sell_count) == (live.close_price, 1)  # not zeroed
    # The row sits inside the write_failed marker's span: kept, and counted as in a gap.
    assert (report.in_gap, report.not_covered, report.rebuilt) == (1, 0, 1)


def test_a_late_trade_is_archived_counted_excluded_live_and_rebuilt_into_its_second(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    c = _venue_collector(tmp_path)
    c._process_data(_venue_book(100.0, 102.0, _VENUE_SEC - 0.5))
    c._process_data(
        _venue_trade(1, _VENUE_SEC - 0.5)
    )  # on time: the archive starts before second S
    _venue_close(c, _VENUE_SEC - 1)
    _venue_close(c, _VENUE_SEC)
    c._process_data(_venue_book(100.0, 102.0, _VENUE_SEC + 1.5))
    c._process_data(
        _venue_trade(2, _VENUE_SEC + 0.7, init_s=_VENUE_SEC + 1.9)
    )  # second S already closed
    assert c._intake(_VENUE_IID).late == 1
    assert [t.trade_id.value for t in c._buffer[(TradeTick, _VENUE_IID)]] == ["1", "2"]
    (live,) = _venue_close(c, _VENUE_SEC + 1)
    assert live.buy_count == 0  # not folded into the arrival second either
    c._report_stale_trades()
    assert error_ledger.counts()["collector.late_trade"] == 1
    asyncio.run(c._flush_once(final=True))
    _rebuild(tmp_path, _VENUE_IID, _VENUE_D0)
    rows = {s.ts_event // _S_NS: s for s in second_snapshots(str(tmp_path), _VENUE_IID, 0, 1 << 62)}
    assert (rows[_VENUE_SEC].buy_count, rows[_VENUE_SEC].close_price) == (1, 100.25)


def test_archive_sees_a_running_capture_and_capture_releases_it_on_exit(tmp_path: Path) -> None:
    async def scenario() -> None:
        lock = await acquire_capture_lock(
            tmp_path, "BYBIT", asyncio.Event(), ledger=error_ledger.record
        )
        assert lock is not None
        with capture_exclusive(tmp_path, "BYBIT") as exclusive:
            assert not exclusive  # a collector runs: repair_catalog must refuse
        with capture_exclusive(tmp_path, "DYDX") as other:
            assert other  # another venue's lock is free
        lock.close()  # the collector exits
        with capture_exclusive(tmp_path, "BYBIT") as exclusive:
            assert exclusive

    asyncio.run(scenario())
