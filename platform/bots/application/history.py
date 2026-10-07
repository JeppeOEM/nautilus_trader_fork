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
Bot trade history: every fill and every round trip's `PositionClosed` recorded into `fills.db`
as it happens, and the four `bots:history:{bot_id}:{day,week,month,all}` keys refreshed on a
timer (architecture AD-10).

The only place in `platform/` that turns a live strategy's fills into history -- bot_tui and the
web dashboard only `GET` the published keys, never `fills.db` or Nautilus's Cache encoding.

Known limit: the keys refresh on the 30 s timer only, not also on each fill as AD-10 allows (a
deliberate scope cut since Story 4.6: no reader needs sub-30 s latency). The fill and close
*writes*, which durability depends on, are on-event. Upgrade path: call `refresh` from
`record_close` through the event loop once a reader needs it.

Known limit: every refresh still scans the bot's whole `fills.db` history (the all-time daily
PnL and the `all` window), so its cost grows with the file. It runs on the loop's default executor,
never on the node's event loop, but stays serialised with row writes by the store's one lock.
Upgrade path: a per-UTC-day rollup table maintained by `write_position_close`, read in place of
the scans.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable

from observability import error_ledger

from bots.application.ports import BotRuntime
from bots.application.ports import BusConnection
from bots.application.ports import Connect
from bots.application.ports import FillsStore
from bots.application.ports import history_key
from bots.domain.fill_ledger import MAX_TRADES
from bots.domain.fill_ledger import RANGE_WINDOW_NS
from bots.domain.fill_ledger import FillLedger
from bots.domain.fill_ledger import FillRecord
from bots.domain.fill_ledger import PositionCloseRecord
from bots.domain.fill_ledger import cutoff_ns
from bots.domain.fill_ledger import history_payload
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.events import PositionClosed


logger = logging.getLogger(__name__)

# ~30-60 s band (AD-10).
HISTORY_REFRESH_SECONDS = 30.0
RECONNECT_SECONDS = 2.0


class HistoryPublisher:
    """
    Records one bot's fills and round-trip closes and publishes its history windows.

    Invariants: every fill and every `PositionClosed` the strategy reports is turned into a row by
    the bot's one `FillLedger` and written once (a conversion or a write that fails is ledgered
    with every field, a re-delivered row is logged, neither is dropped silently; a close whose
    closing fill cannot be linked is still stored, its PnL counted, and ledgered); a refresh cycle
    that fails leaves the previously published keys untouched, so their aging `updated_at` signals
    staleness rather than an empty fallback overwriting good data.
    """

    def __init__(
        self,
        bot_id: str,
        runtime: BotRuntime,
        fills: FillsStore,
        connect: Connect,
        starting_balance: float | None,
        *,
        clock_ns: Callable[[], int] = time.time_ns,
        refresh_seconds: float = HISTORY_REFRESH_SECONDS,
        reconnect_seconds: float = RECONNECT_SECONDS,
    ) -> None:
        self.bot_id = bot_id
        self._runtime = runtime
        self._fills = fills
        self._connect = connect
        self._starting_balance = starting_balance
        self._clock_ns = clock_ns
        self._refresh_seconds = refresh_seconds
        self._reconnect_seconds = reconnect_seconds
        self.ledger = FillLedger(bot_id)
        # Writes queued on the loop's executor, awaited by `drain` before the node disposes it.
        self._pending_writes: set[asyncio.Future[None]] = set()

    def attach(self) -> None:
        """Record this bot's fills and closes from now on; an earlier event is never seen."""
        self._runtime.on_fill(self.record_fill)
        self._runtime.on_position_closed(self.record_close)

    def record_fill(self, fill: OrderFilled) -> None:
        """
        Turn one fill into its row and write it without blocking the node's event loop.

        This runs synchronously on the strategy's message-bus dispatch, and the whole node shares
        one event loop: a blocking sqlite commit here would freeze every bot for the disk write, so
        the write goes to a worker thread. BacktestEngine has no running loop (a purely
        synchronous replay), so there it is written inline.
        """
        try:
            record = self.ledger.record_fill(fill)
        except Exception as exc:
            # Never into the message bus's dispatch.
            error_ledger.record(
                "bots.fill_lost",
                f"fill permanently lost, not converted or persisted to fills.db: {fill}",
                exc,
            )
            return
        self._submit(self._write_fill, record)

    def record_close(self, event: PositionClosed) -> None:
        """
        Turn one `PositionClosed` into its row and write it off the event loop, as `record_fill`
        does. The engine publishes it right after the fill that closed the position, so the
        ledger links that fill's trade id; an unlinked close is stored and ledgered once its row
        is inserted, never dropped.
        """
        try:
            record = self.ledger.record_close(event)
        except Exception as exc:
            # Never into the message bus's dispatch; and never a row with fabricated PnL.
            error_ledger.record(
                "bots.close_lost",
                f"position close permanently lost, not converted or persisted to fills.db: {event}",
                exc,
            )
            return
        self._submit(self._write_close, record)

    def _submit[R](self, write: Callable[[R], None], record: R) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            write(record)
        else:
            future = loop.run_in_executor(None, write, record)
            self._pending_writes.add(future)
            future.add_done_callback(self._pending_writes.discard)

    async def drain(self) -> None:
        """
        Wait for every queued fill and close write. The node's `dispose` shuts its executor down
        with `cancel_futures=True`, so a write still queued then would never run -- lost without
        even a `bots.fill_lost` entry. The composition root drains before disposing.
        """
        if self._pending_writes:
            await asyncio.gather(*self._pending_writes, return_exceptions=True)

    # Both writers never raise: a fire-and-forget executor call must not leave an unretrieved
    # exception, and an inline call must not propagate into the strategy's event handling. No
    # retry -- sqlite's 5 s busy timeout already retries lock contention; the rest (disk full, an
    # unwritable mount) a retry cannot fix. Every field is ledgered, so the lost row is visible
    # and recoverable by hand, never a silent undercount.

    def _write_fill(self, record: FillRecord) -> None:
        try:
            inserted = self._fills.write_fill(record)
        except Exception as exc:
            error_ledger.record(
                "bots.fill_lost", f"fill permanently lost, not persisted to fills.db: {record}", exc
            )
            return
        if not inserted:
            # A fill re-delivered into a later process life (e.g. after the Cache lost its
            # state): the venue's, not a fault of ours, and the stored row already counts it.
            logger.warning(
                "duplicate fill not stored again, trade id already in fills.db: %s", record
            )

    def _write_close(self, record: PositionCloseRecord) -> None:
        try:
            inserted = self._fills.write_position_close(record)
        except Exception as exc:
            error_ledger.record(
                "bots.close_lost",
                f"position close permanently lost, not persisted to fills.db: {record}",
                exc,
            )
            return
        if not inserted:
            # Re-delivered, like a duplicate fill: the stored row already counts this round trip
            # (and was ledgered then if unlinked).
            logger.warning(
                "duplicate position close not stored again, already in fills.db: %s", record
            )
        elif record.trade_id is None:
            # Only a row actually stored is ledgered: one entry per unlinked round trip.
            error_ledger.record(
                "bots.close_unlinked",
                f"position close linked to no closing fill, stored with a NULL trade id and its "
                f"PnL counted: {record}",
                LookupError(f"no fill of {record.position_id} at ts {record.ts_closed} seen"),
            )

    def compute(
        self,
        range_name: str,
        now_ns: int,
        all_time_pnl_by_day: list[dict] | None = None,
    ) -> dict:
        """
        One `bots:history:{bot_id}:{range_name}` payload from `fills.db`. `all_time_pnl_by_day`
        is read here when not given (`compute_all` reads it once for all four ranges).
        """
        cutoff = cutoff_ns(range_name, now_ns)
        if all_time_pnl_by_day is None:
            all_time_pnl_by_day = self._fills.pnl_by_day(self.bot_id, None)
        return history_payload(
            bot_id=self.bot_id,
            range_name=range_name,
            now_ns=now_ns,
            trades=self._fills.recent_trades(self.bot_id, cutoff, MAX_TRADES),
            pnl_series=self._fills.pnl_by_day(self.bot_id, cutoff),
            position_realized_pnls=self._fills.position_realized_pnls(self.bot_id, cutoff),
            all_time_pnl_by_day=all_time_pnl_by_day,
            starting_balance=self._starting_balance,
        )

    def compute_all(self, now_ns: int) -> dict[str, dict]:
        """Every range's payload, in `RANGE_WINDOW_NS` order, sharing one all-time daily PnL."""
        all_time_pnl_by_day = self._fills.pnl_by_day(self.bot_id, None)
        return {
            range_name: self.compute(range_name, now_ns, all_time_pnl_by_day)
            for range_name in RANGE_WINDOW_NS
        }

    async def refresh(self, connection: BusConnection) -> None:
        now_ns = self._clock_ns()
        # The full-history sqlite scans run on the executor: on the loop they would freeze every
        # bot on the node for their duration (DW-222).
        blobs = await asyncio.get_running_loop().run_in_executor(None, self.compute_all, now_ns)
        for range_name in RANGE_WINDOW_NS:
            await connection.set(
                history_key(self.bot_id, range_name), json.dumps(blobs[range_name])
            )

    async def run(self) -> None:
        logger.info("bots:history loop starting for bot_id=%s", self.bot_id)
        self.attach()
        while True:
            try:
                async with self._connect() as connection:
                    await self._refresh_loop(connection)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_ledger.record(
                    "bots.redis",
                    f"bots:history connection for {self.bot_id} failed, "
                    f"reconnecting in {self._reconnect_seconds}s",
                    exc,
                )
                await asyncio.sleep(self._reconnect_seconds)

    async def _refresh_loop(self, connection: BusConnection) -> None:
        while True:
            try:
                await self.refresh(connection)
            except Exception as exc:
                error_ledger.record(
                    "bots.history_refresh",
                    f"bots:history refresh failed for {self.bot_id}, keeping the published keys",
                    exc,
                )
            await asyncio.sleep(self._refresh_seconds)
