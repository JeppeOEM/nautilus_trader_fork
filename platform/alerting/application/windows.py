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
The engine's per-instrument time windows (Story 33.8 review): exact windowed sums and an
as-of series, each answering a query in O(log n) instead of rescanning the window per sample.

Both keep their entries in time order in plain lists with a logical `head` (the oldest retained
entry): trimming advances `head` (O(1) per evicted entry) and the evicted prefix is deleted in one
slice once it is at least half the list, so a window costs amortised O(1) per append and its
memory stays proportional to what it retains (MEM-01).
"""

from bisect import bisect_right
from collections.abc import Iterator
from collections.abc import Sequence
from decimal import Decimal


# Compact once the evicted prefix is this long and at least half the list (amortised O(1)).
_COMPACT_MIN = 64

Amount = tuple[int, int]  # (units, precision): units * 10^-precision


class SumWindow:
    """
    Exact sums of integer amounts over a time window: `total(start, end)` is the sum of every
    retained entry with `start < ts <= end`, per component, as `(units, precision)`.

    Invariant (exact, never a float): each component is held as prefix sums of integer units at
    the finest precision that component has seen (a finer amount rescales the held sums once, an
    exact integer multiplication), so a window sum is the difference of two prefix sums -- equal to
    summing its entries one by one, at any window width and any end time.

    Invariant (one entry per key): an entry inserted with a `key` already retained is refused, and
    a key is forgotten when its entry is trimmed, so a redelivered event is counted once while it
    is in the window.
    """

    def __init__(self, components: int) -> None:
        self._ts: list[int] = []
        self._keys: list[str | None] = []
        self._cum: list[list[int]] = [[] for _ in range(components)]
        self._base: list[int] = [0] * components  # each prefix sum just before list index 0
        self._precision: list[int] = [0] * components
        self._ids: set[str] = set()
        self._head = 0

    def __len__(self) -> int:
        return len(self._ts) - self._head

    def __contains__(self, key: object) -> bool:
        return key in self._ids

    @property
    def newest(self) -> int | None:
        """The newest retained entry's time (the window's horizon is measured from it)."""
        return self._ts[-1] if len(self) else None

    def amounts(self) -> Iterator[tuple[int, list[Amount]]]:
        """Yield each retained entry's `(ts, [(units, precision) per component])`, oldest first."""
        for i in range(self._head, len(self._ts)):
            yield (
                self._ts[i],
                [
                    (self._prefix(c, i + 1) - self._prefix(c, i), self._precision[c])
                    for c in range(len(self._cum))
                ],
            )

    def insert(self, ts: int, amounts: Sequence[Amount], key: str | None = None) -> bool:
        """
        Add one entry in time order; False (nothing added) when `key` is already retained. An
        entry older than the newest is inserted at its place (O(entries after it)).
        """
        if key is not None and key in self._ids:
            return False
        index = bisect_right(self._ts, ts, lo=self._head)
        self._ts.insert(index, ts)
        self._keys.insert(index, key)
        for c, (units, precision) in enumerate(amounts):
            self._insert_amount(c, index, self._at(c, units, precision))
        if key is not None:
            self._ids.add(key)
        return True

    def trim(self, horizon: int) -> None:
        """Drop every entry with `ts <= horizon`."""
        while self._head < len(self._ts) and self._ts[self._head] <= horizon:
            key = self._keys[self._head]
            if key is not None:
                self._ids.discard(key)
            self._head += 1
        self._compact()

    def total(self, start: int, end: int) -> list[Amount]:
        """Return each component's exact sum over the retained entries with `start < ts <= end`."""
        low = bisect_right(self._ts, start, lo=self._head)
        high = max(low, bisect_right(self._ts, end, lo=self._head))
        return [
            (self._prefix(c, high) - self._prefix(c, low), self._precision[c])
            for c in range(len(self._cum))
        ]

    def _prefix(self, component: int, index: int) -> int:
        """Return the component's sum of every list entry before `index`."""
        return self._cum[component][index - 1] if index > 0 else self._base[component]

    def _at(self, component: int, units: int, precision: int) -> int:
        """Return `units` at the component's held precision, rescaling the held sums if finer."""
        held = self._precision[component]
        if precision > held:
            factor = 10 ** (precision - held)
            self._cum[component] = [value * factor for value in self._cum[component]]
            self._base[component] *= factor
            self._precision[component] = precision
            return units
        return units * 10 ** (held - precision)

    def _insert_amount(self, component: int, index: int, units: int) -> None:
        cum = self._cum[component]
        cum.insert(index, self._prefix(component, index) + units)
        for later in range(index + 1, len(cum)):
            cum[later] += units

    def _compact(self) -> None:
        if self._head < _COMPACT_MIN or self._head * 2 < len(self._ts):
            return
        head = self._head
        for c, cum in enumerate(self._cum):
            self._base[c] = cum[head - 1]
            del cum[:head]
        del self._ts[:head]
        del self._keys[:head]
        self._head = 0


class AsOfSeries:
    """
    A strictly time-ordered series of exact values, asked for the newest value at or before a
    time (`at_or_before`, a bisect).

    Invariant (a window's base survives the trim): `trim(horizon)` keeps the newest entry at or
    before `horizon` -- the base of a window starting there -- and drops only what is older.
    """

    def __init__(self) -> None:
        self._ts: list[int] = []
        self._values: list[Decimal] = []
        self._head = 0

    def __iter__(self) -> Iterator[tuple[int, Decimal]]:
        return zip(self._ts[self._head :], self._values[self._head :], strict=True)

    def append(self, ts: int, value: Decimal) -> bool:
        """Append a newer entry; False (ignored) for a repeat or an older one."""
        if len(self._ts) > self._head and ts <= self._ts[-1]:
            return False
        self._ts.append(ts)
        self._values.append(value)
        return True

    def at_or_before(self, ts: int) -> Decimal | None:
        index = bisect_right(self._ts, ts, lo=self._head) - 1
        return self._values[index] if index >= self._head else None

    def trim(self, horizon: int) -> None:
        while self._head + 1 < len(self._ts) and self._ts[self._head + 1] <= horizon:
            self._head += 1
        if self._head >= _COMPACT_MIN and self._head * 2 >= len(self._ts):
            del self._ts[: self._head]
            del self._values[: self._head]
            self._head = 0
