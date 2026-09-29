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
"""The hourly zstd JSONL raw store and its reader: rotation, append, crash tails, prune."""

import shutil
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pytest

from verification.application import sites
from verification.infrastructure.raw_store import RawFileReader
from verification.infrastructure.raw_store import RawStore
from verification.infrastructure.raw_store import TruncatedTail
from verification.infrastructure.raw_store import bytes_by_day
from verification.infrastructure.raw_store import channel_file
from verification.infrastructure.raw_store import has_truncated_tail
from verification.infrastructure.raw_store import hour_of
from verification.infrastructure.raw_store import iter_records
from verification.infrastructure.zstd_frames import CorruptZstd
from verification.infrastructure.zstd_frames import _frame_end
from verification.infrastructure.zstd_frames import scan


_HOUR_NS = 3600 * 1_000_000_000
_T0 = int(datetime(2026, 9, 29, 13, 59, 58, tzinfo=UTC).timestamp()) * 1_000_000_000
_CHANNEL = "linear.publicTrade"


class _Ledger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def __call__(self, site: str, detail: str = "", exc: BaseException | None = None) -> None:
        self.records.append((site, detail))


def _store(root: Path, ledger: _Ledger, retain_days: int = 7) -> RawStore:
    return RawStore(root, "BYBIT", retain_days, ledger)


def _file(root: Path, ts_ns: int, channel: str = _CHANNEL) -> Path:
    return channel_file(root, "BYBIT", channel, hour_of(ts_ns))


def _values(path: Path, allow_truncated: bool = False) -> list[object]:
    return [record["i"] for record in iter_records(path, allow_truncated=allow_truncated)]


def test_lines_round_trip_verbatim_into_the_hour_of_their_own_timestamp(tmp_path: Path) -> None:
    ledger = _Ledger()
    store = _store(tmp_path, ledger)
    raw = '{"topic":"publicTrade.BTCUSDT","data":[{"p":"65000.10"}]} é'
    store.write(_CHANNEL, _T0, {"i": 0, "raw": raw})
    store.write(_CHANNEL, _T0 + 1, {"i": 1, "raw": raw})
    store.close()
    path = _file(tmp_path, _T0)
    assert path == tmp_path / "raw" / "bybit" / _CHANNEL / "2026-09-29T13.jsonl.zst"
    assert [r["raw"] for r in iter_records(path)] == [raw, raw]
    assert ledger.records == []


def test_an_hour_rollover_closes_the_old_file_and_opens_a_new_one(tmp_path: Path) -> None:
    store = _store(tmp_path, _Ledger())
    store.write(_CHANNEL, _T0, {"i": 0})
    store.write(_CHANNEL, _T0 + 3 * 1_000_000_000, {"i": 1})  # 14:00:01
    old, new = _file(tmp_path, _T0), _file(tmp_path, _T0 + 3 * 1_000_000_000)
    assert old.name == "2026-09-29T13.jsonl.zst"
    assert new.name == "2026-09-29T14.jsonl.zst"
    assert not has_truncated_tail(old), "the rotated-away hour's frame is finished"
    assert _values(old) == [0]
    store.close()
    assert _values(new) == [1]


def test_tick_flushes_and_rotates_a_quiet_channel(tmp_path: Path) -> None:
    store = _store(tmp_path, _Ledger())
    store.write(_CHANNEL, _T0, {"i": 0})
    store.tick(_T0)
    assert _values(_file(tmp_path, _T0), allow_truncated=True) == [0], "flushed while open"
    store.tick(_T0 + _HOUR_NS)
    assert _values(_file(tmp_path, _T0)) == [0], "closed at the rollover"
    store.close()


def test_a_restart_appends_a_new_frame_to_a_clean_file(tmp_path: Path) -> None:
    first = _store(tmp_path, _Ledger())
    first.write(_CHANNEL, _T0, {"i": 0})
    first.close()
    ledger = _Ledger()
    second = _store(tmp_path, ledger)
    second.write(_CHANNEL, _T0 + 1, {"i": 1})
    second.close()
    assert _values(_file(tmp_path, _T0)) == [0, 1]
    assert ledger.records == []


def _crashed_copy(tmp_path: Path, lines: int) -> Path:
    """Leave a file as a crash does: `lines` flushed lines in a frame never finished."""
    store = _store(tmp_path / "live", _Ledger())
    for i in range(lines):
        store.write(_CHANNEL, _T0, {"i": i, "pad": "x" * 40})
    store.tick(_T0)
    crashed = _file(tmp_path, _T0)
    crashed.parent.mkdir(parents=True)
    shutil.copy(_file(tmp_path / "live", _T0), crashed)
    store.close()
    return crashed


def test_the_reader_recovers_every_flushed_line_of_an_unfinished_frame(tmp_path: Path) -> None:
    crashed = _crashed_copy(tmp_path, 5000)
    reader = RawFileReader(crashed)
    assert sum(1 for _ in reader.lines()) == 5000
    assert reader.truncated
    with pytest.raises(TruncatedTail):
        _values(crashed)
    assert _values(crashed, allow_truncated=True) == list(range(5000))


def test_a_crash_tail_is_rewritten_before_the_append_and_ledgered(tmp_path: Path) -> None:
    crashed = _crashed_copy(tmp_path, 300)
    ledger = _Ledger()
    store = _store(tmp_path, ledger)
    store.write(_CHANNEL, _T0 + 1, {"i": "after"})
    store.close()
    assert _values(crashed) == [*range(300), "after"]
    assert [site for site, _ in ledger.records] == [sites.TRUNCATED_TAIL]
    assert "300 complete lines kept" in ledger.records[0][1]
    assert not list(crashed.parent.glob("*.tmp"))


def test_a_power_loss_zero_filled_tail_is_repaired_not_set_aside(tmp_path: Path) -> None:
    store = _store(tmp_path, _Ledger())
    for i in range(50):
        store.write(_CHANNEL, _T0, {"i": i})
    store.close()
    path = _file(tmp_path, _T0)
    path.write_bytes(path.read_bytes() + bytes(4096))
    assert has_truncated_tail(path)
    ledger = _Ledger()
    store = _store(tmp_path, ledger)
    store.write(_CHANNEL, _T0 + 1, {"i": "after"})
    store.close()
    assert _values(path) == [*range(50), "after"]
    assert [site for site, _ in ledger.records] == [sites.TRUNCATED_TAIL]
    assert not list(path.parent.glob("*.corrupt-*"))


def test_zero_bytes_before_more_data_are_still_corrupt() -> None:
    with pytest.raises(CorruptZstd):
        scan(memoryview(bytes(8) + b"junk"))


def test_a_cut_partial_line_is_dropped_and_reported(tmp_path: Path) -> None:
    path = tmp_path / "cut.jsonl.zst"
    with pa.CompressedOutputStream(pa.OSFile(str(path), "wb"), "zstd") as stream:
        stream.write(b'{"i":0}\n{"i":1}\n{"i":')
    reader = RawFileReader(path)
    assert list(reader.lines()) == [b'{"i":0}', b'{"i":1}']
    assert reader.truncated


def test_bytes_cut_from_the_end_lose_only_the_unfinished_block(tmp_path: Path) -> None:
    store = _store(tmp_path, _Ledger())
    for i in range(100):
        store.write(_CHANNEL, _T0, {"i": i})
    store.close()
    path = _file(tmp_path, _T0)
    path.write_bytes(path.read_bytes()[:-3])
    assert has_truncated_tail(path)
    assert _values(path, allow_truncated=True) == []  # one block, cut: nothing whole remains


def test_bytes_that_are_not_zstd_are_reported_not_read_short(tmp_path: Path) -> None:
    path = tmp_path / "junk.jsonl.zst"
    path.write_bytes(b"this is not a zstd frame")
    with pytest.raises(CorruptZstd):
        list(RawFileReader(path).lines())
    assert scan(memoryview(b"")).tail_start is None


def test_a_line_stamped_in_an_hour_already_rotated_is_appended_to_its_own_file(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, _Ledger())
    store.write(_CHANNEL, _T0 + _HOUR_NS, {"i": "next hour"})
    store.write(_CHANNEL, _T0, {"i": "late"})
    store.close()
    assert _values(_file(tmp_path, _T0)) == ["late"]
    assert _values(_file(tmp_path, _T0 + _HOUR_NS)) == ["next hour"]


def test_prune_keeps_the_last_retain_days_and_bytes_per_day_sums_them(tmp_path: Path) -> None:
    day_ns = 24 * _HOUR_NS
    history = _store(tmp_path, _Ledger(), retain_days=30)
    for days_ago in (3, 2, 1):
        history.write(_CHANNEL, _T0 - days_ago * day_ns, {"i": days_ago})
        history.write("connection", _T0 - days_ago * day_ns, {"i": days_ago})
    history.close()
    before = bytes_by_day(tmp_path, "BYBIT")
    assert list(before) == ["2026-09-26", "2026-09-27", "2026-09-28"]
    store = _store(tmp_path, _Ledger(), retain_days=2)
    store.write(_CHANNEL, _T0 - day_ns, {"i": "yesterday"})
    store.write(_CHANNEL, _T0, {"i": 0})  # the rollover into today prunes
    store.close()
    after = bytes_by_day(tmp_path, "BYBIT")
    assert list(after) == ["2026-09-28", "2026-09-29"]
    assert after["2026-09-28"] == sum(
        _file(tmp_path, _T0 - day_ns, channel).stat().st_size
        for channel in (_CHANNEL, "connection")
    )


def test_a_prune_failure_is_ledgered(tmp_path: Path) -> None:
    ledger = _Ledger()
    store = _store(tmp_path, ledger, retain_days=1)
    old = _T0 - 48 * _HOUR_NS
    store.write(_CHANNEL, old, {"i": 0})
    store.close()
    blocker = _file(tmp_path, old)
    blocker.unlink()
    blocker.mkdir()  # a directory with the file's name: unlink raises IsADirectoryError
    store.write(_CHANNEL, _T0, {"i": 1})
    store.close()
    assert [site for site, _ in ledger.records] == [sites.PRUNE]


def test_retention_must_be_at_least_a_day(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _store(tmp_path, _Ledger(), retain_days=0)


def _frames(path: Path) -> int:
    data, position, count = memoryview(path.read_bytes()), 0, 0
    while position < len(data):
        end, _ = _frame_end(data, position)
        assert end is not None, "an unfinished frame"
        position, count = end, count + 1
    return count


def test_a_corrupt_hour_file_is_set_aside_once_and_a_fresh_one_opened(tmp_path: Path) -> None:
    path = _file(tmp_path, _T0)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a zstd stream at all")
    ledger = _Ledger()
    store = _store(tmp_path, ledger)
    store.write(_CHANNEL, _T0, {"i": 0})
    store.write(_CHANNEL, _T0 + 1, {"i": 1})
    store.close()
    assert _values(path) == [0, 1]
    (aside,) = path.parent.glob("*.corrupt-*")
    assert aside.read_bytes() == b"not a zstd stream at all"
    assert [site for site, _ in ledger.records] == [sites.CORRUPT_FILE]


def test_a_failed_repair_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crashed = _crashed_copy(tmp_path, 50)

    def refuse(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("verification.infrastructure.raw_store.os.replace", refuse)
    ledger = _Ledger()
    store = _store(tmp_path, ledger)
    store.write(_CHANNEL, _T0 + 1, {"i": "lost"})
    store.close()
    assert not list(crashed.parent.glob("*.repair.tmp"))
    # The repair at start fails, then the open's own repair: both ledgered, neither fatal.
    assert [site for site, _ in ledger.records] == [sites.WRITE, sites.WRITE]
    assert _values(crashed, allow_truncated=True) == list(range(50)), "the original is untouched"


def test_leftovers_are_counted_and_pruned_with_their_day(tmp_path: Path) -> None:
    old = _T0 - 5 * 24 * _HOUR_NS
    for suffix in (".repair.tmp", ".corrupt-1790000000000000000"):
        leftover = _file(tmp_path, old).with_name(_file(tmp_path, old).name + suffix)
        leftover.parent.mkdir(parents=True, exist_ok=True)
        leftover.write_bytes(b"12345")
    assert bytes_by_day(tmp_path, "BYBIT") == {"2026-09-24": 10}
    store = _store(tmp_path, _Ledger(), retain_days=2)
    store.tick(_T0)  # the first tick at start prunes: a restarting recorder still prunes
    store.close()
    assert bytes_by_day(tmp_path, "BYBIT") == {}


def test_late_lines_share_one_stream_until_the_next_tick(tmp_path: Path) -> None:
    store = _store(tmp_path, _Ledger())
    store.write(_CHANNEL, _T0 + _HOUR_NS, {"i": "now"})
    for i in range(3):
        store.write(_CHANNEL, _T0, {"i": i})
    late = _file(tmp_path, _T0)
    assert late.stat().st_size == 0, "buffered in the one late stream, open until the tick"
    store.tick(_T0 + _HOUR_NS)
    assert _frames(late) == 1, "three late lines, one reopen, one frame"
    assert _values(late) == [0, 1, 2]
    store.write(_CHANNEL, _T0, {"i": 3})
    store.close()
    assert _values(late) == [0, 1, 2, 3]


def test_an_unreadable_accounting_is_ledgered_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def vanish(root: Path, venue: str) -> dict[str, int]:
        raise FileNotFoundError("raw/bybit vanished")

    monkeypatch.setattr("verification.infrastructure.raw_store.bytes_by_day", vanish)
    ledger = _Ledger()
    store = _store(tmp_path, ledger)
    store.write(_CHANNEL, _T0, {"i": 0})
    store.close()
    assert [site for site, _ in ledger.records] == [sites.ACCOUNTING]
    assert _values(_file(tmp_path, _T0)) == [0]


def test_a_crash_tail_of_an_hour_that_ended_before_the_restart_is_repaired_at_start(
    tmp_path: Path,
) -> None:
    crashed = _crashed_copy(tmp_path, 40)
    ledger = _Ledger()
    store = _store(tmp_path, ledger)
    store.tick(_T0 + 3 * _HOUR_NS)  # restarted hours later: nothing writes that hour again
    store.close()
    assert not has_truncated_tail(crashed)
    assert _values(crashed) == list(range(40))
    assert [site for site, _ in ledger.records] == [sites.TRUNCATED_TAIL]


def test_a_file_that_cannot_be_opened_now_is_not_set_aside_as_corrupt(tmp_path: Path) -> None:
    crashed = _crashed_copy(tmp_path, 10)
    crashed.chmod(0)  # EACCES: says nothing about the bytes
    ledger = _Ledger()
    store = _store(tmp_path, ledger)
    try:
        store.write(_CHANNEL, _T0 + 1, {"i": "lost"})
    finally:
        crashed.chmod(0o644)
    store.close()
    assert not list(crashed.parent.glob("*.corrupt-*"))
    assert sites.CORRUPT_FILE not in [site for site, _ in ledger.records]
    assert _values(crashed, allow_truncated=True) == list(range(10))


def test_a_failed_open_is_retried_once_per_tick_and_the_lines_lost_are_counted(
    tmp_path: Path,
) -> None:
    path = _file(tmp_path, _T0)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    path.chmod(0o444)  # read-only: every open for append fails
    ledger = _Ledger()
    store = _store(tmp_path, ledger)
    try:
        for i in range(5):
            store.write(_CHANNEL, _T0, {"i": i})
        opened_failures = [d for site, d in ledger.records if "not opened" in d]
        store.tick(_T0)
        path.chmod(0o644)
        store.write(_CHANNEL, _T0 + 1, {"i": "back"})
    finally:
        path.chmod(0o644)
    store.close()
    assert len(opened_failures) == 1, "one open attempt until the tick, not one per line"
    assert any("4 more lines lost" in detail for _, detail in ledger.records)
    assert _values(path) == ["back"]
