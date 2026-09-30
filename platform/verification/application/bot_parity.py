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
The bot parity check (Story 31.9): every bot of one venue with a live signal log, its latest live
segment against its replay's, judged by `verification.domain.bot_parity` over the catalog's stored
snapshot rows (read raw and decoded by the oracle's own book decoder,
`verification.infrastructure.snapshot_book`) and the coverage record's reasons. Each source is a
`Protocol` port wired only by the composition root (`verification.bot_parity`).

A bot is the venue's when its log's `start` record's instrument id is (`kernel.venues.has_venue`),
read leniently first so another venue's malformed log never refuses this venue's run; only the
venue's logs are then parsed strictly. A venue bot without a replay log, a bot logging deeper
than the stored rows hold (its top-N could never equal one), a log whose venue cannot be told, or
no venue bot at all, is refused (`ParityRefused`): a comparison of nothing must never read as a
pass.

Known limit (memory, MEM-01): both logs' latest segments are held whole (about 1 KB per cycle on
disk, a few hundred bytes parsed: some 30 MB per bot-hour pair of sides); the stored rows are read
one hour at a time and at most three hours are held (`HourRows`). Upgrade path: stream both logs in
`ts_ns` order and judge each cycle with a bounded look-back window of both sides.
"""

import json
import math
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from typing import Protocol

from kernel.venues import has_venue

from verification.domain.bot_parity import CLASSES
from verification.domain.bot_parity import MATCH_AFTER_NS
from verification.domain.bot_parity import SIGNALS
from verification.domain.bot_parity import UNEXPLAINED
from verification.domain.bot_parity import BotReport
from verification.domain.bot_parity import GapReasons
from verification.domain.bot_parity import Segment
from verification.domain.bot_parity import compare_bot
from verification.domain.bot_parity import latest_segment
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import SECONDS_PER_HOUR
from verification.domain.conservation import CoverageEntry
from verification.domain.conservation import SecondsRun
from verification.domain.reference_book import StoredBook


class ParityRefused(ValueError):
    """A run that cannot give a verdict: no bot of the venue, or a bot without its replay."""


class SignalLogs(Protocol):
    """A directory of signal logs: the bot ids it holds and each log's `(file:line, text)`."""

    @property
    def root(self) -> str: ...

    def names(self) -> list[str]: ...

    def lines(self, name: str) -> Iterator[tuple[str, str]]: ...


class HourBooks(Protocol):
    """One instrument-window of stored books: how many rows, and one hour's by floor second."""

    @property
    def count(self) -> int: ...

    def hour(self, hour: int) -> dict[int, list[StoredBook]]: ...


class BookRows(Protocol):
    """
    The catalog, read raw and decoded by the oracle's own book decoder: an instrument's snapshot
    rows with `ts_event` in a window.
    """

    def rows(self, instrument_id: str, start_ns: int, end_ns: int) -> HourBooks: ...


class SecondsCoverage(Protocol):
    """Capture's coverage record of the venue."""

    @property
    def path(self) -> str: ...

    def entries(self) -> Iterator[CoverageEntry]: ...


@dataclass(frozen=True)
class ParityInputs:
    """The read-only sources one run judges."""

    live: SignalLogs
    replay: SignalLogs
    catalog: BookRows
    coverage: SecondsCoverage


@dataclass(frozen=True)
class ParityReport:
    """One venue's run: every bot's report; passes only when nothing is unexplained."""

    venue: str
    live_dir: str
    replay_dir: str
    coverage_file: str
    bots: tuple[BotReport, ...]

    @property
    def unexplained(self) -> int:
        return sum(bot.unexplained for bot in self.bots)

    @property
    def passed(self) -> bool:
        return self.unexplained == 0


class HourRows:
    """
    One instrument's stored books by floor second, read one hour at a time. Invariant: at most the
    three hours around the latest one asked for are held (cycles are judged in time order, so the
    look-back never reaches further), each read bounded to its hour (MEM-01).
    """

    def __init__(self, catalog: BookRows, instrument_id: str) -> None:
        self._catalog = catalog
        self._instrument_id = instrument_id
        self._hours: dict[int, dict[int, tuple[StoredBook, ...]]] = {}

    def __call__(self, second: int) -> Sequence[StoredBook]:
        hour = second // SECONDS_PER_HOUR
        if hour not in self._hours:
            self._load(hour)
        return self._hours[hour].get(second, ())

    def depth(self, second: int) -> int:
        """Return the most levels a side of any stored row of `second`'s hour holds (0: none)."""
        hour = second // SECONDS_PER_HOUR
        self(second)
        return max(
            (
                max(len(row.book.bids), len(row.book.asks))
                for rows in self._hours[hour].values()
                for row in rows
            ),
            default=0,
        )

    def _load(self, hour: int) -> None:
        for held in [h for h in self._hours if abs(h - hour) > 1]:
            del self._hours[held]
        start_ns = hour * SECONDS_PER_HOUR * NS_PER_S
        books = self._catalog.rows(
            self._instrument_id, start_ns, start_ns + SECONDS_PER_HOUR * NS_PER_S
        )
        self._hours[hour] = {second: tuple(rows) for second, rows in books.hour(hour).items()}


def gap_reasons(coverage: SecondsCoverage, instrument_id: str) -> GapReasons:
    """Return the coverage record's seconds-without-a-row runs of one instrument."""
    runs = [
        entry
        for entry in coverage.entries()
        if isinstance(entry, SecondsRun) and entry.instrument_id == instrument_id
    ]
    return GapReasons(runs)


# How far past a bot's window the catalog is probed for a row (`require_flushed`): a collector
# flushes about once a minute, so a catalog flushed past the window holds a row this close after it.
FLUSH_PROBE_NS = 60 * NS_PER_S


def require_flushed(catalog: BookRows, live: Segment, replay: Segment) -> None:
    """
    Refuse a window the catalog has not been flushed past: without a stored row in
    `(end + 1 s, end + 61 s]`, the window's last seconds may be rows not written *yet* (a fleet
    still running), which would read as reasonless gaps. Rerun once the collector has flushed.
    """
    end_ns = min(live.last_ns, replay.last_ns)
    probe_ns = end_ns + MATCH_AFTER_NS
    if not catalog.rows(live.instrument_id, probe_ns, probe_ns + FLUSH_PROBE_NS).count:
        raise ParityRefused(
            f"{live.bot_id}: the catalog holds no {live.instrument_id} row in the minute after "
            f"the window's end ({end_ns}): not flushed past it yet (or a capture gap there)"
        )


def require_depth(stored: HourRows, live: Segment) -> None:
    """
    Refuse a bot whose logged depth, `max(ofi_levels, obi_levels)`, exceeds the stored snapshot's
    (the most levels a side holds in the window's first and last hour): its fed top-N could never
    equal a stored row's, and every book difference would read as `book_source`.
    """
    logged = max(live.param("ofi_levels"), live.param("obi_levels"))
    stored_depth = max(
        stored.depth(live.start_ns // NS_PER_S), stored.depth(live.last_ns // NS_PER_S)
    )
    if logged > stored_depth:
        raise ParityRefused(
            f"{live.bot_id}: logs {logged} levels a side, the stored rows of its window hold at "
            f"most {stored_depth}: its fed book can never equal a stored one"
        )


def _venue_of_log(logs: SignalLogs, name: str) -> str | None:
    """
    Return the instrument id of a log's last `start` record, read leniently (a line that is not
    one is passed over here), so the venue filter never parses another venue's log strictly.
    """
    instrument_id = None
    for _, text in logs.lines(name):
        if '"start"' not in text:
            continue
        try:
            record = json.loads(text)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("kind") == "start":
            found = record.get("instrument_id")
            instrument_id = found if isinstance(found, str) else instrument_id
    return instrument_id


def _venue_names(inputs: ParityInputs, venue: str) -> list[str]:
    """
    Return the live logs of `venue`'s bots; a log whose venue cannot be told (no readable `start`
    record) is refused, never passed over.
    """
    names = []
    for name in inputs.live.names():
        instrument_id = _venue_of_log(inputs.live, name)
        if instrument_id is None:
            raise ParityRefused(f"{name}: no readable start record in {inputs.live.root}")
        if has_venue(instrument_id, venue):
            names.append(name)
    return names


def _compare(inputs: ParityInputs, name: str) -> BotReport:
    live = latest_segment(inputs.live.lines(name))
    if name not in set(inputs.replay.names()):
        raise ParityRefused(f"{name}: no replay log in {inputs.replay.root}")
    replay = latest_segment(inputs.replay.lines(name))
    require_flushed(inputs.catalog, live, replay)
    stored = HourRows(inputs.catalog, live.instrument_id)
    require_depth(stored, live)
    return compare_bot(live, replay, stored, gap_reasons(inputs.coverage, live.instrument_id))


def check(venue: str, inputs: ParityInputs) -> ParityReport:
    """
    Compare every live bot of `venue` with its replay; `ParityRefused` for no bot of the venue, a
    bot without its replay log or deeper than the stored rows (malformed logs and mismatched
    segments raise their `ValueError`). Only this venue's logs are parsed strictly.
    """
    bots = [_compare(inputs, name) for name in _venue_names(inputs, venue)]
    if not bots:
        raise ParityRefused(f"no signal log of a {venue} bot in {inputs.live.root}")
    return ParityReport(
        venue, inputs.live.root, inputs.replay.root, inputs.coverage.path, tuple(bots)
    )


# --- output --------------------------------------------------------------------------------------


def _finite(value: float | None) -> float | None:
    return value if value is None or math.isfinite(value) else None


def _counts(counter: Mapping[str, int]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _bot_json(bot: BotReport) -> dict[str, Any]:
    return {
        "bot_id": bot.bot_id,
        "instrument_id": bot.instrument_id,
        "start_ns": bot.start_ns,
        "end_ns": bot.end_ns,
        "unexplained": bot.unexplained,
        "truncated": bot.truncated,
        "replay_input": _counts(bot.replay_input),
        "paired": {"book": bot.paired_book, "bar": bot.paired_bar},
        "skipped_both": bot.skipped_both,
        "beyond_window": {"live": bot.live_beyond, "replay": bot.replay_beyond},
        "signals": {
            name: {
                "paired": stats.paired,
                "equal": stats.equal,
                "equal_share": stats.equal_share,
                "max_abs_diff": _finite(stats.max_abs_diff),
                "classes": _counts(stats.classes),
            }
            for name, stats in bot.signals.items()
        },
        "decisions": {"disagreements": sum(bot.decisions.values()), **_counts(bot.decisions)},
        "action_disagreements": bot.actions,
        "cycle_classes": _counts(bot.cycle_classes),
        "live_only": _counts(bot.live_only),
        "replay_only": _counts(bot.replay_only),
        "gap_reasons": _counts(bot.gap_reasons),
    }


def report_json(report: ParityReport) -> dict[str, Any]:
    """Return the report as JSON-ready data, every bot and signal included."""
    return {
        "passed": report.passed,
        "venue": report.venue,
        "live_dir": report.live_dir,
        "replay_dir": report.replay_dir,
        "coverage_file": report.coverage_file,
        "unexplained": report.unexplained,
        "classes": list(CLASSES),
        "bots": [_bot_json(bot) for bot in report.bots],
    }


def _pairs(counter: Mapping[str, int]) -> str:
    return " ".join(f"{key}={counter[key]}" for key in sorted(counter)) or "none"


def _signal_line(bot: BotReport, name: str) -> str:
    stats = bot.signals[name]
    share = "n/a" if stats.equal_share is None else f"{stats.equal_share:.2%}"
    diff = "n/a" if stats.max_abs_diff is None else f"{stats.max_abs_diff:.6g}"
    return (
        f"    {name:<10} paired {stats.paired:>6}  equal {share:>8}  max|diff| {diff:>12}  "
        f"{_pairs(stats.classes)}"
    )


def _bot_lines(bot: BotReport) -> list[str]:
    verdict = "PASS" if bot.unexplained == 0 else f"FAIL ({bot.unexplained} {UNEXPLAINED})"
    return [
        f"  {bot.bot_id} {bot.instrument_id}: {verdict}",
        f"    window ns [{bot.start_ns}, {bot.end_ns}]; paired book {bot.paired_book}, "
        f"bar {bot.paired_bar}; skipped both {bot.skipped_both}; beyond window live "
        f"{bot.live_beyond}, replay {bot.replay_beyond}",
        *(_signal_line(bot, name) for name in SIGNALS),
        f"    decisions  {sum(bot.decisions.values())} disagreements: {_pairs(bot.decisions)}",
        f"    actions    {bot.actions} disagreements (informational: fills differ by construction)",
        f"    cycles     {_pairs(bot.cycle_classes)}",
        f"    live_only  {_pairs(bot.live_only)}",
        f"    replay_only {_pairs(bot.replay_only)}",
        f"    gap reasons {_pairs(bot.gap_reasons)}",
        f"    replay_input {_pairs(bot.replay_input)} (replay cycles not fed the catalog's row)",
        f"    truncated  {bot.truncated} (cycles past a side stopping over 1 s early)",
    ]


def render_text(report: ParityReport) -> str:
    """Render the report: the inputs, then per bot the pairs, signals, decisions and classes."""
    lines = [
        f"bot_parity {report.venue}: {'PASS' if report.passed else 'FAIL'} "
        f"({report.unexplained} {UNEXPLAINED})",
        f"live {report.live_dir}; replay {report.replay_dir}; coverage {report.coverage_file}",
        f"classes, first match wins: {', '.join(CLASSES)}",
        "",
    ]
    for bot in report.bots:
        lines += _bot_lines(bot)
    return "\n".join(lines)


def dumps(report: ParityReport) -> str:
    """Return the `--json` output (NaN never appears: a non-finite difference is null)."""
    return json.dumps(report_json(report), indent=2, allow_nan=False)
