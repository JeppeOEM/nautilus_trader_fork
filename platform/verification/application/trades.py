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
The trades tool's ports and orchestration (Story 31.4): one venue's closed UTC day, one
instrument at a time, one hour at a time (`verification.domain.trade_check` holds the rules).
It reuses the conservation tool's reading of the same inputs -- the raw store's H-1..H+1 read of
an hour (`channel_trades`), the coverage record and markers (`collect_coverage`, `Explanations`),
the plan's channels and wire index -- so the two tools can never disagree about what was seen.

For hour H: the reference ids with venue time in H (every copy merged, `merge_reference`) are
compared with the archived trades with `ts_event` in H (`compare_ids`); then every second of H
with a snapshot row, reference trades or archived trades is judged (`classify_second`).

Known limit (memory, MEM-01): one instrument-window of archived trades and snapshot rows in Arrow
plus one hour of Python objects (the hour's reference copies, archived trades and rows); every
raw trade file is decoded up to three times per instrument that shares it, plus once per WS trade
channel for its connection lines. Upgrade path: conservation's -- one decode per raw file shared
by every instrument of its channel.

Known limit (plan): the instruments are the venue config's *current* plan, not the plan in force
on the day, as in conservation. Upgrade path: the recorder's `plan_changed` lines trim each
instrument to the windows it was planned.

Known limit (hour partition): both sides are partitioned by their own time, so a trade whose
archived `ts_event` lies in another UTC hour than its venue time is reported as one missing and
one extra id (both failing) rather than as `mismatch_ts_event`. Upgrade path: look a missing id
up in the neighbour hours' archive slices before calling it missing.

Known limit (recorder-only outage): an archive-only id is explained only when capture backfilled
it *and* it lies in a recorder gap. A trade capture archived live while only the recorder was
disconnected (and that no Bybit `recent-trade` poll caught) is `extra_unexplained`: a loud false
fail, never a false pass. The same holds for trades before a recorder that started mid-window.
Upgrade path: a second, independent recorder connection per endpoint, whose union is the
reference.
"""

from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import replace
from datetime import date
from typing import Any
from typing import Protocol

from verification.application.conservation import CoverageSource
from verification.application.conservation import InstrumentCoverage
from verification.application.conservation import ReferenceRecords
from verification.application.conservation import channel_trades
from verification.application.conservation import collect_coverage
from verification.application.conservation import day_hours
from verification.application.conservation import day_start_ns
from verification.application.conservation import hour_label
from verification.application.conservation import missing_raw
from verification.domain.conservation import NS_PER_HOUR
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import Explanations
from verification.domain.conservation import Intervals
from verification.domain.conservation import ReferenceTrade
from verification.domain.conservation import TradeChannel
from verification.domain.conservation import TradeWindow
from verification.domain.conservation import instrument_category
from verification.domain.conservation import trade_channels
from verification.domain.conservation import wire_index
from verification.domain.plan_file import RecordingPlan
from verification.domain.trade_check import FAILING_IDS
from verification.domain.trade_check import FAILING_SECONDS
from verification.domain.trade_check import KNOWN_CAUSES
from verification.domain.trade_check import SECOND_CLASSES
from verification.domain.trade_check import STAGES
from verification.domain.trade_check import ArchivedTrade
from verification.domain.trade_check import HourIds
from verification.domain.trade_check import IdContext
from verification.domain.trade_check import IdCounts
from verification.domain.trade_check import InstrumentTrades
from verification.domain.trade_check import LatencyHistogram
from verification.domain.trade_check import ReferenceId
from verification.domain.trade_check import SecondFacts
from verification.domain.trade_check import SecondTally
from verification.domain.trade_check import StoredRow
from verification.domain.trade_check import TradesDayReport
from verification.domain.trade_check import classify_second
from verification.domain.trade_check import compare_ids
from verification.domain.trade_check import merge_reference
from verification.domain.trade_check import recorder_gaps
from verification.domain.trade_check import tally_seconds


class ArchivedValues(Protocol):
    """
    One instrument-window of archived trades. Invariant: `hour(h)` yields every trade stored
    with `ts_event` in UTC hour `h` (duplicates included), decoded at its own file's precisions;
    `duplicate_ids()` counts ids stored more than once in the window; `first_ts_event` is the
    instrument's earliest archived trade over its whole archive (None without one).
    """

    @property
    def first_ts_event(self) -> int | None: ...

    def hour(self, hour: int) -> Sequence[ArchivedTrade]: ...

    def duplicate_ids(self) -> int: ...


class SnapshotRows(Protocol):
    """One instrument-window of snapshot rows: `hour(h)` maps each second of `h` to its rows."""

    def hour(self, hour: int) -> Mapping[int, Sequence[StoredRow]]: ...


class TradeArchive(Protocol):
    """The catalog, read-only: an instrument's trade values and snapshot rows in a window."""

    def trade_values(self, instrument_id: str, start_ns: int, end_ns: int) -> ArchivedValues: ...

    def snapshot_trade_rows(
        self, instrument_id: str, start_ns: int, end_ns: int
    ) -> SnapshotRows: ...


class TradeCoverage(CoverageSource, Protocol):
    """Conservation's durable explanations, plus the markers' own unwidened `ts_init` spans."""

    def gap_marker_spans(self, instrument_id: str) -> Iterable[TradeWindow]: ...


@dataclass(frozen=True)
class TradeInputs:
    """The three read-only sources one run compares."""

    reference: ReferenceRecords
    archive: TradeArchive
    coverage: TradeCoverage


@dataclass(frozen=True)
class _Run:
    """What every instrument of one run shares."""

    venue: str
    stage: str
    hours: range
    channels: tuple[TradeChannel, ...]
    index: Mapping[tuple[str, str], str]
    gaps: Mapping[str, Intervals]
    inputs: TradeInputs


@dataclass(frozen=True)
class _Subject:
    """One instrument's fixed facts: its channels, id context, rebuild exemptions and runs."""

    instrument_id: str
    channels: tuple[TradeChannel, ...]
    context: IdContext
    exempt: Callable[[int], bool]
    runs: Intervals


def channel_gaps(channel: TradeChannel, hours: range, reference: ReferenceRecords) -> Intervals:
    """Return a WS trade channel's recorder gaps, from its own files of the window and its neighbours."""
    read = range(hours.start - 1, hours.stop + 1)
    lines = (record for hour in read for record in reference.records(channel.name, hour))
    return recorder_gaps(lines, read.start * NS_PER_HOUR, read.stop * NS_PER_HOUR)


def _union(intervals: Iterable[Intervals]) -> Intervals:
    return Intervals.of(span for i in intervals for span in zip(i.starts, i.ends, strict=True))


def _rebuild_exempt(first_ts_event: int | None, markers: Intervals) -> Callable[[int], bool]:
    """
    Whether the nightly rebuild keeps a row's live values (`docs/DATA_DICTIONARY.md` section 6):
    its `ts_event` lies before the floor second of the instrument's first archived trade, or in
    an archive-gap marker's own `ts_init` span.
    """
    start = None if first_ts_event is None else first_ts_event // NS_PER_S * NS_PER_S
    return lambda ts_ns: start is None or ts_ns < start or markers.contains(ts_ns)


def _subject(
    instrument_id: str, coverage: InstrumentCoverage, archived: ArchivedValues, run: _Run
) -> _Subject:
    category = instrument_category(run.venue, instrument_id)
    channels = tuple(c for c in run.channels if c.category == category)
    gaps = _union(run.gaps[c.name] for c in channels if c.name in run.gaps)
    spans = run.inputs.coverage.gap_marker_spans(instrument_id)
    markers = Intervals.of((span.from_ns, span.to_ns) for span in spans)
    return _Subject(
        instrument_id=instrument_id,
        channels=channels,
        context=IdContext(coverage.backfilled, gaps),
        exempt=_rebuild_exempt(archived.first_ts_event, markers),
        runs=Intervals.of((r.first_s, r.last_s) for r in coverage.runs),
    )


def _hour_reference(subject: _Subject, hour: int, run: _Run) -> Iterator[ReferenceTrade]:
    for channel in subject.channels:
        for trade in channel_trades(run.venue, channel, hour, run.inputs.reference, run.index):
            if trade.instrument_id == subject.instrument_id and trade.ts_ns // NS_PER_HOUR == hour:
                yield trade


def _by_second[T](items: Iterable[T], ts_ns: Callable[[T], int]) -> dict[int, list[T]]:
    grouped: dict[int, list[T]] = {}
    for item in items:
        grouped.setdefault(ts_ns(item) // NS_PER_S, []).append(item)
    return grouped


def _judge_hour(
    rows: Mapping[int, Sequence[StoredRow]],
    reference: Mapping[str, ReferenceId],
    ids: HourIds,
    subject: _Subject,
    stage: str,
) -> SecondTally:
    """
    Classify every second of the hour that has a row, reference trades or archived trades (a
    second holding only an archive-only id, backfilled or not, is judged too: it has no row).
    """
    ref_trades = _by_second((r.trade for r in reference.values()), lambda t: t.ts_ns)
    arc_trades = _by_second(ids.folded, lambda t: t.ts_event)
    judged = []
    for second in sorted(rows.keys() | ref_trades.keys() | arc_trades.keys()):
        second_rows = rows.get(second, ())
        facts = SecondFacts(
            rows=second_rows,
            reference=ref_trades.get(second, []),
            archived=arc_trades.get(second, []),
            rebuild_exempt=any(subject.exempt(row.ts_event) for row in second_rows),
            run_covers=subject.runs.contains(second),
            discrepancy=ids.discrepancy(second),
            tick_rounded=second in ids.rounded_seconds,
        )
        judged.append((second, classify_second(facts, stage)))
    return tally_seconds(judged)


def _instrument(instrument_id: str, coverage: InstrumentCoverage, run: _Run) -> InstrumentTrades:
    window = (run.hours.start * NS_PER_HOUR, run.hours.stop * NS_PER_HOUR)
    archived = run.inputs.archive.trade_values(instrument_id, *window)
    rows = run.inputs.archive.snapshot_trade_rows(instrument_id, *window)
    subject = _subject(instrument_id, coverage, archived, run)
    markers = tuple(run.inputs.coverage.gap_markers(instrument_id))
    explanations = Explanations.of((*coverage.windows, *markers))
    result = InstrumentTrades(instrument_id, IdCounts(), SecondTally(), LatencyHistogram())
    for hour in run.hours:
        reference = merge_reference(_hour_reference(subject, hour, run))
        ids = compare_ids(reference, archived.hour(hour), subject.context, explanations)
        explanations = ids.explanations
        seconds = _judge_hour(rows.hour(hour), reference, ids, subject, run.stage)
        result = replace(
            result,
            ids=result.ids.plus(ids.counts),
            seconds=result.seconds.plus(seconds),
            latency=result.latency.plus(ids.latency),
        )
    return replace(result, ids=replace(result.ids, duplicated=archived.duplicate_ids()))


def check_hours(
    plan: RecordingPlan, day: date, hours: range, inputs: TradeInputs, stage: str
) -> TradesDayReport:
    """
    Check the hours `hours` (UTC hour indexes, all inside `day`) of every plan instrument.
    `check_day` is the tool's run; a narrower window is for a look at part of a day (the smoke
    run over a still-open day's closed hours), whose report names the hours it covers.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}, not one of {', '.join(STAGES)}")
    whole = day_hours(day)
    if not hours or hours.step != 1 or hours.start < whole.start or hours.stop > whole.stop:
        raise ValueError(f"hours {hours} are not a window of {day}")
    coverage = collect_coverage(inputs.coverage.entries(), plan.instruments, day_start_ns(day))
    channels = trade_channels(plan)
    gaps = {c.name: channel_gaps(c, hours, inputs.reference) for c in channels if not c.rest}
    run = _Run(plan.venue, stage, hours, channels, wire_index(plan), gaps, inputs)
    reports = tuple(_instrument(iid, coverage[iid], run) for iid in plan.instruments)
    return TradesDayReport(
        venue=plan.venue,
        day=day.isoformat(),
        stage=stage,
        hours=(hour_label(hours.start), hour_label(hours.stop - 1)),
        coverage_file=inputs.coverage.path,
        coverage_present=inputs.coverage.present(),
        missing_raw_files=missing_raw(channels, hours, inputs.reference),
        truncated_neighbour_files=inputs.reference.truncated_neighbours(),
        instruments=reports,
    )


def check_day(plan: RecordingPlan, day: date, inputs: TradeInputs, stage: str) -> TradesDayReport:
    """Check every plan instrument's whole UTC day at `stage` (`live` or `rebuilt`)."""
    return check_hours(plan, day, day_hours(day), inputs, stage)


def _instrument_json(report: InstrumentTrades) -> dict[str, Any]:
    classes = {name: report.seconds.classes.get(name, 0) for name in SECOND_CLASSES}
    return {
        "instrument_id": report.instrument_id,
        "passed": report.passed,
        "failing": report.failing,
        "ids": asdict(report.ids),
        "seconds": {"classes": classes, "examples": [list(e) for e in report.seconds.examples]},
        "latency_ms": report.latency.summary(),
        "known": {
            name: {"count": n, "reason": KNOWN_CAUSES[name]} for name, n in report.known.items()
        },
    }


def report_json(report: TradesDayReport) -> dict[str, Any]:
    """Return the report as JSON-ready data, with every instrument's verdict and all classes."""
    return {
        "passed": report.passed,
        "provisional": report.provisional,
        "venue": report.venue,
        "day": report.day,
        "stage": report.stage,
        "hours": list(report.hours),
        "coverage_file": report.coverage_file,
        "coverage_present": report.coverage_present,
        "missing_raw_files": list(report.missing_raw_files),
        "truncated_neighbour_files": list(report.truncated_neighbour_files),
        "instruments": [_instrument_json(r) for r in report.instruments],
    }


_ID_FIELDS = (
    "seen",
    "matched",
    "backfilled",
    "missing_explained",
    "missing_unexplained",
    "extra_explained",
    "extra_unexplained",
    "duplicated",
    "reference_conflict",
    "off_precision",
)
_FIELD_FIELDS = (
    "mismatch_price",
    "mismatch_size",
    "mismatch_side",
    "mismatch_ts_event",
    "implausible_latency",
)


def _pairs(values: Mapping[str, Any], names: Iterable[str]) -> str:
    return " ".join(f"{name}={values[name]}" for name in names)


def _instrument_text(report: InstrumentTrades) -> list[str]:
    ids = asdict(report.ids)
    classes = {name: report.seconds.classes.get(name, 0) for name in SECOND_CLASSES}
    latency = report.latency.summary()
    tokens = ", ".join(repr(t) for t in report.ids.no_aggressor_tokens) or "none"
    lines = [
        f"{report.instrument_id}: {'PASS' if report.passed else 'FAIL'}",
        f"  ids      {_pairs(ids, _ID_FIELDS)}",
        f"  fields   {_pairs(ids, _FIELD_FIELDS)}",
        f"  seconds  {_pairs(classes, SECOND_CLASSES)}",
        f"  latency  ts_init - recv_ns ms: {_pairs(latency, latency)}",
        f"  wire     no_aggressor={report.ids.wire_no_aggressor} tokens: {tokens}",
    ]
    lines += _known_lines(report)
    if report.ids.examples:
        lines.append(f"  failing ids: {', '.join(report.ids.examples)}")
    if report.seconds.examples:
        seconds = ", ".join(f"{second}:{verdict}" for second, verdict in report.seconds.examples)
        lines.append(f"  failing seconds (epoch s): {seconds}")
    return lines


def _known_lines(report: InstrumentTrades) -> list[str]:
    """Render the known causes: lower severity, reported with their reason, never failing."""
    known = report.known
    if not known:
        return []
    reasons = dict.fromkeys(KNOWN_CAUSES[name] for name in known)
    lines = [f"  known    {_pairs(known, known)} (not failing)"]
    lines += [f"    reason: {reason}" for reason in reasons]
    if report.ids.known_examples:
        lines += [f"    {example}" for example in report.ids.known_examples]
    return lines


_MISSING_SHOWN = 6


def _missing_line(missing: tuple[str, ...]) -> str:
    shown = ", ".join(missing[:_MISSING_SHOWN]) + (", ..." if len(missing) > _MISSING_SHOWN else "")
    return f"raw reference files missing: {len(missing)}" + (f" ({shown}; FAIL)" if missing else "")


def _verdict(report: TradesDayReport) -> str:
    if not report.provisional:
        return "PASS" if report.passed else "FAIL"
    if not report.passed:
        # What the rebuild corrects is `live_provisional`, never failing: a live failure is not
        # one the rebuild is expected to clear, so no rerun is suggested.
        return "FAIL (live stage: none of these failures is one the nightly rebuild corrects)"
    return (
        "PASS (PROVISIONAL: live stage, before the nightly rebuild; it never claims the rebuilt"
        " guarantee -- rerun with --stage rebuilt after it)"
    )


def render_text(report: TradesDayReport) -> str:
    """Render the report: the verdict, the inputs' gaps, then one block per instrument."""
    present = "" if report.coverage_present else "  (MISSING: nothing is explained, FAIL)"
    lines = [
        f"trades {report.venue} {report.day} stage {report.stage}: {_verdict(report)}",
        f"hours: {report.hours[0]} .. {report.hours[1]} UTC",
        f"coverage record: {report.coverage_file}{present}",
        _missing_line(report.missing_raw_files),
        f"neighbour files read truncated: {', '.join(report.truncated_neighbour_files) or 'none'}",
        f"failing: ids {', '.join(FAILING_IDS)}; seconds {', '.join(sorted(FAILING_SECONDS))}",
        f"known (reported, not failing): {', '.join(KNOWN_CAUSES)}",
        "",
    ]
    for instrument in report.instruments:
        lines += _instrument_text(instrument)
    return "\n".join(lines)
