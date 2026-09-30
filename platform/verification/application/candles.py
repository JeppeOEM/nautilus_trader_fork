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
The candles tool's ports and orchestration (Story 31.8): one venue's closed UTC day, one plan
instrument at a time, every one of the ten chart widths (`verification.domain.candle_check` holds
the rules). The reference side never imports the code it checks: the catalog's rows are read raw
(pyarrow), the candle store read-only (`sqlite3`), the served bars over HTTP -- each through a
`Protocol` port wired only by the composition root (`verification.candles`).

Per instrument: the day's snapshot rows (`ts_event` in D); the reference trades of each hour
(venue time in it, every copy merged by id -- the trades tool's `channel_trades` read of hours
H-1..H+1), folded per observed second at that row's precisions, the rest counted by coverage
reason; the recorder gaps of the instrument's WS trade channels (`channel_gaps`), and the coverage
trade windows and archive-gap marker spans that explain a differing second.

Known limit (memory, MEM-01): one instrument-day of snapshot rows as `TradeRow`s plus two decoded
`RefBook` tuples (the rows' and the masked reference's) and one hour of the reference's merged
trades (the busiest Bybit linear hour holds ~150k); per-second reference folds are kept as eight
integers each. The 1W bucket is folded one day at a time (`merge_candles`). Measured on a synthetic
full day (86,400 rows, every second traded, nine widths judged, no I/O): 6.0 s and 259 MB peak RSS
for the process. Upgrade path: fold the rows per hour into running bucket accumulators instead of
materialising the books.

Known limit (runtime): every width folds both books over the whole day (nine widths x two
folds), the stored widths once more inside `judge_width`, and a closed week re-reads its six
other days' rows; every raw trade file is decoded up to three times (as its own hour and each
neighbour's). The served check pages each width from the local data_api (at most
`SERVED_PAGE_LIMIT` bars a page; 1m needs three pages a day). Measured on a real day (the verify
soak's 2026-09-29, ~27,750 rows per instrument, raw decode and served paging included): Bybit's four
instruments 300 s and 519 MiB peak RSS, almost all of it the raw decode (291 s without the served
checks); Hyperliquid 5 s and 194 MiB (`docs/VERIFICATION_REPORT.md`). Upgrade path: one decode per
raw file shared by every instrument of its channel, as conservation's.

Known limit (1W): the week's bar is proven against the catalog fold of its seven days only; its
reference is proven transitively, through each day's 1d reference verdict. Upgrade path: a `--week`
mode folding the week's masked reference.

Known limit (plan): the instruments are the venue config's current plan, as in the other tools.
Upgrade path: the recorder's `plan_changed` lines trim each instrument to the windows it was
planned.

Known limit (a live data_api): the served bars are read from a running data_api whose store the
nightly may rebuild while the tool runs; a bar rebuilt mid-run is judged as served at that moment
(a loud difference, never a silent pass). Upgrade path: read the served bars after the nightly's
`candles.rebuild` step, as 31.11's `verify_day` will.
"""

from collections import Counter
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any
from typing import Protocol

from verification.application.conservation import DAY_SETTLE_NS
from verification.application.conservation import InstrumentCoverage
from verification.application.conservation import ReferenceRecords
from verification.application.conservation import channel_trades
from verification.application.conservation import collect_coverage
from verification.application.conservation import day_hours
from verification.application.conservation import day_start_ns
from verification.application.conservation import missing_raw
from verification.application.trades import TradeCoverage
from verification.application.trades import channel_gaps
from verification.domain.candle_check import BUCKET_KINDS
from verification.domain.candle_check import DAY_BAR_SECONDS
from verification.domain.candle_check import FAILING_REFERENCE
from verification.domain.candle_check import FAILING_SERVED
from verification.domain.candle_check import NS_PER_DAY
from verification.domain.candle_check import REFERENCE_CLASSES
from verification.domain.candle_check import SERVED_CLASSES
from verification.domain.candle_check import SERVED_PAGE_LIMIT
from verification.domain.candle_check import WEEK_CLASSES
from verification.domain.candle_check import CandlesDayReport
from verification.domain.candle_check import Causes
from verification.domain.candle_check import InstrumentCandles
from verification.domain.candle_check import PreparedDay
from verification.domain.candle_check import ServedBar
from verification.domain.candle_check import ServedPage
from verification.domain.candle_check import WeekFacts
from verification.domain.candle_check import WeekReport
from verification.domain.candle_check import WidthReport
from verification.domain.candle_check import fold_reference
from verification.domain.candle_check import judge_day_width
from verification.domain.candle_check import judge_week
from verification.domain.candle_check import merge_candles
from verification.domain.candle_check import prepare_day
from verification.domain.candle_check import week_start_ms
from verification.domain.catalog_check import CANDLE_CLASSES
from verification.domain.catalog_check import FAILING_CANDLES
from verification.domain.catalog_check import NS_PER_MS
from verification.domain.catalog_check import STORE_BAR_SECONDS
from verification.domain.catalog_check import UNKNOWN_WIDTH
from verification.domain.catalog_check import StoredBar
from verification.domain.catalog_check import TradeRow
from verification.domain.conservation import NS_PER_HOUR
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import SECONDS_PER_DAY
from verification.domain.conservation import Intervals
from verification.domain.conservation import ReferenceTrade
from verification.domain.conservation import TradeChannel
from verification.domain.conservation import instrument_category
from verification.domain.conservation import reasons
from verification.domain.conservation import tally_seconds
from verification.domain.conservation import trade_channels
from verification.domain.conservation import wire_index
from verification.domain.plan_file import RecordingPlan
from verification.domain.reference_signals import WEEK_SECONDS
from verification.domain.reference_signals import RefCandle
from verification.domain.reference_signals import fold_candles
from verification.domain.trade_check import TradeColumns
from verification.domain.trade_check import merge_reference


class CatalogRows(Protocol):
    """The catalog, read raw: an instrument's snapshot rows with `ts_event` in a window, in order."""

    def trade_rows(self, instrument_id: str, start_ns: int, end_ns: int) -> Sequence[TradeRow]: ...


class StoreBars(Protocol):
    """The candle store, read-only: an instrument's bars with `t` in the day, and unknown widths."""

    @property
    def path(self) -> str: ...

    def bars(self, instrument_id: str, day_start_ns: int) -> tuple[list[StoredBar], int]: ...


class ServedSource(Protocol):
    """
    The data_api's `GET /api/candles`: one page before a cursor, or every bar of a window paged
    back from its end. Invariant: a page that cannot be read raises; it never comes back partial.
    """

    @property
    def base_url(self) -> str: ...

    def page(
        self, instrument_id: str, before_ns: int, limit: int, bar_seconds: int
    ) -> ServedPage: ...

    def day(
        self, instrument_id: str, bar_seconds: int, start_ns: int, end_ns: int
    ) -> Mapping[int, ServedBar]: ...


@dataclass(frozen=True)
class CandleInputs:
    """The read-only sources one run judges; `served` None skips the served checks."""

    reference: ReferenceRecords
    catalog: CatalogRows
    store: StoreBars
    coverage: TradeCoverage
    served: ServedSource | None


@dataclass(frozen=True)
class _Run:
    """What every instrument of one run shares."""

    venue: str
    start_ns: int
    hours: range
    channels: tuple[TradeChannel, ...]
    index: Mapping[tuple[str, str], str]
    gaps: Mapping[str, Intervals]
    inputs: CandleInputs
    now_ns: int


# --- the reference --------------------------------------------------------------------------------


def _hour_trades(
    instrument_id: str, channels: Iterable[TradeChannel], hour: int, run: _Run
) -> Iterator[ReferenceTrade]:
    for channel in channels:
        for trade in channel_trades(run.venue, channel, hour, run.inputs.reference, run.index):
            if trade.instrument_id == instrument_id and trade.ts_ns // NS_PER_HOUR == hour:
                yield trade


def _instrument_channels(instrument_id: str, run: _Run) -> tuple[TradeChannel, ...]:
    category = instrument_category(run.venue, instrument_id)
    return tuple(c for c in run.channels if c.category == category)


def _reference(
    instrument_id: str, rows: Mapping[int, TradeRow], reason_of: Mapping[int, str], run: _Run
) -> tuple[dict[int, TradeColumns | None], Counter[str]]:
    """Fold the day's reference hour by hour (MEM-01: one hour of trades in memory)."""
    channels = _instrument_channels(instrument_id, run)
    folded: dict[int, TradeColumns | None] = {}
    unobserved: Counter[str] = Counter()
    for hour in run.hours:
        by_second: dict[int, list[ReferenceTrade]] = {}
        for merged in merge_reference(_hour_trades(instrument_id, channels, hour, run)).values():
            by_second.setdefault(merged.trade.ts_ns // NS_PER_S, []).append(merged.trade)
        hour_folded, hour_unobserved = fold_reference(by_second, rows, reason_of)
        folded.update(hour_folded)
        unobserved.update(hour_unobserved)
    return folded, unobserved


def _causes(instrument_id: str, coverage: InstrumentCoverage, run: _Run) -> Causes:
    channels = _instrument_channels(instrument_id, run)
    gaps = [run.gaps[c.name] for c in channels if c.name in run.gaps]
    recorder = Intervals.of(s for i in gaps for s in zip(i.starts, i.ends, strict=True))
    markers = run.inputs.coverage.gap_marker_spans(instrument_id)
    windows = (*coverage.windows, *markers)
    return Causes(recorder, Intervals.of((w.from_ns, w.to_ns) for w in windows))


# --- the week -------------------------------------------------------------------------------------


def _week_fold(
    instrument_id: str, week_start_ns: int, day_rows: Sequence[TradeRow], run: _Run
) -> tuple[RefCandle | None, tuple[int, int]]:
    """Fold the week's seven days of catalog rows, read and folded one day at a time (MEM-01)."""
    parts: list[RefCandle] = []
    price = size = 0
    for start in range(week_start_ns, week_start_ns + WEEK_SECONDS * NS_PER_S, NS_PER_DAY):
        rows = day_rows
        if start != run.start_ns:
            rows = run.inputs.catalog.trade_rows(instrument_id, start, start + NS_PER_DAY)
        parts += fold_candles([row.book() for row in rows], SECONDS_PER_DAY).values()
        price = max([price, *(row.price_precision for row in rows)])
        size = max([size, *(row.size_precision for row in rows)])
    week = merge_candles(week_start_ns // NS_PER_MS, parts) if parts else None
    return week, (price, size)


def _week_bar(served: ServedSource, instrument_id: str, before_ns: int, t: int) -> ServedBar | None:
    page = served.page(instrument_id, before_ns, SERVED_PAGE_LIMIT, WEEK_SECONDS)
    return next((bar for bar in page.bars if bar.t == t), None)


def _week(instrument_id: str, day_rows: Sequence[TradeRow], run: _Run) -> WeekReport:
    start_ms = week_start_ms(run.start_ns)
    start_ns = start_ms * NS_PER_MS
    end_ns = start_ns + WEEK_SECONDS * NS_PER_S
    served = run.inputs.served
    if end_ns + DAY_SETTLE_NS > run.now_ns:
        return judge_week(WeekFacts(start_ms, False, None, (0, 0), None, None, False, False))
    reference, places = _week_fold(instrument_id, start_ns, day_rows, run)
    # The chart-like page is a regression probe for audit D-118, not a second proof: the fixed
    # route rounds its cursor up to the next week's end and reads one whole week, so when the next
    # week has data this page holds that week only and the judged week is `omitted` (accepted); it
    # serves the judged week (whole) only when the next week is empty and the gap jump lands on
    # it. The pre-fix route read `[cursor - 7 days, cursor]`, folding the judged week from its
    # last six days, which this page reports `served_differs`. Not yet observed against the old
    # image (the soak's only week is still open); pinned by `verification/tests/test_candles.py`.
    chart_before = end_ns + NS_PER_DAY
    aligned = chart = None
    fetched = served is not None and chart_before <= run.now_ns
    if served is not None:
        aligned = _week_bar(served, instrument_id, end_ns, start_ms)
        chart = _week_bar(served, instrument_id, chart_before, start_ms) if fetched else None
    facts = WeekFacts(
        week_start=start_ms,
        closed=True,
        reference=reference,
        places=places,
        aligned=aligned,
        chart=chart,
        chart_fetched=fetched,
        served_checked=served is not None,
    )
    return judge_week(facts)


# --- one instrument -------------------------------------------------------------------------------


def _width(
    instrument_id: str, day: PreparedDay, bar_seconds: int, bars: Sequence[StoredBar], run: _Run
) -> WidthReport:
    stored = None
    if bar_seconds in STORE_BAR_SECONDS:
        stored = [bar for bar in bars if bar.bar_seconds == bar_seconds]
    served = run.inputs.served
    window = (run.start_ns, run.start_ns + NS_PER_DAY)
    found = None if served is None else served.day(instrument_id, bar_seconds, *window)
    return judge_day_width(day, bar_seconds, stored, found)


def _instrument(instrument_id: str, coverage: InstrumentCoverage, run: _Run) -> InstrumentCandles:
    start_s = run.start_ns // NS_PER_S
    rows = run.inputs.catalog.trade_rows(instrument_id, run.start_ns, run.start_ns + NS_PER_DAY)
    week = _week(instrument_id, rows, run)
    first: dict[int, TradeRow] = {}
    for row in rows:
        first.setdefault(row.ts_event // NS_PER_S, row)
    reason_of, _ = reasons(start_s, coverage.runs)
    folded, unobserved = _reference(instrument_id, first, reason_of, run)
    day = prepare_day(start_s, rows, folded, _causes(instrument_id, coverage, run))
    bars, unknown = run.inputs.store.bars(instrument_id, run.start_ns)
    seconds = tally_seconds(start_s, Counter(r.ts_event // NS_PER_S for r in rows), coverage.runs)
    return InstrumentCandles(
        instrument_id=instrument_id,
        rows=len(rows),
        seconds=seconds,
        trades_unobserved=MappingProxyType(dict(sorted(unobserved.items()))),
        widths=tuple(_width(instrument_id, day, w, bars, run) for w in DAY_BAR_SECONDS),
        unknown_width=unknown,
        week=week,
    )


def check_day(
    plan: RecordingPlan, day: date, inputs: CandleInputs, now_ns: int
) -> CandlesDayReport:
    """Judge every plan instrument's candles over one closed UTC day, on every width."""
    start_ns = day_start_ns(day)
    hours = day_hours(day)
    coverage = collect_coverage(inputs.coverage.entries(), plan.instruments, start_ns)
    channels = trade_channels(plan)
    gaps = {c.name: channel_gaps(c, hours, inputs.reference) for c in channels if not c.rest}
    run = _Run(plan.venue, start_ns, hours, channels, wire_index(plan), gaps, inputs, now_ns)
    instruments = tuple(_instrument(iid, coverage[iid], run) for iid in plan.instruments)
    return CandlesDayReport(
        venue=plan.venue,
        day=day.isoformat(),
        served_checked=inputs.served is not None,
        data_api=None if inputs.served is None else inputs.served.base_url,
        candle_store=inputs.store.path,
        coverage_file=inputs.coverage.path,
        missing_raw_files=missing_raw(channels, hours, inputs.reference),
        truncated_neighbour_files=inputs.reference.truncated_neighbours(),
        instruments=instruments,
    )


# --- the report -----------------------------------------------------------------------------------


def _width_json(width: WidthReport) -> dict[str, Any]:
    return {
        "failing": width.failing,
        "buckets": dict(width.buckets),
        "catalog": None if width.catalog is None else dict(width.catalog.counts),
        "served": None if width.served is None else dict(width.served),
        "reference": dict(width.reference),
        "examples": list(width.examples),
    }


def _week_json(week: WeekReport) -> dict[str, Any]:
    return {
        "failing": week.failing,
        "week_start": week.week_start,
        "status": week.status,
        "kind": week.kind,
        "served": None if week.served is None else dict(week.served),
        "examples": list(week.examples),
    }


def _instrument_json(report: InstrumentCandles) -> dict[str, Any]:
    seconds = report.seconds
    return {
        "instrument_id": report.instrument_id,
        "failing": report.failing,
        "rows": report.rows,
        "seconds": {
            "expected": seconds.expected,
            "rows": seconds.rows,
            "explained_by_reason": dict(seconds.explained_by_reason),
            "unexplained": seconds.unexplained,
            "examples_unexplained": list(seconds.examples_unexplained),
        },
        "trades_unobserved": dict(report.trades_unobserved),
        UNKNOWN_WIDTH: report.unknown_width,
        "widths": {str(w.bar_seconds): _width_json(w) for w in report.widths},
        "week": _week_json(report.week),
    }


def report_json(report: CandlesDayReport) -> dict[str, Any]:
    """Return the report as JSON-ready data: the verdict, every instrument's classes per width."""
    return {
        "verdict": report.verdict,
        "passed": report.passed,
        "provisional": report.provisional,
        "failing": report.failing,
        "venue": report.venue,
        "day": report.day,
        "served": "checked" if report.served_checked else "not checked",
        "data_api": report.data_api,
        "candle_store": report.candle_store,
        "coverage_file": report.coverage_file,
        "missing_raw_files": list(report.missing_raw_files),
        "truncated_neighbour_files": list(report.truncated_neighbour_files),
        "instruments": [_instrument_json(i) for i in report.instruments],
    }


def _pairs(values: Mapping[str, int], names: Iterable[str]) -> str:
    return " ".join(f"{name}={values[name]}" for name in names)


def _width_line(width: WidthReport) -> list[str]:
    parts = [f"buckets {_pairs(width.buckets, BUCKET_KINDS)}"]
    if width.catalog is not None:
        parts.append(f"catalog {_pairs(width.catalog.counts, CANDLE_CLASSES)}")
    served = "not checked" if width.served is None else _pairs(width.served, SERVED_CLASSES)
    parts += [f"served {served}", f"reference {_pairs(width.reference, REFERENCE_CLASSES)}"]
    lines = [f"  {width.bar_seconds:>6} s: " + "; ".join(parts)]
    return lines + [f"      {example}" for example in width.examples]


def _week_lines(week: WeekReport) -> list[str]:
    if week.served is None:
        detail = "served not checked" if week.status != "week_open" else "not closed: not judged"
        return [f"  1W t={week.week_start}: {week.status} {week.kind or ''} ({detail})"]
    lines = [
        f"  1W t={week.week_start}: {week.status} {week.kind}; {_pairs(week.served, WEEK_CLASSES)}"
    ]
    return lines + [f"      {example}" for example in week.examples]


def _instrument_text(report: InstrumentCandles) -> list[str]:
    seconds = report.seconds
    by_reason = ", ".join(f"{k}={v}" for k, v in seconds.explained_by_reason.items()) or "none"
    unobserved = ", ".join(f"{k}={v}" for k, v in report.trades_unobserved.items()) or "none"
    verdict = "PASS" if report.failing == 0 else f"FAIL ({report.failing} failing)"
    lines = [
        f"{report.instrument_id}: {verdict}",
        f"  seconds  expected={seconds.expected} rows={seconds.rows} "
        f"unexplained={seconds.unexplained}; explained by reason: {by_reason}",
        f"  trades_unobserved (reference trades in seconds without a row): {unobserved}",
        f"  {UNKNOWN_WIDTH}={report.unknown_width}",
    ]
    if seconds.examples_unexplained:
        lines.append(f"  unexplained seconds (epoch s): {list(seconds.examples_unexplained)}")
    for width in report.widths:
        lines += _width_line(width)
    return lines + _week_lines(report.week)


_FAILING = (
    f"failing: unexplained seconds, {UNKNOWN_WIDTH}, missing raw reference files; catalog "
    f"{', '.join(sorted(FAILING_CANDLES))}; served {', '.join(sorted(FAILING_SERVED))}; "
    f"reference {', '.join(sorted(FAILING_REFERENCE))}"
)


def _verdict_line(report: CandlesDayReport) -> str:
    if report.verdict == "FAIL":
        return f"FAIL ({report.failing} failing)"
    if report.verdict == "PROVISIONAL":
        return "PROVISIONAL (served: not checked -- never a full pass; rerun with the data_api)"
    return "PASS"


def render_text(report: CandlesDayReport) -> str:
    """Render the report: the verdict, the inputs, then one block per instrument."""
    served = report.data_api or "not checked"
    missing = ", ".join(report.missing_raw_files[:6]) or "none"
    lines = [
        f"candles {report.venue} {report.day}: {_verdict_line(report)}",
        f"candle store: {report.candle_store}; coverage record: {report.coverage_file}",
        f"served: {served}",
        f"raw reference files missing: {len(report.missing_raw_files)} ({missing})",
        f"neighbour files read truncated: {', '.join(report.truncated_neighbour_files) or 'none'}",
        _FAILING,
        "",
    ]
    for instrument in report.instruments:
        lines += _instrument_text(instrument)
    return "\n".join(lines)
