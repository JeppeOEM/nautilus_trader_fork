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
`RankingBoard` -- the ranking aggregate (DDD spine AD-D10, Story 25.2; architecture AD-9).

It replaces the twelve module globals the pre-Story-25.2 ranking engine module kept: the ranking
mode, the freshness stamps, the live indicator trackers, the rolling windows, the price series, the
slow-loop metrics and the per-venue USD volumes are all state of one board instance, built by
`ranking/__main__.py` and driven by `ranking.application.engine.RankingEngine`.

Pure: no clock (every command takes `now_ns`/`received_ns`), no I/O, no ledger -- a command that
must be ledgered returns what to ledger and the engine records it.
"""

import math
import statistics
from collections import deque
from collections.abc import Callable
from collections.abc import Mapping

from kernel.indicators import OFI_GAP_NS
from kernel.indicators import MultiLevelOBI
from kernel.indicators import MultiLevelOFI
from kernel.indicators import microprice as calc_microprice
from kernel.indicators import mid_price as calc_mid_price
from kernel.indicators import spread as calc_spread
from kernel.indicators import trade_aggregates
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.venues import MalformedInstrumentId
from kernel.venues import base_symbol
from kernel.venues import market_kind
from kernel.venues import venue_kind
from kernel.venues import venue_of

from ranking.domain.metrics import pct_change_from
from ranking.domain.price_series import PriceSeriesStore
from ranking.domain.values import RankingMode
from ranking.domain.values import VolumeReading
from ranking.domain.volatility import VolatilityTracker


# >30s of silence on a liquid instrument is a pipeline failure, not "quiet market" (OBS-01):
# past this an instrument leaves the ranks. Compared against arrival time, never ts_event (DATA-01).
STALE_NS: int = 30_000_000_000

# Instruments stale for less than this are listed in `stale_instrument_ids`; past it their whole
# state is aged out (`age_out`), so an instrument that stopped being collected does not hold
# memory (MEM-02) or keep writing its last values to metrics.db (DATA-01) forever.
RECENTLY_STALE_WINDOW_NS: int = 3_600_000_000_000  # 1 hour

# Rolling window of decoded snapshots per instrument: cvd/avg_trade_size and the fast volatility.
ROLLING_WINDOW: int = 300

# volume_delta's own window, in snapshots -- deliberately shorter than CVD's full 300s window. A
# single-snapshot (1s) delta almost never lands on a second with a trade, so it read as +0.00 on
# every instrument (0/29 nonzero rows on the deployed dashboard, 2026-09-11).
VOLUME_DELTA_WINDOW: int = 60

# 3 missed slow-loop cycles: past this the cached pct/volatility/price are no longer current.
SLOW_METRICS_MAX_AGE_NS: int = 3 * 60 * 1_000_000_000

# `venue_markets`'s key for a volume id with no `.VENUE` suffix: no venue's message may carry it,
# and the engine ledgers every id under it (Story 29.5).
UNPARSEABLE_VENUE: str = ""


class InstrumentMetrics:
    """
    One instrument's live state: its freshness, its indicator trackers and its rolling window.

    Invariant: exactly one set of stateful trackers per instrument, fed only by
    `RankingBoard.ingest` (SSOT-02) -- every published per-tick field of the instrument, and its
    `metrics.db` ofi/microprice/spread, is read from this one set, never from a second copy.
    """

    def __init__(self, last_seen_ns: int) -> None:
        self.last_seen_ns = last_seen_ns
        self.last_fed_ns: int | None = None
        self.ofi_z = MultiLevelOFI(levels=10, window=50, zscore_window=3600)
        self.ofi_raw = {n: MultiLevelOFI(levels=n, window=300) for n in (3, 5, 10)}
        self.obi = {n: MultiLevelOBI(levels=n) for n in (3, 5, 10)}
        self.rolling: deque[dict] = deque(maxlen=ROLLING_WINDOW)
        self.backfilled = False
        self.slow: dict = {}

    def is_fresh(self, now_ns: int) -> bool:
        return (now_ns - self.last_seen_ns) <= STALE_NS

    def feed_book(self, snap: DydxSecondSnapshot) -> None:
        """Feed the OFI/OBI trackers, clearing OFI's previous tick across a reconnect gap."""
        gapped = self.last_fed_ns is not None and (snap.ts_event - self.last_fed_ns) > OFI_GAP_NS
        book = (snap.bid_prices, snap.bid_sizes, snap.ask_prices, snap.ask_sizes)
        for ofi in (self.ofi_z, *self.ofi_raw.values()):
            if gapped:
                ofi.clear_prev_state()
            ofi.update_raw(*book)
        if snap.bid_sizes and snap.ask_sizes:
            for obi in self.obi.values():
                obi.update_raw(snap.bid_sizes, snap.ask_sizes)
        self.last_fed_ns = snap.ts_event

    def fast_metrics(self) -> dict:
        """
        Return the live-tick fields of a rank entry: a pure read of the trackers and the rolling window.

        "volume_delta" is buy-sell volume summed over the last VOLUME_DELTA_WINDOW snapshots. With
        no snapshot held yet, `cvd` and `volume_delta` are None -- an empty window has no flow to
        report, never a measured zero (Story 31.3) -- and the counts are the empty sums, 0.
        """
        snapshots = list(self.rolling)
        latest = snapshots[-1] if snapshots else None
        buy_vol, sell_vol, buy_cnt, sell_cnt = trade_aggregates(snapshots)
        total_cnt = buy_cnt + sell_cnt
        recent = snapshots[-VOLUME_DELTA_WINDOW:]
        recent_buy, recent_sell, _, _ = trade_aggregates(recent)
        mid = calc_mid_price(latest) if latest is not None else None
        microprice_value = calc_microprice(latest) if latest is not None else None
        return {
            "ofi_10_z": _value(self.ofi_z),
            "ofi_3": _value(self.ofi_raw[3]),
            "ofi_5": _value(self.ofi_raw[5]),
            "ofi_10": _value(self.ofi_raw[10]),
            "obi_3": _value(self.obi[3]),
            "obi_5": _value(self.obi[5]),
            "obi_10": _value(self.obi[10]),
            "microprice": microprice_value,
            "microprice_lean": (
                microprice_value - mid if microprice_value is not None and mid is not None else None
            ),
            "spread": calc_spread(latest) if latest is not None else None,
            "cvd": (buy_vol - sell_vol) if snapshots else None,
            "volume_delta": (recent_buy - recent_sell) if snapshots else None,
            "buy_count": buy_cnt,
            "sell_count": sell_cnt,
            "avg_trade_size": (buy_vol + sell_vol) / total_cnt if total_cnt > 0 else None,
            "volatility_fast": _fast_volatility(snapshots),
            "price": mid,
        }

    def book_metrics(self) -> dict:
        """
        metrics.db's historical ofi/microprice/spread columns, from the same live trackers the
        rank entry reads (SSOT-02). "ofi" is the raw ofi_5 -- the closest signal to the old
        top-of-book OrderFlowImbalance(window=5), not the z-scored ofi_10_z.
        """
        fast = self.fast_metrics()
        return {"ofi": fast["ofi_5"], "microprice": fast["microprice"], "spread": fast["spread"]}


def _value(ind: MultiLevelOFI | MultiLevelOBI) -> float | None:
    return ind.value if ind.initialized else None


def _venue_or_unparseable(instrument_id: str) -> str:
    try:
        return venue_of(instrument_id)
    except MalformedInstrumentId:
        return UNPARSEABLE_VENUE


def _fast_volatility(snapshots: list[dict]) -> float | None:
    """
    Fast, 300-point live-tick volatility -- deliberately distinct from volatility_score
    (VolatilityTracker's 3600s cross-sectional stdev) and the catalog-derived "volatility".
    """
    mids = [m for m in (calc_mid_price(s) for s in snapshots) if m is not None]
    rets = [(mids[i] - mids[i - 1]) / mids[i - 1] for i in range(1, len(mids))]
    return statistics.stdev(rets) if len(rets) >= 2 else None


class RankingsPublisher:
    """
    Decides when to publish rankings:live, per AD-9's change+heartbeat discipline.

    Invariant: a message is published when the ranks or the mode changed, or when
    `heartbeat_seconds` passed since the last publish -- and at no other time. The heartbeat
    timer resets on every publish, so a burst of change publishes causes no redundant heartbeat.
    """

    def __init__(self, heartbeat_seconds: float, now_fn: Callable[[], float]) -> None:
        self._heartbeat_seconds = heartbeat_seconds
        self._now_fn = now_fn
        self._last_published_at: float | None = None
        self._last_ranks_key: tuple | None = None
        self._last_mode: str | None = None

    @staticmethod
    def _ranks_key(ranks: list[dict]) -> tuple:
        # A representative subset of the live-tick fields: spread/cvd/microprice/price/ofi_10_z
        # all derive from the same book/trade data, so if none of these changed nothing did.
        return tuple(
            (
                r["instrument_id"],
                r["rank"],
                r.get("ofi_10_z"),
                r.get("spread"),
                r.get("cvd"),
                r.get("microprice"),
                r.get("price"),
            )
            for r in ranks
        )

    def should_publish(self, ranks: list[dict], mode: str) -> bool:
        if self._last_published_at is None:
            return True
        if self._ranks_key(ranks) != self._last_ranks_key or mode != self._last_mode:
            return True
        return (self._now_fn() - self._last_published_at) >= self._heartbeat_seconds

    def record_published(self, ranks: list[dict], mode: str) -> None:
        self._last_published_at = self._now_fn()
        self._last_ranks_key = self._ranks_key(ranks)
        self._last_mode = mode


class RankingBoard:
    """
    The ranking aggregate: the one global mode, one `InstrumentMetrics` per instrument, the price
    series, the cross-sectional volatility, the per-venue USD volumes and the publisher.

    Invariants (AD-9, Story 22.10), each held by the command named:
    - the mode is global and last-write-wins (`switch_mode`);
    - every rank row carries both `volume24h` and `volatility_score`; a row without a fresh USD
      volume is left out of volume mode and kept, `volume24h: None`, in volatility mode
      (`current_ranks`) -- never ranked on a fabricated 0 volume (DATA-01). Known limit: volatility
      mode sorts a row whose `volatility_score` is None (under two returns yet) as 0, i.e. last;
      the score itself is still published as None. Kept byte-identical with the pre-move engine;
      upgrade path: sort None rows after every scored row explicitly (a published order change);
    - a venue source's volumes count only while its last good poll is younger than
      `volume_max_age_ns` (`refresh_volumes`); a failed poll never erases the last good values;
    - an instrument is ranked only while fresh (`STALE_NS`, arrival time) and its state is dropped
      after `RECENTLY_STALE_WINDOW_NS` of silence (`age_out`).
    """

    def __init__(
        self,
        publisher: RankingsPublisher,
        volatility_lookback_seconds: int,
        volume_max_age_ns: int,
    ) -> None:
        self.publisher = publisher
        self._mode = RankingMode.VOLUME
        self._instruments: dict[str, InstrumentMetrics] = {}
        self._prices = PriceSeriesStore()
        self._volatility = VolatilityTracker(lookback_seconds=volatility_lookback_seconds)
        self._volume_max_age_ns = volume_max_age_ns
        self._venue_volumes: dict[str, tuple[int, dict[str, float]]] = {}
        self._volume_24h: dict[str, VolumeReading] = {}

    @property
    def mode(self) -> RankingMode:
        return self._mode

    @property
    def price_lookback_ns(self) -> int:
        return self._prices.lookback_ns

    def instrument_ids(self) -> list[str]:
        return list(self._instruments)

    # --- commands --------------------------------------------------------------------------

    def switch_mode(self, mode: RankingMode) -> None:
        self._mode = mode

    def ingest(self, snap: DydxSecondSnapshot, received_ns: int) -> list[str]:
        """
        Stamp freshness (arrival time) and feed every tracker from one decoded snapshot.

        A non-finite/non-positive close_price would poison every pct/volatility computation with
        no error raised, so it is treated as "no trade this second" (AD-2). A one-sided book feeds
        no book tracker (a thin book is legitimate); a mid <= 0 is not a market state, so it too
        feeds none. Returns every such drop (and an out-of-order price point) as a detail for the
        engine to ledger (DATA-07); empty when the snapshot was used whole.
        """
        iid = snap.instrument_id.value
        inst = self._instruments.get(iid)
        if inst is None:
            inst = self._instruments[iid] = InstrumentMetrics(received_ns)
        inst.last_seen_ns = received_ns
        dropped: list[str] = []
        close_price = snap.close_price
        if close_price is not None and (not math.isfinite(close_price) or close_price <= 0):
            dropped.append(f"{iid}: non-finite/non-positive close_price {close_price!r} DROPPED")
            close_price = None
        out_of_order = self._prices.ingest(iid, snap.ts_event, close_price)
        if out_of_order is not None:
            dropped.append(out_of_order)
        # The indicator functions take the decoded float view; the wire's integers stay in `snap`.
        row = snap.as_floats()
        mid = calc_mid_price(row)
        if mid is None:
            return dropped
        if mid <= 0:
            dropped.append(f"{iid}: non-positive mid {mid!r} DROPPED from the book trackers")
            return dropped
        self._volatility.update(iid, snap.ts_event, mid)
        inst.feed_book(snap)
        inst.rolling.append(row)
        return dropped

    def backfill(self, instrument_id: str, series: list[tuple[int, float]]) -> str | None:
        """
        Seed an instrument's price series from the catalog, exactly once per instrument.

        Returns a live/Parquet price disagreement for the engine to ledger (DATA-07), else None.
        """
        inst = self._instruments.get(instrument_id)
        if inst is None or inst.backfilled:
            return None
        return self._prices.backfill(instrument_id, series)

    def mark_backfilled(self, instrument_id: str) -> None:
        """Never retried, even after a failed read: a recurring catalog read is the OOM path."""
        inst = self._instruments.get(instrument_id)
        if inst is not None:
            inst.backfilled = True

    def unbackfilled_ids(self) -> list[str]:
        return [iid for iid, inst in self._instruments.items() if not inst.backfilled]

    def age_out(self, now_ns: int) -> list[str]:
        """
        Drop every instrument silent for longer than RECENTLY_STALE_WINDOW_NS; return them.

        Its whole state goes (indicators, price series, volatility window, backfilled flag): after
        an hour of silence all of it describes a book that no longer exists, and keeping it would
        grow memory with every instrument ever seen (MEM-02). An instrument that comes back starts
        fresh and is backfilled from the catalog once more -- one read per return, never a
        recurring one (the Story 13.2 rule is about retrying every cycle).
        """
        dead = [
            iid
            for iid, inst in self._instruments.items()
            if now_ns - inst.last_seen_ns > RECENTLY_STALE_WINDOW_NS
        ]
        for iid in dead:
            del self._instruments[iid]
            self._prices.drop(iid)
            self._volatility.drop(iid)
        return dead

    def record_volume_poll(self, source: str, volumes: Mapping[str, float], at_ns: int) -> None:
        """Replace `source`'s volumes whole, so a delisted coin drops out."""
        self._venue_volumes[source] = (at_ns, dict(volumes))

    def refresh_volumes(self, now_ns: int) -> list[tuple[str, int]]:
        """
        Rebuild the merged volume map from every source younger than volume_max_age_ns.

        Returns (source, age_ns) of each expired source: its rows leave volume mode this cycle.
        """
        merged: dict[str, VolumeReading] = {}
        expired: list[tuple[str, int]] = []
        for source, (fetched_at_ns, volumes) in self._venue_volumes.items():
            age_ns = now_ns - fetched_at_ns
            if age_ns > self._volume_max_age_ns:
                expired.append((source, age_ns))
                continue
            merged.update({iid: VolumeReading(v, fetched_at_ns) for iid, v in volumes.items()})
        self._volume_24h = merged
        return expired

    def venue_markets(self, now_ns: int) -> dict[str, list[str]]:
        """
        Return each venue's market ids (sorted), the union of its sources still younger than
        volume_max_age_ns -- `refresh_volumes`'s own age rule, so `markets:live` lists exactly the
        markets whose volumes count (Story 29.5). Bybit's linear and spot sources merge under
        `BYBIT`. A venue with no fresh source is absent, never listed from an expired poll
        (DATA-01). An id with no `.VENUE` suffix is grouped under `UNPARSEABLE_VENUE`, never
        dropped here: the board has no ledger, so the engine ledgers it (DATA-07).
        """
        by_venue: dict[str, set[str]] = {}
        for fetched_at_ns, volumes in self._venue_volumes.values():
            if now_ns - fetched_at_ns > self._volume_max_age_ns:
                continue
            for iid in volumes:
                by_venue.setdefault(_venue_or_unparseable(iid), set()).add(iid)
        return {venue: sorted(ids) for venue, ids in sorted(by_venue.items())}

    def missing_volume_ids(self, now_ns: int) -> list[str]:
        """Fresh instruments with no USD volume: exactly the rows volume mode leaves out."""
        return [
            iid
            for iid, inst in self._instruments.items()
            if inst.is_fresh(now_ns) and iid not in self._volume_24h
        ]

    def slow_rows(
        self,
        now_ns: int,
        price_1w: Mapping[str, float],
        price_1m: Mapping[str, float],
    ) -> list[dict]:
        """
        One metrics.db row per instrument (price/pct/volatility from the price series, book metrics
        from the live trackers), cached as each instrument's slow metrics for the rank entries.
        """
        rows = [self._slow_row(iid, now_ns, price_1w, price_1m) for iid in self._instruments]
        for row in rows:
            self._instruments[row["instrument_id"]].slow = row
        return rows

    def _slow_row(
        self,
        iid: str,
        now_ns: int,
        price_1w: Mapping[str, float],
        price_1m: Mapping[str, float],
    ) -> dict:
        stats = self._prices.stats(iid, now_ns)
        return {
            "ts": now_ns,
            "instrument_id": iid,
            "price": stats.get("price"),
            "pct_1h": stats.get("pct_change_1h"),
            "pct_24h": stats.get("pct_change_24h"),
            "pct_1w": pct_change_from(stats.get("price"), price_1w.get(iid)),
            "pct_1m": pct_change_from(stats.get("price"), price_1m.get(iid)),
            "volatility": stats.get("volatility"),
            **self._instruments[iid].book_metrics(),
        }

    # --- reads -----------------------------------------------------------------------------

    def current_ranks(self, now_ns: int) -> list[dict]:
        """
        Ordered ranks for every fresh instrument, sorted by the active mode's score descending.
        Both volume24h and volatility_score are present in every entry regardless of the mode.
        """
        rows = []
        for iid, inst in self._instruments.items():
            if not inst.is_fresh(now_ns):
                continue
            reading = self._volume_24h.get(iid)
            if reading is None and self._mode == RankingMode.VOLUME:
                continue  # left out of volume mode, counted by missing_volume_ids (DATA-01)
            rows.append(self._rank_row(iid, inst, reading, now_ns))
        if self._mode == RankingMode.VOLATILITY:
            rows.sort(key=lambda r: r["volatility_score"] or 0.0, reverse=True)
        else:
            rows.sort(key=lambda r: r["volume24h"], reverse=True)  # every row has a volume here
        return [{**r, "rank": i + 1} for i, r in enumerate(rows)]

    def _rank_row(
        self, iid: str, inst: InstrumentMetrics, reading: VolumeReading | None, now_ns: int
    ) -> dict:
        slow = inst.slow
        if now_ns - slow.get("ts", 0) > SLOW_METRICS_MAX_AGE_NS:
            slow = {}  # a stalled slow loop must read as a gap (None), not as live (DATA-01)
        row = {
            "instrument_id": iid,
            "venue": (venue := venue_of(iid)),
            "symbol": base_symbol(iid),
            "venue_kind": venue_kind(venue),
            "market": market_kind(iid),
            "volume24h": reading.value_usd if reading is not None else None,
            "volatility_score": self._volatility.score(iid),
            **inst.fast_metrics(),
            "pct_1h": slow.get("pct_1h"),
            "pct_24h": slow.get("pct_24h"),
            "pct_1w": slow.get("pct_1w"),
            "pct_1m": slow.get("pct_1m"),
            "volatility": slow.get("volatility"),
        }
        # `price` is the live mid or None: a trade close from the slow loop is a different quantity
        # (§3.3), so it never stands in for a missing mid (Story 31.3 deleted that fallback).
        return row

    def ranks_by_iid(self, now_ns: int) -> dict[str, int]:
        return {r["instrument_id"]: r["rank"] for r in self.current_ranks(now_ns)}

    def with_ranks(self, rows: list[dict], now_ns: int) -> list[dict]:
        """Attach the current rank/volume24h to metrics.db rows (a persistence-only copy)."""
        ranks = self.ranks_by_iid(now_ns)
        volumes = {iid: reading.value_usd for iid, reading in self._volume_24h.items()}
        return [
            {
                **r,
                "rank": ranks.get(r["instrument_id"]),
                "volume24h": volumes.get(r["instrument_id"]),
            }
            for r in rows
        ]

    def stale_ids(self, now_ns: int) -> list[str]:
        """Instruments seen within the last hour that have gone stale -- dropped from the ranks."""
        return sorted(
            iid
            for iid, inst in self._instruments.items()
            if not inst.is_fresh(now_ns)
            and (now_ns - inst.last_seen_ns) <= RECENTLY_STALE_WINDOW_NS
        )

    def build_message(self, now_ns: int) -> dict:
        """
        Build the exact rankings:live wire payload (AD-9 Consistency Conventions): field names,
        nesting and the per-rank shape are load-bearing -- never renamed. Fields are added, never
        removed.
        """
        return {
            "mode": self._mode,
            "updated_at": now_ns,
            "ranks": self.current_ranks(now_ns),
            "stale_instrument_ids": self.stale_ids(now_ns),
        }
