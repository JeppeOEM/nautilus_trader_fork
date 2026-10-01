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
The derivs tool's ports and orchestration (Story 31.6): one venue's closed UTC day, one
instrument at a time (`verification.domain.derivs_check` and `instrument_check` hold the rules).
It reuses the conservation tool's reading of the same inputs -- the raw store, the coverage
record (`collect_coverage`), the missing-hour rule -- and the trades tool's recorder gaps
(`recorder_gaps`), so the tools never disagree about what the recorder saw.

Per plan instrument: the WS channel (Bybit `linear.tickers`, Hyperliquid `activeAssetCtx`) is read
once over the day's hours plus the hour before (the state at 00:00) and the hour after, and every
type's reference is built from it; the REST polls of the day's hours validate each reference;
then each type's stored rows are matched and every reference update of the day is classified.
Spot ids have no derivative type: only their definitions are judged, and the day's spot section
counts any derivative row stored under a spot id.

Known limit (plan): the instruments are the venue config's *current* plan, as in conservation.
Upgrade path: the recorder's `plan_changed` lines trim each instrument to the windows it was
planned.

Known limit (memory, MEM-01): one instrument-type-day of stored rows and the instrument's
reference frames and updates as Python objects (see `derivs_reader`; the soak's busiest stream,
Bybit BTCUSDT index, was ~7.6k updates an hour). Upgrade path: hour windows carrying the reference
state across them.
"""

from collections import Counter
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any
from typing import Protocol

from kernel.venues import bybit_category

from verification.application.conservation import CoverageSource
from verification.application.conservation import InstrumentCoverage
from verification.application.conservation import ReferenceRecords
from verification.application.conservation import collect_coverage
from verification.application.conservation import day_hours
from verification.application.conservation import day_start_ns
from verification.application.conservation import missing_raw
from verification.domain.conservation import NS_PER_HOUR
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import SECONDS_PER_DAY
from verification.domain.conservation import Intervals
from verification.domain.conservation import MalformedLine
from verification.domain.conservation import TradeChannel
from verification.domain.derivs_check import BYBIT_FIELDS
from verification.domain.derivs_check import FAILED
from verification.domain.derivs_check import FAILING_ROWS
from verification.domain.derivs_check import FAILING_UPDATES
from verification.domain.derivs_check import FEED_LOSS_REASONS
from verification.domain.derivs_check import FUNDING
from verification.domain.derivs_check import HL_MATCH_BOUND_NS
from verification.domain.derivs_check import HYPERLIQUID_FIELDS
from verification.domain.derivs_check import OI_POLL_WINDOW_NS
from verification.domain.derivs_check import OPEN_INTEREST
from verification.domain.derivs_check import PRICE_TYPES
from verification.domain.derivs_check import REST_CLASSES
from verification.domain.derivs_check import ROW_CLASSES
from verification.domain.derivs_check import TYPES
from verification.domain.derivs_check import UPDATE_CLASSES
from verification.domain.derivs_check import DerivsDayReport
from verification.domain.derivs_check import FileLabel
from verification.domain.derivs_check import Frame
from verification.domain.derivs_check import InstrumentDerivs
from verification.domain.derivs_check import Poll
from verification.domain.derivs_check import PollCoverage
from verification.domain.derivs_check import Recording
from verification.domain.derivs_check import RowJudge
from verification.domain.derivs_check import SpotReport
from verification.domain.derivs_check import StoredValue
from verification.domain.derivs_check import TypeReference
from verification.domain.derivs_check import TypeReport
from verification.domain.derivs_check import bybit_list_item
from verification.domain.derivs_check import bybit_references
from verification.domain.derivs_check import bybit_ticker
from verification.domain.derivs_check import bybit_ticker_poll
from verification.domain.derivs_check import classify_updates
from verification.domain.derivs_check import frozen_counts
from verification.domain.derivs_check import hyperliquid_coin_of
from verification.domain.derivs_check import hyperliquid_ctx
from verification.domain.derivs_check import hyperliquid_ctx_poll
from verification.domain.derivs_check import hyperliquid_references
from verification.domain.derivs_check import hyperliquid_universe
from verification.domain.derivs_check import label_check
from verification.domain.derivs_check import match_by_receive
from verification.domain.derivs_check import match_keyed
from verification.domain.derivs_check import match_poll_window
from verification.domain.derivs_check import poll_coverage
from verification.domain.derivs_check import request_symbol
from verification.domain.derivs_check import rest_by_receive
from verification.domain.derivs_check import rest_keyed
from verification.domain.instrument_check import POLL_CLASSES
from verification.domain.instrument_check import PRICE_PRECISION
from verification.domain.instrument_check import DefinitionReport
from verification.domain.instrument_check import OddVenueDefinition
from verification.domain.instrument_check import StoredDefinition
from verification.domain.instrument_check import VenueDefinition
from verification.domain.instrument_check import bybit_linear_definition
from verification.domain.instrument_check import bybit_spot_definition
from verification.domain.instrument_check import hyperliquid_definition
from verification.domain.instrument_check import in_force
from verification.domain.instrument_check import judge_polls
from verification.domain.plan_file import RecordingPlan
from verification.domain.subscriptions import BYBIT
from verification.domain.subscriptions import BYBIT_CATEGORIES
from verification.domain.subscriptions import bybit_symbol
from verification.domain.subscriptions import hyperliquid_coin
from verification.domain.trade_check import RECORDER_GAP_MARGIN_NS
from verification.domain.trade_check import recorder_gaps


BYBIT_WS = "linear.tickers"
BYBIT_REST = "linear.rest.tickers"
HYPERLIQUID_WS = "activeAssetCtx"
HYPERLIQUID_REST = "rest.metaAndAssetCtxs"
_INSTRUMENTS = "rest.instruments-info"

# What a type stores per reference update: the documented sampling (DATA_DICTIONARY 1.19).
_RATIO = MappingProxyType(
    {
        (BYBIT, "mark"): "one row per frame carrying `markPrice`",
        (BYBIT, "index"): "one row per frame carrying `indexPrice`",
        (BYBIT, FUNDING): (
            "one row per frame whose `fundingRate` or `nextFundingTime` string differs from the "
            "last seen (the adapter's funding_cache); `next_time_only` frames are not written "
            "(Known limit, audit D-104)"
        ),
        (BYBIT, OPEN_INTEREST): "one row per `open_interest_poll_seconds` REST poll",
    }
)
_HL_RATIO = "one row per change of the wire string `{field}` (the adapter's string cache)"
_TS_RULES = MappingProxyType(
    {
        (BYBIT, "mark"): "ts_event = the venue frame `ts` (whole ms)",
        (BYBIT, "index"): "ts_event = the venue frame `ts` (whole ms)",
        (BYBIT, FUNDING): "ts_event = the venue frame `ts` (whole ms)",
        (BYBIT, OPEN_INTEREST): (
            "ts_event == ts_init, the collector's clock after the poll response "
            "(capture/venues/bybit/open_interest.py:44), never a venue time"
        ),
    }
)
_HL_TS_RULE = "ts_event == ts_init, the adapter's receive clock"
_NO_LABEL = "none: stored as decimal text"


class PriceWindow(Protocol):
    """A mark or index window: its rows and the label of each file holding one of the day's."""

    @property
    def rows(self) -> Sequence[StoredValue]: ...

    @property
    def files(self) -> Sequence[FileLabel]: ...


class DerivsSource(Protocol):
    """
    The catalog's derivative rows and definitions, read-only. Invariant: every row is decoded
    exactly or refused (`ValueError`), never skipped; a window's rows are those with `ts_event`
    in it; `definitions` are sorted by `ts_init` (`in_force` and `_precisions_over` rely on it).
    """

    def prices(
        self, kind: str, instrument_id: str, start_ns: int, end_ns: int, day: range
    ) -> PriceWindow: ...

    def funding(self, instrument_id: str, start_ns: int, end_ns: int) -> Sequence[StoredValue]: ...

    def open_interest(
        self, instrument_id: str, start_ns: int, end_ns: int
    ) -> Sequence[StoredValue]: ...

    def definitions(self, instrument_id: str) -> Sequence[StoredDefinition]: ...

    def spot_rows(
        self, start_ns: int, end_ns: int, plan_spot: tuple[str, ...]
    ) -> Mapping[str, int]: ...


@dataclass(frozen=True)
class DerivsInputs:
    """The three read-only sources one run compares, and Bybit's open-interest poll period."""

    reference: ReferenceRecords
    catalog: DerivsSource
    coverage: CoverageSource
    oi_period_s: int


def derivs_channels(plan: RecordingPlan) -> tuple[TradeChannel, ...]:
    """Return the raw channels the day's verdict reads (`TradeChannel`: name, category, whether REST)."""
    if plan.venue != BYBIT:
        return (
            TradeChannel(HYPERLIQUID_WS, "", False),
            TradeChannel(HYPERLIQUID_REST, "", True),
        )
    categories = sorted({bybit_category(iid) for iid in plan.instruments})
    linear = [TradeChannel(BYBIT_WS, "linear", False), TradeChannel(BYBIT_REST, "linear", True)]
    return (
        *(linear if "linear" in categories else []),
        *(TradeChannel(f"{c}.{_INSTRUMENTS}", c, True) for c in categories),
    )


# --- reading the raw store -----------------------------------------------------------------------


def _where(channel: str, record: Mapping[str, object]) -> str:
    return f"{channel} line recv_ns={record.get('recv_ns', record.get('ts_ns'))}"


def _line(record: object, channel: str) -> Mapping[str, object]:
    if not isinstance(record, Mapping):  # `json.loads` of a line can be any JSON value
        raise MalformedLine(f"{channel}: a raw line that is not a JSON object: {record!r:.200}")
    return record


def _stamp(record: Mapping[str, object], key: str, where: str) -> int:
    value = record.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedLine(f"{where}: a line without an integer `{key}`")
    return value


class _Scan:
    """
    One WS channel's pass (never escapes `_scan`): the instrument's frames, and what the gaps need
    -- each connection line preceded by the stamp of the line before it (`recorder_gaps` reads the
    previous line's time at an `open` without a gap before it), and the hours without a file.
    """

    def __init__(self) -> None:
        self.frames: list[Frame] = []
        self.gap_lines: list[Mapping[str, object]] = []
        self.missing_hours: list[int] = []
        self.resets: list[int] = []
        self.previous: int | None = None

    def connection(self, record: Mapping[str, object], where: str) -> None:
        if self.previous is not None:
            self.gap_lines.append({"kind": "frame", "recv_ns": self.previous})
        self.gap_lines.append(record)
        self.previous = _stamp(record, "ts_ns", where)
        self.resets.append(self.previous)

    def recording(self, hours: range) -> Recording:
        since, until = hours.start * NS_PER_HOUR, hours.stop * NS_PER_HOUR
        found = recorder_gaps(self.gap_lines, since, until)
        spans = [*zip(found.starts, found.ends, strict=True)]
        # An hour without a file is a gap, widened as a connection gap is: the store files a line
        # by its receipt, so a frame keyed in the last instants of the hour before (venue time)
        # can sit in the missing file (the smoke: a BTCUSDT mark frame `ts` 16:59:59.984Z filed in
        # the unread 17:00Z hour).
        margin = RECORDER_GAP_MARGIN_NS
        spans += [
            (h * NS_PER_HOUR - margin, (h + 1) * NS_PER_HOUR + margin) for h in self.missing_hours
        ]
        resets = [*self.resets, *(h * NS_PER_HOUR for h in self.missing_hours)]
        return Recording(Intervals.of(spans), tuple(sorted(resets)))


@dataclass(frozen=True)
class _Feed:
    """
    One instrument's WS feed: its channel, wire name, the cheap pre-filter a frame must pass to be
    parsed at all (a frame of another instrument is never judged) and the strict frame parser.
    """

    channel: str
    wire: str
    wanted: Callable[[Mapping[str, object]], bool]
    parse: Callable[[Mapping[str, object], str], tuple[str, Frame]]


def _frame_line(feed: _Feed, record: Mapping[str, object], where: str, scan: _Scan) -> None:
    if record.get("kind") != "frame" or not isinstance(record.get("raw"), str):
        raise MalformedLine(f"{where}: not a frame line with a text `raw` in {feed.channel}")
    scan.previous = _stamp(record, "recv_ns", where)
    if feed.wanted(record):
        wire, frame = feed.parse(record, where)
        if wire == feed.wire:
            scan.frames.append(frame)


def _scan(feed: _Feed, hours: range, reference: ReferenceRecords) -> tuple[list[Frame], Recording]:
    """Read the instrument's frames of `hours` (arrival order) and the channel's availability."""
    scan = _Scan()
    for hour in hours:
        if not reference.exists(feed.channel, hour):
            scan.missing_hours.append(hour)
            continue
        for raw in reference.records(feed.channel, hour):
            record = _line(raw, feed.channel)
            where = _where(feed.channel, record)
            if record.get("kind") == "connection":
                scan.connection(record, where)
            else:
                _frame_line(feed, record, where, scan)
    return scan.frames, scan.recording(hours)


def _rest_lines(
    channel: str, hours: range, reference: ReferenceRecords
) -> Iterator[tuple[Mapping[str, object], str]]:
    """Every REST line of a channel's files of `hours` (connection lines skipped)."""
    for hour in hours:
        for raw in reference.records(channel, hour):
            record = _line(raw, channel)
            if record.get("kind") == "connection":
                continue
            where = _where(channel, record)
            if record.get("kind") != "rest":
                raise MalformedLine(f"{where}: not a REST line")
            yield record, where


def _bybit_polls(symbol: str, hours: range, reference: ReferenceRecords) -> list[Poll]:
    polls = []
    for record, where in _rest_lines(BYBIT_REST, hours, reference):
        if request_symbol(record, where) == symbol:
            polls.append(bybit_ticker_poll(record, where)[1])
    return polls


def _bybit_definitions(
    instrument_id: str, hours: range, reference: ReferenceRecords
) -> tuple[list[VenueDefinition], int]:
    category, symbol = bybit_category(instrument_id), bybit_symbol(instrument_id)
    build = bybit_linear_definition if category == "linear" else bybit_spot_definition
    found, failed = [], 0
    for record, where in _rest_lines(f"{category}.{_INSTRUMENTS}", hours, reference):
        if request_symbol(record, where) != symbol:
            continue
        item = bybit_list_item(record, symbol, where)
        try:
            if item is None:
                raise OddVenueDefinition(f"{where}: no usable instruments-info item")
            found.append(build(item, _stamp(record, "recv_ns", where), where))
        except OddVenueDefinition:
            failed += 1  # a venue oddity: counted, never a refusal
    return found, failed


def _hyperliquid_polls(
    coin: str, hours: range, reference: ReferenceRecords
) -> tuple[list[Poll], list[VenueDefinition], int]:
    """
    Every `metaAndAssetCtxs` poll of the coin: its context values, its universe entry's definition
    and the count of polls without a usable definition (failed, or an odd entry).
    """
    polls, definitions, failed = [], [], 0
    for record, where in _rest_lines(HYPERLIQUID_REST, hours, reference):
        poll = hyperliquid_ctx_poll(record, coin, where)
        polls.append(poll)
        found = hyperliquid_universe(record, coin) if poll.ok else None
        try:
            if found is None:
                raise OddVenueDefinition(f"{where}: no usable universe entry")
            definitions.append(hyperliquid_definition(found[0], poll.recv_ns, where))
        except OddVenueDefinition:
            failed += 1  # a venue oddity: counted, never a refusal
    return polls, definitions, failed


# --- one instrument ------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Subject:
    """One instrument's fixed facts: the day, its coverage explanations and stored definitions."""

    instrument_id: str
    venue: str
    hours: range
    day: range
    feed_loss: Intervals
    restarts: Intervals
    definitions: tuple[StoredDefinition, ...]
    inputs: DerivsInputs

    @property
    def matched(self) -> range:
        """
        The stored rows' `ts_event` window read (ns). Hyperliquid's reaches `HL_MATCH_BOUND_NS`
        beyond the day: a row and its update are stamped by different clocks (capture's receipt,
        the recorder's), so an update received at 00:00:00.020 can have its row at 23:59:59.980.
        Only the day's rows are counted (`RowJudge.counted`); the others only consume updates.
        Bybit's rows carry the frame's own venue time, so its window is the day.
        """
        if self.venue == BYBIT:
            return self.day
        return range(self.day.start - HL_MATCH_BOUND_NS, self.day.stop + HL_MATCH_BOUND_NS)

    @property
    def read(self) -> range:
        """The WS hours read: the day's, the hour before (the state at 00:00) and the one after."""
        return range(self.hours.start - 1, self.hours.stop + 1)


def _subject(
    instrument_id: str, venue: str, day: date, coverage: InstrumentCoverage, inputs: DerivsInputs
) -> _Subject:
    start = day_start_ns(day)
    runs = coverage.runs
    return _Subject(
        instrument_id=instrument_id,
        venue=venue,
        hours=day_hours(day),
        day=range(start, start + SECONDS_PER_DAY * NS_PER_S),
        # Seconds: an update is explained when its own second lies in a feed-loss run.
        feed_loss=Intervals.of(
            (r.first_s, r.last_s) for r in runs if r.reason in FEED_LOSS_REASONS
        ),
        # Nanoseconds: a poll gap is explained when a `restart` run overlaps it.
        restarts=Intervals.of(
            (r.first_s * NS_PER_S, (r.last_s + 1) * NS_PER_S - 1)
            for r in runs
            if r.reason == "restart"
        ),
        definitions=tuple(inputs.catalog.definitions(instrument_id)),
        inputs=inputs,
    )


@dataclass(frozen=True)
class _VenueSide:
    """What the recorder saw for one instrument: type references, REST polls, definition polls."""

    references: Mapping[str, TypeReference]
    polls: Sequence[Poll]
    definitions: Sequence[VenueDefinition]
    failed_definitions: int


def _bybit_side(subject: _Subject) -> _VenueSide:
    reference = subject.inputs.reference
    iid = subject.instrument_id
    definitions, failed = _bybit_definitions(iid, subject.hours, reference)
    if bybit_category(iid) != "linear":
        return _VenueSide({}, (), definitions, failed)
    symbol = bybit_symbol(iid)
    marker = f'tickers.{symbol}"'
    feed = _Feed(BYBIT_WS, symbol, lambda record: marker in str(record["raw"]), bybit_ticker)
    frames, recording = _scan(feed, subject.read, reference)
    polls = _bybit_polls(symbol, subject.hours, reference)
    return _VenueSide(bybit_references(frames, recording), polls, definitions, failed)


def _hyperliquid_side(subject: _Subject) -> _VenueSide:
    reference = subject.inputs.reference
    coin = hyperliquid_coin(subject.instrument_id)
    # Every frame is parsed: Hyperliquid pushes about one context a second per coin.
    # Only the coin's own frames are parsed strictly: a malformed frame of another coin is a
    # venue oddity outside the plan, never a refusal of this run.
    feed = _Feed(
        HYPERLIQUID_WS,
        coin,
        lambda record: hyperliquid_coin_of(record) in (coin, None),
        hyperliquid_ctx,
    )
    frames, recording = _scan(feed, subject.read, reference)
    polls, definitions, failed = _hyperliquid_polls(coin, subject.hours, reference)
    return _VenueSide(hyperliquid_references(frames, recording), polls, definitions, failed)


def _rest_counts(kind: str, side: _VenueSide, venue: str) -> Mapping[str, int]:
    fields = BYBIT_FIELDS if venue == BYBIT else HYPERLIQUID_FIELDS
    judge = rest_keyed if venue == BYBIT else rest_by_receive
    reference = side.references[kind]
    field = fields[kind]
    # A poll whose field is absent or not decimal text is a venue oddity: counted `failed`.
    counts = Counter(
        judge(poll, field, reference) if poll.ok and field in poll.values else FAILED
        for poll in side.polls
    )
    return frozen_counts(counts, REST_CLASSES)


def _stored(
    kind: str, subject: _Subject
) -> tuple[Sequence[StoredValue], Sequence[FileLabel] | None]:
    catalog, iid = subject.inputs.catalog, subject.instrument_id
    start, stop = subject.matched.start, subject.matched.stop
    if kind in PRICE_TYPES:
        window = catalog.prices(kind, iid, start, stop, subject.day)
        return window.rows, window.files
    if kind == FUNDING:
        return catalog.funding(iid, start, stop), None
    return catalog.open_interest(iid, start, stop), None


def _precisions_over(definitions: Sequence[StoredDefinition]) -> Callable[[int, int], set[int]]:
    """
    Return the `price_precision`s of every definition in force over `[first, last]`: the one in
    force at `first` (the first definition before any) and every one written after it up to `last`.
    """

    def precisions(first: int, last: int) -> set[int]:
        at_first = in_force(definitions, first) or (definitions[0] if definitions else None)
        later = [d for d in definitions if first < d.ts_init <= last]
        found = [d.fields.get(PRICE_PRECISION) for d in [*([at_first] if at_first else []), *later]]
        return {value for value in found if isinstance(value, int)}

    return precisions


def _match(kind: str, venue: str, rows: Sequence[StoredValue], judge: RowJudge) -> None:
    if venue != BYBIT:
        match_by_receive(rows, judge)
    elif kind == OPEN_INTEREST:
        match_poll_window(rows, judge)
    else:
        match_keyed(rows, judge)


@dataclass(frozen=True)
class _Coverage:
    """A type's coverage: its update classes, or Bybit open interest's poll coverage instead."""

    updates: Mapping[str, int]
    polls: PollCoverage | None
    examples: tuple[str, ...]


def _coverage(
    kind: str, rows: Sequence[StoredValue], judge: RowJudge, subject: _Subject
) -> _Coverage:
    if subject.venue == BYBIT and kind == OPEN_INTEREST:
        stamps = [row.ts_event for row in rows]
        polls = poll_coverage(stamps, subject.day, subject.inputs.oi_period_s, subject.restarts)
        return _Coverage(MappingProxyType({}), polls, polls.examples)
    updates, examples = classify_updates(
        judge.reference, judge.consumed, subject.day, subject.feed_loss
    )
    return _Coverage(frozen_counts(updates, UPDATE_CLASSES), None, tuple(examples))


def _labels_of(
    files: Sequence[FileLabel] | None, subject: _Subject
) -> tuple[tuple[int, ...] | None, int, tuple[str, ...]]:
    if files is None:
        return None, 0, ()
    return label_check(files, _precisions_over(subject.definitions))


def _type_report(kind: str, side: _VenueSide, subject: _Subject) -> TypeReport:
    rows, files = _stored(kind, subject)
    judge = RowJudge(side.references[kind], counted=subject.day)
    _match(kind, subject.venue, rows, judge)
    coverage = _coverage(kind, rows, judge, subject)
    labels, wrong, label_examples = _labels_of(files, subject)
    return TypeReport(
        kind=kind,
        ratio=_ratio(kind, subject.venue),
        ts_rule=_TS_RULES.get((subject.venue, kind), _HL_TS_RULE),
        rest=_rest_counts(kind, side, subject.venue),
        rows=sum(row.ts_event in subject.day for row in rows),
        row_classes=frozen_counts(judge.classes, ROW_CLASSES),
        updates=coverage.updates,
        coverage=coverage.polls,
        labels=labels,
        label_vs_definition=wrong,
        examples=(*judge.examples, *coverage.examples, *label_examples),
    )


def _ratio(kind: str, venue: str) -> str:
    if venue == BYBIT:
        return _RATIO[(venue, kind)]
    return _HL_RATIO.format(field=HYPERLIQUID_FIELDS[kind])


def _instrument(subject: _Subject) -> InstrumentDerivs:
    side = _bybit_side(subject) if subject.venue == BYBIT else _hyperliquid_side(subject)
    definitions = judge_polls(side.definitions, side.failed_definitions, subject.definitions)
    types = tuple(_type_report(kind, side, subject) for kind in TYPES if side.references)
    return InstrumentDerivs(subject.instrument_id, types, definitions)


def _refuse_unrecorded(plan: RecordingPlan) -> None:
    """
    Refuse (`ValueError`) a Bybit id of a category the recorder does not record (inverse): it has
    no reference channel, and the spot rules would judge its definition as a spot pair's.
    """
    if plan.venue != BYBIT:
        return
    for iid in plan.instruments:
        if bybit_category(iid) not in BYBIT_CATEGORIES:
            raise ValueError(f"{iid}: Bybit category {bybit_category(iid)!r} is not recorded")


def check_day(plan: RecordingPlan, day: date, inputs: DerivsInputs) -> DerivsDayReport:
    """Check every plan instrument's derivative rows and definitions over one UTC day."""
    _refuse_unrecorded(plan)
    start = day_start_ns(day)
    end = start + SECONDS_PER_DAY * NS_PER_S
    coverage = collect_coverage(inputs.coverage.entries(), plan.instruments, start)
    reports = tuple(
        _instrument(_subject(iid, plan.venue, day, coverage[iid], inputs))
        for iid in plan.instruments
    )
    spot = None
    if plan.venue == BYBIT:
        plan_spot = tuple(i for i in plan.instruments if bybit_category(i) == "spot")
        spot = SpotReport(MappingProxyType(dict(inputs.catalog.spot_rows(start, end, plan_spot))))
    return DerivsDayReport(
        venue=plan.venue,
        day=day.isoformat(),
        oi_poll_seconds=inputs.oi_period_s if plan.venue == BYBIT else None,
        coverage_file=inputs.coverage.path,
        coverage_present=inputs.coverage.present(),
        missing_raw_files=missing_raw(derivs_channels(plan), day_hours(day), inputs.reference),
        truncated_neighbour_files=inputs.reference.truncated_neighbours(),
        instruments=reports,
        spot=spot,
    )


# --- the report ----------------------------------------------------------------------------------


def _labels(report: TypeReport) -> object:
    return _NO_LABEL if report.labels is None else list(report.labels)


def _type_json(report: TypeReport) -> dict[str, Any]:
    coverage = report.coverage
    return {
        "kind": report.kind,
        "passed": report.passed,
        "failing": report.failing,
        "reference": report.reference,
        "ratio": report.ratio,
        "ts_rule": report.ts_rule,
        "rest": dict(report.rest),
        "rows": report.rows,
        "row_classes": dict(report.row_classes),
        "updates": dict(report.updates),
        "coverage": None
        if coverage is None
        else {
            "period_s": coverage.period_s,
            "expected": coverage.expected,
            "rows": coverage.rows,
            "poll_gaps": coverage.gaps,
            "poll_gaps_explained": coverage.gaps_explained,
        },
        "labels": _labels(report),
        "label_vs_definition": report.label_vs_definition,
        "examples": list(report.examples),
    }


def _definitions_json(report: DefinitionReport) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "failing": report.failing,
        "polls": dict(report.polls),
        "differs": list(report.differs),
        "venue_changes": list(report.venue_changes),
        "definitions_ts_init": list(report.definitions),
        "not_declared": list(report.not_declared),
        "no_definition": report.no_definition,
    }


def report_json(report: DerivsDayReport) -> dict[str, Any]:
    """Return the report as JSON-ready data, every instrument's and type's verdict included."""
    return {
        "passed": report.passed,
        "venue": report.venue,
        "day": report.day,
        "bounds_ns": {"hl_match": HL_MATCH_BOUND_NS, "oi_poll_window": OI_POLL_WINDOW_NS},
        "open_interest_poll_seconds": report.oi_poll_seconds,
        "coverage_file": report.coverage_file,
        "coverage_present": report.coverage_present,
        "missing_raw_files": list(report.missing_raw_files),
        "truncated_neighbour_files": list(report.truncated_neighbour_files),
        "instruments": [
            {
                "instrument_id": r.instrument_id,
                "passed": r.passed,
                "failing": r.failing,
                "types": [_type_json(t) for t in r.types],
                "definitions": _definitions_json(r.definitions),
            }
            for r in report.instruments
        ],
        "spot": None
        if report.spot is None
        else {"rows": dict(report.spot.rows), "fabricated": report.spot.fabricated},
    }


def _pairs(values: Mapping[str, int], names: Iterable[str]) -> str:
    return " ".join(f"{name}={values.get(name, 0)}" for name in names)


def _type_lines(report: TypeReport) -> list[str]:
    verdict = (
        "PASS"
        if report.passed
        else f"FAIL ({report.failing} failing, reference {report.reference})"
    )
    labels = (
        _NO_LABEL
        if report.labels is None
        else (
            f"{','.join(map(str, report.labels)) or 'none (no rows)'} "
            f"label_vs_definition={report.label_vs_definition}"
        )
    )
    lines = [
        f"  {report.kind}: {verdict}",
        f"    expected  {report.ratio}",
        f"    rows      {report.rows}: {_pairs(report.row_classes, ROW_CLASSES)}",
    ]
    if report.coverage is None:
        lines.append(f"    updates   {_pairs(report.updates, UPDATE_CLASSES)}")
    else:
        c = report.coverage
        lines.append(
            f"    polls     period {c.period_s} s: expected {c.expected}, rows {c.rows}, "
            f"poll_gaps={c.gaps} (explained by a restart run {c.gaps_explained})"
        )
    lines += [f"    labels    {labels}", f"    ts rule   {report.ts_rule}"]
    return lines + [f"    failing   {example}" for example in report.examples]


def _definition_lines(report: DefinitionReport) -> list[str]:
    verdict = "PASS" if report.passed else "FAIL"
    if report.no_definition:
        verdict += " (no_definition)"
    elif not report.verified:
        verdict += " (no poll judged)"
    lines = [
        f"  definitions: {verdict} {_pairs(report.polls, POLL_CLASSES)}",
        f"    stored definitions ts_init: {', '.join(map(str, report.definitions)) or 'none'}",
        f"    venue changes: {'; '.join(report.venue_changes) or 'none'}",
    ]
    lines += [f"    {note}" for note in report.not_declared]
    return lines + [f"    differs   {example}" for example in report.differs]


def _instrument_lines(report: InstrumentDerivs) -> list[str]:
    lines = [f"{report.instrument_id}: {'PASS' if report.passed else 'FAIL'}"]
    for t in report.types:
        lines.append(
            f"  rest {t.kind:<14}{_pairs(t.rest, REST_CLASSES)} -> reference {t.reference}"
        )
    for t in report.types:
        lines += _type_lines(t)
    if not report.types:
        lines.append("  no derivative types (spot): see the spot section")
    return lines + _definition_lines(report.definitions)


def _spot_lines(spot: SpotReport | None) -> list[str]:
    if spot is None:
        return ["spot: no spot instruments on this venue"]
    lines = ["spot (rows with ts_event in the day under a derivative type; every one fabricated):"]
    lines += [f"  {name}: {count} rows" for name, count in spot.rows.items()]
    return [*lines, f"  fabricated={spot.fabricated}"]


_MISSING_SHOWN = 6
_FAILING = (
    f"failing: rows {', '.join(sorted(FAILING_ROWS))}; updates {', '.join(sorted(FAILING_UPDATES))}; "
    "reference unvalidated; labels > 1, label_vs_definition; unexplained open-interest poll gaps; "
    "definitions differs, no_definition, no poll judged; spot fabricated"
)


def _missing_line(missing: tuple[str, ...]) -> str:
    shown = ", ".join(missing[:_MISSING_SHOWN]) + (", ..." if len(missing) > _MISSING_SHOWN else "")
    return f"raw reference files missing: {len(missing)}" + (f" ({shown}; FAIL)" if missing else "")


def render_text(report: DerivsDayReport) -> str:
    """Render the report: the inputs, then per instrument REST first, the types, definitions; spot."""
    present = "" if report.coverage_present else "  (MISSING: nothing is explained, FAIL)"
    seconds = report.oi_poll_seconds
    period = "" if seconds is None else f"; Bybit open_interest_poll_seconds {seconds}"
    lines = [
        f"derivs {report.venue} {report.day}: {'PASS' if report.passed else 'FAIL'}",
        f"coverage record: {report.coverage_file}{present}",
        _missing_line(report.missing_raw_files),
        f"neighbour files read truncated: {', '.join(report.truncated_neighbour_files) or 'none'}",
        f"time-alignment bounds (never value tolerances): HL match {HL_MATCH_BOUND_NS // 1_000_000} ms,"
        f" OI poll window {OI_POLL_WINDOW_NS // 1_000_000} ms{period}",
        _FAILING,
        "",
    ]
    for instrument in report.instruments:
        lines += _instrument_lines(instrument)
    return "\n".join([*lines, *_spot_lines(report.spot)])
