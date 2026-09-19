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
In-memory long-window price series (Story 13.2) -- replaces `_slow_loop_task`'s old
recurring `metrics_computer.compute_all()` Parquet re-scan, the root cause of
nifelheim's OOM-restart loop (troll/CLAUDE.md DATA-02 incident, 2026-09-11).

Per-instrument fixed-capacity numpy ring buffers of (ts_event_ns, close_price), fed
incrementally from the same live `snapshots:raw` ingest `ranking_engine.engine`
already processes, plus a one-time lazy Parquet backfill per instrument (see
`engine._slow_loop_task`). `stats()` calls `ml_signals.catalog_stats`'s
`price_stats_from_series()` -- the exact same formula the old catalog-backed path
used -- so this in-memory path is guaranteed numerically identical, not a second,
independently-written computation (SSOT-02, DATA-02).

Capacity is a hard, deterministic memory bound (MEM-02/03): the collector emits at
most one close_price per instrument per second, so `PRICE_LOOKBACK_HOURS * 3600`
slots per instrument covers the full lookback window regardless of arrival rate.
"""

import logging

import numpy as np

from ml_signals import catalog_stats
from ml_signals.metrics_computer import PRICE_LOOKBACK_HOURS

logger = logging.getLogger(__name__)


class _RingBuffer:
    """Fixed-capacity circular buffer of (ts_event_ns, price) pairs.

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
        """(ts, price) numpy arrays in ascending ts order, filtered to ts >= cutoff_ns.

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
    """Per-instrument long-window (ts_event_ns, close_price) series, in memory.

    `backfill()` seeds an instrument's history exactly once (idempotency for
    "exactly once" is enforced by the caller, engine.py's `_BACKFILLED` set -- this
    method itself only knows how to merge, not how to dedupe repeat calls).
    `ingest()` appends live points as they stream in. `stats()` derives
    price/pct_1h/pct_24h/volatility via the shared `price_stats_from_series()`
    formula (SSOT-02).
    """

    def __init__(self, lookback_hours: float = PRICE_LOOKBACK_HOURS) -> None:
        self.lookback_ns: int = int(lookback_hours * 3_600 * 1_000_000_000)
        self._capacity: int = int(lookback_hours * 3600)
        self._buffers: dict[str, _RingBuffer] = {}

    def ingest(self, instrument_id: str, ts_event_ns: int, close_price: float | None) -> None:
        """No-op when close_price is None -- matches price_series()'s existing
        "seconds with no trade contribute nothing" rule.

        A point older than or equal to the buffer's last-appended ts is dropped and
        logged rather than appended (DATA-02): the ring buffer's ascending() and every
        stat derived from it assume append order == chronological order, so an
        out-of-order or duplicate ts (a Redis redelivery, a race) would otherwise
        silently corrupt pct_change/volatility with no error raised.
        """
        if close_price is None:
            return
        buf = self._buffers.setdefault(instrument_id, _RingBuffer(self._capacity))
        if buf.last_ts is not None and ts_event_ns <= buf.last_ts:
            logger.warning(
                "Out-of-order price point dropped for %s: ts_event_ns=%d <= last_ts=%d",
                instrument_id, ts_event_ns, buf.last_ts,
            )
            return
        buf.append(ts_event_ns, close_price)

    def backfill(self, instrument_id: str, series: list[tuple[int, float]]) -> None:
        """Seed an instrument's buffer from a Parquet-read historical series.

        Handles the backfill/live-ingest race (Design Notes): live points may
        already be in the buffer (appended via ingest() while this series' Parquet
        read was in flight). Those live points are kept verbatim; only historical
        points strictly older than the earliest already-buffered live point are
        prepended -- no duplication, no clobbered live data. If the buffer is
        empty/nonexistent, seed it directly from `series`.
        """
        if not series:
            return
        existing = self._buffers.get(instrument_id)
        if existing is None or existing.count == 0:
            buf = _RingBuffer(self._capacity)
            for ts, price in series:
                buf.append(ts, price)
            self._buffers[instrument_id] = buf
            return

        live_ts, live_px = existing.ascending()
        earliest_live_ts = int(live_ts[0])
        historical = [(ts, price) for ts, price in series if ts < earliest_live_ts]
        live_by_ts = dict(zip(live_ts.tolist(), live_px.tolist(), strict=True))
        # Overlap is assumed redundant (live wins); a disagreement would be a data-integrity
        # problem that must not be resolved silently (DATA-02).
        mismatched = [ts for ts, price in series if live_by_ts.get(ts, price) != price]
        if mismatched:
            logger.warning(
                "%s: %d live/Parquet price mismatches at same ts (first ts=%d); keeping live",
                instrument_id, len(mismatched), mismatched[0],
            )

        merged = _RingBuffer(self._capacity)
        for ts, price in historical:
            merged.append(ts, price)
        for ts, price in zip(live_ts.tolist(), live_px.tolist(), strict=True):
            merged.append(ts, price)
        self._buffers[instrument_id] = merged

    def stats(self, instrument_id: str, now_ns: int) -> dict:
        """price/pct_change_1h/pct_change_24h/volatility over the retained window,
        via the shared price_stats_from_series() formula (SSOT-02, DATA-02).
        """
        buf = self._buffers.get(instrument_id)
        if buf is None or buf.count == 0:
            return catalog_stats.price_stats_from_series([])
        ts, px = buf.ascending(cutoff_ns=now_ns - self.lookback_ns)
        series = list(zip(ts.tolist(), px.tolist(), strict=True))
        return catalog_stats.price_stats_from_series(series)
