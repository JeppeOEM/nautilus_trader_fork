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
Ranking engine (Story 1.8 / architecture AD-9): sole computer/publisher of Coin Ranking.

Owns the volume24h poll (relocated from ml_signals.dashboard), the cross-sectional
volatility tracker (ranking_engine.volatility), the single active Ranking Mode, and the
snapshots:raw / rankings:live / ranking:control Redis wiring. dashboard/bot_tui are pure
readers of rankings:live -- see AD-9's Consistency Conventions row for the exact wire
schema every reader depends on.
"""

import asyncio
import json
import logging
import os
import statistics
import time
import urllib.request
from collections import deque
from collections.abc import Callable
from pathlib import Path

import redis.asyncio as aioredis

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.core.nautilus_pyo3 import get_dydx_http_url  # type: ignore[attr-defined]

from ml_signals import metrics_computer
from ml_signals.indicators import MultiLevelOBI
from ml_signals.indicators import MultiLevelOFI
from ml_signals.indicators import microprice as calc_microprice
from ml_signals.indicators import mid_price as calc_mid_price
from ml_signals.indicators import spread as calc_spread
from ml_signals.indicators import trade_aggregates
from ml_signals.indicators import volume_delta as calc_volume_delta
from ranking_engine import metrics_store
from ranking_engine.volatility import VolatilityTracker


logger = logging.getLogger(__name__)

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
CATALOG_PATH: str = os.environ.get("CATALOG_PATH", "troll/dydx_collector/catalog")

# Path to the shared SQLite metrics store -- ranking_engine is the sole writer (Task
# 7). Mounted from a dedicated *directory* in docker-compose.yml (not a single-file
# mount): SQLite WAL mode creates metrics.db-wal/metrics.db-shm sidecar files next to
# the main file, which a single-file bind mount can't expose on a path shared with
# dashboard's own read-only mount of the same store.
METRICS_DB_PATH: str = os.environ.get(
    "METRICS_DB_PATH", str(Path(CATALOG_PATH).parent / "metrics" / "metrics.db"),
)

# DYDX_NETWORK must match the collector's configured network (collector.toml's
# [network]) -- if it doesn't, volume24h (and therefore rankings) will silently
# reflect the wrong network's data (mainnet vs. testnet are different perpetualMarkets
# datasets with overlapping-looking tickers).
DYDX_NETWORK: DydxNetwork = DydxNetwork.from_str(  # type: ignore[attr-defined]
    os.environ.get("DYDX_NETWORK", "mainnet").lower(),
)

# How often the volume-24h poll refreshes _VOLUME_24H.
VOLUME_POLL_SECONDS: int = 60

# Volatility's cross-sectional lookback -- configurable with no code change (AC2).
RANKING_VOLATILITY_LOOKBACK_SECONDS: int = int(
    os.environ.get("RANKING_VOLATILITY_LOOKBACK_SECONDS", "3600"),
)

# How often the relocated slow loop recomputes full snapshot metrics from Parquet and
# flushes them (with rank/volume24h merged in) to SQLite -- same cadence as
# ml_signals.dashboard's pre-relocation _slow_loop_task/DB_WRITE_INTERVAL_SECONDS.
DB_WRITE_INTERVAL_SECONDS: int = 60

# rankings:live heartbeat cadence -- see Story 1.8 Dev Notes' "Heartbeat interval
# default" section for the full rationale (short enough that a crash/restart goes
# visibly stale within a few seconds; long enough not to flood Redis on top of the
# rank-change publishes that already fire roughly once per ingest batch).
RANKING_HEARTBEAT_SECONDS: int = int(os.environ.get("RANKING_HEARTBEAT_SECONDS", "5"))

# A coin the collector stops sending snapshots for must eventually drop out of the
# ranking -- reuses dashboard._WATCHLIST_STALE_NS's exact OBS-01-derived threshold and
# rationale verbatim (see ml_signals/dashboard.py's citation): >30s of silence on a
# liquid instrument is a pipeline failure, not "quiet market".
_WATCHLIST_STALE_NS: int = 30_000_000_000

# Wall-clock ns of last-received snapshots:raw entry per instrument -- this module's
# own freshness state.
_LAST_SEEN: dict[str, int] = {}

# Cross-sectional volatility tracker, fed on every snapshots:raw ingest.
_VOLATILITY = VolatilityTracker(lookback_seconds=RANKING_VOLATILITY_LOOKBACK_SECONDS)

# Live per-tick indicator state, one instance per instrument, fed on every
# snapshots:raw ingest -- relocated from ml_signals.dashboard's own independent copy
# (troll/CLAUDE.md SSOT-02): ranking_engine is now the sole computer of these,
# dashboard/bot_tui are pure readers of the rankings:live fields they produce below.
_OFI_INDS: dict[str, MultiLevelOFI] = {}
_OFI_RAW_INDS: dict[str, dict[int, MultiLevelOFI]] = {}
_OBI_INDS: dict[str, dict[int, MultiLevelOBI]] = {}
# No persistent Microprice() instance -- ml_signals.indicators.microprice() is a pure
# function of one snapshot (no meaningful state to hold between calls), computed fresh
# per rank-building pass in _fast_metrics_for below.

# ts_event (ns) of the last snapshot actually fed to the OFI trackers above, per
# instrument -- used only to detect a reconnect gap (see _OFI_GAP_NS) and clear stale
# previous-tick state, mirroring dashboard.py's identical precedent.
_LAST_FED: dict[str, int] = {}

# Gap threshold before an OFI tracker's previous-tick state is dropped rather than
# diffed against a stale pre-gap book -- same 3s threshold dashboard.py used.
_OFI_GAP_NS: int = 3_000_000_000

# Rolling 300-second window of raw snapshot dicts per instrument -- feeds
# trade_aggregates() (cvd/avg_trade_size) and the fast live-tick volatility stdev
# below. Relocated from dashboard.py's _second_rolling.
_SECOND_ROLLING: dict[str, deque] = {}

# Latest catalog-derived slow-loop snapshot per instrument (price/pct_1h/pct_24h/
# volatility) -- relocated from dashboard's _LIVE_SLOW; refreshed every
# DB_WRITE_INTERVAL_SECONDS by _slow_loop_task and folded into _current_ranks() below
# so dashboard/bot_tui get it via rankings:live instead of touching metrics_store
# directly.
_SLOW_METRICS: dict[str, dict] = {}

# The single global Ranking Mode -- module-level mutable state, switched atomically by
# a ranking:control message. Defaults to "volume", matching FR6's existing default.
_ACTIVE_MODE: str = "volume"

# volume24H (USD) per instrument, polled independently from dYdX's public indexer --
# not available anywhere else in this pipeline. Relocated verbatim from
# ml_signals.dashboard (Story 1.2) -- see that module's Dev Notes for why this poll is
# a deliberate, independent stdlib fetch rather than a reuse of
# dydx_collector.open_interest._fetch_markets_json (AD-4: network I/O never qualifies
# as a "pure utility" for cross-namespace reuse).
_VOLUME_24H: dict[str, float] = {}


def _fetch_volume_24h_json(network: DydxNetwork) -> dict:
    """GET dYdX's public indexer perpetualMarkets endpoint.

    Deliberately independent of dydx_collector.open_interest._fetch_markets_json,
    which hits the same endpoint for the same reason: that function performs network
    I/O, and AD-4 only permits cross-namespace imports of shared data types and pure,
    I/O-free utilities -- a network-calling function does not qualify for reuse across
    the dydx_collector/ranking_engine module boundary.
    """
    url = f"{get_dydx_http_url(network)}/v4/perpetualMarkets"
    request = urllib.request.Request(  # noqa: S310 (fixed https indexer URL)
        url,
        headers={"User-Agent": "nautilus-dydx-ranking-engine/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


def parse_volume_24h(markets_json: dict) -> dict[str, float]:
    """Parse volume24H (USD) per instrument from a dYdX perpetualMarkets response."""
    result: dict[str, float] = {}
    for market in markets_json.get("markets", {}).values():
        ticker = market.get("ticker")
        if ticker is None:
            continue
        try:
            vol = float(market.get("volume24H") or 0)
        except (ValueError, TypeError):
            vol = 0.0
        result[f"{ticker}-PERP.DYDX"] = vol
    return result


async def _fetch_volume_24h(network: DydxNetwork) -> dict[str, float]:
    markets_json = await asyncio.to_thread(_fetch_volume_24h_json, network)
    return parse_volume_24h(markets_json)


async def _volume_loop_task(network: DydxNetwork) -> None:
    """Refresh _VOLUME_24H every VOLUME_POLL_SECONDS -- drives the volume ranking mode."""
    while True:
        try:
            _VOLUME_24H.update(await _fetch_volume_24h(network))
        except Exception:
            logger.exception("Volume-24h poll failed")
        await asyncio.sleep(VOLUME_POLL_SECONDS)


def _is_fresh(iid: str, now_ns: int) -> bool:
    """Check whether iid has a _LAST_SEEN entry within _WATCHLIST_STALE_NS.

    _WATCHLIST_STALE_NS (30s) is the OBS-01-derived threshold: zero book updates on a
    liquid instrument for more than 30s is a pipeline failure, not a quiet market (see
    that constant's own comment above for the full rationale/citation).
    """
    ts = _LAST_SEEN.get(iid)
    return ts is not None and (now_ns - ts) <= _WATCHLIST_STALE_NS


# Bounds _recently_stale_iids() below so an instrument that stopped being liquid/
# subscribed weeks ago doesn't clutter that list forever -- only instruments seen at
# all within the last hour are reported as "recently" stale.
_RECENTLY_STALE_WINDOW_NS: int = 3_600_000_000_000  # 1 hour


def _recently_stale_iids(now_ns: int) -> list[str]:
    """
    Instruments this engine has seen recently but that have since gone stale --
    exactly the instruments _current_ranks() silently drops from the ranked list
    (OBS-01: a liquid instrument's book going silent for 30s+ is a pipeline failure,
    never a quiet market). Surfacing this separately (as rankings:live's own
    stale_instrument_ids field, additive -- _current_ranks()'s fresh-only filter is
    untouched) lets an operator see *which* coin's feed died instead of it just
    vanishing from the table with no trace.
    """
    return sorted(
        iid
        for iid, ts in _LAST_SEEN.items()
        if not _is_fresh(iid, now_ns) and (now_ns - ts) <= _RECENTLY_STALE_WINDOW_NS
    )


def _ingest_snapshot_batch(batch: list[dict]) -> None:
    """Update freshness state and feed every live indicator tracker from a
    snapshots:raw batch -- volatility, OFI (z-scored + raw 3/5/10), OBI (3/5/10),
    microprice, and the 300s rolling window (cvd/avg_trade_size/fast volatility).

    Each snap is processed in its own try/except: one malformed entry (missing key,
    bad shape) is logged and skipped rather than aborting every remaining snap in the
    same Redis message.
    """
    for snap in batch:
        try:
            iid = snap["instrument_id"]
            _LAST_SEEN[iid] = time.time_ns()
            bid_prices, ask_prices = snap["bid_prices"], snap["ask_prices"]
            bid_sizes, ask_sizes = snap["bid_sizes"], snap["ask_sizes"]
            if not bid_prices or not ask_prices:  # thin/one-sided book -- legitimate, skip
                continue
            mid = (bid_prices[0] + ask_prices[0]) / 2
            if mid <= 0:  # not a legitimate market state -- fail closed (AD-2), skip this snap
                continue
            _VOLATILITY.update(iid, snap["ts_event"], mid)

            last_fed = _LAST_FED.get(iid)
            gapped = last_fed is not None and (snap["ts_event"] - last_fed) > _OFI_GAP_NS

            ofi_z = _OFI_INDS.setdefault(
                iid, MultiLevelOFI(levels=10, window=50, zscore_window=3600),
            )
            if gapped:
                ofi_z.clear_prev_state()
            ofi_z.update_raw(bid_prices, bid_sizes, ask_prices, ask_sizes)

            raw_ofis = _OFI_RAW_INDS.setdefault(
                iid, {n: MultiLevelOFI(levels=n, window=300) for n in (3, 5, 10)},
            )
            for raw_ofi in raw_ofis.values():
                if gapped:
                    raw_ofi.clear_prev_state()
                raw_ofi.update_raw(bid_prices, bid_sizes, ask_prices, ask_sizes)

            if bid_sizes and ask_sizes:
                obis = _OBI_INDS.setdefault(iid, {n: MultiLevelOBI(levels=n) for n in (3, 5, 10)})
                for obi in obis.values():
                    obi.update_raw(bid_sizes, ask_sizes)

            _LAST_FED[iid] = snap["ts_event"]
            _SECOND_ROLLING.setdefault(iid, deque(maxlen=300)).append(snap)
        except Exception:
            logger.warning("Malformed snapshots:raw entry skipped: %r", snap, exc_info=True)


def _fast_metrics_for(iid: str) -> dict:
    """Re-shape one instrument's already-updated tracker state + rolling window into
    the live-tick fields of a rankings:live rank entry. Pure read of state
    _ingest_snapshot_batch already maintains -- computes nothing new itself.
    """
    snapshots = list(_SECOND_ROLLING.get(iid, ()))
    latest = snapshots[-1] if snapshots else None

    ofi_z_ind = _OFI_INDS.get(iid)
    raw_ofis = _OFI_RAW_INDS.get(iid, {})
    obis = _OBI_INDS.get(iid, {})

    def _value(ind) -> float | None:
        return ind.value if ind is not None and ind.initialized else None

    buy_vol, sell_vol, buy_cnt, sell_cnt = (
        trade_aggregates(snapshots) if snapshots else (0.0, 0.0, 0, 0)
    )
    total_cnt = buy_cnt + sell_cnt

    mid = calc_mid_price(latest) if latest is not None else None
    microprice_value = calc_microprice(latest) if latest is not None else None

    # Fast, 300-point live-tick volatility -- deliberately distinct from
    # volatility_score (VolatilityTracker's 3600s cross-sectional stdev) and the
    # catalog-derived "volatility" folded in from _SLOW_METRICS below (existing
    # design: ranking_engine.volatility's own docstring calls out these as
    # legitimately separate metrics, not to be merged -- only single-sourced).
    mids = [m for m in (calc_mid_price(s) for s in snapshots) if m is not None]
    rets = [(mids[i] - mids[i - 1]) / mids[i - 1] for i in range(1, len(mids))]
    volatility_fast = statistics.stdev(rets) if len(rets) >= 2 else None

    return {
        "ofi_10_z": _value(ofi_z_ind),
        "ofi_3": _value(raw_ofis.get(3)),
        "ofi_5": _value(raw_ofis.get(5)),
        "ofi_10": _value(raw_ofis.get(10)),
        "obi_3": _value(obis.get(3)),
        "obi_5": _value(obis.get(5)),
        "obi_10": _value(obis.get(10)),
        "microprice": microprice_value,
        "microprice_lean": (
            microprice_value - mid if microprice_value is not None and mid is not None else None
        ),
        "spread": calc_spread(latest) if latest is not None else None,
        "cvd": buy_vol - sell_vol,
        "volume_delta": calc_volume_delta(latest) if latest is not None else None,
        "buy_count": buy_cnt,
        "sell_count": sell_cnt,
        "avg_trade_size": (buy_vol + sell_vol) / total_cnt if total_cnt > 0 else None,
        "volatility_fast": volatility_fast,
        "price": mid,
    }


def _legacy_book_metrics_for(iid: str) -> dict:
    """
    metrics_store's historical ofi/microprice/spread columns (persisted every
    DB_WRITE_INTERVAL_SECONDS by _slow_loop_task, plotted by dashboard.py's per-coin
    history chart), sourced from the exact same live indicator state _fast_metrics_for
    reads for rankings:live -- troll/CLAUDE.md SSOT-02: this process must never run
    two independent stateful OFI/microprice trackers for the same instrument.
    Replaces the old metrics_computer._book_metrics, a from-scratch Parquet replay (a
    fresh OrderFlowImbalance/Microprice instance re-fed the last 60s of deltas on every
    call) that duplicated, and slowly diverged from, this exact signal.

    "ofi" maps to the live raw (unscored) ofi_5 -- the closest already-computed signal
    to the old OrderFlowImbalance(window=5) top-of-book value; deliberately not the
    z-scored ofi_10_z used for cross-sectional ranking, to avoid a scale discontinuity
    in this historical chart.
    """
    fast = _fast_metrics_for(iid)
    return {"ofi": fast["ofi_5"], "microprice": fast["microprice"], "spread": fast["spread"]}


def _handle_control_message(message: dict) -> None:
    """Apply a ranking:control mode-switch request. Unrecognized modes are logged and
    ignored (AD-2's fail-closed spirit applied to a control message, not market data).
    """
    global _ACTIVE_MODE
    mode = message.get("mode")
    if mode not in ("volume", "volatility"):
        logger.warning("ranking:control unrecognized mode ignored: %r", mode)
        return
    _ACTIVE_MODE = mode


def _current_ranks() -> list[dict]:
    """Ordered ranks for every currently-fresh instrument, sorted by the active mode's
    score descending. Both volume24h and volatility_score are always computed and
    present in every entry regardless of active mode (AC1) -- only the sort key
    changes with _ACTIVE_MODE.
    """
    now_ns = time.time_ns()
    fresh_iids = [iid for iid in _LAST_SEEN if _is_fresh(iid, now_ns)]

    rows = []
    for iid in fresh_iids:
        volume24h = _VOLUME_24H.get(iid, 0.0)
        volatility_score = _VOLATILITY.score(iid)
        slow = _SLOW_METRICS.get(iid, {})
        row = {
            "instrument_id": iid,
            "volume24h": volume24h,
            "volatility_score": volatility_score,
            **_fast_metrics_for(iid),
            "pct_1h": slow.get("pct_1h"),
            "pct_24h": slow.get("pct_24h"),
            "volatility": slow.get("volatility"),
        }
        if row["price"] is None:  # no fresh snapshot yet -- fall back to the last
            row["price"] = slow.get("price")  # catalog-derived price, same as before
        rows.append(row)

    if _ACTIVE_MODE == "volatility":
        rows.sort(key=lambda r: r["volatility_score"] or 0.0, reverse=True)
    else:
        rows.sort(key=lambda r: r["volume24h"], reverse=True)

    return [{**r, "rank": i + 1} for i, r in enumerate(rows)]


def _ranks_by_iid() -> dict[str, int]:
    """{instrument_id: 1-indexed rank} for every currently-fresh instrument.

    _current_ranks() returns Task 5's richer list[dict] shape (each entry also carries
    volume24h/volatility_score for rankings:live) -- this reconciles that against
    _merge_rank_into_snapshots's Story-1.4-era dict[str, int] expectation.
    """
    return {r["instrument_id"]: r["rank"] for r in _current_ranks()}


def _merge_rank_into_snapshots(
    snapshots: list[dict], ranks: dict[str, int], volumes: dict[str, float],
) -> list[dict]:
    """Attach the current live rank/volume24h to each snapshot before persisting to
    metrics_store. Relocated verbatim from ml_signals.dashboard (Story 1.4) -- see that
    story's Dev Notes for why this is deliberately a persistence-only copy, never
    merged into any live in-memory cache.
    """
    return [
        {**s, "rank": ranks.get(s["instrument_id"]), "volume24h": volumes.get(s["instrument_id"])}
        for s in snapshots
    ]


def _persist_snapshots(snapshots: list[dict], db_path: str) -> None:
    """Merge the current rank/volume24h into snapshots and write them to metrics_store.

    A synchronous convenience wrapper for direct/test use. _slow_loop_task does *not*
    call this as a single unit -- it computes the merge on the main thread and pushes
    only metrics_store.write to asyncio.to_thread, to avoid a cross-thread race against
    _redis_listener's mutation of _LAST_SEEN/VolatilityTracker (see that loop's
    docstring).
    """
    persisted = _merge_rank_into_snapshots(snapshots, _ranks_by_iid(), _VOLUME_24H)
    metrics_store.write(persisted, db_path)


async def _slow_loop_task(catalog_path: str) -> None:
    """Full snapshot (price/pct/vol + book metrics) every DB_WRITE_INTERVAL_SECONDS.

    Relocated from ml_signals.dashboard (Task 7) -- ranking_engine is now the sole
    caller of ml_signals.metrics_computer.compute_all() and the sole writer of
    metrics_store, closing the two-writer race Story 1.4's review already fixed once
    against the old single-process dashboard.

    The rank/merge computation (_ranks_by_iid -> _merge_rank_into_snapshots) reads
    _LAST_SEEN and VolatilityTracker's internal buffers -- the same state
    _ingest_snapshot_batch mutates on the main event-loop thread from _redis_listener.
    That computation is cheap (in-memory dict iteration, no I/O), so it runs here
    directly on the main thread rather than inside asyncio.to_thread, avoiding a
    cross-thread read/mutate race against _redis_listener. Only the genuinely blocking
    I/O (metrics_store.write) is pushed to a worker thread.

    Same reasoning applies to _legacy_book_metrics_for: it reads the live OFI/OBI
    tracker dicts _ingest_snapshot_batch also mutates on this same main thread, so
    every instrument's book-metrics dict is computed here, up front, and handed into
    compute_all() as a plain (now-frozen) dict lookup -- never read live from inside
    the ThreadPoolExecutor compute_all() runs in via asyncio.to_thread.
    """
    while True:
        try:
            book_metrics_by_iid = {iid: _legacy_book_metrics_for(iid) for iid in _LAST_SEEN}
            snapshots = await asyncio.to_thread(
                metrics_computer.compute_all,
                catalog_path,
                book_metrics_fn=lambda iid: book_metrics_by_iid.get(iid, {}),
            )
            if snapshots:
                _SLOW_METRICS.update({s["instrument_id"]: s for s in snapshots})
                persisted = _merge_rank_into_snapshots(snapshots, _ranks_by_iid(), _VOLUME_24H)
                await asyncio.to_thread(metrics_store.write, persisted, METRICS_DB_PATH)
        except Exception:
            logger.exception("Slow metrics loop failed")
        await asyncio.sleep(DB_WRITE_INTERVAL_SECONDS)


def _build_rankings_message() -> dict:
    """The exact rankings:live wire schema -- AD-9/Consistency Conventions table field
    names, nesting, and per-rank shape are load-bearing; never rename for "clarity."

    stale_instrument_ids is an additive field (dashboard/bot_tui readers predating it
    simply never look at it) -- never renamed either, once shipped.
    """
    now_ns = time.time_ns()
    return {
        "mode": _ACTIVE_MODE,
        "updated_at": now_ns,
        "ranks": _current_ranks(),
        "stale_instrument_ids": _recently_stale_iids(now_ns),
    }


class RankingsPublisher:
    """Decides when to publish rankings:live, per AD-9's change+heartbeat discipline.

    Chosen discipline (Task 6): the heartbeat timer resets on *every* publish, whether
    triggered by a rank/mode change or by the heartbeat itself -- so a burst of
    rank-change publishes doesn't also cause a redundant heartbeat publish moments
    later. Either discipline satisfies AC4; this is the one implemented here.
    """

    def __init__(self, heartbeat_seconds: float, now_fn: Callable[[], float] = time.time) -> None:
        self._heartbeat_seconds = heartbeat_seconds
        self._now_fn = now_fn
        self._last_published_at: float | None = None
        self._last_ranks_key: tuple | None = None
        self._last_mode: str | None = None

    @staticmethod
    def _ranks_key(ranks: list[dict]) -> tuple:
        # A representative subset of the live-tick fields, not every one of them --
        # spread/cvd/microprice/price/ofi_10_z are all derived from the same incoming
        # book/trade data, so if none of these five changed nothing else meaningfully
        # did either. Broadened here (was rank-only) so the new per-tick fields
        # (SSOT-02 migration from dashboard.py) actually refresh live instead of only
        # on the RANKING_HEARTBEAT_SECONDS heartbeat.
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


_PUBLISHER = RankingsPublisher(heartbeat_seconds=RANKING_HEARTBEAT_SECONDS)

# _maybe_publish is called from two independently-scheduled loops (_redis_listener per
# message, _heartbeat_loop every 1s) against the one shared _PUBLISHER instance above.
# should_publish/publish/record_published is a check-then-act sequence with an `await`
# in the middle -- without serializing it, both loops could observe "should publish"
# before either records, producing a duplicate publish. One lock, held for the whole
# sequence, shared by both call sites.
_PUBLISH_LOCK = asyncio.Lock()


async def _maybe_publish(redis_client: aioredis.Redis) -> None:
    """Build the current rankings message and publish it iff RankingsPublisher says to."""
    async with _PUBLISH_LOCK:
        message = _build_rankings_message()
        if _PUBLISHER.should_publish(message["ranks"], message["mode"]):
            await redis_client.publish("rankings:live", json.dumps(message))
            _PUBLISHER.record_published(message["ranks"], message["mode"])


async def _heartbeat_loop(redis_client: aioredis.Redis) -> None:
    """Independent of the ingest-triggered publish -- guarantees a heartbeat fires even
    during a quiet market with zero snapshots:raw/ranking:control traffic.
    """
    while True:
        await asyncio.sleep(1)
        try:
            await _maybe_publish(redis_client)
        except Exception:
            logger.exception("Heartbeat publish failed")


async def _redis_listener(redis_url: str) -> None:
    """Subscribe to snapshots:raw and ranking:control on one connection, branching on
    message["channel"] -- mirrors ml_signals.dashboard._redis_listener's reconnect
    discipline (outer while True reconnects on any non-cancellation exception).
    """
    logger.info("Ranking engine Redis listener starting, url=%s", redis_url)
    while True:
        try:
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe("snapshots:raw", "ranking:control")
                logger.info("Subscribed to snapshots:raw, ranking:control")
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                        if message["channel"] == "snapshots:raw":
                            _ingest_snapshot_batch(payload)
                        elif message["channel"] == "ranking:control":
                            _handle_control_message(payload)
                        await _maybe_publish(client)
                    except Exception as exc:
                        logger.warning("Redis message parse/ingest error: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Redis subscriber error — reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)


async def main() -> None:
    async with aioredis.Redis.from_url(REDIS_URL, decode_responses=True) as publish_client:
        await asyncio.gather(
            _redis_listener(REDIS_URL),
            _heartbeat_loop(publish_client),
            _volume_loop_task(DYDX_NETWORK),
            _slow_loop_task(CATALOG_PATH),
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
