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
The two clocks and the one skew bound (DDD spine AD-D3, AD-D7, AD-D18).

Every row the platform stores carries two clocks: `ts_event` (venue time) and `ts_init` (our
arrival/sampling time). Catalog file names span `ts_init`; readers and the rebuild select rows by
`ts_event`. They meet only through one number:

Invariant: `MAX_TS_INIT_SKEW_NS` is the largest `ts_init - ts_event` any writer may produce for a
row (the reconnect trade backfill refuses older trades, `Collector._apply_backfill`), and every
other skew-related margin -- the readers' file-span widening (`READ_SPAN_MARGIN_NS`), the
rebuild's `ts_init` window, the sampler's catch-up and hold-back bounds -- is defined as, or
asserted to be, at most it (`kernel/tests/test_clocks.py`, `platform/tests/test_skew_constants.py`).
Change it only together with every consumer: it couples capture's carry/backfill rules with
archive's rebuild/prune windows.

`CatalogFileSpan` is the only parse of a catalog file-name stem (the former
`catalog_stats._stamp_to_ns`).
"""

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path


NS_PER_MS = 1_000_000
NS_PER_S = 1_000_000_000
NS_PER_DAY = 86_400 * NS_PER_S

# The largest `ts_init - ts_event` any writer may produce (formerly `archive_gaps.ARRIVAL_MARGIN_NS`):
# the live age filter (`stale_trade_seconds`, 10 s) bounds it for live trades, and the reconnect
# trade backfill refuses -- and counts -- any unseen REST trade older than this. The rebuild's
# `ts_init` query window, the quarantine marker and the prune gate's previous-day check use it.
MAX_TS_INIT_SKEW_NS = 300 * NS_PER_S

# How far the read helpers widen a file's `ts_init` span, on both sides, when choosing which files
# can hold rows of a `ts_event` window (the exact `ts_event` filter then decides). Every live row's
# skew is far below it: a venue-timed row trails by at most catch-up + 1 s + hold-back, and a
# venue clock can run ahead by `_VENUE_AHEAD_NS` (asserted in `platform/tests/test_skew_constants`).
# Known limit: a backfilled trade's second row is not re-sampled, so a snapshot row's skew never
# approaches `MAX_TS_INIT_SKEW_NS`; if one ever could, this margin must grow to that bound (more
# files opened per read). Asserted <= `MAX_TS_INIT_SKEW_NS`.
READ_SPAN_MARGIN_NS = 60 * NS_PER_S


@dataclass(frozen=True)
class TwoClocks:
    """A row's venue clock (`ts_event`) and arrival clock (`ts_init`), both UNIX ns."""

    ts_event: int
    ts_init: int

    @property
    def skew_ns(self) -> int:
        """`ts_init - ts_event`: negative when the venue clock runs ahead of ours."""
        return self.ts_init - self.ts_event

    def within_skew(self, bound_ns: int = MAX_TS_INIT_SKEW_NS) -> bool:
        """Return whether the arrival trails the venue time by no more than `bound_ns`."""
        return self.skew_ns <= bound_ns


def _stamp_ns(stamp: str) -> int:
    """`2026-06-30T17-17-34-103475440Z` (a catalog filename bound) -> epoch ns; else `ValueError`."""
    date, _, clock = stamp.rstrip("Z").partition("T")
    hour, minute, second, nanos = clock.split("-")
    moment = datetime.strptime(f"{date} {hour}:{minute}:{second}", "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007 (UTC set below)
    return int(moment.replace(tzinfo=UTC).timestamp()) * NS_PER_S + int(nanos)


@dataclass(frozen=True)
class CatalogFileSpan:
    """
    The `[start_ns, end_ns]` `ts_init` span a `ParquetDataCatalog` file name records
    (`<start>_<end>.parquet`, each bound `YYYY-MM-DDTHH-MM-SS-NNNNNNNNNZ`), inclusive.
    """

    start_ns: int
    end_ns: int

    @classmethod
    def from_stem(cls, stem: str) -> "CatalogFileSpan":
        """Parse a file-name stem; `ValueError` for a name the catalog did not write."""
        first, _, last = stem.partition("_")
        return cls(_stamp_ns(first), _stamp_ns(last))

    @classmethod
    def from_path(cls, path: str | Path) -> "CatalogFileSpan":
        return cls.from_stem(Path(path).stem)

    def covers(self, ts_event: int, margin_ns: int = MAX_TS_INIT_SKEW_NS) -> bool:
        """
        Return whether the file can hold a row with this `ts_event`: its `ts_init` lies within
        `margin_ns` of the span on either side (a venue clock may also run ahead of ours).
        """
        return self.start_ns - margin_ns <= ts_event <= self.end_ns + margin_ns

    def overlaps(self, lo_ns: int, hi_ns: int, margin_ns: int = 0) -> bool:
        """Return whether the span, widened by `margin_ns` both ways, meets `[lo_ns, hi_ns]`."""
        return self.end_ns >= lo_ns - margin_ns and self.start_ns <= hi_ns + margin_ns
