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
The book tool's ports and orchestration (Story 31.5): one venue's closed UTC day, one instrument
at a time (`verification.domain.reference_book` holds the rules). It reuses the conservation
tool's reading of the same inputs -- the raw store, the coverage record (`collect_coverage`), the
plan's wire index and missing-hour rule -- so the tools never disagree about what was seen.

Per instrument: the REST book polls of the day are loaded and sorted by key; the reference book
replays the recorder's book frames in arrival order from its replay start (`replay_start`) to the
end of the hour after the day; every second of the day is closed once and classified against the
stored row of that second.

Known limit (replay cost): a Bybit book is rebuilt from the topic's latest `snapshot` before the
day, which the recorder receives only at (re)connect, so the look-back scan and the replay grow
with the recorder connection's age, up to `VERIFY_RETAIN_DAYS` of raw hours (Hyperliquid's look
back is one hour: every `l2Book` is the whole book). Upgrade path: an end-of-day reference
checkpoint (the top book and `u` at 24:00), continuity-checked by `u` at the next day's first
delta, so each day replays only itself. Measured (Story 31.5 smoke, one connection since the soak
start): ~95 s of one core for Bybit's four instruments over 3 h, ~1.6M frames, so ~13 min per full
Bybit day (a frame of another instrument costs a substring test, not a decode); Hyperliquid 4 s.

Known limit (memory, MEM-01): one instrument-day of the stored book columns in Arrow (~60 MB, see
`snapshot_book`), one hour of rows as Python objects, the day's REST polls of the instrument (at
most 1,440) and one reference book. Upgrade path: read the rows hour by hour.

Known limit (plan): the instruments are the venue config's *current* plan, as in conservation.
Upgrade path: the recorder's `plan_changed` lines trim each instrument to the windows it was
planned.

Known limit (disconnect): a connection line makes the book unavailable at once, so the seconds
not yet closed by a message when it arrived (up to the last second before it) are
`reference_unavailable` rather than judged against the book as it stood: a smaller verified
set, never a false verdict. Upgrade path: close the seconds whose end precedes the line's own
time, on a venue clock proven aligned with ours.
"""

from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from typing import Protocol

from verification.application.conservation import CoverageSource
from verification.application.conservation import ReferenceRecords
from verification.application.conservation import collect_coverage
from verification.application.conservation import day_hours
from verification.application.conservation import day_start_ns
from verification.application.conservation import missing_raw
from verification.domain.conservation import NS_PER_HOUR
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import SECONDS_PER_HOUR
from verification.domain.conservation import Intervals
from verification.domain.conservation import MalformedLine
from verification.domain.conservation import TradeChannel
from verification.domain.conservation import instrument_category
from verification.domain.conservation import wire_index
from verification.domain.plan_file import RecordingPlan
from verification.domain.reference_book import FAILED
from verification.domain.reference_book import FAILING_REST
from verification.domain.reference_book import FAILING_SECONDS
from verification.domain.reference_book import REPLAY_COUNTS
from verification.domain.reference_book import REST_CLASSES
from verification.domain.reference_book import SECOND_CLASSES
from verification.domain.reference_book import BookDayReport
from verification.domain.reference_book import BookMessage
from verification.domain.reference_book import BookReplay
from verification.domain.reference_book import InstrumentBook
from verification.domain.reference_book import ReferenceBook
from verification.domain.reference_book import RestBook
from verification.domain.reference_book import RestJudge
from verification.domain.reference_book import SecondCounter
from verification.domain.reference_book import StoredBook
from verification.domain.reference_book import book_channels
from verification.domain.reference_book import bybit_frame
from verification.domain.reference_book import bybit_rest
from verification.domain.reference_book import classify_second
from verification.domain.reference_book import closed_seconds
from verification.domain.reference_book import hyperliquid_frame
from verification.domain.reference_book import hyperliquid_rest
from verification.domain.subscriptions import BYBIT


# Hyperliquid pushes the whole book every ~5.4 s, so the hour before the day always holds one.
HYPERLIQUID_LOOK_BACK_HOURS = 1

FrameParser = Callable[[Mapping[str, object], str], tuple[str, BookMessage]]
PollParser = Callable[[Mapping[str, object], str], tuple[str, RestBook]]


class StoredBooks(Protocol):
    """One instrument-window of stored books: `hour(h)` maps each second of `h` to its rows."""

    @property
    def count(self) -> int: ...

    def hour(self, hour: int) -> Mapping[int, Sequence[StoredBook]]: ...


class SnapshotSource(Protocol):
    """The catalog's stored snapshot books, read-only, of an instrument in a window."""

    def rows(self, instrument_id: str, start_ns: int, end_ns: int) -> StoredBooks: ...


@dataclass(frozen=True)
class BookInputs:
    """The three read-only sources one run compares."""

    reference: ReferenceRecords
    snapshots: SnapshotSource
    coverage: CoverageSource


@dataclass(frozen=True)
class _Feed:
    """One instrument's raw book feed: its channels, wire name and the parsers of each line kind."""

    ws: str
    rest: str
    wire: str
    frame: FrameParser
    poll: PollParser
    replaces: bool


def _feed(venue: str, instrument_id: str, channels: Sequence[TradeChannel], wire: str) -> _Feed:
    category = instrument_category(venue, instrument_id)
    ws = next((c.name for c in channels if c.category == category and not c.rest), None)
    rest = next((c.name for c in channels if c.category == category and c.rest), None)
    if ws is None or rest is None:
        raise ValueError(f"no book channel for {instrument_id} (category {category!r})")
    if venue == BYBIT:
        return _Feed(ws, rest, wire, bybit_frame, bybit_rest, replaces=False)
    return _Feed(ws, rest, wire, hyperliquid_frame, hyperliquid_rest, replaces=True)


def _where(channel: str, record: Mapping[str, object]) -> str:
    return f"{channel} line recv_ns={record.get('recv_ns', record.get('ts_ns'))}"


def _line(record: object, channel: str) -> Mapping[str, object]:
    if not isinstance(record, Mapping):  # `json.loads` of a line can be any JSON value
        raise MalformedLine(f"{channel}: a raw line that is not a JSON object: {record!r:.200}")
    return record


def _connection(record: Mapping[str, object], where: str) -> bool:
    """
    Whether the line is a connection line (every event of one breaks the book), else a frame --
    whose `raw` must be text before any pre-filter reads it (a missing one is refused, never
    dropped as the text "None").
    """
    kind = record.get("kind")
    if kind == "connection":
        if record.get("event") not in ("open", "close", "error"):
            raise MalformedLine(f"{where}: connection event {record.get('event')!r}")
        return True
    if kind != "frame":
        raise MalformedLine(f"{where}: unexpected line kind {kind!r} in a book channel")
    if not isinstance(record.get("raw"), str):
        raise MalformedLine(f"{where}: a frame line without a text `raw`")
    return False


def _events(
    feed: _Feed, hours: Iterable[int], reference: ReferenceRecords
) -> Iterator[BookMessage | None]:
    """
    Yield the instrument's book messages of the channel's files of `hours`, in file (arrival)
    order, and None for each connection line -- and for each hour with no file: an hour the
    recorder did not record breaks the book exactly as a disconnect does, so none of its seconds
    is ever closed against the book from before the gap. A frame is parsed only when its raw text
    names the wire symbol (a frame of the instrument always does; one of another that happens to is
    parsed and dropped by its own symbol), so the other instruments' frames of a shared channel
    cost no decode.
    """
    for hour in hours:
        if not reference.exists(feed.ws, hour):
            yield None
            continue
        for raw in reference.records(feed.ws, hour):
            record = _line(raw, feed.ws)
            where = _where(feed.ws, record)
            if _connection(record, where):
                yield None
            elif feed.wire in str(record.get("raw")):
                wire, message = feed.frame(record, where)
                if wire == feed.wire:
                    yield message


def _baseline_in(feed: _Feed, lines: Iterable[object]) -> bool | None:
    """
    Return the last replay event of one file: True for a baseline of the instrument (a Bybit
    snapshot, any Hyperliquid book), False for a connection line, None when it holds neither.
    """
    last = None
    for raw in lines:
        record = _line(raw, feed.ws)
        where = _where(feed.ws, record)
        if _connection(record, where):
            last = False
        elif feed.wire in (text := str(record.get("raw"))) and (
            feed.replaces or "snapshot" in text
        ):
            wire, message = feed.frame(record, where)
            last = True if wire == feed.wire and message.baseline else last
    return last


def replay_start(feed: _Feed, day: range, reference: ReferenceRecords) -> int:
    """
    Return the hour to replay from: the latest hour before the day whose last replay event is a
    baseline of the instrument or a connection line, looking back while the files exist (one hour
    for Hyperliquid); the day's first hour when no file holds either -- the seconds before the
    day's first baseline are then `reference_unavailable`. A connection line is replayed, never
    skipped: it breaks the book, and on Hyperliquid it marks the next `l2Book` as the recorder's
    own subscribe reply (audit D-101), which a replay starting after it would judge as a push.
    """
    floor = day.start - HYPERLIQUID_LOOK_BACK_HOURS if feed.replaces else None
    hour = day.start - 1
    while (floor is None or hour >= floor) and reference.exists(feed.ws, hour):
        if _baseline_in(feed, reference.records(feed.ws, hour)) is not None:
            return hour
        hour -= 1
    return day.start


def _polls(feed: _Feed, hours: range, reference: ReferenceRecords) -> list[RestBook]:
    """Every REST book poll of the instrument filed under the day's hours (failed ones too)."""
    polls = []
    for hour in hours:
        for raw in reference.records(feed.rest, hour):
            record = _line(raw, feed.rest)
            if record.get("kind") == "connection":
                continue
            if record.get("kind") != "rest":
                raise MalformedLine(f"{_where(feed.rest, record)}: not a REST line")
            wire, poll = feed.poll(record, _where(feed.rest, record))
            if wire == feed.wire:
                polls.append(poll)
    return polls


class _Rows:
    """The stored rows of the second being judged, one hour of Python objects at a time."""

    def __init__(self, books: StoredBooks) -> None:
        self._books = books
        self._hour: int | None = None
        self._rows: Mapping[int, Sequence[StoredBook]] = {}

    def at(self, second: int) -> Sequence[StoredBook]:
        hour = second // SECONDS_PER_HOUR
        if hour != self._hour:
            self._hour, self._rows = hour, self._books.hour(hour)
        return self._rows.get(second, ())


@dataclass(frozen=True)
class _Run:
    """What every instrument of one run shares."""

    venue: str
    hours: range
    channels: tuple[TradeChannel, ...]
    wires: Mapping[str, str]
    runs: Mapping[str, Intervals]
    inputs: BookInputs


def _instrument(instrument_id: str, run: _Run) -> InstrumentBook:
    feed = _feed(run.venue, instrument_id, run.channels, run.wires[instrument_id])
    start, stop = run.hours.start * NS_PER_HOUR, run.hours.stop * NS_PER_HOUR
    books = run.inputs.snapshots.rows(instrument_id, start, stop)
    judge = RestJudge()
    book = ReferenceBook(replaces=feed.replaces, counted=range(start, stop))
    polls = _polls(feed, run.hours, run.inputs.reference)
    for failed in (poll for poll in polls if not poll.ok):
        judge.classify(FAILED, failed)
    seconds = range(start // NS_PER_S, stop // NS_PER_S)
    replay = BookReplay(book, seconds, [poll for poll in polls if poll.ok], judge)
    replayed = range(replay_start(feed, run.hours, run.inputs.reference), run.hours.stop + 1)
    events = _events(feed, replayed, run.inputs.reference)
    rows, tally, covered = _Rows(books), SecondCounter(), run.runs[instrument_id]
    for closed in closed_seconds(replay, events):
        verdict = classify_second(closed, rows.at(closed.second), covered.contains(closed.second))
        if verdict is not None:
            tally.add(closed.second, verdict)
    return InstrumentBook.of(instrument_id, judge, book.counts, tally, books.count)


def check_day(plan: RecordingPlan, day: date, inputs: BookInputs) -> BookDayReport:
    """Check every plan instrument's whole UTC day: its stored books against the rebuilt one."""
    hours = day_hours(day)
    coverage = collect_coverage(inputs.coverage.entries(), plan.instruments, day_start_ns(day))
    runs = {
        iid: Intervals.of((r.first_s, r.last_s) for r in found.runs)
        for iid, found in coverage.items()
    }
    wires = {iid: wire for (_, wire), iid in wire_index(plan).items()}
    channels = book_channels(plan)
    run = _Run(plan.venue, hours, channels, wires, runs, inputs)
    reports = tuple(_instrument(iid, run) for iid in plan.instruments)
    return BookDayReport(
        venue=plan.venue,
        day=day.isoformat(),
        coverage_file=inputs.coverage.path,
        coverage_present=inputs.coverage.present(),
        missing_raw_files=missing_raw(channels, hours, inputs.reference),
        truncated_neighbour_files=inputs.reference.truncated_neighbours(),
        instruments=reports,
    )


# --- the report ----------------------------------------------------------------------------------


def _instrument_json(report: InstrumentBook) -> dict[str, Any]:
    return {
        "instrument_id": report.instrument_id,
        "passed": report.passed,
        "failing": report.failing,
        "reference": report.reference,
        "rest": dict(report.rest),
        "rest_examples": list(report.rest_examples),
        "replay": dict(report.replay),
        "rows": report.rows,
        "verified_seconds": report.verified,
        "seconds": dict(report.seconds),
        "levels": dict(report.levels),
        "examples": [[s, verdict, list(levels)] for s, verdict, levels in report.examples],
    }


_LAYOUT = "stored layout: integer units (Epic 30.2), compared exactly; float-layout files refused"


def report_json(report: BookDayReport) -> dict[str, Any]:
    """Return the report as JSON-ready data, with every instrument's verdict and all classes."""
    return {
        "passed": report.passed,
        "venue": report.venue,
        "day": report.day,
        "layout": _LAYOUT,
        "coverage_file": report.coverage_file,
        "coverage_present": report.coverage_present,
        "missing_raw_files": list(report.missing_raw_files),
        "truncated_neighbour_files": list(report.truncated_neighbour_files),
        "instruments": [_instrument_json(r) for r in report.instruments],
    }


def _pairs(values: Mapping[str, int], names: Iterable[str]) -> str:
    return " ".join(f"{name}={values.get(name, 0)}" for name in names)


def _verdict(report: InstrumentBook) -> str:
    if report.passed:
        return "PASS"
    reasons = [f"reference {report.reference.upper()}"] if report.reference != "validated" else []
    if not report.replay.get("messages", 0):
        reasons.append("no reference data")
    if report.failing:
        reasons.append(f"{report.failing} failing")
    if report.rows and not report.verified:
        reasons.append("nothing verified")
    return f"FAIL ({', '.join(reasons)})"


def _instrument_text(report: InstrumentBook) -> list[str]:
    levels = " ".join(f"{name.replace(' ', '.')}={n}" for name, n in report.levels.items())
    lines = [
        f"{report.instrument_id}: {_verdict(report)}",
        f"  rest     {_pairs(report.rest, REST_CLASSES)} -> reference {report.reference}",
        f"  replay   {_pairs(report.replay, REPLAY_COUNTS)}",
        f"  seconds  rows={report.rows} verified={report.verified} "
        f"{_pairs(report.seconds, SECOND_CLASSES)}",
        f"  levels   {levels or 'none'}",
    ]
    if report.rest_examples:
        lines.append(f"  failing polls: {', '.join(report.rest_examples)}")
    for second, verdict, details in report.examples:
        lines.append(f"  failing second {second} (epoch s): {verdict} {' '.join(details)}".rstrip())
    return lines


_MISSING_SHOWN = 6


def _missing_line(missing: tuple[str, ...]) -> str:
    shown = ", ".join(missing[:_MISSING_SHOWN]) + (", ..." if len(missing) > _MISSING_SHOWN else "")
    return f"raw reference files missing: {len(missing)}" + (f" ({shown}; FAIL)" if missing else "")


def render_text(report: BookDayReport) -> str:
    """Render the report: the verdict and inputs, then per instrument the REST agreement first."""
    present = "" if report.coverage_present else "  (MISSING: nothing is explained, FAIL)"
    lines = [
        f"book {report.venue} {report.day}: {'PASS' if report.passed else 'FAIL'}",
        f"coverage record: {report.coverage_file}{present}",
        _missing_line(report.missing_raw_files),
        f"neighbour files read truncated: {', '.join(report.truncated_neighbour_files) or 'none'}",
        _LAYOUT,
        f"failing: rest {', '.join(FAILING_REST)}; seconds {', '.join(sorted(FAILING_SECONDS))}",
        "",
    ]
    for instrument in report.instruments:
        lines += _instrument_text(instrument)
    return "\n".join(lines)
