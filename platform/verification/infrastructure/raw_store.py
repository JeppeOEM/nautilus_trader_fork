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
The reference recorder's raw store: one JSON object per line, zstd-compressed, one file per
(venue, channel, UTC hour) at `<root>/raw/<venue>/<channel>/<YYYY-MM-DDTHH>.jsonl.zst`
(`docs/DATA_DICTIONARY.md` section 1.15), and the reader every later comparator uses.

Invariant: a line lives in exactly one file -- the one of its channel and of the UTC hour of its
own timestamp -- and every file is a run of complete zstd frames, apart from the one stream the
store is still writing. Commands that could break it and how they are held:

- a crash leaves the open stream's last frame unfinished: the store flushes every stream on each
  `tick` (the recorder calls it every second), so a crash loses at most about a second of lines,
  and the reader recovers every flushed line (`zstd_frames`) and reports the tail as truncated;
- a restart appends to the hour's file: `pyarrow.CompressedOutputStream` in append mode starts a
  new frame after the old ones, but a frame appended after an *unfinished* one would be read as
  part of it, so a truncated file is first rewritten with only its complete lines (temp file +
  `os.replace`) and the repair is ledgered at `verification.recorder.truncated_tail`;
- a crash leaves the tail unfinished in a file whose hour ends before the restart: at start the
  store repairs each channel's two newest files (the only ones a crash can have left open: the
  current hour's and a late stream's), so no older hour stays truncated;
- an hour ends: every stream of an older hour is closed (its frame finished), the bytes per UTC
  day of the venue are logged and files older than the retention are pruned.

Written with `pyarrow`'s zstd codec (already a dependency) rather than a new zstd package.
Known limit: lines are flushed to the kernel, not `fsync`ed, so a host power loss (not a process
crash) can lose more than a second. Upgrade path: an `os.fsync` of each file on `tick`.
"""

import json
import logging
import os
import time
from collections.abc import Iterator
from collections.abc import Mapping
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pyarrow as pa

from verification.application import sites
from verification.application.ports import Ledger
from verification.infrastructure.zstd_frames import FrameScan
from verification.infrastructure.zstd_frames import rebuild_tail
from verification.infrastructure.zstd_frames import scan


logger = logging.getLogger(__name__)

RAW_DIR = "raw"
FILE_SUFFIX = ".jsonl.zst"
_CODEC = "zstd"
_HOUR_FORMAT = "%Y-%m-%dT%H"
_NS_PER_HOUR = 3600 * 1_000_000_000
_READ_CHUNK_BYTES = 1 << 20


class TruncatedTail(ValueError):
    """A raw file whose last zstd frame or last line was cut (a crash, or a file still written)."""


def hour_of(ts_ns: int) -> int:
    """Return the UTC hour index (hours since the epoch) of a nanosecond timestamp."""
    return ts_ns // _NS_PER_HOUR


def hour_name(hour: int) -> str:
    """Return the file stem of an hour index (`2026-09-29T14`)."""
    return datetime.fromtimestamp(hour * 3600, UTC).strftime(_HOUR_FORMAT)


def venue_dir(root: Path, venue: str) -> Path:
    """Return `<root>/raw/<venue in lower case>`."""
    return root / RAW_DIR / venue.lower()


def channel_file(root: Path, venue: str, channel: str, hour: int) -> Path:
    """Return the file holding one channel's lines of one UTC hour."""
    return venue_dir(root, venue) / channel / f"{hour_name(hour)}{FILE_SUFFIX}"


def file_day(path: Path) -> date | None:
    """
    Return the UTC day of a raw file from its name, or None for a name that is not one. A repair
    leftover (`<hour>.jsonl.zst.repair.tmp`) or a file set aside as corrupt
    (`<hour>.jsonl.zst.corrupt-<ns>`) carries its hour's day too, so it is counted and pruned.
    """
    stem, suffix, _ = path.name.partition(FILE_SUFFIX)
    if not suffix:
        return None
    try:
        return datetime.strptime(f"{stem}+0000", f"{_HOUR_FORMAT}%z").date()
    except ValueError:
        return None


def _venue_files(root: Path, venue: str) -> Iterator[Path]:
    """Every raw file of a venue, leftovers and set-aside files included."""
    return venue_dir(root, venue).glob(f"*/*{FILE_SUFFIX}*")


def encode_line(line: Mapping[str, object]) -> bytes:
    """One JSON object on one line: compact separators, ASCII-escaped, newline-terminated."""
    return json.dumps(line, separators=(",", ":")).encode() + b"\n"


def _decoded_chunks(buffer: pa.Buffer) -> Iterator[bytes]:
    with pa.CompressedInputStream(pa.BufferReader(buffer), _CODEC) as stream:
        while chunk := stream.read(_READ_CHUNK_BYTES):
            yield chunk


class RawFileReader:
    """
    Reads one raw file's complete lines. After `lines()` is exhausted, `truncated` tells whether
    the file ended in an unfinished frame or an unterminated line -- a crash, or the hour the
    recorder is still writing -- whose flushed complete lines were still yielded.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.truncated = False

    def _chunks(self, data: pa.Buffer, found: FrameScan) -> Iterator[bytes]:
        yield from _decoded_chunks(data.slice(0, found.complete_end))
        if found.tail_start is not None:
            self.truncated = True
            tail = rebuild_tail(memoryview(data), found)
            if tail:
                yield from _decoded_chunks(pa.py_buffer(tail))

    def lines(self) -> Iterator[bytes]:
        """Yield every complete line (without its newline), in file order."""
        self.truncated = False
        if self.path.stat().st_size == 0:
            return
        with pa.memory_map(str(self.path), "r") as mapped:
            data = mapped.read_buffer()
            pending = b""
            for chunk in self._chunks(data, scan(memoryview(data))):
                *complete, pending = (pending + chunk).split(b"\n")
                yield from complete
        if pending:
            self.truncated = True


def iter_records(path: Path, *, allow_truncated: bool = False) -> Iterator[dict[str, object]]:
    """
    Yield one raw file's lines as dicts. A truncated tail raises `TruncatedTail` after every
    complete line was yielded, unless `allow_truncated` (a reader of the hour still being written).
    """
    reader = RawFileReader(path)
    for line in reader.lines():
        yield json.loads(line)
    if reader.truncated and not allow_truncated:
        raise TruncatedTail(f"{path}: the last zstd frame or line is truncated")


class RawReader:
    """
    One venue's raw files, read-only, by (channel, hour index) -- the comparators' port onto the
    store, for one checked UTC day. Invariant: a file of an hour of the day must be whole (a
    truncated tail raises `TruncatedTail`: a crash the recorder has not repaired yet, whose lost
    lines no comparator may silently miss). A neighbour hour outside the day (read only to catch
    lines received across the day's edges, and possibly the hour still being written) may end
    truncated: its complete lines are read and its file is named by `truncated_neighbours`.
    """

    def __init__(self, root: Path, venue: str, day_hours: range) -> None:
        self._root = root
        self._venue = venue
        self._day_hours = day_hours
        self._truncated: dict[str, None] = {}  # insertion-ordered set of file names

    def exists(self, channel: str, hour: int) -> bool:
        return channel_file(self._root, self._venue, channel, hour).is_file()

    def records(self, channel: str, hour: int) -> Iterator[dict[str, object]]:
        path = channel_file(self._root, self._venue, channel, hour)
        if not path.is_file():
            return
        reader = RawFileReader(path)
        for line in reader.lines():
            yield json.loads(line)
        if reader.truncated and hour in self._day_hours:
            raise TruncatedTail(f"{path}: the last zstd frame or line is truncated")
        if reader.truncated:
            self._truncated[f"{channel}/{path.name.removesuffix(FILE_SUFFIX)}"] = None

    def truncated_neighbours(self) -> tuple[str, ...]:
        return tuple(self._truncated)


def has_truncated_tail(path: Path) -> bool:
    """Whether a raw file ends in an unfinished zstd frame (header walk only, no decoding)."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    with pa.memory_map(str(path), "r") as mapped:
        return scan(memoryview(mapped.read_buffer())).tail_start is not None


def bytes_by_day(root: Path, venue: str) -> dict[str, int]:
    """Bytes on disk of a venue's raw files per UTC day (`YYYY-MM-DD` -> bytes), sorted by day."""
    totals: dict[str, int] = {}
    for path in _venue_files(root, venue):
        day = file_day(path)
        if day is None:
            continue
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue  # renamed or pruned between the listing and the stat: not on disk any more
        totals[day.isoformat()] = totals.get(day.isoformat(), 0) + size
    return dict(sorted(totals.items()))


class RawStore:
    """
    The hourly zstd JSONL writer of one venue (see the module docstring for its invariant).
    Single-threaded: the recorder's event loop is its one caller.

    A line stamped in an hour already rotated away (a late REST response) goes to a *late* stream
    of its own (channel, hour), kept open until the next `tick` and then closed, so a burst of late
    lines costs one reopen and one frame, not one per line.

    A (channel, hour) whose open or write failed is not retried before the next `tick`: each retry
    re-reads and may rewrite the whole hour file on the one event loop, so retrying per line (tens a
    second on a book channel, e.g. with the disk full) would stall every other channel. The lines
    lost meanwhile are counted and ledgered at that `tick`.
    """

    def __init__(self, root: Path, venue: str, retain_days: int, ledger: Ledger) -> None:
        if retain_days < 1:
            raise ValueError(f"retain_days must be >= 1, got {retain_days}")
        self._root = root
        self._venue = venue
        self._retain_days = retain_days
        self._ledger = ledger
        self._streams: dict[tuple[str, int], pa.CompressedOutputStream] = {}
        self._late: dict[tuple[str, int], pa.CompressedOutputStream] = {}
        self._failed: dict[tuple[str, int], int] = {}  # lines lost since the failure, per key
        self._hour: int | None = None

    def write(self, channel: str, ts_ns: int, line: Mapping[str, object]) -> None:
        """File one line under `channel` in the UTC hour of `ts_ns`."""
        hour = hour_of(ts_ns)
        if self._hour is None or hour > self._hour:
            self._rotate(hour)
        streams = self._late if self._hour is not None and hour < self._hour else self._streams
        key = (channel, hour)
        if key in self._failed:
            self._failed[key] += 1  # counted, ledgered at the next `tick`
            return
        stream = streams.get(key) or self._open(key, streams)
        if stream is None:
            self._failed[key] = 0  # `_open` ledgered the failure and this lost line
            return
        try:
            stream.write(encode_line(line))
        except OSError as exc:
            self._ledger(sites.WRITE, f"{self._venue} {channel}: line lost", exc)
            self._close(key, streams)
            self._failed[key] = 0

    def tick(self, now_ns: int) -> None:
        """Rotate when a new UTC hour began, flush every open stream, close the late ones."""
        hour = hour_of(now_ns)
        if self._hour is None or hour > self._hour:
            self._rotate(hour)
        for key, stream in list(self._streams.items()):
            try:
                stream.flush()
            except OSError as exc:
                self._ledger(sites.WRITE, f"{self._venue} {key[0]}: flush failed", exc)
                self._close(key, self._streams)
        for key in list(self._late):
            self._close(key, self._late)
        for (channel, _), lost in self._failed.items():
            if lost:
                detail = f"{self._venue} {channel}: {lost} more lines lost after a failed write"
                self._ledger(sites.WRITE, detail)
        self._failed.clear()

    def close(self) -> None:
        """Finish every open stream's frame (a clean shutdown leaves no truncated tail)."""
        for streams in (self._streams, self._late):
            for key in list(streams):
                self._close(key, streams)

    def _rotate(self, hour: int) -> None:
        """Enter `hour` (the first line or tick at start, then each new UTC hour)."""
        if self._hour is None:
            self._repair_after_crash()
        self._hour = hour
        for key in [key for key in self._streams if key[1] < hour]:
            self._close(key, self._streams)
        try:
            logger.info("raw/%s bytes per UTC day: %s", self._venue.lower(), self.bytes_by_day())
        except OSError as exc:
            self._ledger(sites.ACCOUNTING, f"raw/{self._venue.lower()}: bytes per day unread", exc)
        self.prune(hour)

    def bytes_by_day(self) -> dict[str, int]:
        """Return the venue's bytes on disk per UTC day."""
        return bytes_by_day(self._root, self._venue)

    def prune(self, hour: int) -> None:
        """
        Delete files of UTC days before the last `retain_days` days (ending at `hour`'s day),
        repair leftovers and set-aside files included. Runs at start and at every rotation, so a
        recorder that keeps restarting still prunes.
        """
        cutoff = datetime.fromtimestamp(hour * 3600, UTC).date() - timedelta(self._retain_days - 1)
        try:
            paths = list(_venue_files(self._root, self._venue))
        except OSError as exc:
            self._ledger(sites.PRUNE, f"raw/{self._venue.lower()}: not listed", exc)
            return
        for path in paths:
            day = file_day(path)
            if day is None or day >= cutoff:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                self._ledger(sites.PRUNE, f"{path}: not deleted", exc)

    def _repair_after_crash(self) -> None:
        """
        Make each channel's two newest files whole at start: a crash can leave an unfinished frame
        only in a file that was open then -- the current hour's, or a late stream's of the hour
        before -- and one whose hour has ended by the restart is never reopened by `write`.
        """
        try:
            channels = list(venue_dir(self._root, self._venue).iterdir())
        except FileNotFoundError:
            return  # nothing recorded yet
        except OSError as exc:
            self._ledger(sites.WRITE, f"raw/{self._venue.lower()}: not listed for repair", exc)
            return
        for channel in channels:
            for path in sorted(channel.glob(f"*{FILE_SUFFIX}"))[-2:]:
                try:
                    self._prepare(path)
                except OSError as exc:
                    self._ledger(sites.WRITE, f"{path}: not repaired at start", exc)

    def _open(
        self, key: tuple[str, int], streams: dict[tuple[str, int], pa.CompressedOutputStream]
    ) -> pa.CompressedOutputStream | None:
        path = channel_file(self._root, self._venue, *key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._prepare(path)
            stream = pa.CompressedOutputStream(pa.OSFile(str(path), "ab"), _CODEC)
        except OSError as exc:
            self._ledger(sites.WRITE, f"{path}: not opened, line lost", exc)
            return None
        streams[key] = stream
        return stream

    def _prepare(self, path: Path) -> None:
        """
        Make an existing file safe to append to: a truncated tail is cut back to its complete
        lines; a file that cannot be read at all is renamed aside and a fresh one started, so one
        damaged file never blocks its channel for the rest of the hour.
        """
        try:
            truncated = has_truncated_tail(path)
            if truncated:
                _read_through(path)
        except (ValueError, OSError) as exc:  # CorruptZstd, or a frame the decoder refuses
            if isinstance(exc, OSError) and exc.errno is not None:
                raise  # the file could not be read now (EMFILE, EACCES, EIO): no verdict on it
            self._set_aside(path, exc)
            return
        if truncated:
            self._repair(path)

    def _set_aside(self, path: Path, cause: BaseException) -> None:
        aside = path.with_name(f"{path.name}.corrupt-{time.time_ns()}")
        os.replace(path, aside)
        self._ledger(sites.CORRUPT_FILE, f"{path}: unreadable, set aside as {aside.name}", cause)

    def _repair(self, path: Path) -> None:
        """Rewrite a file with a truncated tail as its complete lines only, atomically."""
        temporary = path.with_name(path.name + ".repair.tmp")
        kept = 0
        try:
            with pa.CompressedOutputStream(pa.OSFile(str(temporary), "wb"), _CODEC) as stream:
                for line in RawFileReader(path).lines():
                    stream.write(line + b"\n")
                    kept += 1
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        self._ledger(
            sites.TRUNCATED_TAIL, f"{path}: truncated tail cut, {kept} complete lines kept"
        )

    def _close(
        self, key: tuple[str, int], streams: dict[tuple[str, int], pa.CompressedOutputStream]
    ) -> None:
        stream = streams.pop(key, None)
        if stream is None:
            return
        try:
            stream.close()
        except OSError as exc:
            self._ledger(sites.WRITE, f"{self._venue} {key[0]}: close failed", exc)


def _read_through(path: Path) -> None:
    """Decode a whole file, discarding its lines: raises if any whole frame cannot be read."""
    for _ in RawFileReader(path).lines():
        pass
