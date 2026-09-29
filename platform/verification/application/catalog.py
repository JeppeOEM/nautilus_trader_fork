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
The catalog tool's ports and orchestration (Story 31.7, `docs/DATA_DICTIONARY.md` section 1.20):
one venue's closed UTC day, in four checks -- structure, the consolidation rehearsal, backtest-read
parity and candle parity (`verification.domain.catalog_check` holds the rules).

The ports split the oracle from the subject (DATA-02). `RawCatalog` and `CandleSource` are the
oracle's raw pyarrow and `sqlite3` reads (`verification.infrastructure.catalog_scan`);
`NautilusReader`, `BacktestReader` and `Rehearsal` are the code under test, driven by
`verification.subject`, which only the composition root (`verification.catalog`) imports. This
module declares them as `Protocol`s and never imports the subject.

Scope: the plan's instruments (the venue config's current plan, as in the other tools) and every
`data/<type>/` directory holding a leaf for one; a leaf's day files are those whose name span meets
`[D - READ_MARGIN_NS, D + 1 + READ_MARGIN_NS)`, or whose stamps' statistics do. The day files and
their (name, inode, size, mtime) are fingerprinted first, after the rehearsal and at the end: a day
file that vanished, appeared or was rewritten meanwhile (a maintenance run) makes the day
unjudgeable, never a verdict on a moving catalog; a live collector's file of a later day is no day
file and never refuses the run. The candle store's bars are read once, at the start.

Known limit (runtime and memory): every stored row of the day is digested in Python per `repr` --
on the rehearsal side once linked and again only for a type whose files a stage changed, once for
the stored leg, once re-encoded for the query leg and once for the received leg (about 7.5 us a
trade row and 60 us a snapshot row per pass). Measured on the Story 31.2 soak's 2026-09-29
12:59-19:30Z (6.5 h): Bybit, four instruments, 2,227,486 trades and 93,721 snapshots, 257 s and
1.56 GB peak RSS; Hyperliquid, 30,701 trades and 23,436 snapshots, 38 s and 0.71 GB. The peak is
up to one hour of every plan instrument's objects plus the engines in the backtest (ETHUSDT linear,
the busiest, ~150k trades an hour) and the archive's own merge of the busiest leaf-day in the
rehearsal (the code under test);
the runtime grows with the day's rows (a full Bybit day: ~16 min). Upgrade path: an Arrow-compute
hash over each column instead of per-row `repr`.
"""

from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from pathlib import Path
from types import MappingProxyType
from typing import Any
from typing import Protocol

from verification.application.conservation import day_start_ns
from verification.domain.catalog_check import BEYOND_MARGIN
from verification.domain.catalog_check import CANDLE_CLASSES
from verification.domain.catalog_check import DUPLICATE_TS_EVENT
from verification.domain.catalog_check import FAILING_CANDLES
from verification.domain.catalog_check import FLOAT_NOISE
from verification.domain.catalog_check import LEAF_CLASSES
from verification.domain.catalog_check import LEGS
from verification.domain.catalog_check import NS_PER_DAY
from verification.domain.catalog_check import PARITY_TYPES
from verification.domain.catalog_check import READ_MARGIN_NS
from verification.domain.catalog_check import READ_MISMATCH
from verification.domain.catalog_check import SCHEMAS
from verification.domain.catalog_check import SNAPSHOT_TYPE
from verification.domain.catalog_check import STAGES
from verification.domain.catalog_check import UNKNOWN_TYPE
from verification.domain.catalog_check import UNKNOWN_WIDTH
from verification.domain.catalog_check import CandleReport
from verification.domain.catalog_check import CatalogDayReport
from verification.domain.catalog_check import ConsolidationRun
from verification.domain.catalog_check import DayFiles
from verification.domain.catalog_check import Digest
from verification.domain.catalog_check import FileOpen
from verification.domain.catalog_check import FileScan
from verification.domain.catalog_check import LeafFile
from verification.domain.catalog_check import LeafReport
from verification.domain.catalog_check import Leg
from verification.domain.catalog_check import ParityReport
from verification.domain.catalog_check import Received
from verification.domain.catalog_check import RehearsalReport
from verification.domain.catalog_check import Span
from verification.domain.catalog_check import Stage
from verification.domain.catalog_check import StoredBar
from verification.domain.catalog_check import StoredLeg
from verification.domain.catalog_check import TypeRehearsal
from verification.domain.catalog_check import TypeStructure
from verification.domain.catalog_check import day_window
from verification.domain.catalog_check import duplicate_count
from verification.domain.catalog_check import hour_windows
from verification.domain.catalog_check import judge_candles
from verification.domain.catalog_check import judge_leaf
from verification.domain.catalog_check import rehearsal_verdict
from verification.domain.catalog_check import schema_classes
from verification.domain.plan_file import RecordingPlan


Fingerprint = frozenset[tuple[str, int, int, int]]


class Unjudgeable(Exception):
    """The day cannot be judged from these inputs: the root refuses with the message."""


class NoDefinition(Unjudgeable):
    """A plan instrument has no stored definition: no backtest can load it."""


class CatalogChanged(Unjudgeable):
    """A day file vanished, appeared or changed (inode, size, mtime) while the run read it."""


class RawCatalog(Protocol):
    """
    The oracle's raw reads of one catalog root. Invariant: pyarrow and the file names only, no
    Nautilus; a listed file that is gone raises `FileNotFoundError`, never reads as empty.
    """

    def type_dirs(self) -> tuple[str, ...]: ...

    def has_leaf(self, data_type: str, instrument_id: str) -> bool: ...

    def day_files(
        self, data_type: str, instrument_id: str, window: tuple[int, int]
    ) -> DayFiles: ...

    def fingerprint(self, paths: Sequence[Path]) -> Fingerprint: ...

    def scan(self, path: Path) -> FileScan: ...

    def schema(self, path: Path) -> tuple[str, tuple[str, ...]]: ...

    def snapshot_ts_events(self, paths: Sequence[Path], day: range) -> list[int]: ...

    def digest(self, paths: Sequence[Path]) -> Digest: ...

    def stored(self, paths: Sequence[Path], day: range) -> StoredLeg: ...


class CandleSource(Protocol):
    """The candle store, read-only: an instrument's bars with `t` in the day."""

    @property
    def path(self) -> str: ...

    def bars(self, instrument_id: str, day_start_ns: int) -> tuple[list[StoredBar], int]: ...


class NautilusReader(Protocol):
    """
    `ParquetDataCatalog` driven (the subject). Invariant: every read bounded by `start=`/`end=`;
    rows reach the oracle re-encoded by the catalog's serializer.
    """

    def known(self, data_type: str) -> bool: ...

    def open_file(
        self, data_type: str, iid: str, path: Path, windows: Sequence[tuple[int, int]]
    ) -> FileOpen: ...

    def hourly(
        self, data_type: str, iid: str, day_start_ns: int, columns: Sequence[str]
    ) -> Leg: ...

    def settlement(self, iid: str) -> str | None: ...


class BacktestReader(Protocol):
    """What a `BacktestNode` actor receives per plan instrument over the day (the subject)."""

    def receive(
        self,
        currencies: Mapping[str, str],
        day_start_ns: int,
        columns: Mapping[str, Sequence[str]],
    ) -> Mapping[str, Received]: ...


class ScratchCatalog(Protocol):
    """A scratch copy the archive's own consolidation runs on (the subject)."""

    @property
    def root(self) -> Path: ...

    def intraday(self, now_ns: int) -> ConsolidationRun: ...

    def nightly(self, now_ns: int) -> ConsolidationRun: ...


class Rehearsal(Protocol):
    """Makes scratch copies of listed catalog files; nothing under the catalog is written."""

    def scratch(
        self, catalog: Path, files: Sequence[Path]
    ) -> AbstractContextManager[ScratchCatalog]: ...


@dataclass(frozen=True)
class CatalogInputs:
    """The catalog root, the oracle's reads (and its reader of a scratch root) and the subject."""

    catalog: Path
    raw: RawCatalog
    raw_of: Callable[[Path], RawCatalog]
    nautilus: NautilusReader
    backtest: BacktestReader
    rehearsal: Rehearsal
    candles: CandleSource


@dataclass(frozen=True)
class _Leaf:
    """One in-scope leaf and its day files, as listed at the start of the run."""

    data_type: str
    instrument_id: str
    day_files: DayFiles

    @property
    def paths(self) -> list[Path]:
        return [path for path, _ in self.day_files.files]


@dataclass(frozen=True)
class _Day:
    """The run's fixed facts: the day, its scope and each day file's schema."""

    start: int
    iso: str
    plan: RecordingPlan
    leaves: tuple[_Leaf, ...]
    schemas: Mapping[Path, tuple[str, tuple[str, ...]]]
    inputs: CatalogInputs
    before: Fingerprint
    bars: Mapping[str, tuple[list[StoredBar], int]]

    @property
    def span(self) -> range:
        return range(self.start, self.start + NS_PER_DAY)

    def of_type(self, data_type: str) -> list[_Leaf]:
        return [leaf for leaf in self.leaves if leaf.data_type == data_type]

    def leaf(self, data_type: str, iid: str) -> _Leaf | None:
        found = [leaf for leaf in self.of_type(data_type) if leaf.instrument_id == iid]
        return found[0] if found else None


# --- scope ----------------------------------------------------------------------------------------


def _scope(plan: RecordingPlan, raw: RawCatalog, start: int) -> tuple[_Leaf, ...]:
    window = day_window(start)
    return tuple(
        _Leaf(data_type, iid, raw.day_files(data_type, iid, window))
        for data_type in raw.type_dirs()
        for iid in plan.instruments
        if raw.has_leaf(data_type, iid)
    )


def _day_set(leaves: Iterable[_Leaf], raw: RawCatalog, start: int) -> Fingerprint | None:
    """
    Fingerprint (name, inode, size, mtime) every day file the scope selects now; None when
    one vanished while it was read. Only day files are guarded: a live collector's new file of a
    later day is not the checked day's and never refuses the run.
    """
    window = day_window(start)
    try:
        paths = [
            path
            for leaf in leaves
            for path, _ in raw.day_files(leaf.data_type, leaf.instrument_id, window).files
        ]
        return raw.fingerprint(paths)
    except FileNotFoundError:
        return None


def _currencies(plan: RecordingPlan, nautilus: NautilusReader) -> dict[str, str]:
    currencies = {iid: nautilus.settlement(iid) for iid in plan.instruments}
    missing = [iid for iid, currency in currencies.items() if currency is None]
    if missing:
        raise NoDefinition(f"plan instruments without a stored definition: {missing}")
    return {iid: currency for iid, currency in currencies.items() if currency is not None}


# --- structure ------------------------------------------------------------------------------------


def _opened(day: _Day, leaf: _Leaf, path: Path, scan: FileScan, known: bool) -> FileOpen | None:
    """
    Open a file over the hours its rows occupy, each clipped to the rows' own `[min, max]`
    `ts_init`, never its name's: neither a corrupt name spanning years nor one stray stamp far
    from the rest becomes thousands of hour queries. None for an unknown type.
    """
    if not known:
        return None
    if scan.low is None or scan.high is None:
        return FileOpen(0)  # no stamped row to decode: `empty` / `null_ts` judge it
    windows = hour_windows(Span(scan.low, scan.high), scan.hours)
    return day.inputs.nautilus.open_file(leaf.data_type, leaf.instrument_id, path, windows)


def _leaf_file(day: _Day, leaf: _Leaf, path: Path, span: Span, known: bool) -> LeafFile:
    scan = day.inputs.raw.scan(path)
    return LeafFile(path.name, span, scan, _opened(day, leaf, path, scan, known))


def _leaf_report(day: _Day, leaf: _Leaf, known: bool) -> LeafReport:
    found = leaf.day_files
    files = [_leaf_file(day, leaf, path, span, known) for path, span in found.files]
    return judge_leaf(leaf.data_type, leaf.instrument_id, files, found.bad_names, found.others)


def _duplicates(day: _Day, leaves: Sequence[_Leaf]) -> int:
    raw = day.inputs.raw
    return sum(
        duplicate_count(raw.snapshot_ts_events(leaf.paths, day.span), day.span) for leaf in leaves
    )


def _type_structure(day: _Day, data_type: str) -> TypeStructure:
    leaves = day.of_type(data_type)
    known = day.inputs.nautilus.known(data_type)
    signed = [(day.schemas[p][0], str(p)) for leaf in leaves for p in leaf.paths]
    return TypeStructure(
        data_type=data_type,
        known=known,
        schemas=schema_classes(signed),
        leaves=tuple(_leaf_report(day, leaf, known) for leaf in leaves),
        duplicate_ts_event=_duplicates(day, leaves) if data_type == SNAPSHOT_TYPE else None,
    )


def _types(day: _Day) -> list[str]:
    return sorted({leaf.data_type for leaf in day.leaves})


# --- the rehearsal --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Seen:
    """One type's scratch files after a stage: their fingerprint and the digest of their rows."""

    fingerprint: Fingerprint
    stage: Stage


def _look(day: _Day, raw: RawCatalog, data_type: str, before: _Seen | None) -> _Seen:
    window = day_window(day.start)
    leaves = day.of_type(data_type)
    paths = [
        path
        for leaf in leaves
        for path, _ in raw.day_files(data_type, leaf.instrument_id, window).files
    ]
    fingerprint = raw.fingerprint(paths)
    if before is not None and before.fingerprint == fingerprint:
        return before  # the same inodes, sizes and times: the same rows, no need to re-read
    return _Seen(fingerprint, Stage(len(paths), raw.digest(paths)))


def _look_all(day: _Day, raw: RawCatalog, seen: Mapping[str, list[_Seen]]) -> None:
    for data_type, history in seen.items():
        history.append(_look(day, raw, data_type, history[-1] if history else None))


def _rehearse(day: _Day, now_ns: int) -> RehearsalReport:
    """
    Link the day files into a scratch catalog and digest each type (`linked`), run the archive's
    intraday merge as of the day's last instant (`intraday`), then its nightly consolidation as of
    `now_ns` (`nightly`), digesting after each.
    """
    inputs = day.inputs
    files = [p for leaf in day.leaves for p in leaf.paths]
    seen: dict[str, list[_Seen]] = {t: [] for t in _types(day)}
    with inputs.rehearsal.scratch(inputs.catalog, files) as scratch:
        raw = inputs.raw_of(scratch.root)
        _look_all(day, raw, seen)
        intraday = scratch.intraday(day.start + NS_PER_DAY - 1)
        _look_all(day, raw, seen)
        nightly = scratch.nightly(now_ns)
        _look_all(day, raw, seen)
    return RehearsalReport(
        types=tuple(_type_rehearsal(t, history) for t, history in seen.items()),
        leaves_failed=intraday.leaves_failed + nightly.leaves_failed,
        days_refused=intraday.days_refused + nightly.days_refused,
    )


def _type_rehearsal(data_type: str, history: Sequence[_Seen]) -> TypeRehearsal:
    changed = any(a.fingerprint != b.fingerprint for a, b in pairwise(history))
    stages = tuple(seen.stage for seen in history)
    return TypeRehearsal(data_type, stages, rehearsal_verdict(stages, changed))


# --- parity and candles ---------------------------------------------------------------------------


def _columns(day: _Day, data_type: str) -> tuple[str, ...]:
    """Every stored column of the type's day files, in first-seen order (the projection)."""
    names: dict[str, None] = {}
    for leaf in day.of_type(data_type):
        for path in leaf.paths:
            names.update(dict.fromkeys(day.schemas[path][1]))
    return tuple(names)


def _parity_report(
    day: _Day, iid: str, data_type: str, columns: Sequence[str], received: Received
) -> ParityReport:
    leaf = day.leaf(data_type, iid)
    paths = leaf.paths if leaf else []
    stored = day.inputs.raw.stored(paths, day.span)
    query = (
        day.inputs.nautilus.hourly(data_type, iid, day.start, columns) if leaf else Leg(Digest())
    )
    legs = {"stored": Leg(stored.digest), "query": query, "received": received.legs[data_type]}
    return ParityReport(iid, data_type, MappingProxyType(legs), stored.beyond_margin)


def _parity(
    day: _Day, currencies: Mapping[str, str]
) -> tuple[tuple[ParityReport, ...], Mapping[str, Received]]:
    columns = {t: _columns(day, t) for t in PARITY_TYPES}
    received = day.inputs.backtest.receive(currencies, day.start, columns)
    reports = tuple(
        _parity_report(day, iid, t, columns[t], received[iid])
        for iid in day.plan.instruments
        for t in PARITY_TYPES
    )
    return reports, received


def _candles(day: _Day, received: Mapping[str, Received]) -> tuple[CandleReport, ...]:
    """Judge the bars read at the run's start (a live store keeps moving) against the fold."""
    reports = []
    for iid in day.plan.instruments:
        bars, unknown = day.bars[iid]
        reports.append(judge_candles(iid, received[iid].trade_rows, bars, unknown))
    return tuple(reports)


# --- the day --------------------------------------------------------------------------------------


def _judge(day: _Day, currencies: Mapping[str, str], now_ns: int) -> CatalogDayReport:
    structure = tuple(_type_structure(day, t) for t in _types(day))
    rehearsal = _rehearse(day, now_ns)
    # The scratch holds hard links: a writer mutating a file in place would change the source.
    _refuse_if_changed(day.leaves, day.inputs.raw, day.start, day.before)
    parity, received = _parity(day, currencies)
    return CatalogDayReport(
        venue=day.plan.venue,
        day=day.iso,
        catalog=str(day.inputs.catalog),
        candle_store=day.inputs.candles.path,
        instruments=day.plan.instruments,
        structure=structure,
        rehearsal=rehearsal,
        parity=parity,
        candles=_candles(day, received),
    )


def _refuse_if_changed(
    leaves: Sequence[_Leaf], raw: RawCatalog, start: int, before: Fingerprint | None
) -> None:
    """Refuse (`CatalogChanged`) when the day files or any of their fingerprints changed."""
    after = _day_set(leaves, raw, start)
    if before is None or after is None or after != before:
        moved = sorted({entry[0] for entry in (before or frozenset()) ^ (after or frozenset())})
        shown = ", ".join(moved[:5]) or "a day file vanished while it was read"
        raise CatalogChanged(f"catalog changed during the check (maintenance ran?): {shown}")


def _start(
    plan: RecordingPlan, inputs: CatalogInputs, start: int
) -> tuple[dict[str, str], tuple[_Leaf, ...], Fingerprint]:
    """Read the scope, its day-file fingerprint and the definitions' currencies, first."""
    try:
        leaves = _scope(plan, inputs.raw, start)
        before = _day_set(leaves, inputs.raw, start)
        currencies = _currencies(plan, inputs.nautilus)
    except FileNotFoundError as exc:
        raise CatalogChanged(f"catalog changed during the check (maintenance ran?): {exc}") from exc
    if before is None:
        raise CatalogChanged(
            "catalog changed during the check (maintenance ran?): a day file vanished"
        )
    return currencies, leaves, before


def check_day(
    plan: RecordingPlan, day: date, inputs: CatalogInputs, now_ns: int
) -> CatalogDayReport:
    """Judge one closed UTC day of the venue's plan instruments (`now_ns`: the nightly stage's)."""
    start = day_start_ns(day)
    currencies, leaves, before = _start(plan, inputs, start)
    bars = {iid: inputs.candles.bars(iid, start) for iid in plan.instruments}
    try:
        schemas = {p: inputs.raw.schema(p) for leaf in leaves for p in leaf.paths}
        facts = _Day(start, day.isoformat(), plan, leaves, schemas, inputs, before, bars)
        report = _judge(facts, currencies, now_ns)
    except Exception:
        _refuse_if_changed(leaves, inputs.raw, start, before)
        raise
    _refuse_if_changed(leaves, inputs.raw, start, before)
    return report


# --- the report -----------------------------------------------------------------------------------


def _pairs(values: Mapping[str, int], names: Iterable[str]) -> str:
    return " ".join(f"{name}={values.get(name, 0)}" for name in names)


def _leaf_json(leaf: LeafReport) -> dict[str, Any]:
    return {
        "instrument_id": leaf.instrument_id,
        "files": leaf.files,
        "rows": leaf.rows,
        "counts": dict(leaf.counts),
        "examples": list(leaf.examples),
    }


def _structure_json(report: TypeStructure) -> dict[str, Any]:
    return {
        "data_type": report.data_type,
        "failing": report.failing,
        "known": report.known,
        "schemas": [
            {"signature": c.signature, "files": c.files, "example": c.example}
            for c in report.schemas
        ],
        "leaves": [_leaf_json(leaf) for leaf in report.leaves],
        "duplicate_ts_event": report.duplicate_ts_event,
    }


def _digest_json(digest: Digest) -> dict[str, Any]:
    return {"rows": digest.count, "digest": f"{digest.value:032x}"}


def _rehearsal_json(report: RehearsalReport) -> dict[str, Any]:
    return {
        "failing": report.failing,
        "leaves_failed": report.leaves_failed,
        "days_refused": report.days_refused,
        "types": [
            {
                "data_type": t.data_type,
                "verdict": t.verdict,
                "stages": {
                    name: {"files": stage.files, **_digest_json(stage.digest)}
                    for name, stage in zip(STAGES, t.stages, strict=True)
                },
            }
            for t in report.types
        ],
    }


def _parity_json(report: ParityReport) -> dict[str, Any]:
    return {
        "instrument_id": report.instrument_id,
        "data_type": report.data_type,
        "legs": {
            name: {**_digest_json(leg.digest), "missing_columns": list(leg.missing_columns)}
            for name, leg in report.legs.items()
        },
        READ_MISMATCH: report.read_mismatch,
        "mismatches": list(report.mismatches),
        BEYOND_MARGIN: report.beyond_margin,
    }


def _candles_json(report: CandleReport) -> dict[str, Any]:
    return {
        "instrument_id": report.instrument_id,
        "rows": report.rows,
        UNKNOWN_WIDTH: report.unknown_width,
        "widths": {
            str(w.bar_seconds): {"counts": dict(w.counts), "examples": list(w.examples)}
            for w in report.widths
        },
    }


def report_json(report: CatalogDayReport) -> dict[str, Any]:
    """Return the report as JSON-ready data, every section's counts and digests included."""
    return {
        "passed": report.passed,
        "failing": report.failing,
        "venue": report.venue,
        "day": report.day,
        "catalog": report.catalog,
        "candle_store": report.candle_store,
        "read_margin_ns": READ_MARGIN_NS,
        "instruments": list(report.instruments),
        "structure": [_structure_json(t) for t in report.structure],
        "rehearsal": _rehearsal_json(report.rehearsal),
        "parity": [_parity_json(p) for p in report.parity],
        "candles": [_candles_json(c) for c in report.candles],
        "float_noise": sum(c.float_noise for c in report.candles),
    }


def _verdict(failing: int) -> str:
    return "PASS" if failing == 0 else f"FAIL ({failing} failing)"


def _structure_lines(report: TypeStructure) -> list[str]:
    unknown = "" if report.known else f" {UNKNOWN_TYPE}=1 (no Nautilus class)"
    lines = [
        f"  {report.data_type}: {_verdict(report.failing)}{unknown}",
        f"    {SCHEMAS}={len(report.schemas)}",
    ]
    for c in report.schemas:
        lines.append(f"      {c.files} files, e.g. {c.example}: {c.signature}")
    if report.duplicate_ts_event is not None:
        lines.append(f"    {DUPLICATE_TS_EVENT}={report.duplicate_ts_event}")
    for leaf in report.leaves:
        lines.append(
            f"    {leaf.instrument_id}: {leaf.files} files, {leaf.rows} rows: "
            f"{_pairs(leaf.counts, LEAF_CLASSES)}"
        )
        lines += [f"      failing {example}" for example in leaf.examples]
    return lines


def _rehearsal_lines(report: RehearsalReport) -> list[str]:
    lines = [
        f"rehearsal (a scratch copy: {' -> '.join(STAGES)}): {_verdict(report.failing)}",
        f"  archive runs: leaves_failed={report.leaves_failed} days_refused={report.days_refused}",
    ]
    for t in report.types:
        stages = " -> ".join(
            f"{name} {stage.files} files {stage.digest.text()}"
            for name, stage in zip(STAGES, t.stages, strict=True)
        )
        lines.append(f"  {t.data_type}: {t.verdict}: {stages}")
    return lines


def _parity_lines(report: ParityReport) -> list[str]:
    legs = "; ".join(f"{name} {report.legs[name].digest.text()}" for name in LEGS)
    verdict = "agree" if not report.mismatches else f"differ: {', '.join(report.mismatches)}"
    return [
        f"  {report.instrument_id} {report.data_type}: {verdict}; "
        f"{BEYOND_MARGIN}={report.beyond_margin}",
        f"    {legs}",
    ]


def _candle_lines(report: CandleReport) -> list[str]:
    lines = [
        f"  {report.instrument_id}: {_verdict(report.failing)} ({report.rows} received rows "
        f"with ts_event in the day; {UNKNOWN_WIDTH}={report.unknown_width})"
    ]
    for width in report.widths:
        lines.append(f"    {width.bar_seconds:>6} s: {_pairs(width.counts, CANDLE_CLASSES)}")
        lines += [f"      {example}" for example in width.examples]
    return lines


_FAILING = (
    f"failing: structure {', '.join(LEAF_CLASSES)}, {UNKNOWN_TYPE}, {SCHEMAS} > 1, "
    f"{DUPLICATE_TS_EVENT}; rehearsal different, leaves_failed, days_refused; parity "
    f"{READ_MISMATCH}, {BEYOND_MARGIN}; candles {', '.join(sorted(FAILING_CANDLES))}, "
    f"{UNKNOWN_WIDTH}"
)


def render_text(report: CatalogDayReport) -> str:
    """Render the report: structure, the rehearsal, parity, candles, then any DEVIATION."""
    noise = sum(c.float_noise for c in report.candles)
    margin_s = READ_MARGIN_NS // 10**9
    lines = [
        f"catalog {report.venue} {report.day}: {_verdict(report.failing)}",
        f"catalog: {report.catalog}; candle store: {report.candle_store}",
        f"day files: name span or stamps meeting [D - {margin_s} s, D + 1 + {margin_s} s); "
        f"instruments: {', '.join(report.instruments)}",
        _FAILING,
        "",
        "structure:",
    ]
    for t in report.structure:
        lines += _structure_lines(t)
    lines += ["", *_rehearsal_lines(report.rehearsal), ""]
    lines.append(
        "parity (ts_init in the day; stored = raw pyarrow, query = 24 hourly catalog.query, "
        "received = a BacktestNode actor):"
    )
    for p in report.parity:
        lines += _parity_lines(p)
    lines += ["", "candles (the store against the reference fold of the received rows):"]
    for c in report.candles:
        lines += _candle_lines(c)
    if noise:
        lines.append(
            f"DEVIATION {FLOAT_NOISE}: {noise} buckets equal at the instrument's places but not "
            "float-equal (candles/domain/fold.py Known limit: the store folds decoded floats)"
        )
    return "\n".join(lines)
