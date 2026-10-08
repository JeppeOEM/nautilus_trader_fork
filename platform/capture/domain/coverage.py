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
The coverage record (Story 31.2, DATA-07): what capture says about every second it did not write
and every trade it dropped, backfilled or could not recover, so `python -m
verification.conservation` can prove a missing second or trade is explained.

One JSON object per line in `<catalog>/../coverage/<venue lower>.jsonl`
(`capture.infrastructure.coverage_file`), five kinds (the fifth, Story 33.1, appended):

    {"kind":"seconds","instrument_id":…,"reason":…,"first_s":…,"last_s":…,"count":…}
    {"kind":"trades_dropped","instrument_id":…,"reason":"stale","first_ns":…,"last_ns":…,"count":…}
    {"kind":"trades_backfilled","instrument_id":…,"count":…,"trade_ids":[…]}
    {"kind":"trades_unrecoverable","instrument_id":…,"reason":"depth|fetch_failed","from_ns":…,"to_ns":…}
    {"kind":"liquidations_unrecoverable","instrument_id":…,"reason":"feed_down|not_running","from_ns":…,"to_ns":…}

A second is `ts_event // 1 s`, the same number the archive's snapshot rows carry.

`liquidations_unrecoverable` is an inclusive `ts_event` window (venue time, ns) of one instrument
whose liquidations were never received: the liquidation socket was not active (`feed_down`) or no
process was running (`not_running`). Known limit: there is no venue history endpoint to backfill
from (Bybit has none), so the window is the record, never a gap to fill. Upgrade path: a Bybit
liquidation history endpoint, should one appear, read by a backfill like the trades'.

Pure: no I/O, no logging, no clock. The `CaptureService` notes the verdicts, takes the runs at each
flush, encodes them with `CoverageLine.to_json_line` and appends them through its `ArchiveWriter`.
"""

import dataclasses
import json
from dataclasses import dataclass


# The reasons a planned instrument's second has no snapshot row. The first five are the write
# gate's rejections (`capture.domain.verdicts`); the rest are seconds nothing sampled.
NO_BOOK = "no_book"
EMPTY_TOP = "empty_top"
CROSSED = "crossed"
STALE = "stale"
UNENCODABLE = "unencodable"
NOT_COLLECTED = "not_collected"  # planned, not subscribed on the wire
CATCH_UP_CAP = "catch_up_cap"  # venue mode: older than the loop's catch-up cap after a stall
MISSED_TICK = "missed_tick"  # arrival mode: no sample tick landed in the second
RESTART = "restart"  # between the last archived row and this instance's first verdict
WRITE_FAILED = "write_failed"  # a row the gate accepted whose catalog write then failed
SECOND_REASONS = frozenset(
    {
        NO_BOOK,
        EMPTY_TOP,
        CROSSED,
        STALE,
        UNENCODABLE,
        NOT_COLLECTED,
        CATCH_UP_CAP,
        MISSED_TICK,
        RESTART,
        WRITE_FAILED,
    }
)
# An accepted row: noted so the next reason starts a new run, never itself a line.
ROW = "row"

# `trades_unrecoverable` reasons: the venue's REST depth did not reach back far enough, or the
# fetch could not run at all (an error, no definition, no trade history).
DEPTH = "depth"
FETCH_FAILED = "fetch_failed"


def _line(fields: dict[str, object]) -> str:
    return json.dumps(fields, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class SecondsRun:
    """Seconds `first_s..last_s` (inclusive) of one instrument, all without a row for `reason`."""

    instrument_id: str
    reason: str
    first_s: int
    last_s: int

    @property
    def count(self) -> int:
        return self.last_s - self.first_s + 1

    def to_json_line(self) -> str:
        return _line(
            {
                "kind": "seconds",
                "instrument_id": self.instrument_id,
                "reason": self.reason,
                "first_s": self.first_s,
                "last_s": self.last_s,
                "count": self.count,
            }
        )


@dataclass(frozen=True, slots=True)
class TradesDropped:
    """`count` trades dropped for `reason`, their `ts_event` inside `first_ns..last_ns`."""

    instrument_id: str
    reason: str
    first_ns: int
    last_ns: int
    count: int

    def to_json_line(self) -> str:
        return _line(
            {
                "kind": "trades_dropped",
                "instrument_id": self.instrument_id,
                "reason": self.reason,
                "first_ns": self.first_ns,
                "last_ns": self.last_ns,
                "count": self.count,
            }
        )


@dataclass(frozen=True, slots=True)
class TradesBackfilled:
    """Trades the REST backfill archived (never folded live), by id."""

    instrument_id: str
    trade_ids: tuple[str, ...]

    def to_json_line(self) -> str:
        return _line(
            {
                "kind": "trades_backfilled",
                "instrument_id": self.instrument_id,
                "count": len(self.trade_ids),
                "trade_ids": list(self.trade_ids),
            }
        )


@dataclass(frozen=True, slots=True)
class TradesUnrecoverable:
    """A `ts_event` window whose trades a backfill could not check (`DEPTH` or `FETCH_FAILED`)."""

    instrument_id: str
    reason: str
    from_ns: int
    to_ns: int

    def to_json_line(self) -> str:
        return _line(
            {
                "kind": "trades_unrecoverable",
                "instrument_id": self.instrument_id,
                "reason": self.reason,
                "from_ns": self.from_ns,
                "to_ns": self.to_ns,
            }
        )


# `liquidations_unrecoverable` reasons (Story 33.1).
FEED_DOWN = "feed_down"
NOT_RUNNING = "not_running"
# `WRITE_FAILED` (above): archived liquidations whose catalog write failed, from the first lost
# row's `ts_event` to the last's.
LIQUIDATION_REASONS = frozenset({FEED_DOWN, NOT_RUNNING, WRITE_FAILED})


@dataclass(frozen=True, slots=True)
class LiquidationsUnrecoverable:
    """
    A `ts_event` window (inclusive, ns) whose liquidations of one instrument were never received.

    Invariant: `reason` is a `LIQUIDATION_REASONS` member and the window is not inverted --
    construction refuses anything else (`ValueError`), so the line the verifier parses strictly is
    always well formed.
    """

    instrument_id: str
    reason: str
    from_ns: int
    to_ns: int

    def __post_init__(self) -> None:
        if self.reason not in LIQUIDATION_REASONS:
            raise ValueError(f"not a liquidation coverage reason: {self.reason!r}")
        if self.from_ns > self.to_ns:
            raise ValueError(f"inverted liquidation window {self.from_ns} > {self.to_ns}")

    def to_json_line(self) -> str:
        return _line(
            {
                "kind": "liquidations_unrecoverable",
                "instrument_id": self.instrument_id,
                "reason": self.reason,
                "from_ns": self.from_ns,
                "to_ns": self.to_ns,
            }
        )


CoverageLine = (
    SecondsRun | TradesDropped | TradesBackfilled | TradesUnrecoverable | LiquidationsUnrecoverable
)


class SecondCoverage:
    """
    Per instrument, the runs of seconds that got no row, and why.

    Invariant: every second noted for an instrument lands in exactly one place -- a written row
    (`ROW`, never a line) or one run of its reason -- and `take` hands each run out exactly once.
    A note extends the instrument's open run only when it has the same reason and starts right
    after it; anything else closes that run and opens a new one, so a run never spans a second
    noted with another reason. The commands that could break it: `note_span` with a reason that
    is not a `SECOND_REASONS` member or `ROW` (refused, `ValueError`), and `take` (the caller
    must write what it returns, or keep it: it is gone from here).
    """

    __slots__ = ("_closed", "_open")

    def __init__(self) -> None:
        self._open: dict[str, SecondsRun] = {}
        self._closed: list[SecondsRun] = []

    def note(self, instrument_id: str, second: int, reason: str) -> None:
        """Note one second's verdict: `ROW` or a `SECOND_REASONS` reason."""
        self.note_span(instrument_id, second, second, reason)

    def note_span(self, instrument_id: str, first_s: int, last_s: int, reason: str) -> None:
        """Note seconds `first_s..last_s` (inclusive), all with `reason`; empty when inverted."""
        if reason != ROW and reason not in SECOND_REASONS:
            raise ValueError(f"not a coverage reason: {reason!r}")
        if first_s > last_s:
            return
        run = self._open.get(instrument_id)
        if run is not None and run.reason == reason and run.last_s + 1 == first_s:
            self._open[instrument_id] = dataclasses.replace(run, last_s=last_s)
            return
        if run is not None and run.reason != ROW:
            self._closed.append(run)
        self._open[instrument_id] = SecondsRun(instrument_id, reason, first_s, last_s)

    def take(self) -> list[SecondsRun]:
        """Return every closed and open run (rows left out) and forget them all."""
        runs = [*self._closed, *(run for run in self._open.values() if run.reason != ROW)]
        self._closed = []
        self._open = {}
        return runs
