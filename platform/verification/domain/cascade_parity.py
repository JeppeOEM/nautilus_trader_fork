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
Liquidation cascade bot parity, the pure side (Story 33.14): one `liquidation_cascade` bot's live
signal log against its catalog replay (`bots.signal_replay`), cycle by cycle. The records are
`LiquidationCascadeStrategy`'s, read here as plain JSON: this module never imports `bots`,
`research`, `kernel.indicators` or `nautilus_trader` (DATA-02, `tests/test_boundaries.py`).
The dummy bots' parity is `verification.domain.bot_parity`, unchanged.

**Records.** A segment opens with a `start` record (`strategy: "liquidation_cascade"`, `bot_id`,
`instrument_id`, `ts_ns` and every config field); then one record per detector update: `kind`
(`tick`, the 1 s timer on whole UTC seconds, or `liquidation`, one fed row, with its
`venue_event_id`), `bot_id`, `instrument_id`, `ts_ns` (a liquidation's: the detector's clock; a
tick's: the timer's second), the floats
`rate_long`, `rate_short`, `baseline`, `intensity`, the exact `direction` (-1, 0, 1), `active`,
`spent`, `decision` (`enter_short`, `enter_long`, `exit`, `none`, `not_ready`) and `reason`. A
record of any other shape is refused (`MalformedRecord`), never skipped.

**Pairing.** The two `start` records must agree on everything but `ts_ns`, and the replay must start
at the live start: the detector's baseline remembers `baseline_s` of history, which a later replay
start never had (`SegmentMismatch`). `tick` records pair by `ts_ns`, `liquidation` records by
`venue_event_id` and its occurrence (Bybit's ids are synthesized `{T}:{S}:{v}:{p}` and may
legitimately repeat across frames, so the n-th record of an id pairs with the other side's n-th; a
repeated `tick` time is refused); the window is `(start, min(last live, last replay)]` (a
liquidation pair counts when either side's `ts_ns` is in it), and when one side's last record falls
over one timer interval short of the other's, the longer side's records past the window are
`truncated`, a failing count, as in the dummy comparison.

**Comparison.** The four floats are judged by `signal_compare.relative` (`REL_TOL`); `direction`,
`active`, `spent` and `decision` exactly, and a `liquidation` pair's `ts_ns` exactly too. A
differing `reason` with an equal decision is counted, informational (it names which rule fired).
Every differing field of a paired record gets one class:

1. `late_arrival`: the live bridge delivered a row after the detector's clock had passed its
   `ts_init` (the strategy then feeds it at the clock, `kernel.indicators.LiquidationCascade`),
   which the replay, streaming rows on `ts_init`, never does. A live `liquidation` record is late
   when an earlier record of the live file carries a later `ts_ns` than the row's `ts_init` -- read
   off the replay's record of the same `venue_event_id`, the replay feeding every row at its
   `ts_init`. With L the `ts_init` of the most recent late row at or before T, a difference at T is
   `late_arrival`:
   - for any field of the late row's own record pair (live wrote it at its clock, fed there or
     refused as `stale_row` when older than the window; the replay at its `ts_init`: two instants);
   - for any field of a late tick's own record pair: a live `tick` written after a record with a
     later `ts_ns` (a row received after the tick's second was fed before the timer's callback
     ran). The live tick holds that row, the replay's (ticks before rows of a later `ts_init`) does
     not; the exact baseline makes every later record equal again, so only that pair differs;
   - for any field, when T lies within L's window `[L, L + window_s + 1 s]` (the window rates hold
     a row `window_s`, plus the 1 s it may have been placed late);
   - past that window, for `baseline` (the exponential average: it remembers the row for good but
     forgets it at its own pace), when the difference is at most the bound `d0 * exp(-(T - t0) /
     baseline_s) * (1 + REL_TOL)`, `d0` being the baseline difference observed at the first paired
     record after the window ends, at `t0` (a tick, or a liquidation record if one comes first).
     Two runs fed equal rates after the window differ by exactly that decaying amount; the bias
     correction (a divisor growing toward 1) and the floor (1-Lipschitz) only shrink it. A larger
     difference is a defect an old late row must not mask;
   - past the window, for `intensity` (total rate / baseline: not an average, a rate burst
     multiplies a baseline difference, so it gets no bound of its own), when the record's rates are
     equal and its baseline differs as `late_arrival`;
   - for `direction`, `active` and `spent`, when a late row precedes T and every float of the record
     is equal or `late_arrival`: a threshold crossed earlier or later by that same drift;
   - for `decision`, when the record's indicator fields (the four floats, `direction`, `active`,
     `spent`) do not all agree and every one that differs is `late_arrival`.
2. `quote_cadence`: a `decision` difference on a record whose indicator fields all agree (the floats
   within `REL_TOL`). The replay is fed one quote per second, the top of each stored snapshot
   (`bots.signal_replay`), while the live bot gets every venue quote, so the OFI confirmation, the
   ATR, the stop and take-profit exits and the fills differ by construction, as the dummy
   comparison's `quote_cadence`. Explained, never failing.
3. `unexplained`: anything else, among it a difference with no late row before it; it fails the run.

A record's own class is `unexplained` if any of its fields is, else `late_arrival` if any is, else
`quote_cadence`. A record on one side only (`live_only`/`replay_only`, by kind) is failing: a
liquidation the bridge never delivered (published while it was down, `liquidation_data_client`'s
Known limit) or one the archive lost.

Known limit: `late_arrival` is inferred from file order, the order the live strategy wrote its
records, never from a delivery timestamp of its own (the live log keeps none), and a row is assumed
placed at most 1 s late. Upgrade path: log the row's `ts_init` beside the detector's clock on
`liquidation` records.

Known limit: the decaying bound is measured, not derived: `d0` is the live/replay difference at
`t0`, so a defect already present at `t0` is explained with it (and only while it decays at the
baseline's pace; one that persists or grows past the bound reads `unexplained`). Upgrade path:
recompute the late row's exact baseline displacement from the two logs' rates.

Known limit: `quote_cadence` explains any decision difference over agreeing indicators, so a
defect of the entry/exit rules themselves hides there. The rules are pure and unit-tested on their
own (`research/tests/test_cascade_rules.py`). Upgrade path: log the bid/ask and OFI the decision
read, and judge the rules on them.
"""

import bisect
import dataclasses
import json
import math
from collections import Counter
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from verification.domain.bot_parity import MalformedRecord
from verification.domain.bot_parity import SegmentMismatch
from verification.domain.conservation import NS_PER_S
from verification.domain.signal_compare import FAILING
from verification.domain.signal_compare import REL_TOL
from verification.domain.signal_compare import relative


STRATEGY = "liquidation_cascade"
START = "start"
TICK = "tick"
LIQUIDATION = "liquidation"
KINDS = (TICK, LIQUIDATION)

FLOATS = ("rate_long", "rate_short", "baseline", "intensity")
EXACT = ("direction", "active", "spent", "decision")
# A liquidation pair's own field compared exactly besides the record's values.
TS_NS = "ts_ns"
FIELDS = (*FLOATS, *EXACT, TS_NS)
_RATES = ("rate_long", "rate_short")
# The detector's exact outputs; with `FLOATS`, a record's indicator fields (the docstring).
_INDICATOR_EXACT = ("direction", "active", "spent")
_INDICATORS = (*FLOATS, *_INDICATOR_EXACT)
DECISIONS = frozenset({"enter_short", "enter_long", "exit", "none", "not_ready"})

LATE_ARRIVAL = "late_arrival"
QUOTE_CADENCE = "quote_cadence"
UNEXPLAINED = "unexplained"
CLASSES = (LATE_ARRIVAL, QUOTE_CADENCE, UNEXPLAINED)
TIMER_INTERVAL_NS = NS_PER_S

_COMMON_KEYS = frozenset({"kind", "bot_id", "instrument_id", "ts_ns", "reason", *FLOATS, *EXACT})
_KIND_KEYS = MappingProxyType({TICK: _COMMON_KEYS, LIQUIDATION: _COMMON_KEYS | {"venue_event_id"}})
_START_INTS = ("ts_ns", "window_s", "baseline_s")

# (kind, ts_ns or venue_event_id, occurrence of that event id in its segment; 0 for a tick)
Key = tuple[str, int | str, int]


@dataclass(frozen=True)
class CascadeCycle:
    """
    One detector-update record, with its position in its file (`index`) and, for a liquidation,
    how many records of its `venue_event_id` precede it in its segment (`occurrence`).
    """

    kind: str
    ts_ns: int
    index: int
    values: Mapping[str, Any]
    reason: str | None
    venue_event_id: str | None = None
    occurrence: int = 0

    @property
    def key(self) -> Key:
        if self.kind == LIQUIDATION:
            assert self.venue_event_id is not None  # `parse_cycle` requires it on this kind
            return (self.kind, self.venue_event_id, self.occurrence)
        return (self.kind, self.ts_ns, 0)


@dataclass(frozen=True)
class CascadeSegment:
    """One run of one cascade bot: its `start` record and every record after it, in file order."""

    start: Mapping[str, Any]
    cycles: tuple[CascadeCycle, ...]

    @property
    def bot_id(self) -> str:
        return str(self.start["bot_id"])

    @property
    def instrument_id(self) -> str:
        return str(self.start["instrument_id"])

    @property
    def start_ns(self) -> int:
        return int(self.start["ts_ns"])

    @property
    def last_ns(self) -> int:
        return max((cycle.ts_ns for cycle in self.cycles), default=self.start_ns)

    @property
    def window_ns(self) -> int:
        return int(self.start["window_s"]) * NS_PER_S

    @property
    def baseline_ns(self) -> int:
        return int(self.start["baseline_s"]) * NS_PER_S


# --- parsing -------------------------------------------------------------------------------------


def _json(text: str, where: str) -> dict[str, Any]:
    try:
        record = json.loads(text)
    except ValueError as exc:
        raise MalformedRecord(f"{where}: not JSON: {text[:200]!r}") from exc
    if not isinstance(record, dict):
        raise MalformedRecord(f"{where}: not a JSON object: {text[:200]!r}")
    return record


def _int(value: object, where: str, key: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedRecord(f"{where}: `{key}` must be an integer, got {value!r}")
    return value


def _text(value: object, where: str, key: str) -> str:
    if not isinstance(value, str) or not value:
        raise MalformedRecord(f"{where}: `{key}` must be a non-empty string, got {value!r}")
    return value


def _values(record: Mapping[str, Any], where: str) -> dict[str, Any]:
    """Return the record's floats, direction, booleans and decision, each of its documented type."""
    values: dict[str, Any] = {}
    for name in FLOATS:
        value = record[name]
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise MalformedRecord(f"{where}: `{name}` must be a number, got {value!r}")
        values[name] = float(value)
    if record["direction"] not in (-1, 0, 1) or isinstance(record["direction"], bool):
        raise MalformedRecord(
            f"{where}: `direction` must be -1, 0 or 1, got {record['direction']!r}"
        )
    for name in ("active", "spent"):
        if not isinstance(record[name], bool):
            raise MalformedRecord(f"{where}: `{name}` must be a boolean, got {record[name]!r}")
    if record["decision"] not in DECISIONS:
        raise MalformedRecord(f"{where}: unknown decision {record['decision']!r}")
    values.update({name: record[name] for name in EXACT})
    return values


def parse_cycle(record: Mapping[str, Any], where: str, index: int) -> CascadeCycle:
    """Parse one non-`start` record; `MalformedRecord` for anything but the documented shape."""
    kind = record.get("kind")
    if not isinstance(kind, str) or kind not in _KIND_KEYS:
        raise MalformedRecord(f"{where}: unknown record kind {kind!r}")
    if set(record) != _KIND_KEYS[kind]:
        raise MalformedRecord(f"{where}: {kind} keys {sorted(record)}")
    reason = record["reason"]
    if reason is not None:
        _text(reason, where, "reason")
    event = (
        _text(record["venue_event_id"], where, "venue_event_id") if kind == LIQUIDATION else None
    )
    return CascadeCycle(
        kind=kind,
        ts_ns=_int(record["ts_ns"], where, "ts_ns"),
        index=index,
        values=MappingProxyType(_values(record, where)),
        reason=reason,
        venue_event_id=event,
    )


def _start(record: dict[str, Any], where: str) -> dict[str, Any]:
    if record.get("strategy") != STRATEGY:
        raise MalformedRecord(f"{where}: not a {STRATEGY} start record")
    _text(record.get("bot_id"), where, "bot_id")
    _text(record.get("instrument_id"), where, "instrument_id")
    for key in _START_INTS:
        if _int(record.get(key), where, key) < (0 if key == "ts_ns" else 1):
            raise MalformedRecord(f"{where}: `{key}` out of range: {record[key]!r}")
    return record


def latest_segment(lines: Iterable[tuple[str, str]]) -> CascadeSegment:
    """
    Parse a cascade log's `(file:line, text)` lines and return its latest segment; `MalformedRecord`
    for a malformed line anywhere, a record before any `start`, a record of another bot or
    instrument, or one stamped before its segment's start.
    """
    start: dict[str, Any] | None = None
    cycles: list[CascadeCycle] = []
    seen: Counter[str] = Counter()  # each event id's records so far in the segment
    for where, text in lines:
        record = _json(text, where)
        if record.get("kind") == START:
            start, cycles, seen = _start(record, where), [], Counter()
            continue
        if start is None:
            raise MalformedRecord(f"{where}: a record before any start record")
        cycle = parse_cycle(record, where, len(cycles))
        if (record["bot_id"], record["instrument_id"]) != (start["bot_id"], start["instrument_id"]):
            raise MalformedRecord(f"{where}: a record of another bot or instrument")
        if cycle.ts_ns < start["ts_ns"]:
            raise MalformedRecord(f"{where}: ts_ns {cycle.ts_ns} before its start {start['ts_ns']}")
        event = cycle.venue_event_id
        if event is not None:
            cycle = dataclasses.replace(cycle, occurrence=seen[event])
            seen[event] += 1
        cycles.append(cycle)
    if start is None:
        raise MalformedRecord("a signal log without a start record")
    return CascadeSegment(MappingProxyType(start), tuple(cycles))


def keyed(segment: CascadeSegment) -> dict[Key, CascadeCycle]:
    """
    Key the segment's records (`tick` by `ts_ns`, `liquidation` by event id and occurrence); a
    repeated tick time is refused.
    """
    cycles: dict[Key, CascadeCycle] = {}
    for cycle in segment.cycles:
        if cycle.key in cycles:
            raise MalformedRecord(
                f"{segment.bot_id}: two {cycle.kind} records keyed {cycle.key[1]}"
            )
        cycles[cycle.key] = cycle
    return cycles


# --- late arrival --------------------------------------------------------------------------------


def late_rows(live: CascadeSegment, replay: Mapping[Key, CascadeCycle]) -> list[int]:
    """
    Return the `ts_init` (the replay's `ts_ns` of the same event) of every live liquidation an
    earlier live record had already passed: delivered after the detector's clock moved beyond it.
    """
    late: list[int] = []
    clock = None
    for cycle in live.cycles:
        twin = replay.get(cycle.key) if cycle.kind == LIQUIDATION else None
        if twin is not None and clock is not None and clock > twin.ts_ns:
            late.append(twin.ts_ns)
        clock = cycle.ts_ns if clock is None else max(clock, cycle.ts_ns)
    return sorted(late)


def late_ticks(live: CascadeSegment) -> frozenset[int]:
    """
    Return the `ts_ns` of every live tick written after a record with a later `ts_ns`: its timer
    callback ran after a row received past its second had been fed.
    """
    late: set[int] = set()
    clock = None
    for cycle in live.cycles:
        if cycle.kind == TICK and clock is not None and clock > cycle.ts_ns:
            late.add(cycle.ts_ns)
        clock = cycle.ts_ns if clock is None else max(clock, cycle.ts_ns)
    return frozenset(late)


@dataclass(frozen=True)
class LateRows:
    """
    The live segment's late rows (their `ts_init`, sorted), the rate window they stay in
    (`memory_ns`), the baseline's time constant, every paired record's `ts_ns` with its baseline
    difference `|live - replay|`, in time order (what the decaying bound is measured from), and the
    `ts_ns` of its late ticks (`late_ticks`).
    Invariant: the bound at T is read off the first paired record after the window of the most
    recent late row at or before T, never off an older row's (the module docstring).
    """

    ts_init: tuple[int, ...]
    memory_ns: int
    baseline_ns: int
    diff_ts: tuple[int, ...] = ()
    diffs: tuple[float, ...] = ()
    late_ticks: frozenset[int] = frozenset()

    def last_before(self, ts_ns: int) -> int | None:
        """Return the `ts_init` of the most recent late row at or before `ts_ns`, if any."""
        i = bisect.bisect_right(self.ts_init, ts_ns)
        return self.ts_init[i - 1] if i else None

    def in_window(self, ts_ns: int) -> bool:
        """Whether `ts_ns` lies in the most recent late row's window (`[L, L + memory_ns]`)."""
        last = self.last_before(ts_ns)
        return last is not None and ts_ns - last <= self.memory_ns

    def baseline_bound(self, ts_ns: int) -> float | None:
        """
        Return the largest baseline difference at `ts_ns` the most recent late row explains past
        its window, `d0 * exp(-(T - t0) / baseline_s) * (1 + REL_TOL)`; None without a late row
        before `ts_ns` or a paired record after its window.
        """
        last = self.last_before(ts_ns)
        if last is None:
            return None
        i = bisect.bisect_right(self.diff_ts, last + self.memory_ns)
        if i == len(self.diff_ts):
            return None
        decay = math.exp(-(ts_ns - self.diff_ts[i]) / self.baseline_ns)
        return self.diffs[i] * decay * (1 + REL_TOL)


def late_rows_of(
    live: CascadeSegment,
    replay: Mapping[Key, CascadeCycle],
    pairs: Iterable[tuple[CascadeCycle, CascadeCycle]],
) -> LateRows:
    """Build the segment's `LateRows` from its late rows and its in-window `pairs`."""
    diffs = sorted((a.ts_ns, abs(a.values["baseline"] - b.values["baseline"])) for a, b in pairs)
    return LateRows(
        ts_init=tuple(late_rows(live, replay)),
        memory_ns=live.window_ns + NS_PER_S,
        baseline_ns=live.baseline_ns,
        diff_ts=tuple(t for t, _ in diffs),
        diffs=tuple(d for _, d in diffs),
        late_ticks=late_ticks(live),
    )


# --- the report ----------------------------------------------------------------------------------


class FieldStats:
    """
    One field over one bot's paired records. Invariant: `equal <= paired`; `max_abs_diff` is the
    largest finite difference of a non-equal numeric pair (None while there is none).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.paired = 0
        self.equal = 0
        self.max_abs_diff: float | None = None
        self.classes: Counter[str] = Counter()

    def add(self, live: object, replay: object) -> bool:
        """Count one paired value; return whether it agreed."""
        self.paired += 1
        if self.name in FLOATS:
            assert isinstance(live, float)
            assert isinstance(replay, float)
            same = relative(live, replay) not in FAILING
        else:
            same = live == replay and type(live) is type(replay)
        if same:
            self.equal += 1
            return True
        numbers = all(
            isinstance(v, int | float) and not isinstance(v, bool) for v in (live, replay)
        )
        if numbers:
            diff = abs(float(live) - float(replay))  # type: ignore[arg-type]
            if math.isfinite(diff):
                self.max_abs_diff = (
                    diff if self.max_abs_diff is None else max(self.max_abs_diff, diff)
                )
        return False

    @property
    def equal_share(self) -> float | None:
        return self.equal / self.paired if self.paired else None


class CascadeReport:
    """
    One cascade bot's comparison. Invariant: every key of the window is counted exactly once --
    paired (tick or liquidation) or one-sided -- by `compare_cascade`, the only writer.
    """

    strategy = STRATEGY

    def __init__(self, live: CascadeSegment, end_ns: int, beyond: tuple[int, int]) -> None:
        self.bot_id = live.bot_id
        self.instrument_id = live.instrument_id
        self.start_ns = live.start_ns
        self.end_ns = end_ns
        self.truncated = 0
        self.live_beyond, self.replay_beyond = beyond
        self.paired: Counter[str] = Counter()
        self.late_rows = 0
        self.fields = {name: FieldStats(name) for name in FIELDS}
        self.reasons = 0
        self.cycle_classes: Counter[str] = Counter()
        self.live_only: Counter[str] = Counter()
        self.replay_only: Counter[str] = Counter()

    @property
    def unexplained(self) -> int:
        """
        Failing count: unexplained records, every one-sided record, a truncated side's records.
        """
        one_sided = sum(self.live_only.values()) + sum(self.replay_only.values())
        return self.cycle_classes[UNEXPLAINED] + one_sided + self.truncated


def _check_starts(live: CascadeSegment, replay: CascadeSegment) -> None:
    live_config = {k: v for k, v in live.start.items() if k != "ts_ns"}
    replay_config = {k: v for k, v in replay.start.items() if k != "ts_ns"}
    if live_config != replay_config:
        raise SegmentMismatch(
            f"{live.bot_id}: live start {live_config} != replay start {replay_config}"
        )
    if replay.start_ns != live.start_ns:
        raise SegmentMismatch(
            f"{live.bot_id}: the replay starts at {replay.start_ns}, not the live start "
            f"{live.start_ns}: the detector's baseline history cannot be replayed from a later one"
        )


def _float_classes(
    differing: set[str], late: LateRows, live: CascadeCycle, replay: CascadeCycle
) -> dict[str, str]:
    """Class each differing float of one paired record (the module docstring's rules)."""
    if late.in_window(live.ts_ns):
        return {name: LATE_ARRIVAL for name in FLOATS if name in differing}
    classes = {name: UNEXPLAINED for name in _RATES if name in differing}
    if "baseline" in differing:
        bound = late.baseline_bound(live.ts_ns)
        diff = abs(live.values["baseline"] - replay.values["baseline"])
        classes["baseline"] = LATE_ARRIVAL if bound is not None and diff <= bound else UNEXPLAINED
    if "intensity" in differing:
        # only a late-arrival baseline under equal rates explains it
        drift = classes == {"baseline": LATE_ARRIVAL}
        classes["intensity"] = LATE_ARRIVAL if drift else UNEXPLAINED
    return classes


def _exact_classes(
    differing: set[str], floats: Mapping[str, str], late_before: bool
) -> dict[str, str]:
    """Class each differing exact field, given the record's float classes."""
    drift = late_before and all(cls == LATE_ARRIVAL for cls in floats.values())
    classes = {
        name: LATE_ARRIVAL if drift else UNEXPLAINED
        for name in _INDICATOR_EXACT
        if name in differing
    }
    if "decision" in differing:
        indicators = {**floats, **classes}
        if not indicators:
            classes["decision"] = QUOTE_CADENCE
        elif all(cls == LATE_ARRIVAL for cls in indicators.values()):
            classes["decision"] = LATE_ARRIVAL
        else:
            classes["decision"] = UNEXPLAINED
    return classes


def classify(
    differing: set[str], late: LateRows, live: CascadeCycle, replay: CascadeCycle
) -> dict[str, str]:
    """Return the class of every differing field of one paired record (the module docstring)."""
    if _is_placed_late_row(late, live, replay) or _is_late_tick(late, live):
        # The late row's own record: live wrote it at its clock (fed there, or refused as
        # `stale_row`), the replay at its `ts_init` -- two instants, so every field may differ. A
        # late tick's: the live one holds a row the replay's tick precedes.
        return dict.fromkeys(differing, LATE_ARRIVAL)
    classes = _float_classes(differing, late, live, replay)
    late_before = late.last_before(live.ts_ns) is not None
    classes.update(_exact_classes(differing, classes, late_before))
    if TS_NS in differing:  # a `ts_ns` difference that is not a placed late row
        classes[TS_NS] = UNEXPLAINED
    return classes


def _is_placed_late_row(late: LateRows, live: CascadeCycle, replay: CascadeCycle) -> bool:
    return live.kind == LIQUIDATION and replay.ts_ns < live.ts_ns and replay.ts_ns in late.ts_init


def _is_late_tick(late: LateRows, live: CascadeCycle) -> bool:
    return live.kind == TICK and live.ts_ns in late.late_ticks


def _record_class(classes: Iterable[str]) -> str:
    found = set(classes)
    return next(cls for cls in (UNEXPLAINED, LATE_ARRIVAL, QUOTE_CADENCE) if cls in found)


def _judge_pair(
    report: CascadeReport, late: LateRows, live: CascadeCycle, replay: CascadeCycle
) -> None:
    report.paired[live.kind] += 1
    pairs = [(name, live.values[name], replay.values[name]) for name in (*FLOATS, *EXACT)]
    if live.kind == LIQUIDATION:
        pairs.append((TS_NS, live.ts_ns, replay.ts_ns))
    differing = {name for name, a, b in pairs if not report.fields[name].add(a, b)}
    classes = classify(differing, late, live, replay)
    for name, cls in classes.items():
        report.fields[name].classes[cls] += 1
    if live.reason != replay.reason and live.values["decision"] == replay.values["decision"]:
        report.reasons += 1
    if classes:
        report.cycle_classes[_record_class(classes.values())] += 1


def _in_window(live: CascadeCycle | None, replay: CascadeCycle | None, end_ns: int) -> bool:
    return min(c.ts_ns for c in (live, replay) if c is not None) <= end_ns


def _truncated(live: CascadeSegment, replay: CascadeSegment, end_ns: int) -> int:
    if abs(live.last_ns - replay.last_ns) <= TIMER_INTERVAL_NS:
        return 0
    return sum(c.ts_ns > end_ns for c in (*live.cycles, *replay.cycles))


def compare_cascade(live: CascadeSegment, replay: CascadeSegment) -> CascadeReport:
    """
    Compare one cascade bot's latest live segment with its replay's (the module docstring);
    `SegmentMismatch` for two segments of different bots or configurations or start times.
    """
    _check_starts(live, replay)
    end_ns = min(live.last_ns, replay.last_ns)
    sides = (keyed(live), keyed(replay))
    beyond = tuple(sum(c.ts_ns > end_ns for c in seg.cycles) for seg in (live, replay))
    report = CascadeReport(live, end_ns, (beyond[0], beyond[1]))
    report.truncated = _truncated(live, replay, end_ns)
    pairs: list[tuple[CascadeCycle, CascadeCycle]] = []
    for key in set(sides[0]) | set(sides[1]):
        live_cycle, replay_cycle = sides[0].get(key), sides[1].get(key)
        if not _in_window(live_cycle, replay_cycle, end_ns):
            continue
        if live_cycle is not None and replay_cycle is not None:
            pairs.append((live_cycle, replay_cycle))
        elif live_cycle is not None:
            report.live_only[live_cycle.kind] += 1
        else:
            assert replay_cycle is not None  # every key comes from one side at least
            report.replay_only[replay_cycle.kind] += 1
    late = late_rows_of(live, sides[1], pairs)
    report.late_rows = len(late.ts_init)
    for live_cycle, replay_cycle in pairs:
        _judge_pair(report, late, live_cycle, replay_cycle)
    return report
