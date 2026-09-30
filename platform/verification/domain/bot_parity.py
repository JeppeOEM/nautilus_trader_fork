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
Bot signal parity, the pure side (Story 31.9): one bot's live signal log against its catalog replay,
cycle by cycle, every divergence quantified and classified.

The record format is `bots.strategies.signal_log`'s, read here as plain JSON: this module never
imports `bots` or `nautilus_trader` (DATA-02, `tests/test_boundaries.py`). A log holds run
segments, each opened by a `start` record; only the latest segment of a file is compared. The two
`start` records agree on everything but `ts_ns`, and the replay's lies on the live grid (`live
start + k s`, k >= 0: a `--start` override). Records are paired by equal `ts_ns` within their
group (`book` and `book_skipped` share the 1 s timer's group, `bar` is its own), over `(replay
start, min(last live, last replay)]`; when one side's last cycle falls over one timer interval
short of the other's, the longer side's cycles past it are `truncated`, a failing count (a replay
cut short, or a live run the replay outlived, never passes as a shorter window).

The replay's own input is checked on every replay timer cycle, independently of the live side
(`replay_input`, failing): its fed levels must be the stored row active at T by `ts_init` (the
latest with `start <= ts_init <= T`, compared by `grid_units`' exact rule), its microprice that
row's top's (`reference_microprice`, within `signal_compare.REL_TOL`), and a skip means no row was
active. That is what catches a broken snapshot conversion even when both logs agree.

Per signal (`microprice`, `ofi`, `obi`, `mlofi`, `trend`): paired count, exact-equal count (a null
equals a null, a NaN a NaN, nothing else is rounded) and the largest absolute difference. Every
non-equal signal of a paired cycle gets exactly one class, first match wins:

1. `gap`: a second in `[T-3 s, T)` has no stored row and the coverage record states why (the reason
   is carried in `gap_reasons`); a missing second without a reason is `unexplained`.
2. `book_timing`: the fed levels differ, and the live top-N equals a stored row's top-N at some
   second in `[T-5 s, T+1 s]` (the replay saw that book, later or earlier).
3. `book_source`: the fed levels differ and the live top-N equals no stored row in that window (the
   full live book against the gated top-20 snapshot).
4. `quote_cadence`: `microprice` and `ofi` only (live updates on every quote, the replay once per
   stored row), and only when the replay's microprice is its active row's top's -- the evidence
   that the replay's quotes are exactly the stored tops; else `unexplained`. `ofi` (the top-of-book
   OFI over the last 50 quotes) is fed the very same quotes, so it rests on that evidence rather
   than on a restatement of the replay's whole quote history.
5. `bar_source`: `trend` only, the latest bars' `{ts_event, close}` up to T differ.
6. `carried_state`: the inputs are equal this cycle but differed within the indicator's memory.
7. `unexplained`, which fails the run.

`trend` is fed by bars alone, so it is judged only by 5, 6, 7 (a missing snapshot row or a differing
book cannot move it). On a `bar` cycle no book is fed, so `obi`/`mlofi` there carry the last book
cycle's value: 6 or 7. A cycle's own class is `unexplained` if any of its signals or its decision
is, else the earliest class among them -- so a real `mlofi` defect is never hidden behind a
`quote_cadence` difference of the same cycle.

Memory (6): `obi` has none (a function of the current book); `mlofi` sums `ofi_window` per-update
contributions, each from the current and the previous book, so it remembers the current input and
`ofi_window` prior updates per side. `trend` is an online SGD model whose weights carry every bar it
was fed, so its memory is every bar of the segment -- deliberately not `trend_lookback` bars, which
bounds only its feature vector.

A decision (`signal`) disagreement is attributed to the class of its diverging input (`trend`,
`mlofi`; the worse of the two when both diverge), `unexplained` when both are equal. An `action`
disagreement is informational: fills differ by construction. A cycle on one side only is
`live_only`/`replay_only`: a replay book the live side skipped at the same T carries the live
skip's own reason (`book_skipped:<reason>`); a live book the replay skipped is explained only by
the replay's cold start (`cold_start`: `no_book` before the first stored row with `ts_init` at or
after the replay start, when the replay could hold no book yet) or by a coverage-explained `gap`;
anything else is `unexplained`.
"""

import json
import math
from bisect import bisect_left
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN
from decimal import Decimal
from decimal import localcontext
from types import MappingProxyType
from typing import Any

from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import SecondsRun
from verification.domain.reference_book import StoredBook
from verification.domain.reference_book import UnitLevel
from verification.domain.signal_compare import FAILING
from verification.domain.signal_compare import relative


START = "start"
BOOK = "book"
SKIP_NO_BOOK = "no_book"
BOOK_SKIPPED = "book_skipped"
BAR = "bar"

MICROPRICE = "microprice"
OFI = "ofi"
OBI = "obi"
MLOFI = "mlofi"
TREND = "trend"
SIGNALS = (MICROPRICE, OFI, OBI, MLOFI, TREND)
_QUOTE_FED = frozenset({MICROPRICE, OFI})

GAP = "gap"
BOOK_TIMING = "book_timing"
BOOK_SOURCE = "book_source"
QUOTE_CADENCE = "quote_cadence"
BAR_SOURCE = "bar_source"
CARRIED_STATE = "carried_state"
UNEXPLAINED = "unexplained"
CLASSES = (GAP, BOOK_TIMING, BOOK_SOURCE, QUOTE_CADENCE, BAR_SOURCE, CARRIED_STATE, UNEXPLAINED)

# The windows the spec names, as floor-second starts S (a row's second is `ts_event // 1 s`): a
# gap is a second with `T - 3 s <= S < T`, a match candidate one with `T - 5 s <= S <= T + 1 s`.
GAP_LOOKBACK_NS = 3 * NS_PER_S
MATCH_BEFORE_NS = 5 * NS_PER_S
MATCH_AFTER_NS = NS_PER_S
# The decision timer's interval: both sides' last cycles must lie within one of each other.
TIMER_INTERVAL_NS = NS_PER_S

# A one-sided cycle's explanation when the replay had no book yet (its data starts at the window
# start on `ts_init`, so it holds no book until the first stored row sampled after it).
COLD_START = "cold_start"
# Why a replay timer cycle's own input is not what the replay was fed from the catalog
# (`Judge.replay_input`), each a failing `replay_input` count.
INPUT_NO_ROW = "no_row"
INPUT_LEVELS = "levels"
INPUT_SKIPPED = "skipped_with_row"
INPUT_MICROPRICE = "microprice"
# The stored row active at T (`Judge.active_row`) is looked for in the seconds up to T, back to one
# hour (the reader holds the hours around the latest one) and never before the window start less
# `SAMPLE_LAG_S`: a row is sampled `1 + hold_back` s after its second (under 5 s measured on every
# venue), so no row of an earlier second can carry a `ts_init` at or after the start. Scanning
# stops `ORDER_SLACK_S` seconds below the best candidate, in case two rows' samplings interleave.
ACTIVE_LOOKBACK_S = 3_600
SAMPLE_LAG_S = 60
ORDER_SLACK_S = 5

_COMMON_KEYS = frozenset({"kind", "bot_id", "instrument_id", "ts_ns", *SIGNALS, "signal", "action"})
_KIND_KEYS = MappingProxyType(
    {
        BOOK: (frozenset({"book_ts_ns", "bids", "asks"}), frozenset()),
        BOOK_SKIPPED: (frozenset({"reason"}), frozenset({"book_ts_ns"})),
        BAR: (frozenset({"bar"}), frozenset()),
    }
)
_START_INTS = ("ts_ns", "ofi_window", "ofi_levels", "obi_levels")

Level = tuple[float, float]
Key = tuple[str, int]


class MalformedRecord(ValueError):
    """A signal-log line that is not the documented record: refused, never skipped."""


class SegmentMismatch(ValueError):
    """
    Two segments that cannot be compared: a different bot or configuration, or a replay start off
    the live run's 1 s grid (or before the live start).
    """


@dataclass(frozen=True)
class Cycle:
    """One decision-cycle record: its kind, `ts_ns`, the five values, decision, action, inputs."""

    kind: str
    ts_ns: int
    values: tuple[float | None, ...]
    signal: str
    action: str | None
    bids: tuple[Level, ...] = ()
    asks: tuple[Level, ...] = ()
    bar: tuple[int, float] | None = None
    reason: str | None = None

    @property
    def group(self) -> str:
        """The timer's group (`book`, `book_skipped`) or `bar`: records pair within a group."""
        return BAR if self.kind == BAR else BOOK

    def value(self, name: str) -> float | None:
        return self.values[SIGNALS.index(name)]


@dataclass(frozen=True)
class Segment:
    """One run of one bot: its `start` record and every cycle after it, in file order."""

    start: Mapping[str, Any]
    cycles: tuple[Cycle, ...]

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

    def param(self, name: str) -> int:
        return int(self.start[name])


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


def _number(value: object, where: str, key: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise MalformedRecord(f"{where}: `{key}` must be a number, got {value!r}")
    return float(value)


def _text(value: object, where: str, key: str) -> str:
    if not isinstance(value, str) or not value:
        raise MalformedRecord(f"{where}: `{key}` must be a non-empty string, got {value!r}")
    return value


def _levels(value: object, where: str, key: str) -> tuple[Level, ...]:
    if not isinstance(value, list):
        raise MalformedRecord(f"{where}: `{key}` must be a list of [price, size], got {value!r}")
    levels = []
    for level in value:
        if not isinstance(level, list) or len(level) != 2:
            raise MalformedRecord(f"{where}: `{key}` level {level!r} is not [price, size]")
        levels.append((_number(level[0], where, key), _number(level[1], where, key)))
    return tuple(levels)


def _bar(value: object, where: str) -> tuple[int, float]:
    if not isinstance(value, dict) or set(value) != {"ts_event", "close"}:
        raise MalformedRecord(f"{where}: `bar` must be {{ts_event, close}}, got {value!r}")
    return _int(value["ts_event"], where, "bar.ts_event"), _number(value["close"], where, "close")


def _check_keys(record: Mapping[str, Any], kind: str, where: str) -> None:
    required, optional = _KIND_KEYS[kind]
    keys = set(record)
    if not (_COMMON_KEYS | required) <= keys <= (_COMMON_KEYS | required | optional):
        raise MalformedRecord(f"{where}: {kind} keys {sorted(keys)}")


def parse_cycle(record: Mapping[str, Any], where: str) -> Cycle:
    """Parse one non-`start` record; `MalformedRecord` for anything but the documented shape."""
    kind = record.get("kind")
    if not isinstance(kind, str) or kind not in _KIND_KEYS:
        raise MalformedRecord(f"{where}: unknown record kind {kind!r}")
    _check_keys(record, kind, where)
    values = tuple(
        None if record[name] is None else _number(record[name], where, name) for name in SIGNALS
    )
    action = record["action"]
    return Cycle(
        kind=kind,
        ts_ns=_int(record["ts_ns"], where, "ts_ns"),
        values=values,
        signal=_text(record["signal"], where, "signal"),
        action=None if action is None else _text(action, where, "action"),
        bids=_levels(record["bids"], where, "bids") if kind == BOOK else (),
        asks=_levels(record["asks"], where, "asks") if kind == BOOK else (),
        bar=_bar(record["bar"], where) if kind == BAR else None,
        reason=_text(record["reason"], where, "reason") if kind == BOOK_SKIPPED else None,
    )


def _start(record: dict[str, Any], where: str) -> dict[str, Any]:
    _text(record.get("bot_id"), where, "bot_id")
    _text(record.get("instrument_id"), where, "instrument_id")
    for key in _START_INTS:
        _int(record.get(key), where, key)
    return record


def latest_segment(lines: Iterable[tuple[str, str]]) -> Segment:
    """
    Parse a log's `(file:line, text)` lines and return its latest segment; `MalformedRecord` for a
    malformed line anywhere, a cycle before any `start`, a record of another bot or instrument, or
    a cycle stamped before its segment's start.
    """
    start: dict[str, Any] | None = None
    cycles: list[Cycle] = []
    for where, text in lines:
        record = _json(text, where)
        if record.get("kind") == START:
            start, cycles = _start(record, where), []
            continue
        if start is None:
            raise MalformedRecord(f"{where}: a cycle before any start record")
        cycle = parse_cycle(record, where)
        if (record["bot_id"], record["instrument_id"]) != (start["bot_id"], start["instrument_id"]):
            raise MalformedRecord(f"{where}: a record of another bot or instrument")
        if cycle.ts_ns < start["ts_ns"]:
            raise MalformedRecord(f"{where}: ts_ns {cycle.ts_ns} before its start {start['ts_ns']}")
        cycles.append(cycle)
    if start is None:
        raise MalformedRecord("a signal log without a start record")
    return Segment(MappingProxyType(start), tuple(cycles))


def keyed(segment: Segment, end_ns: int) -> dict[Key, Cycle]:
    """Key the segment's cycles by `(group, ts_ns)` up to `end_ns`; two in one key are refused."""
    cycles: dict[Key, Cycle] = {}
    for cycle in segment.cycles:
        key = (cycle.group, cycle.ts_ns)
        if key in cycles:
            raise MalformedRecord(f"{segment.bot_id}: two {cycle.group} records at {cycle.ts_ns}")
        cycles[key] = cycle
    return {key: cycle for key, cycle in cycles.items() if key[1] <= end_ns}


# --- the stored rows -----------------------------------------------------------------------------


def grid_units(value: float, precision: int) -> int | None:
    """
    Return the integer count of `10^-precision` a logged float stands for (None: not finite).

    The equality rule for "the live top-N equals a stored row's top-N": a live level equals a
    stored level iff each logged float, taken at its exact binary value and rounded half-even to
    the row's precision grid, is the stored integer. This is exact equality of grid values, not a
    tolerance: the live float is `Price.as_double()`/`Quantity.as_double()` of a value at the
    instrument's precision (the same definition the row's precision comes from), and that
    conversion is off its grid value by a few ulp at most (`83062.59999999999` for 83062.6),
    many orders of magnitude below half a grid step for any stored magnitude, so the rounding can
    only ever land on the value the live object held. Two different grid values never compare
    equal. (Comparing the float with `float(stored)` instead would fail on exactly those
    one-ulp conversions and report a timing match as `book_source`.)
    """
    if not math.isfinite(value):
        return None
    return int(Decimal(value).scaleb(precision).to_integral_value(rounding=ROUND_HALF_EVEN))


def _side_equals(
    levels: Sequence[Level], stored: Sequence[UnitLevel], depth: int, pp: int, sp: int
) -> bool:
    top = stored[:depth]
    if len(top) != len(levels):
        return False
    return all(
        grid_units(price, pp) == units_price and grid_units(size, sp) == units_size
        for (price, size), (units_price, units_size) in zip(levels, top, strict=True)
    )


def top_equals(row: StoredBook, cycle: Cycle, depth: int) -> bool:
    """
    Whether the cycle's logged levels are the stored row's top `depth` levels, side by side
    (`grid_units`' rule; a side the book holds fewer levels of must hold exactly as many).
    """
    pp, sp = row.price_precision, row.size_precision
    return _side_equals(cycle.bids, row.book.bids, depth, pp, sp) and _side_equals(
        cycle.asks, row.book.asks, depth, pp, sp
    )


# The stored rows of one floor second (the oracle's own decode, `reference_book.StoredBook`).
StoredLookup = Callable[[int], Sequence[StoredBook]]


class GapReasons:
    """The coverage record's reasons for seconds without a row, of one instrument."""

    def __init__(self, runs: Iterable[SecondsRun]) -> None:
        self._runs = tuple(runs)

    def reason(self, second: int) -> str | None:
        return next((r.reason for r in self._runs if r.first_s <= second <= r.last_s), None)


# --- judging -------------------------------------------------------------------------------------


def same(a: float | None, b: float | None) -> bool:
    """Exact equality of two logged values: null == null, NaN == NaN, else `==` (no rounding)."""
    if a is None or b is None:
        return a is b
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return a == b


def worst(classes: Iterable[str]) -> str:
    """`unexplained` if present, else the earliest class of the ordered list."""
    return min(classes, key=lambda c: -1 if c == UNEXPLAINED else CLASSES.index(c))


def _bar_ts(bar: tuple[int, tuple[int, float] | None]) -> int:
    return bar[0]


def _same_inputs(live: Cycle | None, replay: Cycle | None) -> bool:
    if live is None or replay is None or live.kind != replay.kind:
        return False
    return live.kind == BOOK_SKIPPED or (live.bids, live.asks) == (replay.bids, replay.asks)


def reference_microprice(row: StoredBook) -> Decimal | None:
    """
    Return the microprice of a stored row's top of book, exactly: `reference_signals.microprice`'s
    formula, `(bid * ask_size + ask * bid_size) / (bid_size + ask_size)`, restated over the row's
    integer units (that module decodes a whole `RefBook`; a row here is already `StoredBook`
    units). None on an empty side or a zero top size total (undefined, as there).
    """
    if not row.book.bids or not row.book.asks:
        return None
    (bid_units, bid_size_units), (ask_units, ask_size_units) = row.book.bids[0], row.book.asks[0]
    with localcontext(prec=60):
        bid, ask = (
            Decimal(bid_units).scaleb(-row.price_precision),
            Decimal(ask_units).scaleb(-row.price_precision),
        )
        bid_size = Decimal(bid_size_units).scaleb(-row.size_precision)
        ask_size = Decimal(ask_size_units).scaleb(-row.size_precision)
        total = bid_size + ask_size
        return None if total == 0 else (bid * ask_size + ask * bid_size) / total


class Judge:
    """
    Classifies one bot's divergences from both sides' keyed cycles, the stored rows and the
    coverage reasons, and checks the replay's own input against the catalog (`replay_input`).
    Invariant: read-only over its inputs; the only state is its memos.
    """

    def __init__(
        self,
        sides: tuple[Mapping[Key, Cycle], Mapping[Key, Cycle]],
        stored: StoredLookup,
        gaps: GapReasons,
        segments: tuple[Segment, Segment],
    ) -> None:
        self._live, self._replay = sides
        self._stored = stored
        self._gaps = gaps
        live, replay = segments
        self._depth = max(live.param("ofi_levels"), live.param("obi_levels"))
        self._ofi_window = live.param("ofi_window")
        self._replay_start = replay.start_ns
        self._book_ts = sorted({ts for group, ts in (*self._live, *self._replay) if group == BOOK})
        self._bars = tuple(
            sorted((ts, c.bar) for (group, ts), c in side.items() if group == BAR) for side in sides
        )
        self._gap_memo: dict[int, tuple[str, ...]] = {}
        self._active_memo: dict[int, StoredBook | None] = {}
        self._first_ts_init: int | None = self._first_row_ts_init()

    def _first_row_ts_init(self) -> int | None:
        """Return the earliest `ts_init` at or after the replay start (before it: no book)."""
        first: int | None = None
        second = self._replay_start // NS_PER_S - SAMPLE_LAG_S
        last = (self._book_ts[-1] if self._book_ts else self._replay_start) // NS_PER_S
        while second <= last and (first is None or second <= first // NS_PER_S + ORDER_SLACK_S):
            for row in self._stored(second):
                if row.ts_init >= self._replay_start and (first is None or row.ts_init < first):
                    first = row.ts_init
            second += 1
        return first

    def cold(self, ts_ns: int) -> bool:
        """Whether T precedes every stored row the replay could hold (its cold start)."""
        return self._first_ts_init is None or ts_ns < self._first_ts_init

    def active_row(self, ts_ns: int) -> StoredBook | None:
        """
        Return the stored row the replay's book is at T: the latest with `start <= ts_init <= T` (both
        derived types are stamped at the row's `ts_init` and read from the window start on).
        """
        if ts_ns not in self._active_memo:
            self._active_memo[ts_ns] = self._scan_active(ts_ns)
        return self._active_memo[ts_ns]

    def _scan_active(self, ts_ns: int) -> StoredBook | None:
        best: StoredBook | None = None
        best_second = second = ts_ns // NS_PER_S
        lowest = max(second - ACTIVE_LOOKBACK_S, self._replay_start // NS_PER_S - SAMPLE_LAG_S)
        while second >= lowest and (best is None or second >= best_second - ORDER_SLACK_S):
            for row in self._stored(second):
                if self._replay_start <= row.ts_init <= ts_ns and (
                    best is None or row.ts_init > best.ts_init
                ):
                    best, best_second = row, second
            second -= 1
        return best

    def replay_microprice_ok(self, replay: Cycle) -> bool:
        """
        Whether the replay's microprice at T is the microprice of the top of book it was fed (the
        active row's, which a book cycle's logged levels equal: `replay_input`), within
        `signal_compare.REL_TOL`; before any row, it must still be null. The evidence behind every
        `quote_cadence`: the replay's quotes are exactly its stored rows' tops.
        """
        row = self.active_row(replay.ts_ns)
        produced = replay.value(MICROPRICE)
        if row is None:
            return produced is None
        return relative(produced, reference_microprice(row)) not in FAILING

    def replay_input(self, replay: Cycle) -> str | None:
        """
        Why a replay timer cycle's input is not the catalog's (None: it is): a book fed with no
        row active (`no_row`), levels other than the active row's top (`levels`, by `grid_units`'
        exact rule), a skip while a row was active (`skipped_with_row`), or a microprice that is
        not its fed top's (`microprice`). This is what a broken snapshot conversion fails.
        """
        row = self.active_row(replay.ts_ns)
        if replay.kind == BOOK_SKIPPED:
            return None if row is None else INPUT_SKIPPED
        if row is None:
            return INPUT_NO_ROW
        if not top_equals(row, replay, self._depth):
            return INPUT_LEVELS
        return None if self.replay_microprice_ok(replay) else INPUT_MICROPRICE

    def gap(self, ts_ns: int) -> tuple[str, ...]:
        """Reasons of the seconds in `[T-3 s, T)` with no stored row (`unexplained`: none given)."""
        if ts_ns not in self._gap_memo:
            first = -((GAP_LOOKBACK_NS - ts_ns) // NS_PER_S)  # ceil((T - 3 s) / 1 s)
            last = (ts_ns - 1) // NS_PER_S
            self._gap_memo[ts_ns] = tuple(
                self._gaps.reason(s) or UNEXPLAINED
                for s in range(first, last + 1)
                if not self._stored(s)
            )
        return self._gap_memo[ts_ns]

    def _gap_class(self, ts_ns: int) -> str | None:
        reasons = self.gap(ts_ns)
        if not reasons:
            return None
        return UNEXPLAINED if UNEXPLAINED in reasons else GAP

    def _book_class(self, live: Cycle) -> str:
        first = -((MATCH_BEFORE_NS - live.ts_ns) // NS_PER_S)
        last = (live.ts_ns + MATCH_AFTER_NS) // NS_PER_S
        for second in range(first, last + 1):
            if any(top_equals(row, live, self._depth) for row in self._stored(second)):
                return BOOK_TIMING
        return BOOK_SOURCE

    def _inputs_differ_before(self, ts_ns: int, updates: int) -> bool:
        """Whether the fed books differed within each side's last `updates` updates before T."""
        counts = [0, 0]
        index = bisect_left(self._book_ts, ts_ns)
        while min(counts) < updates and index > 0:
            index -= 1
            key = (BOOK, self._book_ts[index])
            live, replay = self._live.get(key), self._replay.get(key)
            if not _same_inputs(live, replay):
                return True
            counts[0] += live is not None and live.kind == BOOK
            counts[1] += replay is not None and replay.kind == BOOK
        return False

    def _carried(self, name: str, ts_ns: int, prior_updates: int) -> str:
        if name == MLOFI:
            prior_updates += self._ofi_window
        differ = prior_updates > 0 and self._inputs_differ_before(ts_ns, prior_updates)
        return CARRIED_STATE if differ else UNEXPLAINED

    def _trend_class(self, ts_ns: int) -> str:
        live, replay = (bars[: bisect_right(bars, ts_ns, key=_bar_ts)] for bars in self._bars)
        if live[-1:] != replay[-1:]:
            return BAR_SOURCE
        return CARRIED_STATE if live != replay else UNEXPLAINED

    def _quote_class(self, replay: Cycle) -> str:
        # `ofi` (top-of-book OFI over the last 50 quotes) is fed the very quotes `microprice` is:
        # its own replay value would need the replay's whole quote history restated, so its
        # `quote_cadence` rests on the microprice evidence that those quotes are the rows' tops.
        return QUOTE_CADENCE if self.replay_microprice_ok(replay) else UNEXPLAINED

    def classify(self, name: str, live: Cycle, replay: Cycle) -> str:
        """Return the class of one non-equal signal of a paired cycle (first match wins)."""
        if name == TREND:
            return self._trend_class(live.ts_ns)
        if live.kind == BAR:  # no book fed: obi/mlofi carry the last book cycle's value
            if name in _QUOTE_FED:
                return self._quote_class(replay)
            return self._carried(name, live.ts_ns, 1)
        gap = self._gap_class(live.ts_ns)
        if gap is not None:
            return gap
        if (live.bids, live.asks) != (replay.bids, replay.asks):
            return self._book_class(live)
        if name in _QUOTE_FED:
            return self._quote_class(replay)
        return self._carried(name, live.ts_ns, 0)

    def one_sided(self, present: Cycle, other: Cycle | None, live_present: bool) -> str:
        """
        Why a cycle exists on one side only. A replay book the live side skipped is that skip's
        own recorded reason at the same T; a live book the replay skipped is explained only by the
        replay's cold start (`no_book` before its first stored row) or a coverage-explained gap;
        anything else -- a replay skip mid-window, a cycle missing outright -- is a gap or
        unexplained.
        """
        if other is not None and other.kind == BOOK_SKIPPED and present.kind == BOOK:
            if not live_present:
                return f"{BOOK_SKIPPED}:{other.reason}"
            if other.reason == SKIP_NO_BOOK and self.cold(present.ts_ns):
                return COLD_START
        gap = self._gap_class(present.ts_ns)
        return GAP if gap == GAP else UNEXPLAINED


# --- the report ----------------------------------------------------------------------------------


class SignalStats:
    """
    One signal over one bot's paired cycles. Invariant: `equal <= paired`, and `max_abs_diff` is
    the largest finite difference among the non-equal pairs (None while there is none); only `add`
    counts, one call per paired cycle.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.paired = 0
        self.equal = 0
        self.max_abs_diff: float | None = None
        self.classes: Counter[str] = Counter()

    def add(self, live: float | None, replay: float | None) -> bool:
        """Count one paired value; return whether it was equal."""
        self.paired += 1
        if same(live, replay):
            self.equal += 1
            return True
        if live is not None and replay is not None and not math.isnan(live - replay):
            diff = abs(live - replay)
            self.max_abs_diff = diff if self.max_abs_diff is None else max(self.max_abs_diff, diff)
        return False

    @property
    def equal_share(self) -> float | None:
        return self.equal / self.paired if self.paired else None


class BotReport:
    """
    One bot's comparison: pairs, per-signal statistics, decisions, actions, one-sided cycles.
    Invariant: every key of the window is counted exactly once -- paired (book or bar), skipped on
    both sides, or one-sided -- by `compare_bot`, the only writer.
    """

    def __init__(self, live: Segment, start_ns: int, end_ns: int, beyond: tuple[int, int]) -> None:
        self.bot_id = live.bot_id
        self.instrument_id = live.instrument_id
        self.start_ns = start_ns
        self.end_ns = end_ns
        self.truncated = 0
        self.replay_input: Counter[str] = Counter()
        self.live_beyond, self.replay_beyond = beyond
        self.paired_book = 0
        self.paired_bar = 0
        self.skipped_both = 0
        self.signals = {name: SignalStats(name) for name in SIGNALS}
        self.decisions: Counter[str] = Counter()
        self.actions = 0
        self.cycle_classes: Counter[str] = Counter()
        self.live_only: Counter[str] = Counter()
        self.replay_only: Counter[str] = Counter()
        self.gap_reasons: Counter[str] = Counter()

    @property
    def unexplained(self) -> int:
        """
        Failing count: unexplained cycles (any signal or decision) and one-sided cycles, replay
        cycles whose own input is not the catalog's (`replay_input`), and a truncated side's
        cycles past the window.
        """
        tallies = (self.cycle_classes, self.live_only, self.replay_only)
        failing = sum(c[UNEXPLAINED] for c in tallies)
        return failing + sum(self.replay_input.values()) + self.truncated


def _judge_pair(judge: Judge, report: BotReport, live: Cycle, replay: Cycle) -> None:
    classes: dict[str, str] = {}
    for name in SIGNALS:
        stats = report.signals[name]
        if not stats.add(live.value(name), replay.value(name)):
            classes[name] = judge.classify(name, live, replay)
            stats.classes[classes[name]] += 1
    if live.signal != replay.signal:
        diverging = [classes[name] for name in (TREND, MLOFI) if name in classes]
        classes["signal"] = worst(diverging) if diverging else UNEXPLAINED
        report.decisions[classes["signal"]] += 1
    report.actions += int(live.action != replay.action)
    if classes:
        report.cycle_classes[worst(classes.values())] += 1
    if GAP in classes.values():
        report.gap_reasons.update(set(judge.gap(live.ts_ns)))


def _judge_key(judge: Judge, report: BotReport, live: Cycle | None, replay: Cycle | None) -> None:
    if live is not None and replay is not None and live.kind == replay.kind:
        if live.kind == BOOK_SKIPPED:
            report.skipped_both += 1
            return
        report.paired_bar += int(live.kind == BAR)
        report.paired_book += int(live.kind == BOOK)
        _judge_pair(judge, report, live, replay)
        return
    if live is not None and (replay is None or live.kind == BOOK):
        present, other, tally = live, replay, report.live_only
    elif replay is not None:
        present, other, tally = replay, live, report.replay_only
    else:
        return  # unreachable: every key comes from one side at least
    explained = judge.one_sided(present, other, live_present=present is live)
    tally[explained] += 1
    if explained == GAP:
        report.gap_reasons.update(set(judge.gap(present.ts_ns)))


def _check_starts(live: Segment, replay: Segment) -> None:
    """
    Refuse two segments that are not one bot's run and its replay: the `start` records must agree
    on everything but `ts_ns`, and the replay must start on the live grid (`live start + k s`,
    k >= 0), so its cycles fall on live cycles' nanoseconds (`bots.signal_replay --start`).
    """
    live_config = {k: v for k, v in live.start.items() if k != "ts_ns"}
    replay_config = {k: v for k, v in replay.start.items() if k != "ts_ns"}
    if live_config != replay_config:
        raise SegmentMismatch(
            f"{live.bot_id}: live start {live_config} != replay start {replay_config}"
        )
    offset = replay.start_ns - live.start_ns
    if offset < 0 or offset % TIMER_INTERVAL_NS:
        raise SegmentMismatch(
            f"{live.bot_id}: the replay start {replay.start_ns} is not on the live grid "
            f"{live.start_ns} + k s (k >= 0)"
        )


def _truncated(live: Segment, replay: Segment, end_ns: int) -> int:
    """Count the longer side's cycles past `end_ns` when the shorter stops > 1 interval early."""
    if abs(live.last_ns - replay.last_ns) <= TIMER_INTERVAL_NS:
        return 0
    return sum(c.ts_ns > end_ns for c in (*live.cycles, *replay.cycles))


def compare_bot(
    live: Segment, replay: Segment, stored: StoredLookup, gaps: GapReasons
) -> BotReport:
    """
    Compare one bot's latest live segment with its replay's over `(replay start, min(last live,
    last replay)]`; `SegmentMismatch` for two segments of different bots or configurations or a
    replay start off the live grid. The live cycles before a later replay start stay visible to
    the judge as memory the replay never had (`carried_state`), never paired.
    """
    _check_starts(live, replay)
    end_ns = min(live.last_ns, replay.last_ns)
    sides = (keyed(live, end_ns), keyed(replay, end_ns))
    judge = Judge(sides, stored, gaps, (live, replay))
    beyond = tuple(sum(c.ts_ns > end_ns for c in seg.cycles) for seg in (live, replay))
    report = BotReport(live, replay.start_ns, end_ns, (beyond[0], beyond[1]))
    report.truncated = _truncated(live, replay, end_ns)
    later = replay.start_ns > live.start_ns
    keys = [key for key in set(sides[0]) | set(sides[1]) if not later or key[1] > replay.start_ns]
    for key in sorted(keys, key=lambda k: (k[1], k[0])):
        replay_cycle = sides[1].get(key)
        if replay_cycle is not None and replay_cycle.group == BOOK:
            reason = judge.replay_input(replay_cycle)
            if reason is not None:
                report.replay_input[reason] += 1
        _judge_key(judge, report, sides[0].get(key), replay_cycle)
    return report
