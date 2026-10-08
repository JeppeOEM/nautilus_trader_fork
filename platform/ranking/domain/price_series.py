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
In-memory long-window price series (Story 13.2) -- replaced the ranking loop's old recurring
catalog re-scan, the root cause of nifelheim's OOM-restart loop (platform/CLAUDE.md DATA-02
incident, 2026-09-11). Moved into the ranking context in Story 25.2.

Per-instrument fixed-capacity numpy ring buffers of (ts_event_ns, close_price), fed incrementally
from the live `snapshots:raw` ingest (`RankingBoard.ingest`), plus a one-time lazy catalog backfill
per instrument (`RankingEngine.slow_loop_once` through the `PriceHistory` port). `stats()` calls
`ranking.domain.metrics.price_stats_from_series()` -- the one formula (SSOT-02, DATA-02).

Capacity is a hard, deterministic memory bound (MEM-02/03): the collector emits at most one
close_price per instrument per second, so `PRICE_LOOKBACK_HOURS * 3600` slots per instrument covers
the full lookback window regardless of arrival rate; `drop()` releases an aged-out instrument.
"""

import math
from decimal import Decimal
from typing import NamedTuple

import numpy as np

from ranking.domain.metrics import price_stats_from_series


# 25h covers the full 24h pct-change calc with a small buffer.
PRICE_LOOKBACK_HOURS: float = 25.0

# The window of `high_24h`/`low_24h`/`range_position_24h` (Story 33.4): the 24 h the names promise,
# cut from the 25 h the store keeps (the backfill margin), as `metrics.VOLATILITY_WINDOW_NS` is.
RANGE_WINDOW_NS: int = 24 * 3_600 * 1_000_000_000


class PricePoint(NamedTuple):
    """
    One archived traded second, as the `PriceHistory` port returns it (Story 33.4 added the exact
    columns): `price` is the decoded float close the price series has always held; `close` and
    `volume` (buy + sell size) are the same second's exact `Decimal`s, read from the stored integer
    units at the row's precisions -- the hourly traded volume and the mark-last basis read these,
    never a float (DATA-04).
    """

    ts_event: int
    price: float
    close: Decimal
    volume: Decimal


class PriceRange(NamedTuple):
    """The highest, lowest and newest trade close inside `RANGE_WINDOW_NS`, as stored (floats)."""

    high: float
    low: float
    last: float


class _RingBuffer:
    """
    Fixed-capacity circular buffer of (ts_event_ns, price) pairs.

    Private to this module -- PriceSeriesStore is the public surface. Backed by
    parallel numpy int64/float64 arrays (not a deque of tuples) to keep the ~29
    instrument * 90,000 slot steady-state footprint at the ~40MB the epic AC
    budgets, and to make the ascending-order read a cheap array slice/concatenate
    instead of a Python-level sort on every stats() call.
    """

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError(f"_RingBuffer capacity must be positive, got {capacity}")
        self._capacity = capacity
        self._ts = np.zeros(capacity, dtype=np.int64)
        self._px = np.zeros(capacity, dtype=np.float64)
        self._cursor = 0  # next write index
        self._count = 0  # min(total appends, capacity)
        self._last_ts: int | None = None  # most recently appended ts, for ordering checks

    @property
    def count(self) -> int:
        return self._count

    @property
    def last_ts(self) -> int | None:
        return self._last_ts

    def append(self, ts_event_ns: int, price: float) -> None:
        self._ts[self._cursor] = ts_event_ns
        self._px[self._cursor] = price
        self._cursor = (self._cursor + 1) % self._capacity
        self._count = min(self._count + 1, self._capacity)
        self._last_ts = ts_event_ns

    def ascending(self, cutoff_ns: int = 0) -> tuple[np.ndarray, np.ndarray]:
        """
        (ts, price) numpy arrays in ascending ts order, filtered to ts >= cutoff_ns.

        Never wrapped (count < capacity): the buffer is already in append order from
        index 0. Wrapped (count == capacity): the oldest entry sits at the current
        write cursor (about to be overwritten next), so the ascending order is
        [cursor:] followed by [:cursor] -- this also reduces to identity when
        cursor == 0 (buffer filled exactly once, never wrapped past the end).
        """
        if self._count < self._capacity:
            ts, px = self._ts[: self._count], self._px[: self._count]
        else:
            ts = np.concatenate((self._ts[self._cursor :], self._ts[: self._cursor]))
            px = np.concatenate((self._px[self._cursor :], self._px[: self._cursor]))
        mask = ts >= cutoff_ns
        return ts[mask], px[mask]


class PriceSeriesStore:
    """
    Per-instrument long-window (ts_event_ns, close_price) series, in memory.

    `backfill()` seeds an instrument's history exactly once (idempotency for
    "exactly once" is enforced by the caller, `InstrumentMetrics.backfilled` -- this
    method itself only knows how to merge, not how to dedupe repeat calls).
    `ingest()` appends live points as they stream in. `stats()` derives
    price/pct_1h/pct_24h/volatility via the shared `price_stats_from_series()`
    formula (SSOT-02).
    """

    def __init__(self, lookback_hours: float = PRICE_LOOKBACK_HOURS) -> None:
        self.lookback_ns: int = int(lookback_hours * 3_600 * 1_000_000_000)
        self._capacity: int = int(lookback_hours * 3600)
        self._buffers: dict[str, _RingBuffer] = {}

    def ingest(self, instrument_id: str, ts_event_ns: int, close_price: float | None) -> str | None:
        """
        No-op when close_price is None -- matches price_series()'s existing
        "seconds with no trade contribute nothing" rule.

        A point older than or equal to the buffer's last-appended ts is dropped rather
        than appended (DATA-02): the ring buffer's ascending() and every stat derived
        from it assume append order == chronological order, so an out-of-order or
        duplicate ts (a Redis redelivery, a race) would otherwise silently corrupt
        pct_change/volatility with no error raised. The drop is returned as a detail for
        the caller to ledger (DATA-07); None when the point was kept or had no price.
        """
        if close_price is None:
            return None
        buf = self._buffers.setdefault(instrument_id, _RingBuffer(self._capacity))
        if buf.last_ts is not None and ts_event_ns <= buf.last_ts:
            return (
                f"{instrument_id}: out-of-order price point DROPPED "
                f"(ts_event_ns={ts_event_ns} <= last_ts={buf.last_ts})"
            )
        buf.append(ts_event_ns, close_price)
        return None

    def backfill(self, instrument_id: str, series: list[tuple[int, float]]) -> list[str]:
        """
        Seed an instrument's buffer from a Parquet-read historical series.

        The series is validated first (`_validated_series`): sorted by ts, with every
        non-finite/non-positive price and every duplicate ts dropped -- the ring buffer's
        ascending() and every stat derived from it assume strictly ascending, positive
        points, the same invariant ingest() holds for live points.

        Handles the backfill/live-ingest race (Design Notes): live points may
        already be in the buffer (appended via ingest() while this series' Parquet
        read was in flight). Those live points are kept verbatim; only historical
        points strictly older than the earliest already-buffered live point are
        prepended -- no duplication, no clobbered live data. If the buffer is
        empty/nonexistent, seed it directly from the validated series.

        Returns each validation drop kind and a live/Parquet disagreement as a detail for the
        caller to ledger (DATA-07); empty when neither occurred. Known limit: a valid catalog point
        at or after the earliest live point with no live point at its ts (a `snapshots:raw`
        message this process missed) is left out by the merge without a detail; upgrade path:
        report those as a live-gap detail.
        """
        series, details = _validated_series(instrument_id, series)
        if not series:
            return details
        existing = self._buffers.get(instrument_id)
        if existing is None or existing.count == 0:
            buf = _RingBuffer(self._capacity)
            for ts, price in series:
                buf.append(ts, price)
            self._buffers[instrument_id] = buf
            return details

        live_ts, live_px = existing.ascending()
        earliest_live_ts = int(live_ts[0])
        historical = [(ts, price) for ts, price in series if ts < earliest_live_ts]
        live_by_ts = dict(zip(live_ts.tolist(), live_px.tolist(), strict=True))
        # Overlap is assumed redundant (live wins); a disagreement would be a data-integrity
        # problem that must not be resolved silently (DATA-02).
        mismatched = [ts for ts, price in series if live_by_ts.get(ts, price) != price]

        merged = _RingBuffer(self._capacity)
        for ts, price in historical:
            merged.append(ts, price)
        for ts, price in zip(live_ts.tolist(), live_px.tolist(), strict=True):
            merged.append(ts, price)
        self._buffers[instrument_id] = merged
        if mismatched:
            details.append(
                f"{instrument_id}: {len(mismatched)} live/Parquet price mismatches at the same ts "
                f"(first ts={mismatched[0]}); kept live"
            )
        return details

    def stats(self, instrument_id: str, now_ns: int) -> dict:
        """
        price/pct_change_1h/pct_change_24h/volatility over the retained window,
        via the shared price_stats_from_series() formula (SSOT-02, DATA-02).
        """
        buf = self._buffers.get(instrument_id)
        if buf is None or buf.count == 0:
            return price_stats_from_series([])
        ts, px = buf.ascending(cutoff_ns=now_ns - self.lookback_ns)
        series = list(zip(ts.tolist(), px.tolist(), strict=True))
        return price_stats_from_series(series)

    def range_24h(self, instrument_id: str, now_ns: int) -> PriceRange | None:
        """
        Return the trade-close range over the `RANGE_WINDOW_NS` before `now_ns`; None with no close
        in it. Known limit: a series younger than 24 h (a fresh start over a short archive) gives
        the range of what it holds, not None -- the high/low of the hours seen; upgrade path: None
        until the series reaches the window's start, as `pct_change_24h` does.
        """
        buf = self._buffers.get(instrument_id)
        if buf is None or buf.count == 0:
            return None
        _ts, px = buf.ascending(cutoff_ns=now_ns - RANGE_WINDOW_NS)
        if len(px) == 0:
            return None
        return PriceRange(high=float(px.max()), low=float(px.min()), last=float(px[-1]))

    def drop(self, instrument_id: str) -> None:
        """Release an aged-out instrument's buffer (MEM-02); a later return starts empty."""
        self._buffers.pop(instrument_id, None)


def _validated_series(
    instrument_id: str,
    series: list[tuple[int, float]],
) -> tuple[list[tuple[int, float]], list[str]]:
    """
    Return `series` sorted by ts, with non-finite/non-positive prices and duplicate ts removed,
    plus one detail per drop kind (count and first ts) for the caller to ledger (DATA-07).

    Bad prices go first, so a NaN never shadows a valid point at the same ts; of the remaining
    duplicates the first in catalog order is kept (the sort is stable).
    """
    ordered = sorted(series, key=lambda point: point[0])
    details: list[str] = []
    bad_ts = [ts for ts, price in ordered if not math.isfinite(price) or price <= 0]
    if bad_ts:
        details.append(
            f"{instrument_id}: {len(bad_ts)} non-finite/non-positive catalog prices DROPPED "
            f"from the backfill (first ts={bad_ts[0]})"
        )
    valid = [(ts, price) for ts, price in ordered if math.isfinite(price) and price > 0]
    kept: list[tuple[int, float]] = []
    duplicate_ts: list[int] = []
    conflicting = 0
    for ts, price in valid:
        if kept and kept[-1][0] == ts:
            duplicate_ts.append(ts)
            conflicting += price != kept[-1][1]
            continue
        kept.append((ts, price))
    if duplicate_ts:
        # A conflicting duplicate is two archived prices for one second -- a data-integrity fault,
        # not file overlap -- so it is counted apart from the harmless identical ones (DATA-02).
        details.append(
            f"{instrument_id}: {len(duplicate_ts)} duplicate-ts catalog prices DROPPED from the "
            f"backfill ({conflicting} with a different price), first kept "
            f"(first ts={duplicate_ts[0]})"
        )
    return kept, details
