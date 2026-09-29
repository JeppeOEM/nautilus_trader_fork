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
Per-channel subscription bookkeeping and wire pacing for a venue client (Story 29.4).

A venue client's `subscribe(iid)`/`unsubscribe(iid)` opens or closes several channels (trades,
book, ticker, ...). `CaptureService.apply` and its retry loop call them again after a partial
failure, and a runtime plan change calls them at any time, so each channel must be sent at most
once per hold and paced under the venue's measured limit. dYdX keeps its own equivalent
(`capture.venues.dydx.client`'s `_hold`/`_release`), predating this module.
"""

import asyncio
import logging
from collections.abc import Awaitable
from collections.abc import Callable


logger = logging.getLogger(__name__)

ChannelKey = tuple[str, str]  # (channel name, instrument id)
Send = Callable[[], Awaitable[object]]


class WireChannels:
    """
    The channels one venue client holds on the wire, and the pacer every wire call waits on.

    Invariant: at most one held reference per (channel, id) -- `hold` sends only for a channel not
    held, `release` only for a held one -- the key is held exactly when the Rust client's own
    bookkeeping holds the channel, and consecutive wire calls start at least
    `1 / frames_per_second` apart (monotonic loop clock), whichever caller issues them.

    A failed call can leave the Rust client's bookkeeping changed: Bybit's `add_reference`/
    `remove_reference` and Hyperliquid's `asset_context_subs` are updated before their fallible
    command-channel send (crates/adapters/{bybit,hyperliquid}/src/websocket/client.rs). So each
    call names its inverse, `undo`: when `send()` raises an `Exception`, `undo()` runs (paced) to
    restore that bookkeeping, the key's held state is left as it was, and the failure is
    re-raised to the caller, which ledgers and retries -- a retry then sends the channel again
    instead of finding it half-held. A call whose Rust side keeps no state before the failure
    (Hyperliquid's `trades`/`l2Book`) passes none. An undo's own failure is logged at debug and
    suppressed, because it cannot leave the bookkeeping wrong: each inverse also changes that
    bookkeeping before its own fallible send (the same client.rs functions), and Bybit's
    `unsubscribe` never raises at all (it swallows its send failure, client.rs:834-836). So a
    failed undo has still restored the state, and only its frame, for a channel whose
    subscribe frame never went out either, is missing.

    Cancellation: during the pacing wait nothing was sent and nothing changes. After `send()` was
    called the key is recorded as done (held, or released) before `CancelledError` propagates,
    as the likely outcome: the pyo3 call's Rust future is already running on tokio, and its
    few non-blocking steps usually finish before the cancellation reaches it
    (pyo3-async-runtimes drops the Rust future when the Python one is cancelled, so this is not
    guaranteed). Wrong either way costs nothing: capture cancels these calls only when `run()`
    tears down, and `run_forever` builds a new client, with a new `WireChannels`, on every
    attempt.

    Commands that could violate it: a direct wire call bypassing `hold`/`release` (every channel
    call of the Bybit and Hyperliquid clients goes through here), or two instances for one socket.
    """

    def __init__(self, frames_per_second: float) -> None:
        if not frames_per_second > 0:
            raise ValueError(f"frames_per_second must be > 0, got {frames_per_second!r}")
        self._interval = 1.0 / frames_per_second
        self._held: set[ChannelKey] = set()
        self._next_send_at = 0.0
        # One call at a time: the held-check, the paced send and the bookkeeping are one step, so
        # a resync and a subscribe of the same channel cannot both see it unheld.
        self._lock = asyncio.Lock()

    def is_held(self, key: ChannelKey) -> bool:
        return key in self._held

    def held(self) -> frozenset[ChannelKey]:
        """Every key held now (for a client counting its wire subscriptions)."""
        return frozenset(self._held)

    async def hold(self, key: ChannelKey, send: Send, undo: Send | None = None) -> None:
        """Send `send()` unless `key` is already held; on success the key is held (see class)."""
        async with self._lock:
            if key not in self._held:
                await self._call(send, undo, lambda: self._held.add(key))

    async def release(self, key: ChannelKey, send: Send, undo: Send | None = None) -> None:
        """Send `send()` only if `key` is held; on success the key is released (see class)."""
        async with self._lock:
            if key in self._held:
                await self._call(send, undo, lambda: self._held.discard(key))

    async def _call(self, send: Send, undo: Send | None, record: Callable[[], None]) -> None:
        """Run one paced wire call and `record` it when it went out (see class)."""
        await self._pace()  # a cancellation here: nothing sent, nothing to record
        try:
            await send()
        except asyncio.CancelledError:
            # The pyo3 call is a non-blocking channel send already running on tokio: it lands
            # whether or not we await it, so it is recorded before the cancellation goes on.
            record()
            raise
        except Exception:
            if undo is not None:
                await self._undo(undo)
            raise
        record()

    async def _undo(self, undo: Send) -> None:
        await self._pace()
        try:
            await undo()
        except Exception as e:
            # Best effort: the call being undone already failed and is re-raised to the caller.
            logger.debug("wire undo failed: %r", e)

    async def _pace(self) -> None:
        # The call is built only after the wait: a pyo3 call starts its Rust future when called,
        # not when awaited, so building it earlier would put the frame on the wire unpaced.
        loop = asyncio.get_running_loop()
        wait = self._next_send_at - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        self._next_send_at = loop.time() + self._interval
