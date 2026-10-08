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
`RankingEngine` -- ranking's application service (Story 25.2): the four loops of the ranking
process (the `snapshots:raw`/`ranking:control` handler, the volume poll, the slow metrics loop and
the heartbeat), driving one `RankingBoard` through the ports. Constructed by `ranking/__main__.py`.

Story 29.5: every volume cycle also publishes each venue's market names on `markets:live`
(`publish_markets`, `markets_message`) -- the full market list the volume poll already fetched,
names only, so `bot_tui`'s market browser can offer every coin a venue lists without a venue REST
call of its own. It carries no volume, price or other metric (operator decision 2026-09-26).

Story 33.4: the handler also takes `derivs:raw` (decoded only by `kernel.derivs_wire.from_wire`)
and `liquidations:raw` (`Liquidation.from_dict`) into the board, and the one-time backfill also
reads 25 h of open interest and 1 h of liquidations for the instruments that have them.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from kernel.derivs_wire import from_wire
from kernel.liquidation import Liquidation
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.venues import MalformedInstrumentId
from kernel.venues import base_symbol
from observability import error_ledger

from ranking.application.ports import CONTROL_CHANNEL
from ranking.application.ports import DERIVS_CHANNEL
from ranking.application.ports import LIQUIDATIONS_CHANNEL
from ranking.application.ports import SNAPSHOTS_CHANNEL
from ranking.application.ports import DerivsHistory
from ranking.application.ports import LivePublisher
from ranking.application.ports import PriceHistory
from ranking.application.ports import RankingHistory
from ranking.application.ports import VolumeSource
from ranking.domain.board import RankingBoard
from ranking.domain.derivs import LIQUIDATION_WINDOW_NS
from ranking.domain.derivs import OI_LOOKBACK_NS
from ranking.domain.values import RankingMode


logger = logging.getLogger(__name__)

VOLUME_SITE = "ranking_engine.volume24h"  # ledger site names are published language: kept
# Story 29.5: a `markets:live` message not published, or an id left out of one.
MARKETS_SITE = "ranking_engine.markets"
# Story 33.4: an undecodable `derivs:raw`/`liquidations:raw` entry, or one the board refused; and a
# failed or disagreeing derivatives backfill read.
DERIVS_SITE = "ranking_engine.derivs_entry"
LIQUIDATION_SITE = "ranking_engine.liquidation_entry"
DERIVS_BACKFILL_SITE = "ranking_engine.derivs_backfill"
# A message's bad entries itemised on the ledger (each line's entry repr cut at `_REPR_MAX` chars),
# then one summary line with the total: a venue-wide payload change must not write one line per
# entry of every message (capture's `_MALFORMED_SHOWN` rule).
_ENTRIES_SHOWN = 3
_REPR_MAX = 200


def short_repr(value: object) -> str:
    """Return `repr(value)`, cut at `_REPR_MAX` characters with the full length named."""
    text = repr(value)
    return text if len(text) <= _REPR_MAX else f"{text[:_REPR_MAX]}... ({len(text)} chars)"


def markets_message(venue: str, ts: int, ids: Sequence[str]) -> str:
    """
    Return one venue's `markets:live` payload: `{"venue", "ts", "markets": [{"instrument_id",
    "symbol"}]}` in that key order, `markets` sorted by id. `symbol` is `kernel.venues.base_symbol`
    derived here on publish and stored nowhere (SIGNAL-01). Names only: no volume, price or metric.
    Raises `MalformedInstrumentId` for an id without a `.VENUE` suffix; the engine leaves those out
    (ledgered) before calling.
    """
    markets = [{"instrument_id": iid, "symbol": base_symbol(iid)} for iid in sorted(ids)]
    return json.dumps({"venue": venue, "ts": ts, "markets": markets})


@dataclass(frozen=True)
class RankingConfig:
    """The engine's cadences: fixed defaults, not read from the environment."""

    # How often every venue's USD volume is polled.
    volume_poll_seconds: int = 60
    # Upper bound on one source's whole fetch. The kernel's socket timeout bounds each socket
    # operation, not the whole response, so a slowly trickling body could otherwise hold the
    # gathered cycle -- and with it every other venue's refresh -- open indefinitely.
    volume_fetch_timeout_s: float = 45.0
    # How often the slow loop recomputes price/pct/volatility and writes metrics.db.
    db_write_interval_seconds: int = 60

    @property
    def volume_max_age_ns(self) -> int:
        """About 3 missed polls: past this a source's last good volumes are no longer current."""
        return 3 * self.volume_poll_seconds * 1_000_000_000


class RankingEngine:
    """
    The ranking process: every board command is issued from here, on the event loop thread.

    Invariant: one publish decision at a time -- `maybe_publish` is called by two independently
    scheduled loops (every handled message and the heartbeat), and its check-then-publish-then-
    record sequence spans an `await`, so it runs under one lock or both could publish the same
    state twice. Only genuinely blocking I/O (the catalog backfill read, metrics.db) leaves the
    event loop thread; board state is never touched from a worker thread.
    """

    def __init__(
        self,
        board: RankingBoard,
        *,
        volume_sources: Sequence[VolumeSource],
        prices: PriceHistory,
        derivs: DerivsHistory,
        history: RankingHistory,
        live: LivePublisher,
        markets: LivePublisher,
        config: RankingConfig,
        clock: Callable[[], int] = time.time_ns,
    ) -> None:
        self._board = board
        self._volume_sources = tuple(volume_sources)
        self._prices = prices
        self._derivs = derivs
        self._history = history
        self._live = live
        self._markets = markets
        self._config = config
        self._clock = clock
        self._publish_lock = asyncio.Lock()

    # --- snapshots:raw / ranking:control -----------------------------------------------------

    async def handle(self, channel: str, data: str) -> None:
        """One Redis message: decode, apply to the board, then publish if the publisher says so."""
        try:
            if self._apply(channel, json.loads(data)):
                await self.maybe_publish()
        except Exception as exc:
            # A failed decode or publish is not market data lost here (the next message or
            # heartbeat republishes the whole state), but it must be counted, never only logged.
            error_ledger.record("ranking_engine.message", f"{channel} message not handled", exc)

    def _apply(self, channel: str, payload: object) -> bool:
        """
        Apply one decoded message to the board; return whether a rank row may have changed. A
        derivatives or liquidation row reaches the rank row only through the next slow-loop row,
        so neither triggers a publish decision of its own.
        """
        if channel == SNAPSHOTS_CHANNEL:
            self.ingest_snapshot_batch(payload)
        elif channel == CONTROL_CHANNEL:
            self.switch_mode(payload)
        elif channel == DERIVS_CHANNEL:
            self.ingest_derivs_batch(payload)
            return False
        elif channel == LIQUIDATIONS_CHANNEL:
            self.ingest_liquidation_batch(payload)
            return False
        return True

    def ingest_snapshot_batch(self, batch: object) -> None:
        """
        Decode each entry with `DydxSecondSnapshot.from_dict` (the one `snapshots:raw` parser of the
        integer layout, Story 30.2; the floats it computes are this process's own) and
        ingest it. One malformed entry is ledgered and skipped, never the rest of the batch; a
        field the board drops from an otherwise usable entry is ledgered at the same site. A payload
        that is not a list is one failed message, never one ledger entry per key or character.
        """
        self._ingest_batch(
            SNAPSHOTS_CHANNEL,
            batch,
            lambda entry: self._board.ingest(DydxSecondSnapshot.from_dict(entry), self._clock()),
            "ranking_engine.snapshot_entry",
        )

    def ingest_derivs_batch(self, batch: object) -> None:
        """Decode each `derivs:raw` entry with `kernel.derivs_wire.from_wire`, the one parser."""
        self._ingest_batch(
            DERIVS_CHANNEL,
            batch,
            lambda entry: self._board.ingest_derivs(from_wire(entry), self._clock()),
            DERIVS_SITE,
        )

    def ingest_liquidation_batch(self, batch: object) -> None:
        """Decode each `liquidations:raw` entry with `Liquidation.from_dict`, the one parser."""
        self._ingest_batch(
            LIQUIDATIONS_CHANNEL,
            batch,
            lambda entry: self._board.ingest_liquidation(
                Liquidation.from_dict(entry), self._clock()
            ),
            LIQUIDATION_SITE,
        )

    @staticmethod
    def _ingest_batch(
        channel: str, batch: object, apply: Callable[[Any], list[str]], site: str
    ) -> None:
        """
        Apply every entry of one message's JSON array: a malformed entry is skipped, never the rest
        of the batch, and every detail the board returns for a usable entry (the rest of the entry
        was used; only the named field was not) is a problem too (DATA-07). The first
        `_ENTRIES_SHOWN` problems of the message are ledgered at `site` one line each, the entry's
        repr cut short; past them one summary line names the total. A payload that is not a list is
        one failed message.
        """
        if not isinstance(batch, list):
            raise ValueError(f"{channel} payload is a {type(batch).__name__}, not a list")
        problems = 0
        for entry in batch:
            try:
                lines: list[tuple[str, Exception | None]] = [(d, None) for d in apply(entry)]
            except Exception as exc:
                lines = [(f"malformed {channel} entry SKIPPED: {short_repr(entry)}", exc)]
            for detail, cause in lines:
                problems += 1
                if problems <= _ENTRIES_SHOWN:
                    error_ledger.record(site, detail, cause)
        if problems > _ENTRIES_SHOWN:
            error_ledger.record(
                site,
                f"{problems} {channel} entries of one message were malformed or refused, "
                f"{problems - _ENTRIES_SHOWN} beyond the first {_ENTRIES_SHOWN} not itemised",
            )

    def switch_mode(self, message: object) -> None:
        """Apply a ranking:control request; an unrecognised mode is logged and ignored (AD-2)."""
        requested = message.get("mode") if isinstance(message, dict) else None
        mode = RankingMode.parse(requested)
        if mode is None:
            logger.warning("ranking:control unrecognized mode ignored: %r", requested)
            return
        self._board.switch_mode(mode)

    async def maybe_publish(self) -> None:
        """Build the current rankings message and publish it iff the publisher says to."""
        async with self._publish_lock:
            message = self._board.build_message(self._clock())
            publisher = self._board.publisher
            if publisher.should_publish(message["ranks"], message["mode"]):
                await self._live.publish(json.dumps(message))
                publisher.record_published(message["ranks"], message["mode"])

    async def heartbeat(self) -> None:
        """Publish even in a quiet market with no snapshots:raw/ranking:control traffic."""
        while True:
            await asyncio.sleep(1)
            try:
                await self.maybe_publish()
            except Exception as exc:
                error_ledger.record("ranking_engine.publish", "heartbeat publish failed", exc)

    # --- USD 24 h volume ---------------------------------------------------------------------

    async def volume_cycle(self) -> None:
        """One poll cycle: every source concurrently, then the rebuild, then the missing ledger."""
        await asyncio.gather(*(self._poll(source) for source in self._volume_sources))
        now_ns = self._clock()
        max_age_s = self._config.volume_max_age_ns // 1_000_000_000
        for source, age_ns in self._board.refresh_volumes(now_ns):
            error_ledger.record(
                VOLUME_SITE,
                f"{source}: last good volume poll is {age_ns // 1_000_000_000}s old "
                f"(max {max_age_s}s), its rows are out of volume mode",
            )
        for iid in self._board.missing_volume_ids(now_ns):
            error_ledger.record(
                VOLUME_SITE, f"{iid}: no USD 24h volume from its venue, left out of volume mode"
            )
        await self.publish_markets(now_ns)

    async def publish_markets(self, now_ns: int) -> None:
        """
        Publish one `markets:live` message per venue with a fresh volume source (`venue_markets`),
        `ts` = this cycle's `now_ns`. Each venue is its own try: a failed publish is ledgered at
        `MARKETS_SITE` and the other venues' messages still go out; nothing here can stop the
        volume cycle. A venue left with no nameable id publishes nothing (DATA-01).
        """
        for venue, ids in self._board.venue_markets(now_ns).items():
            nameable = self._nameable_ids(venue, ids)
            if not nameable:
                continue
            try:
                await self._markets.publish(markets_message(venue, now_ns, nameable))
            except Exception as exc:
                error_ledger.record(MARKETS_SITE, f"{venue}: markets:live publish failed", exc)

    @staticmethod
    def _nameable_ids(venue: str, ids: list[str]) -> list[str]:
        """Return the ids `base_symbol` can name; each one it cannot is ledgered and left out."""
        nameable = []
        for iid in ids:
            try:
                base_symbol(iid)
            except MalformedInstrumentId as exc:
                error_ledger.record(
                    MARKETS_SITE, f"{venue or '?'}: {iid!r} has no symbol, left out", exc
                )
                continue
            nameable.append(iid)
        return nameable

    async def _poll(self, source: VolumeSource) -> None:
        """Replace the source's volumes on success; on failure ledger and keep the last good."""
        try:
            timeout = self._config.volume_fetch_timeout_s
            volumes = await asyncio.wait_for(source.fetch(), timeout=timeout)
            if not volumes:
                # Every venue lists hundreds of markets: an empty parse is a broken or error
                # response, never "no volume anywhere" -- it must not wipe the last good values.
                raise ValueError("poll returned no volumes at all")
        except Exception as exc:
            error_ledger.record(
                VOLUME_SITE,
                f"{source.name}: volume poll failed, keeping its last good volumes",
                exc,
            )
            return
        self._board.record_volume_poll(source.name, volumes, self._clock())

    async def volume_loop(self) -> None:
        while True:
            try:
                await self.volume_cycle()
            except Exception as exc:
                error_ledger.record(VOLUME_SITE, "volume poll cycle failed", exc)
            await asyncio.sleep(self._config.volume_poll_seconds)

    # --- slow metrics loop -------------------------------------------------------------------

    async def slow_loop_once(self) -> None:
        """One cycle: age out, backfill new instruments, compute and persist every row."""
        now_ns = self._clock()
        self._board.age_out(now_ns)
        await self._backfill_new_instruments(now_ns)
        price_1w, price_1m = await self._prices_days_ago()
        # Stamped when the board is read, not when the cycle began: a batch ingested during the
        # awaits above is in the row, so the earlier stamp would predate its own state (Story 31.9).
        read_ns = self._clock()
        rows = self._board.slow_rows(read_ns, price_1w, price_1m)
        if rows:
            persisted = self._board.with_ranks(rows, read_ns)
            await asyncio.to_thread(self._history.write, persisted)

    async def _backfill_new_instruments(self, now_ns: int) -> None:
        """
        One-time catalog backfill per instrument, marked done even when a read fails: a failing
        instrument must not be retried every cycle -- that is the recurring read Story 13.2 removed.
        Each of the three reads (prices with volume, open interest, liquidations) fails alone.
        """
        for iid in self._board.unbackfilled_ids():
            try:
                await self._backfill_prices(iid, now_ns)
                wants_open_interest, wants_liquidations = self._board.derivs_backfill_needs(iid)
                if wants_open_interest:
                    await self._backfill_open_interest(iid, now_ns)
                if wants_liquidations:
                    await self._backfill_liquidations(iid, now_ns)
            finally:
                self._board.mark_backfilled(iid)

    async def _backfill_prices(self, iid: str, now_ns: int) -> None:
        try:
            start_ns = now_ns - self._board.price_lookback_ns
            series = await asyncio.to_thread(self._prices.series, iid, start_ns)
            for detail in self._board.backfill(iid, series):
                error_ledger.record("ranking_engine.price_backfill", detail)
        except Exception as exc:
            error_ledger.record(
                "ranking_engine.price_backfill",
                f"price-series backfill failed for {iid}, NOT retried",
                exc,
            )

    async def _backfill_open_interest(self, iid: str, now_ns: int) -> None:
        try:
            read = self._derivs.open_interest
            rows = await asyncio.to_thread(read, iid, now_ns - OI_LOOKBACK_NS, now_ns)
            for detail in self._board.backfill_open_interest(iid, rows):
                error_ledger.record(DERIVS_BACKFILL_SITE, detail)
        except Exception as exc:
            error_ledger.record(
                DERIVS_BACKFILL_SITE, f"open-interest backfill failed for {iid}, NOT retried", exc
            )

    async def _backfill_liquidations(self, iid: str, now_ns: int) -> None:
        try:
            read = self._derivs.liquidations
            rows = await asyncio.to_thread(read, iid, now_ns - LIQUIDATION_WINDOW_NS, now_ns)
            for detail in self._board.backfill_liquidations(iid, rows):
                error_ledger.record(DERIVS_BACKFILL_SITE, detail)
        except Exception as exc:
            error_ledger.record(
                DERIVS_BACKFILL_SITE, f"liquidation backfill failed for {iid}, NOT retried", exc
            )

    async def _prices_days_ago(self) -> tuple[dict[str, float], dict[str, float]]:
        """
        1w/1m need more history than the 25h series holds, so they come from metrics.db. Optional
        enrichment: a failed read leaves pct_1w/pct_1m None and must not stall the rest.
        """
        price_1w: dict[str, float] = {}
        price_1m: dict[str, float] = {}
        try:
            price_1w = await asyncio.to_thread(self._history.price_near_days_ago, 7)
            price_1m = await asyncio.to_thread(self._history.price_near_days_ago, 30)
        except Exception as exc:
            error_ledger.record(
                "ranking_engine.metrics_history",
                "1w/1m price lookup failed; pct_1w/pct_1m unavailable this cycle",
                exc,
            )
        return price_1w, price_1m

    async def slow_loop(self) -> None:
        """
        Every db_write_interval_seconds, with a cycle-duration canary (DATA-02): a cold start that
        backfills every instrument in one cycle must stay visible if it runs past the interval.
        """
        interval = self._config.db_write_interval_seconds
        while True:
            cycle_start = time.monotonic()
            try:
                await self.slow_loop_once()
            except Exception as exc:
                error_ledger.record("ranking_engine.slow_loop", "slow metrics cycle failed", exc)
            cycle_seconds = time.monotonic() - cycle_start
            if cycle_seconds > interval:
                logger.warning(
                    "Slow metrics loop cycle took %.1fs, exceeding the %ds write interval",
                    cycle_seconds,
                    interval,
                )
            await asyncio.sleep(interval)
