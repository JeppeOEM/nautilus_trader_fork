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
The capture hot path's per-flush figures (Story 28.1, audit D-07/D-10/D-136): the window's
length, the ingest queue's peak depth, the messages processed, the sample loop's wake-up lag (max
and nearest-rank p99) and the catalog writes (count, the last one's wall time and the slowest's).
`CaptureService` feeds the window and reports it once per periodic flush (a `hotpath:` INFO
line and the `capture:hotpath` record, `docs/DATA_DICTIONARY.md` §1.23). Pure arithmetic, no clock
and no I/O, so it is tested apart from the service.
"""

from dataclasses import dataclass


_NS_PER_MS = 1_000_000


def nearest_rank_p99(samples: list[int]) -> int | None:
    """Return the 99th percentile by nearest rank (`ceil(0.99 * n)`-th smallest), None if empty."""
    if not samples:
        return None
    rank = (99 * len(samples) + 99) // 100  # ceil(0.99 n) in exact integers
    return sorted(samples)[rank - 1]


def _ms(ns: int | None) -> float | None:
    return None if ns is None else round(ns / _NS_PER_MS, 3)


@dataclass(frozen=True, slots=True)
class HotPathReport:
    """
    One flush window's figures; the window in seconds, the lag and write times in milliseconds,
    None when unmeasured. At the 60 s flush a window holds ~60 wakes, and the nearest-rank p99 of
    60 samples is their maximum: `lag_p99_ms` departs from `lag_max_ms` only from 100 wakes on
    (`ceil(0.99 * 100)` = 99, the second largest).
    """

    window_s: float
    queue_depth_max: int
    messages_processed: int
    wakes: int
    lag_max_ms: float | None
    lag_p99_ms: float | None
    writes: int
    write_data_ms: float | None
    write_data_max_ms: float | None

    def to_dict(self) -> dict[str, int | float | None]:
        return {
            "window_s": self.window_s,
            "queue_depth_max": self.queue_depth_max,
            "messages_processed": self.messages_processed,
            "wakes": self.wakes,
            "lag_max_ms": self.lag_max_ms,
            "lag_p99_ms": self.lag_p99_ms,
            "writes": self.writes,
            "write_data_ms": self.write_data_ms,
            "write_data_max_ms": self.write_data_max_ms,
        }

    def log_text(self) -> str:
        return " ".join(f"{key}={value}" for key, value in self.to_dict().items())


class HotPathWindow:
    """
    The figures of one flush window. Invariant: every figure `take` reports covers exactly the
    wakes and writes noted since the previous `take`, and the samples it holds are bounded by that
    window's wakes (one lag per sample-loop wake, ~60 at the 60 s flush) and batch writes (one per
    `(type, instrument)` batch) -- never carried into the next report. Violated by a `take` that
    does not reset, or by noting a lag anywhere but once per loop wake.

    Known limit: the bound is the window's, so a flush that hangs (a stuck disk) lets the lag list
    grow one int per second until it returns. Upgrade path: a running max plus a bounded
    reservoir, if a hang of hours ever matters more than the stuck flush itself.
    """

    def __init__(self) -> None:
        self._lags_ns: list[int] = []
        self._writes_ns: list[int] = []

    def note_lag(self, lag_ns: int) -> None:
        self._lags_ns.append(lag_ns)

    def note_write(self, elapsed_ns: int) -> None:
        """Note one successful catalog write's wall time (one per batch, a few per flush)."""
        self._writes_ns.append(elapsed_ns)

    def take(self, window_ns: int, queue_depth_max: int, messages_processed: int) -> HotPathReport:
        """Return the report of a window `window_ns` long and start a new, empty window."""
        lags, self._lags_ns = self._lags_ns, []
        writes, self._writes_ns = self._writes_ns, []
        return HotPathReport(
            window_s=round(window_ns / 1e9, 3),
            queue_depth_max=queue_depth_max,
            messages_processed=messages_processed,
            wakes=len(lags),
            lag_max_ms=_ms(max(lags)) if lags else None,
            lag_p99_ms=_ms(nearest_rank_p99(lags)),
            writes=len(writes),
            write_data_ms=_ms(writes[-1]) if writes else None,
            write_data_max_ms=_ms(max(writes)) if writes else None,
        )
