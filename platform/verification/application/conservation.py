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
The conservation tool's ports and orchestration (Story 31.2): one venue's closed UTC day -- or,
since Story 31.10, one settled window of whole seconds `[start, end)` (`conserve_window`, which
`conserve` is over the day) -- one instrument at a time, one hour at a time
(`verification.domain.conservation` holds the rules).

A window counts the trades whose venue time lies in it, on both sides, and the seconds in it; the
coverage runs and windows that touch it are kept and a run is clipped to it; the raw hours read
are those it touches plus their neighbours (`channel_trades`).

Trades are partitioned by their own venue time on both sides -- the reference's `T`/`time`, the
archive's `ts_event` -- so a trade whose venue time and archived `ts_event` disagree about the
hour is reported (unexplained + archived-not-seen) rather than matched. The raw store files lines
by *receive* hour, so hour H's reference is read from the files of hours H-1, H and H+1: a trade
received after its hour ended (or, with a venue clock ahead of ours, before it began) is counted.

Known limit (memory, MEM-01): the bound is one instrument-day of archived trade ids in Arrow plus
one hour of Python id sets (and the day's coverage runs and windows of that instrument); the price
is that every raw channel file is decoded up to three times per instrument that shares it (as its
own hour and as each neighbour's). Upgrade path: one decode per raw file shared by every
instrument of its channel, bucketing trades into a sliding window of per-hour sets.

Known limit (reference reach): a trade first received two or more hours after its venue time --
only possible through a Bybit `recent-trade` poll in a quiet market, whose 1000-trade (linear) or
60-trade (spot) window can reach that far back -- is not counted as seen for its hour, so it
cannot surface as unexplained. The WS `publicTrade` stream is the completeness reference; the
poll only adds the `rest_only` ids that WS missed. Upgrade path: read a REST channel's files up to
its window's measured reach after the hour.

Known limit (plan): the instruments are the venue config's *current* plan
(`verification.domain.plan_file`), not the plan in force on the day; an instrument added or
removed since is reported for the whole day. Upgrade path: the recorder's `plan_changed`
connection lines trim each instrument to the windows it was planned.
"""

from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import replace
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import time
from typing import Any
from typing import Protocol

from verification.domain.conservation import HOURS_PER_DAY
from verification.domain.conservation import NS_PER_HOUR
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import SECONDS_PER_DAY
from verification.domain.conservation import Backfilled
from verification.domain.conservation import CoverageEntry
from verification.domain.conservation import DayReport
from verification.domain.conservation import Explanations
from verification.domain.conservation import InstrumentReport
from verification.domain.conservation import ReferenceHour
from verification.domain.conservation import ReferenceTrade
from verification.domain.conservation import SecondsRun
from verification.domain.conservation import TradeChannel
from verification.domain.conservation import TradeCounts
from verification.domain.conservation import TradeWindow
from verification.domain.conservation import instrument_category
from verification.domain.conservation import reference_trades
from verification.domain.conservation import tally_seconds
from verification.domain.conservation import tally_trades
from verification.domain.conservation import trade_channels
from verification.domain.conservation import wire_index
from verification.domain.plan_file import RecordingPlan


class ReferenceRecords(Protocol):
    """
    The recorder's raw store, read-only. Invariant: `records(channel, hour)` yields exactly the
    lines filed under that channel and UTC hour index (`ns // NS_PER_HOUR`), and none for an hour
    with no file; `exists` tells the two apart for the report. A file of an hour of the day ending
    truncated is refused; one of a neighbour hour outside it is read up to its last complete line
    and named by `truncated_neighbours`.
    """

    def exists(self, channel: str, hour: int) -> bool: ...

    def records(self, channel: str, hour: int) -> Iterable[Mapping[str, object]]: ...

    def truncated_neighbours(self) -> tuple[str, ...]: ...


class ArchivedTrades(Protocol):
    """
    One instrument-day of archived trades. Invariant: `hour(h)` maps every trade id stored with
    `ts_event` in UTC hour `h` to that `ts_event`, and `duplicate_ids()` counts the ids stored
    more than once with `ts_event` in the day.
    """

    def hour(self, hour: int) -> Mapping[str, int]: ...

    def duplicate_ids(self) -> int: ...


class Archive(Protocol):
    """The catalog, read-only: an instrument's trades and snapshot rows in `[start_ns, end_ns)`."""

    def trades(self, instrument_id: str, start_ns: int, end_ns: int) -> ArchivedTrades: ...

    def second_rows(self, instrument_id: str, start_ns: int, end_ns: int) -> Mapping[int, int]:
        """Snapshot rows per second (`ts_event // 1 s`) with `ts_event` in the window."""
        ...


class CoverageSource(Protocol):
    """
    The durable explanations: capture's coverage record and archive-gap markers. Invariant: every
    line is yielded parsed or refused (`MalformedLine`), never skipped.
    """

    @property
    def path(self) -> str: ...

    def present(self) -> bool: ...

    def entries(self) -> Iterable[CoverageEntry]: ...

    def gap_markers(self, instrument_id: str) -> Iterable[TradeWindow]: ...


@dataclass(frozen=True)
class Inputs:
    """The three read-only sources one run reconciles."""

    reference: ReferenceRecords
    archive: Archive
    coverage: CoverageSource


@dataclass(frozen=True)
class InstrumentCoverage:
    """An instrument's coverage that can explain something in the day."""

    runs: tuple[SecondsRun, ...]
    windows: tuple[TradeWindow, ...]
    backfilled: frozenset[str]


def day_start_ns(day: date) -> int:
    """00:00 UTC of `day`, in epoch nanoseconds."""
    return int(datetime.combine(day, time(), UTC).timestamp()) * NS_PER_S


def day_end_ns(start_ns: int) -> int:
    """Return the end (exclusive) of the UTC day starting at `start_ns`."""
    return start_ns + SECONDS_PER_DAY * NS_PER_S


def _overlaps(entry: SecondsRun | TradeWindow, start_ns: int, end_ns: int) -> bool:
    if isinstance(entry, SecondsRun):
        low, high = entry.first_s * NS_PER_S, entry.last_s * NS_PER_S
    else:
        low, high = entry.from_ns, entry.to_ns
    return high >= start_ns and low < end_ns


def _file_entry(
    entry: CoverageEntry,
    runs: dict[str, list[SecondsRun]],
    windows: dict[str, list[TradeWindow]],
    backfilled: dict[str, set[str]],
    span: tuple[int, int],
) -> None:
    iid = entry.instrument_id
    if iid not in runs:
        return  # another instrument's line: not this report's subject
    if isinstance(entry, Backfilled):
        backfilled[iid].update(entry.trade_ids)
    elif not _overlaps(entry, *span):
        return
    elif isinstance(entry, SecondsRun):
        runs[iid].append(entry)
    else:
        windows[iid].append(entry)


def collect_coverage(
    entries: Iterable[CoverageEntry],
    instruments: Iterable[str],
    start_ns: int,
    end_ns: int | None = None,
) -> dict[str, InstrumentCoverage]:
    """
    Group the coverage lines of the plan's instruments, keeping runs and windows that touch
    `[start_ns, end_ns)` (the day from `start_ns` when `end_ns` is None). Known limit: backfilled
    ids carry no time, so every one of an instrument's is kept (the coverage file's whole
    history). Upgrade path: a time on the `trades_backfilled` line.

    Known limit (a window's capped budgets): a `trades_dropped` range or counted marker that only
    partly overlaps a window keeps its whole `count`, so it can explain up to that many missing
    trades inside the window although some of the drops it counted lay outside -- the same
    limit a day has at midnight. Upgrade path: capture writes a drop range per second, so a
    range never straddles a window's edge by more than one second.
    """
    span = (start_ns, day_end_ns(start_ns) if end_ns is None else end_ns)
    runs: dict[str, list[SecondsRun]] = {iid: [] for iid in instruments}
    windows: dict[str, list[TradeWindow]] = {iid: [] for iid in runs}
    backfilled: dict[str, set[str]] = {iid: set() for iid in runs}
    for entry in entries:
        _file_entry(entry, runs, windows, backfilled, span)
    return {
        iid: InstrumentCoverage(tuple(runs[iid]), tuple(windows[iid]), frozenset(backfilled[iid]))
        for iid in runs
    }


@dataclass(frozen=True)
class _Day:
    """What every instrument of one run shares: its span `[start_ns, end_ns)` among it."""

    venue: str
    start_ns: int
    end_ns: int
    channels: tuple[TradeChannel, ...]
    index: Mapping[tuple[str, str], str]
    inputs: Inputs


def channel_trades(
    venue: str,
    channel: TradeChannel,
    hour: int,
    reference: ReferenceRecords,
    index: Mapping[tuple[str, str], str],
) -> Iterator[ReferenceTrade]:
    """
    Yield the reference trades of the raw files of hours `hour - 1 .. hour + 1`: the store files
    lines by receive hour, so a trade of venue hour H can be received in either neighbour. The
    caller keeps the ones whose venue time lies in H.
    """
    for file_hour in (hour - 1, hour, hour + 1):
        for record in reference.records(channel.name, file_hour):
            yield from reference_trades(venue, channel, record, index)


def _counted(trade: ReferenceTrade, instrument_id: str, hour: int, day: _Day) -> bool:
    """Whether a reference trade is the instrument's, of venue hour `hour` and in the span."""
    in_span = day.start_ns <= trade.ts_ns < day.end_ns
    return trade.instrument_id == instrument_id and trade.ts_ns // NS_PER_HOUR == hour and in_span


def _reference_hour(
    instrument_id: str, hour: int, channels: Iterable[TradeChannel], day: _Day
) -> ReferenceHour:
    times: dict[str, int] = {}
    ws_ids: set[str] = set()
    for channel in channels:
        for trade in channel_trades(day.venue, channel, hour, day.inputs.reference, day.index):
            if _counted(trade, instrument_id, hour, day):
                times.setdefault(trade.trade_id, trade.ts_ns)
                if not trade.via_rest:
                    ws_ids.add(trade.trade_id)
    return ReferenceHour(times, frozenset(ws_ids))


def _trade_counts(instrument_id: str, coverage: InstrumentCoverage, day: _Day) -> TradeCounts:
    markers = tuple(day.inputs.coverage.gap_markers(instrument_id))
    explanations = Explanations.of((*coverage.windows, *markers))
    archived = day.inputs.archive.trades(instrument_id, day.start_ns, day.end_ns)
    category = instrument_category(day.venue, instrument_id)
    channels = [channel for channel in day.channels if channel.category == category]
    counts = TradeCounts()
    for hour in window_hours(day.start_ns, day.end_ns):
        reference = _reference_hour(instrument_id, hour, channels, day)
        hour_counts, explanations = tally_trades(
            reference, archived.hour(hour), coverage.backfilled, explanations
        )
        counts = counts.plus(hour_counts)
    return replace(counts, archived_twice=archived.duplicate_ids())


def _instrument_report(
    instrument_id: str, coverage: InstrumentCoverage, day: _Day
) -> InstrumentReport:
    trades = _trade_counts(instrument_id, coverage, day)
    rows = day.inputs.archive.second_rows(instrument_id, day.start_ns, day.end_ns)
    seconds = tally_seconds(
        day.start_ns // NS_PER_S, rows, coverage.runs, end_s=day.end_ns // NS_PER_S
    )
    return InstrumentReport(instrument_id, trades, seconds)


def hour_label(hour: int) -> str:
    """`2026-09-29T14`: the raw store's name for an hour index."""
    return datetime.fromtimestamp(hour * 3600, UTC).strftime("%Y-%m-%dT%H")


def missing_raw(
    channels: Iterable[TradeChannel], hours: range, reference: ReferenceRecords
) -> tuple[str, ...]:
    """Return `<channel>/<hour>` of every reference channel file of `hours` that does not exist."""
    return tuple(
        f"{channel.name}/{hour_label(hour)}"
        for channel in channels
        for hour in hours
        if not reference.exists(channel.name, hour)
    )


def window_hours(start_ns: int, end_ns: int) -> range:
    """Return the UTC hour indexes a non-empty span `[start_ns, end_ns)` touches."""
    return range(start_ns // NS_PER_HOUR, (end_ns - 1) // NS_PER_HOUR + 1)


def day_hours(day: date) -> range:
    """Return the UTC hour indexes of `day`."""
    start_ns = day_start_ns(day)
    hours = window_hours(start_ns, day_end_ns(start_ns))
    assert len(hours) == HOURS_PER_DAY  # a UTC day has no leap hour
    return hours


# How long after a day ends its archive and coverage record are complete: capture writes a
# second's row and its coverage run at the next flush (every 60 s), a backfill of the day's last
# reconnect settles and fetches later still, and the recorder's hour file after the day (read
# for the boundary) closes an hour after midnight. Restated here, not imported: capture's config
# is code the reference side checks.
# Known limit: a fixed margin, not a signal from capture that a day is complete. A collector down
# across it still leaves the day's tail unwritten, and the day then fails loudly. Upgrade path:
# capture records a per-venue "flushed through" second in the coverage record, and the tool
# waits for it to pass the day's end.
DAY_SETTLE_NS = 2 * NS_PER_HOUR


def is_closed(day: date, now_ns: int) -> bool:
    """
    Whether the whole UTC day has ended and settled (`DAY_SETTLE_NS`): an open day's archive and
    coverage still grow, and a just-ended one's last flush and backfills are still to come.
    """
    return day_start_ns(day) + SECONDS_PER_DAY * NS_PER_S + DAY_SETTLE_NS <= now_ns


# How long after a window ends its archive and coverage record are complete (Story 31.10): the
# window's last second is written at the next flush (every 60 s, at second :02), a backfill of a
# reconnect or restart inside it settles and fetches after that, and the flush after the backfill
# writes what it fetched -- 10 min covers a flush, the settle and one more flush with room. The
# window's last touched hour must also have ended, so its raw hour file is whole
# (`window_settled`). Restated, not imported, like `DAY_SETTLE_NS`.
# Known limit: a fixed margin, the same as the day's. Upgrade path: the same "flushed through"
# second in the coverage record.
WINDOW_SETTLE_NS = 10 * 60 * NS_PER_S


def window_settled(start_ns: int, end_ns: int, now_ns: int) -> bool:
    """
    Whether `[start_ns, end_ns)` can be judged: its last touched UTC hour has ended (the raw
    store's file of it is complete) and `WINDOW_SETTLE_NS` has passed since its end.
    """
    last_hour_end = window_hours(start_ns, end_ns).stop * NS_PER_HOUR
    return max(last_hour_end, end_ns + WINDOW_SETTLE_NS) <= now_ns


def iso_second(ts_ns: int) -> str:
    """`2026-09-30T10:00:00Z`: a whole-second instant as the window's JSON spells it."""
    return datetime.fromtimestamp(ts_ns // NS_PER_S, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reconcile(plan: RecordingPlan, span: tuple[int, int], inputs: Inputs, label: str) -> DayReport:
    """Reconcile a span of whole seconds: the one reconciliation the day and the window share."""
    start_ns, end_ns = span
    coverage = collect_coverage(inputs.coverage.entries(), plan.instruments, start_ns, end_ns)
    shared = _Day(plan.venue, start_ns, end_ns, trade_channels(plan), wire_index(plan), inputs)
    reports = tuple(_instrument_report(iid, coverage[iid], shared) for iid in plan.instruments)
    hours = window_hours(start_ns, end_ns)
    return DayReport(
        venue=plan.venue,
        day=label,
        coverage_file=inputs.coverage.path,
        coverage_present=inputs.coverage.present(),
        missing_raw_files=missing_raw(shared.channels, hours, inputs.reference),
        truncated_neighbour_files=inputs.reference.truncated_neighbours(),
        instruments=reports,
    )


def conserve(plan: RecordingPlan, day: date, inputs: Inputs) -> DayReport:
    """Reconcile every plan instrument's day against the reference and the durable records."""
    start_ns = day_start_ns(day)
    return _reconcile(plan, (start_ns, day_end_ns(start_ns)), inputs, day.isoformat())


def conserve_window(plan: RecordingPlan, start_ns: int, end_ns: int, inputs: Inputs) -> DayReport:
    """
    Reconcile every plan instrument over `[start_ns, end_ns)`, whole seconds (`ValueError`
    otherwise): the day's rules, with the report labelled and bounded by the window.
    """
    if start_ns % NS_PER_S or end_ns % NS_PER_S or end_ns <= start_ns:
        raise ValueError(f"window [{start_ns}, {end_ns}) is not a non-empty span of whole seconds")
    start, end = iso_second(start_ns), iso_second(end_ns)
    report = _reconcile(plan, (start_ns, end_ns), inputs, f"{start}/{end}")
    return replace(report, start=start, end=end)


def report_json(report: DayReport) -> dict[str, Any]:
    """
    Return the report as JSON-ready data, each instrument's and the day's `passed` included, and
    each instrument's `failing` count (Story 31.11: the day verdict reads it, never re-derives it).
    """
    body = asdict(report)
    if report.start is None:  # a day's report keeps its Story 31.2 shape
        del body["start"], body["end"]
    for entry, instrument in zip(body["instruments"], report.instruments, strict=True):
        entry["passed"] = instrument.passed
        entry["failing"] = instrument.failing
    return {"passed": report.passed, **body}


_TRADE_COLUMNS = (
    ("seen", 9),
    ("rest_only", 10),
    ("archived", 9),
    ("backfilled", 11),
    ("ledgered_unrecoverable", 23),
    ("unexplained", 12),
    ("archived_not_seen", 18),
    ("archived_twice", 15),
)
_SECOND_COLUMNS = (
    ("expected", 9),
    ("rows", 7),
    ("unexplained", 12),
    ("duplicate_rows", 15),
    ("row_and_reason", 15),
    ("multiple_reasons", 17),
)
_ID_WIDTH = 28
_MISSING_SHOWN = 6


def _table(title: str, columns: tuple[tuple[str, int], ...], rows: list[tuple[str, Any]]) -> str:
    header = f"{title:<{_ID_WIDTH}}" + "".join(f"{name:>{width}}" for name, width in columns)
    lines = [header]
    for instrument_id, counts in rows:
        cells = "".join(f"{getattr(counts, name):>{width}}" for name, width in columns)
        lines.append(f"{instrument_id:<{_ID_WIDTH}}{cells}")
    return "\n".join(lines)


def _second_label(second: int) -> str:
    return datetime.fromtimestamp(second, UTC).strftime("%H:%M:%S")


def _details(report: InstrumentReport) -> list[str]:
    reasons = ", ".join(f"{k}={v}" for k, v in report.seconds.explained_by_reason.items())
    lines = [f"{report.instrument_id}: seconds explained by reason: {reasons or 'none'}"]
    if report.trades.examples_unexplained:
        lines.append(f"  unexplained trade ids: {', '.join(report.trades.examples_unexplained)}")
    if report.seconds.examples_unexplained:
        seconds = ", ".join(map(_second_label, report.seconds.examples_unexplained))
        lines.append(f"  unexplained seconds (UTC): {seconds}")
    return lines


def _missing_line(missing: tuple[str, ...]) -> str:
    shown = ", ".join(missing[:_MISSING_SHOWN]) + (", ..." if len(missing) > _MISSING_SHOWN else "")
    verdict = f" ({shown}; FAIL: the day's reference is incomplete)" if missing else ""
    return f"raw reference files missing: {len(missing)}{verdict}"


def render_text(report: DayReport) -> str:
    """Render the report as a table: the verdict, the inputs' gaps, trades, then seconds."""
    verdict = "PASS" if report.passed else "FAIL"
    present = "" if report.coverage_present else "  (MISSING: nothing is explained, FAIL)"
    lines = [
        f"conservation {report.venue} {report.day}: {verdict}",
        f"coverage record: {report.coverage_file}{present}",
        _missing_line(report.missing_raw_files),
        f"neighbour files read truncated: {', '.join(report.truncated_neighbour_files) or 'none'}",
        "",
        _table("trades", _TRADE_COLUMNS, [(r.instrument_id, r.trades) for r in report.instruments]),
        "",
        _table(
            "seconds", _SECOND_COLUMNS, [(r.instrument_id, r.seconds) for r in report.instruments]
        ),
        "",
    ]
    lines += [line for r in report.instruments for line in _details(r)]
    return "\n".join(lines)
