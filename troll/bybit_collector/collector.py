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
Bybit market-data collector: `collector_core.Collector` over `BybitClient` (Story 22.1).

Bybit has a central book, so a crossed local book is corruption: the core skips the
sample, ledgers it, and -- because `BybitClient` exposes `resync_orderbook` -- forces a
fresh snapshot only once it stayed crossed past `crossed_resync_seconds` (DATA-03 fallback).
Open interest is dropped by the Rust bindings on the linear ticker path, so it is polled
over REST as an extra loop.
"""

import asyncio
import os
from collections import defaultdict
from pathlib import Path
from typing import Literal

from collector_core.collector import Collector
from collector_core.collector import run_forever
from ml_signals import error_ledger

from bybit_collector.client import BybitClient
from bybit_collector.config import BybitConfig
from bybit_collector.config import load_config
from bybit_collector.open_interest import fetch_open_interest
from nautilus_trader.core.nautilus_pyo3 import BybitEnvironment
from nautilus_trader.model.data import OrderBookDeltas


CONFIG_PATH = Path(os.environ.get("BYBIT_COLLECTOR_CONFIG", Path(__file__).parent / "config.toml"))


Verdict = Literal["ok", "gap", "regress", "snapshot"]


def _sequence_verdict(last_u: int | None, u: int, is_snapshot: bool) -> Verdict:
    """
    Classify a Bybit book message's update id `u` against the previous one (DATA-02 canary).

    Bybit documents `u` as a per-topic sequence and `u == 1` as a service-restart snapshot.
    With no baseline (`last_u is None`) a non-snapshot cannot be judged: "ok".
    """
    if is_snapshot or u == 1:
        return "snapshot"
    if last_u is None:
        return "ok"
    if u <= last_u:
        return "regress"
    return "gap" if u - last_u > 1 else "ok"


def _message_u(deltas: OrderBookDeltas) -> int | None:
    """
    The message's `u`, or None when it carries no level to read it from.

    Verified against crates/adapters/bybit/src/websocket/parse.rs (`parse_orderbook_deltas`,
    lines 232-316): `u` becomes `BookOrder.order_id` of *every* level delta of the message
    (one value per message); `seq` is `OrderBookDelta.sequence`; a `type=snapshot` message
    starts with a `Clear` delta (no order, hence skipped here); a message with zero levels
    carries no `u` at all.
    """
    for delta in deltas.deltas:
        if not delta.is_clear:
            return delta.order.order_id
    return None


class BybitCollector(Collector):
    """
    Bybit's `u` canary lives in `_apply_deltas`. Either break in the sequence -- a regress
    (`u` <= previous: messages replayed or reordered) or a gap (`u` skips one or more: messages
    lost) -- means the local book can no longer be trusted, so both are ledgered
    (`collector.book_sequence`), the message is dropped, the book is dropped and a resync queued.

    A gap counts as loss because `u` was measured contiguous in a healthy stream: raw capture
    2026-09-20 (`scripts/capture_hl_ws.py --venue bybit`, 800 s, BTCUSDT), 31,862 `orderbook.50`
    frames, **31,860 of 31,860 delta steps were exactly +1**, zero gaps and zero regressions
    (DATA_INTEGRITY_AUDIT.md D-41). That capture covers one instrument and one topic, so a
    venue whose `u` behaves differently on spot or a thin instrument shows up as a rising
    `collector.book_sequence` count with a resync per break -- loudly, never silently. A resync is the fallback, never the fix: a rising
    `collector.book_sequence` count is an open incident (DATA-03).
    """

    _config: BybitConfig

    def __init__(self, config: BybitConfig) -> None:
        # `self._on_data` is a bound method: safe to hand out before super().__init__ since
        # no message can arrive before connect().
        env = (
            BybitEnvironment.TESTNET
            if config.environment == "testnet"
            else BybitEnvironment.MAINNET
        )
        client = BybitClient(on_data=self._on_data, environment=env, trade_feeds=config.trade_feeds)
        super().__init__(config, client, extra_loops=(self._open_interest_loop,))
        self._last_u: dict[str, int] = {}
        self._book_sequence_errors: defaultdict[str, int] = defaultdict(int)

    def _clear_book_state(self, iid: str) -> None:
        super()._clear_book_state(iid)
        self._last_u.pop(iid, None)

    def _apply_deltas(self, iid: str, deltas: OrderBookDeltas) -> None:
        u = _message_u(deltas)
        if u is not None:
            verdict = _sequence_verdict(self._last_u.get(iid), u, deltas.deltas[0].is_clear)
            last = self._last_u.get(iid)
            if verdict in ("regress", "gap"):
                self._book_sequence_errors[iid] += 1
                detail = (
                    f"u={u} <= last={last} (replayed/reordered)"
                    if verdict == "regress"
                    else f"u={u} skips {u - (last or 0) - 1} message(s) after last={last}"
                )
                error_ledger.record(
                    "collector.book_sequence",
                    f"{iid} {detail} (#{self._book_sequence_errors[iid]}): "
                    "book dropped, resync queued",
                )
                self._clear_book_state(iid)  # also forgets last_u: the snapshot re-baselines it
                self._resync_pending.add(iid)  # _apply_deltas is sync; _sample_tick resyncs
                return
            self._last_u[iid] = u
        super()._apply_deltas(iid, deltas)

    async def _open_interest_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._config.open_interest_poll_seconds)
            try:
                wanted = set(self._instrument_ids())
                for item in await fetch_open_interest(self._config.environment):
                    if str(item.instrument_id) in wanted:
                        # Straight to the buffer, not _on_data: REST-polled data must not
                        # count as WS feed liveness (`_last_feed_message_ns`, story 22.5).
                        self._buffer[(type(item), str(item.instrument_id))].append(item)
            except Exception as e:
                error_ledger.record("collector.open_interest_poll", "open interest poll failed", e)


if __name__ == "__main__":
    asyncio.run(run_forever(lambda: BybitCollector(load_config(CONFIG_PATH))))
