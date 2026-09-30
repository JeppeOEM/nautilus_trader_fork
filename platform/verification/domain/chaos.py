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
Fault injection's rules (Story 31.10), pure: the scenarios, their timing, the scenario log's line
format, the window each scenario is judged over and the expected-outcome table it is judged by.

The reference side never imports the code it checks: the coverage reasons and the collector's
ledger sites below are restated from their published formats (`docs/DATA_DICTIONARY.md` §1.11,
§1.16), and each table row's reasoning names the capture code it was derived from, so a row can
be re-derived by reading that code -- never fitted to an observation.

Invariants:
- a scenario log line is parsed exactly or refused (`MalformedLine`, naming file:line); a `start`
  is followed by exactly its own `end` before the next `start`, so an interrupted run stays open
  and blocks the next one (`refusal`);
- two scenarios' judged windows never overlap: `SCENARIO_SPACING_NS` exceeds the window's margins
  (`WINDOW_BEFORE_NS + WINDOW_AFTER_NS`), and a run is refused sooner than that after the last end;
- `evaluate` reports a mismatch for every non-zero failing count, every required reason, site or
  trade count missing and every forbidden reason present: it never passes a scenario whose
  conservation failed.
"""

import json
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from verification.domain.conservation import NS_PER_MS
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import MalformedLine
from verification.domain.subscriptions import BYBIT
from verification.domain.subscriptions import HYPERLIQUID
from verification.domain.subscriptions import VENUES


NS_PER_MIN = 60 * NS_PER_S

SIGKILL_FLUSH = "sigkill_flush"
GRACEFUL_RESTART = "graceful_restart"
PAUSE_15S = "pause_15s"
PAUSE_45S = "pause_45s"
NETWORK_CUT = "network_cut"
CATALOG_READONLY = "catalog_readonly"
REDIS_STOP = "redis_stop"
# The collector image redeploy a story makes on the verify stack: logged like a scenario so its
# window is excluded from a clean soak (Story 31.11), never judged.
DEPLOY = "deploy"

SCENARIOS = (
    SIGKILL_FLUSH,
    GRACEFUL_RESTART,
    PAUSE_15S,
    PAUSE_45S,
    NETWORK_CUT,
    CATALOG_READONLY,
    REDIS_STOP,
)
RUNNABLE = (*SCENARIOS, DEPLOY)
# What faults every venue at once: judged for (redis_stop) or excluded from (deploy) both.
ALL_VENUE_SCENARIOS = frozenset({REDIS_STOP, DEPLOY})

# The judged window around a fault: 90 s before (the seconds just before the fault must still be
# clean) and 180 s after its undo -- the collector's next flush (60 s), a backfill's settle and
# fetch, and the flush that writes what it fetched, with room. The windowed conservation then
# still waits `WINDOW_SETTLE_NS` after the window's end.
WINDOW_BEFORE_NS = 90 * NS_PER_S
WINDOW_AFTER_NS = 180 * NS_PER_S
# Six minutes from one scenario's end to the next one's start: more than the 270 s of margins, so
# no two judged windows overlap and no fault leaks into another scenario's window.
SCENARIO_SPACING_NS = 6 * NS_PER_MIN

# Capture flushes 2 s past each minute (`capture_service._FLUSH_PHASE_S`, restated): `sigkill_flush`
# kills 0.25 s into that flush, while its batches are being written.
KILL_SECOND = 2
KILL_OFFSET_NS = 250 * NS_PER_MS
# `catalog_readonly` freezes the leaves from :55 to :10 of the next minute: exactly one :02 flush.
READONLY_SECOND = 55
READONLY_HOLD_NS = 15 * NS_PER_S
PAUSE_NS = MappingProxyType({PAUSE_15S: 15 * NS_PER_S, PAUSE_45S: 45 * NS_PER_S})
OUTAGE_NS = 60 * NS_PER_S  # network_cut, redis_stop

# The coverage record's second reasons (§1.16) and the collector's ledger sites (§1.11), restated.
RESTART = "restart"
WRITE_FAILED = "write_failed"
CATCH_UP_CAP = "catch_up_cap"
STALE = "stale"
RESTART_GAP_SITE = "collector.restart_gap"
TRADE_BACKFILL_SITE = "collector.trade_backfill"
STALE_TRADE_SITE = "collector.stale_trade"
SKIPPED_SECONDS_SITE = "collector.skipped_seconds"
FLUSH_WRITE_SITE = "collector.flush_write"
SNAPSHOT_PUBLISH_SITE = "collector.snapshot_publish"


def next_at(now_ns: int, second_of_minute: int, offset_ns: int = 0) -> int:
    """Return the first instant at or after `now_ns` that is `:SS + offset` of a UTC minute."""
    phase = second_of_minute * NS_PER_S + offset_ns
    minute = now_ns - now_ns % NS_PER_MIN
    candidate = minute + phase
    return candidate if candidate >= now_ns else candidate + NS_PER_MIN


def evaluation_window(fault_start_ns: int, fault_end_ns: int) -> tuple[int, int]:
    """
    Return the whole-second window `[start, end)` a scenario is judged over: from
    `WINDOW_BEFORE_NS` before its fault (floored) to `WINDOW_AFTER_NS` after its undo (ceiled).
    """
    start = fault_start_ns - WINDOW_BEFORE_NS
    end = fault_end_ns + WINDOW_AFTER_NS
    return start - start % NS_PER_S, -(-end // NS_PER_S) * NS_PER_S


# -- the scenario log ---------------------------------------------------------------------------

START = "start"
END = "end"
_LOG_KEYS = frozenset(
    {"event", "scenario", "venues", "target", "fault_start_ns", "fault_end_ns", "commands"}
)
_COMMAND_KEYS = frozenset({"argv", "returncode", "output"})


@dataclass(frozen=True)
class CommandRecord:
    """One command a run executed (a pseudo-argv for an in-process step) and its return code."""

    argv: tuple[str, ...]
    returncode: int
    output: str = ""

    def as_json(self) -> dict[str, Any]:
        return {"argv": list(self.argv), "returncode": self.returncode, "output": self.output}


@dataclass(frozen=True)
class ScenarioRun:
    """
    One run in the scenario log: its `start` line, and its `end` line's time and commands (None:
    still open -- the tool never wrote the end, so whether the fault was undone is unknown).
    """

    scenario: str
    venues: tuple[str, ...]
    target: str
    fault_start_ns: int
    fault_end_ns: int | None
    commands: tuple[CommandRecord, ...]

    def failed_commands(self) -> tuple[str, ...]:
        """Return every command that did not return 0, as `argv -> returncode`."""
        return tuple(
            f"{' '.join(c.argv)} -> {c.returncode}" for c in self.commands if c.returncode != 0
        )

    def log_line(self, event: str) -> dict[str, Any]:
        """Return this run as its `start` or `end` log line."""
        return {
            "event": event,
            "scenario": self.scenario,
            "venues": list(self.venues),
            "target": self.target,
            "fault_start_ns": self.fault_start_ns,
            "fault_end_ns": self.fault_end_ns if event == END else None,
            "commands": [c.as_json() for c in self.commands] if event == END else [],
        }


def _int(entry: Mapping[str, Any], key: str, where: str) -> int:
    value = entry[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedLine(f"{where}: `{key}` must be an integer, got {value!r}")
    return value


def _command(item: Any, where: str) -> CommandRecord:
    if not isinstance(item, dict) or set(item) != _COMMAND_KEYS:
        raise MalformedLine(f"{where}: a command must have exactly {sorted(_COMMAND_KEYS)}")
    argv = item["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise MalformedLine(f"{where}: a command's `argv` must be a non-empty list of strings")
    if not isinstance(item["output"], str):
        raise MalformedLine(f"{where}: a command's `output` must be a string")
    return CommandRecord(tuple(argv), _int(item, "returncode", where), item["output"])


def _venues(entry: Mapping[str, Any], where: str) -> tuple[str, ...]:
    venues = entry["venues"]
    if not isinstance(venues, list) or not venues or not set(venues) <= set(VENUES):
        raise MalformedLine(f"{where}: `venues` must be a non-empty list of {VENUES}")
    return tuple(venues)


def parse_log_line(text: str, where: str) -> tuple[str, ScenarioRun]:
    """Parse one scenario log line into its event and run; `MalformedLine` for anything else."""
    try:
        entry = json.loads(text)
    except ValueError as exc:
        raise MalformedLine(f"{where}: not JSON: {text[:200]!r}") from exc
    if not isinstance(entry, dict) or set(entry) != _LOG_KEYS:
        raise MalformedLine(f"{where}: a scenario line must have exactly {sorted(_LOG_KEYS)}")
    event, scenario, target = entry["event"], entry["scenario"], entry["target"]
    if event not in (START, END) or scenario not in RUNNABLE or not isinstance(target, str):
        raise MalformedLine(f"{where}: unknown event {event!r} or scenario {scenario!r}")
    commands = entry["commands"]
    if not isinstance(commands, list) or (event == START and commands):
        raise MalformedLine(f"{where}: `commands` must be a list, empty on a start line")
    end_ns = None if event == START else _int(entry, "fault_end_ns", where)
    if event == START and entry["fault_end_ns"] is not None:
        raise MalformedLine(f"{where}: a start line's `fault_end_ns` must be null")
    run = ScenarioRun(
        scenario,
        _venues(entry, where),
        target,
        _int(entry, "fault_start_ns", where),
        end_ns,
        tuple(_command(item, where) for item in commands),
    )
    return event, run


def _closes(open_run: ScenarioRun, end: ScenarioRun) -> bool:
    same = (open_run.scenario, open_run.venues, open_run.target, open_run.fault_start_ns)
    return same == (end.scenario, end.venues, end.target, end.fault_start_ns)


def parse_log(lines: Iterable[tuple[str, str]]) -> list[ScenarioRun]:
    """
    Pair the log's `(where, text)` lines into runs, oldest first; the last may be open. A `start`
    while one is open, or an `end` that does not close the open one, is `MalformedLine`.
    """
    runs: list[ScenarioRun] = []
    open_run: ScenarioRun | None = None
    for where, text in lines:
        event, run = parse_log_line(text, where)
        if event == START and open_run is not None:
            raise MalformedLine(f"{where}: a start while {open_run.scenario} is still open")
        if event == END and (open_run is None or not _closes(open_run, run)):
            raise MalformedLine(f"{where}: an end that closes no open start")
        open_run = run if event == START else None
        if event == END:
            runs.append(run)
    return [*runs, open_run] if open_run is not None else runs


def refusal(runs: list[ScenarioRun], now_ns: int) -> str | None:
    """
    Return why no new fault may start now (None: it may): the last run is still open, or its end
    is less than `SCENARIO_SPACING_NS` ago.
    """
    if not runs:
        return None
    last = runs[-1]
    if last.fault_end_ns is None:
        return (
            f"the last scenario ({last.scenario}, fault start {last.fault_start_ns}) is still "
            "open: a start without an end, so whether its fault was undone is unknown -- check "
            "the target by hand, then append its end line"
        )
    waited = now_ns - last.fault_end_ns
    if waited < SCENARIO_SPACING_NS:
        return (
            f"only {waited // NS_PER_S} s since {last.scenario} ended: scenarios are spaced "
            f"{SCENARIO_SPACING_NS // NS_PER_S} s so their judged windows never overlap"
        )
    return None


# -- the expected-outcome table ------------------------------------------------------------------


@dataclass(frozen=True)
class Expected:
    """
    One scenario's expected outcome on one venue: the second reasons its window must and must not
    show, the collector ledger sites it must show, and whether backfilled or unrecoverable trades
    must be more than 0.
    """

    required_reasons: frozenset[str] = frozenset()
    forbidden_reasons: frozenset[str] = frozenset()
    required_sites: frozenset[str] = frozenset()
    backfilled: bool = False
    unrecoverable: bool = False


# Each row's reasoning, from the capture code (`capture/application/capture_service.py` unless
# named) -- DATA_DICTIONARY §1.23 holds the same table:
# - sigkill_flush: SIGKILL loses the in-memory flush buffer (up to a minute of rows and trades)
#   and may cut a Parquet file mid-write (quarantined at the next start, `quarantine_corrupt`).
#   The next process reads its last archived second (`_prepare_ids`) and its first verdict notes
#   `restart` over the gap (`_note_restart_gaps`, ledgered `collector.restart_gap`); the archive
#   baseline it seeds makes each instrument's first message on a feed schedule a `restart:
#   archived baseline` backfill (D-61, `collector.trade_backfill`). No catalog write fails.
# - graceful_restart: `docker restart` sends SIGTERM, the final flush writes everything (`run`'s
#   `finally`), so only the downtime is lost: the same `restart` run and restart backfill.
# - pause_15s: the freezer stops every thread; at the thaw the socket's buffered frames are
#   received with a late `ts_init`, so trades of the pause's first seconds are older than
#   `stale_trade_seconds` (10 s) on arrival and dropped (`collector.stale_trade`, coverage
#   `trades_dropped`); each feed's arrival gap passes its silence bound (5 s Bybit, 12 s
#   Hyperliquid) and schedules a backfill. The 15 s stall is under the venue loop's 30 s
#   catch-up cap (`_MAX_CATCH_UP_SECONDS`), so no `catch_up_cap`; nothing restarts or fails to
#   write.
# - pause_45s: as pause_15s, but 45 s passes the 30 s cap: the older seconds are noted
#   `catch_up_cap` (`_note_skipped`, `collector.skipped_seconds`).
# - network_cut: no book delta arrives for 60 s, so the gate rejects the seconds `stale`
#   (`SecondSampler`, `stale_book_seconds`); the reconnect schedules a backfill. Bybit's linear
#   REST depth (1000 trades, D-60) covers the minute of BTCUSDT: trades are backfilled.
#   Hyperliquid's `recentTrades` returns the last 10 trades only (D-48): the rest is a `depth`
#   window, so unrecoverable trades are more than 0.
# - catalog_readonly: `write_data` into a leaf without write permission raises: the flush
#   ledgers `collector.flush_write`, re-notes the batch's seconds `write_failed`
#   (`_note_lost_rows`) and marks the trade batch's archive gap (`_mark_lost_trades`). Nothing
#   restarts.
# - redis_stop: only the live publish fails (`_publish`, `collector.snapshot_publish`); Parquet,
#   the loops and the process are unaffected, so no restart, write failure or skipped second.


def _restarted() -> Expected:
    return Expected(
        required_reasons=frozenset({RESTART}),
        forbidden_reasons=frozenset({WRITE_FAILED}),
        required_sites=frozenset({RESTART_GAP_SITE, TRADE_BACKFILL_SITE}),
    )


def _network_cut(backfilled: bool, unrecoverable: bool) -> Expected:
    return Expected(
        required_reasons=frozenset({STALE}),
        forbidden_reasons=frozenset({RESTART, WRITE_FAILED}),
        required_sites=frozenset({TRADE_BACKFILL_SITE}),
        backfilled=backfilled,
        unrecoverable=unrecoverable,
    )


_TABLE: Mapping[str, Expected] = MappingProxyType(
    {
        SIGKILL_FLUSH: _restarted(),
        GRACEFUL_RESTART: _restarted(),
        PAUSE_15S: Expected(
            forbidden_reasons=frozenset({RESTART, CATCH_UP_CAP, WRITE_FAILED}),
            required_sites=frozenset({STALE_TRADE_SITE, TRADE_BACKFILL_SITE}),
        ),
        PAUSE_45S: Expected(
            required_reasons=frozenset({CATCH_UP_CAP}),
            forbidden_reasons=frozenset({RESTART, WRITE_FAILED}),
            required_sites=frozenset({SKIPPED_SECONDS_SITE, STALE_TRADE_SITE, TRADE_BACKFILL_SITE}),
        ),
        CATALOG_READONLY: Expected(
            required_reasons=frozenset({WRITE_FAILED}),
            forbidden_reasons=frozenset({RESTART}),
            required_sites=frozenset({FLUSH_WRITE_SITE}),
        ),
        REDIS_STOP: Expected(
            forbidden_reasons=frozenset({RESTART, WRITE_FAILED, CATCH_UP_CAP}),
            required_sites=frozenset({SNAPSHOT_PUBLISH_SITE}),
        ),
    }
)
_PER_VENUE: Mapping[tuple[str, str], Expected] = MappingProxyType(
    {
        (NETWORK_CUT, BYBIT): _network_cut(backfilled=True, unrecoverable=False),
        (NETWORK_CUT, HYPERLIQUID): _network_cut(backfilled=False, unrecoverable=True),
    }
)


def expected(scenario: str, venue: str) -> Expected:
    """Return the table's row for a judged scenario on a venue (`KeyError` for `deploy`)."""
    row = _PER_VENUE.get((scenario, venue))
    return row if row is not None else _TABLE[scenario]


@dataclass(frozen=True)
class Observed:
    """
    What one scenario's window showed on one venue: the windowed conservation's failing counts
    (summed over the plan's instruments), whether its inputs were whole (coverage record present,
    no raw hour missing), the second reasons with their counts, the collector ledger sites seen,
    the backfilled and ledgered-unrecoverable trades, and the run's failed commands.
    """

    unexplained_trades: int
    unexplained_seconds: int
    archived_twice: int
    duplicate_rows: int
    row_and_reason: int
    inputs_whole: bool
    reasons: Mapping[str, int]
    sites: frozenset[str]
    backfilled: int
    unrecoverable: int
    failed_commands: tuple[str, ...] = ()


_FAILING_COUNTS = (
    "unexplained_trades",
    "unexplained_seconds",
    "archived_twice",
    "duplicate_rows",
    "row_and_reason",
)


def _count_mismatches(observed: Observed, row: Expected) -> list[str]:
    mismatches = [
        f"{name} = {getattr(observed, name)}" for name in _FAILING_COUNTS if getattr(observed, name)
    ]
    if row.backfilled and observed.backfilled <= 0:
        mismatches.append("no trade was backfilled (the row requires backfilled > 0)")
    if row.unrecoverable and observed.unrecoverable <= 0:
        mismatches.append("no trade was ledgered unrecoverable (the row requires > 0)")
    return mismatches


def evaluate(observed: Observed, row: Expected) -> tuple[str, ...]:
    """Return every way the window differs from its row or fails conservation (empty: a pass)."""
    mismatches = _count_mismatches(observed, row)
    if not observed.inputs_whole:
        mismatches.append("inputs incomplete: coverage record absent or a raw hour missing")
    present = {reason for reason, count in observed.reasons.items() if count}
    mismatches += [f"required reason {r} absent" for r in sorted(row.required_reasons - present)]
    mismatches += [f"forbidden reason {r} present" for r in sorted(row.forbidden_reasons & present)]
    missing_sites = sorted(row.required_sites - observed.sites)
    mismatches += [f"required ledger site {site} absent" for site in missing_sites]
    mismatches += [f"command failed: {command}" for command in observed.failed_commands]
    return tuple(mismatches)
