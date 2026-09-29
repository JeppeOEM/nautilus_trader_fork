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
Thin wrapper around Hyperliquid's Rust-backed HTTP/WebSocket clients (perps).

Same reason as `capture.venues.dydx.client`: bypass TradingNode/DataEngine and drive the Rust
connect/reconnect/decode logic with our own asyncio loop and callback.

Verified against crates/adapters/hyperliquid (Story 19.4 Task 1) -- independently of dYdX/Bybit:
  * precision: mark/index/book prices are all parsed at `instrument.price_precision()`
    (websocket/parse.rs), a per-instrument constant -> no `_at_fixed_precision`.
  * open interest IS forwarded (`subscribe_open_interest` -> pyo3 CustomData), so no REST
    poll workaround, unlike dYdX/Bybit.
  * every `l2Book` payload is a full snapshot (parse_ws_order_book_deltas emits Clear + all
    levels), so the local book can't drift from missed deltas -> no resync machinery.
  * NO `OrderBookDelta.sequence`/`order_id` gap check (opt-out of story 22.5's Bybit `u`
    canary): websocket/parse.rs `parse_ws_order_book_deltas` (lines 103-163) stamps
    `sequence=0` and `order_id=0` on every delta and emits Clear + all levels per message,
    so there is no venue sequence to check and nothing to desync. Book correctness is
    covered instead by the time-aligned REST cross-check (`fetch_book_snapshot`, D-64).
  * subscriptions: the Rust client keeps no reference count (crates/adapters/hyperliquid/src/
    websocket/client.rs:1077-1660): `trades`/`l2Book` send one Subscribe frame per call (a
    repeated call sends a duplicate frame) and fail before any state ("Instrument not found")
    or at the command-channel send. Mark/index/funding/open interest share ONE `activeAssetCtx`
    frame, sent on the first of the four, and `subscribe_asset_context_data`/
    `unsubscribe_asset_context_data` update `asset_context_subs` BEFORE their fallible send: a
    failed first mark subscribe leaves the set non-empty, so a plain retry would send no frame
    and return Ok. Every channel call therefore goes through one `WireChannels`; the four
    asset-context calls pass their inverse as `undo`, restoring that set after a failure, while
    `trades`/`l2Book` pass none. A retry after a partial failure sends only the channels not
    held, and an unsubscribe releases each held channel once (Story 29.4).
  * pacing: Hyperliquid documents 2000 sent messages per minute and 1000 subscriptions per IP.
    Observed live 2026-09-29 (`scripts/measure_ws_limits.py --venue hyperliquid`,
    docs/DATA_DICTIONARY.md §1.14): one burst of 100 subscribe frames (4 ms) was accepted, and
    a ramp at 20/s was accepted up to 1000 channels, the 1001st refused ("Cannot subscribe to
    more than 1000 channels."). Every wire call waits on `HYPERLIQUID_WS_FRAMES_PER_SECOND`, a
    chosen margin under those observations and the documented rate, not a measured ceiling;
    pacing per Python call over-counts the shared `activeAssetCtx` frame, the safe direction.
    Known limit: the Rust client's reconnect replay of its subscriptions is not paced by us. It
    is bounded only by the plan's size, which has no cap (3 frames per id, 4 with the
    trades-only twin, up to the 1000-channel budget below), so a large plan replays well past
    the 100-frame burst observed accepted; upgrade path: a pacing hook in the Rust client
    (outside `platform/`, FORK-01).
  * channel budget: `subscribe` refuses (raises) an id whose new wire subscriptions would take
    this client past `HYPERLIQUID_MAX_WS_CHANNELS`, since the venue rejects the excess only
    asynchronously and capture would show the id applied. Capture then keeps it `pending` and
    ledgers `collector.subscribe_failed` on every retry. A venue limit, not a plan cap: the
    plan stays uncapped. Known limit: only this process's channels are counted, so any other
    Hyperliquid socket from the same IP (e.g. `live-paper`) shrinks the real budget unseen;
    upgrade path: a per-IP budget shared across processes (e.g. a Redis counter).
  * Known limit (apply latency): an id is 6 paced calls (7 with the trades-only twin), about
    0.7 s at 10/s, all under capture's subscription lock, so a large add delays other commands
    by that much per id. At start, `CaptureService.run` applies the whole plan before it starts
    any extra loop, so `collector:status` and `collector:control` wait that long per planned id
    too (a command published meanwhile is lost, as for a stopped collector); upgrade path: pace
    per real wire frame (the Rust client would have to report which calls sent one).

Feeds (story 22.14): messages are tagged `MAIN_FEED`, and with `trade_feeds = 2` a second,
trades-only `HyperliquidWebSocketClient` delivers as `main-trades` (same group). Hyperliquid's
REST `recentTrades` returns only the last 10 trades (verified 2026-09-21), so the second
connection is the only way to close a reconnect gap on this venue (audit D-48). `feed_states()`
exposes each socket's `is_active()`, the core's reconnect signal.

Trade `ts_event` is re-stamped to the venue's exact millisecond (`_at_exact_millis`): the adapter
converts Hyperliquid's integer-ms `time` to ns through `f64` (crates/adapters/hyperliquid/src/
common/parse.rs `millis_to_nanos`), and at ~1.79e18 ns the f64 spacing is 256 ns, so live values
land up to 128 ns off the venue's (wire, 2026-09-21: 12 of 19 trades off, e.g. `...206000128` for
`...206000000`; audit D-62). A REST-backfilled copy of the same trade is exact, so without this the
archive would hold two clocks for one venue.
"""

import asyncio
import contextlib
import functools
from collections.abc import Callable
from typing import Any

from kernel.open_interest import OpenInterest

from capture.application.book_check import BookSnapshot
from capture.application.feed import MAIN_FEED
from capture.application.feed import Feed
from capture.application.feed import OptionalStepFailed
from capture.application.feed import optional_feed_send
from capture.application.feed import optional_feed_step
from capture.application.feed import report_unknown_message
from capture.application.ports import Ledger
from capture.application.wire_channels import Send
from capture.application.wire_channels import WireChannels
from capture.venues.hyperliquid.book_snapshot import fetch_l2_book
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.data import capsule_to_data


TRADES_FEED = Feed("main-trades", "main", trades_only=True)

# Wire calls per second across this client's sockets: a chosen margin, not a measured ceiling.
# Hyperliquid documents 2000 sent messages per minute per IP (~33/s). Observed 2026-09-29
# (DATA_DICTIONARY §1.14, `scripts/measure_ws_limits.py --venue hyperliquid`): one burst of 100
# subscribe frames (4 ms) accepted in total, and a 20/s ramp accepted up to 1000 channels. 10/s
# (600/min) keeps under a third of the documented rate, leaving the rest to the Rust client's
# unpaced reconnect replay and any other socket from this IP.
HYPERLIQUID_WS_FRAMES_PER_SECOND = 10.0

# Subscriptions per IP the venue accepts: documented, and observed 2026-09-29 (DATA_DICTIONARY
# §1.14): a ramp was acked up to 1000 channels and every further one refused with "Cannot
# subscribe to more than 1000 channels.". Enforced by `HyperliquidClient.subscribe` over what this
# client holds (a venue limit, not a plan cap).
HYPERLIQUID_MAX_WS_CHANNELS = 1000

# One wire subscription each; the four asset-context channels of an id share one `activeAssetCtx`.
_SINGLE_FRAME_CHANNELS = ("trades", "book", "twin-trades")
_ASSET_CTX_CHANNELS = ("mark", "index", "funding", "open_interest")

_NS_PER_MS = 1_000_000


def _at_exact_millis(trade: TradeTick) -> TradeTick:
    """
    Return `trade` with `ts_event` rounded to the nearest millisecond, in integer arithmetic.

    Exact recovery, not invention: the venue sends integer milliseconds and the adapter's f64
    error is at most 128 ns, far inside half a millisecond. A second boundary (`s * 10**9`,
    divisible by 512) is already exact, so the correction never moves a trade across a second.
    """
    ts_event = (trade.ts_event + _NS_PER_MS // 2) // _NS_PER_MS * _NS_PER_MS
    if ts_event == trade.ts_event:
        return trade
    return TradeTick(
        trade.instrument_id,
        trade.price,
        trade.size,
        trade.aggressor_side,
        trade.trade_id,
        ts_event,
        trade.ts_init,
    )


class HyperliquidClient:
    """
    Owns one Hyperliquid HTTP client and one WebSocket connection, plus an independent
    trades-only connection when `trade_feeds == 2`.

    `on_data` must be O(1) (append to a queue): it runs on the event loop via
    call_soon_threadsafe.
    """

    def __init__(
        self,
        on_data: Callable[[object, Feed], None],
        environment: str = "mainnet",
        trade_feeds: int = 1,
        *,
        ledger: Ledger,
    ) -> None:
        self._on_data = on_data
        self._ledger = ledger  # the collector's: a failed trades-only socket is ledgered there
        env = nautilus_pyo3.HyperliquidEnvironment.from_str(environment)  # type: ignore[attr-defined]
        self._http = nautilus_pyo3.HyperliquidHttpClient(environment=env)  # type: ignore[attr-defined]
        self._ws: Any = nautilus_pyo3.HyperliquidWebSocketClient(environment=env)  # type: ignore[attr-defined]
        self._ws_trades: Any = (
            nautilus_pyo3.HyperliquidWebSocketClient(environment=env)  # type: ignore[attr-defined]
            if trade_feeds == 2
            else None
        )
        self._environment = environment
        self._coins: dict[str, str] = {}  # instrument id -> wire coin (the instrument's raw_symbol)
        self._wire = WireChannels(HYPERLIQUID_WS_FRAMES_PER_SECOND)

    def _sockets(self) -> dict[Feed, Any]:
        sockets = {MAIN_FEED: self._ws}
        if self._ws_trades is not None:
            sockets[TRADES_FEED] = self._ws_trades
        return sockets

    def feed_states(self) -> dict[Feed, bool]:
        return {feed: ws.is_active() for feed, ws in self._sockets().items()}

    async def fetch_instruments(self) -> list:
        """Raw pyo3-native perp instruments; convert with `instruments_from_pyo3` for the catalog."""
        instruments = await self._http.load_instrument_definitions(
            include_spot=False, include_perps=True
        )
        self._coins = {str(i.id): i.raw_symbol.value for i in instruments}
        return instruments

    async def fetch_book_snapshot(self, instrument_id: str) -> BookSnapshot:
        """REST l2Book with its `time` for the aligned cross-check (story 22.5, audit D-64)."""
        return await fetch_l2_book(self._environment, self._coins[instrument_id])

    async def connect(self, loop: asyncio.AbstractEventLoop, instruments: list) -> None:
        await self._connect_socket(self._ws, MAIN_FEED, loop, instruments)
        if self._ws_trades is not None and not await optional_feed_step(
            TRADES_FEED,
            "connect",
            self._connect_socket(self._ws_trades, TRADES_FEED, loop, instruments),
            self._ledger,
        ):
            self._ws_trades = None

    async def _connect_socket(
        self, ws: Any, feed: Feed, loop: asyncio.AbstractEventLoop, instruments: list
    ) -> None:
        await ws.connect(loop, instruments, functools.partial(self._handle_message, feed))
        await ws.wait_until_active(30.0)

    async def disconnect(self) -> None:
        """Close every socket, even when one close fails (the first failure is re-raised)."""
        failure: Exception | None = None
        for ws in self._sockets().values():
            try:
                if not ws.is_closed():
                    await ws.close()
            except Exception as e:
                failure = failure or e
        if failure is not None:
            raise failure

    def _channels(self, instrument_id: str) -> list[tuple[str, Send, Send, bool]]:
        """
        Return the id's main-socket channels: (name, subscribe, unsubscribe, undoable). Only the
        asset-context four change Rust state before their fallible send, so only they are undone.
        """
        iid = nautilus_pyo3.InstrumentId.from_str(instrument_id)
        ws = self._ws
        return [
            ("trades", lambda: ws.subscribe_trades(iid), lambda: ws.unsubscribe_trades(iid), False),
            ("book", lambda: ws.subscribe_book(iid), lambda: ws.unsubscribe_book(iid), False),
            (
                "mark",
                lambda: ws.subscribe_mark_prices(iid),
                lambda: ws.unsubscribe_mark_prices(iid),
                True,
            ),
            (
                "index",
                lambda: ws.subscribe_index_prices(iid),
                lambda: ws.unsubscribe_index_prices(iid),
                True,
            ),
            (
                "funding",
                lambda: ws.subscribe_funding_rates(iid),
                lambda: ws.unsubscribe_funding_rates(iid),
                True,
            ),
            (
                "open_interest",
                lambda: ws.subscribe_open_interest(iid),
                lambda: ws.unsubscribe_open_interest(iid),
                True,
            ),
        ]

    def wire_subscriptions(self) -> int:
        """Return the venue subscriptions this client holds (`HYPERLIQUID_MAX_WS_CHANNELS` counts)."""
        held = self._wire.held()
        singles = sum(1 for name, _ in held if name in _SINGLE_FRAME_CHANNELS)
        return singles + len({iid for name, iid in held if name in _ASSET_CTX_CHANNELS})

    def _needed_subscriptions(self, instrument_id: str) -> int:
        names = ["trades", "book"] + (["twin-trades"] if self._ws_trades is not None else [])
        needed = sum(1 for name in names if not self._wire.is_held((name, instrument_id)))
        if not any(self._wire.is_held((name, instrument_id)) for name in _ASSET_CTX_CHANNELS):
            needed += 1
        return needed

    async def subscribe(self, instrument_id: str) -> None:
        """
        Hold every channel of the id not already held: a retry sends only the missing ones.
        Refused before sending anything when it would pass the venue's channel budget.
        """
        held, needed = self.wire_subscriptions(), self._needed_subscriptions(instrument_id)
        if held + needed > HYPERLIQUID_MAX_WS_CHANNELS:
            raise RuntimeError(
                f"{instrument_id} needs {needed} more Hyperliquid subscriptions; {held} held, "
                f"the venue accepts {HYPERLIQUID_MAX_WS_CHANNELS} channels per IP"
            )
        for name, sub, unsub, undoable in self._channels(instrument_id):
            await self._wire.hold((name, instrument_id), sub, undo=unsub if undoable else None)
        if self._ws_trades is None:
            return
        iid = nautilus_pyo3.InstrumentId.from_str(instrument_id)
        twin = self._ws_trades
        # Optional: a failure is ledgered (`collector.trade_feed`), never fatal, and leaves the
        # channel unheld. The id still counts as applied, so capture's retry loop does not retry
        # it: only the next `subscribe` of the id does (a re-add, a restart), and meanwhile the
        # id has one feed.
        with contextlib.suppress(OptionalStepFailed):
            await self._wire.hold(
                ("twin-trades", instrument_id),
                lambda: optional_feed_send(
                    TRADES_FEED, "subscribe", twin.subscribe_trades(iid), self._ledger
                ),
            )

    async def unsubscribe(self, instrument_id: str) -> None:
        """
        Release every held channel of the id, each once; a failure (the trades-only socket's
        included, so it never leaks a subscription) raises for capture to ledger and retry.
        """
        for name, sub, unsub, undoable in self._channels(instrument_id):
            await self._wire.release((name, instrument_id), unsub, undo=sub if undoable else None)
        if self._ws_trades is not None:
            iid = nautilus_pyo3.InstrumentId.from_str(instrument_id)
            twin = self._ws_trades
            await self._wire.release(
                ("twin-trades", instrument_id), lambda: twin.unsubscribe_trades(iid)
            )

    def _handle_message(self, feed: Feed, message: object) -> None:
        # Trades/book/mark/index arrive as PyCapsules; funding as a pyo3 object; open
        # interest as a pyo3 CustomData wrapper (mirrors nautilus_trader/adapters/hyperliquid/data.py).
        if nautilus_pyo3.is_pycapsule(message):
            data = capsule_to_data(message)
            if isinstance(data, TradeTick):
                data = _at_exact_millis(data)
            self._on_data(data, feed)
        elif isinstance(message, nautilus_pyo3.FundingRateUpdate):
            self._on_data(FundingRateUpdate.from_pyo3(message), feed)
        elif isinstance(message, nautilus_pyo3.CustomData) and isinstance(
            message.data,
            nautilus_pyo3.HyperliquidOpenInterest,  # type: ignore[attr-defined]
        ):
            self._on_data(OpenInterest.from_pyo3(message.data), feed)
        else:
            report_unknown_message(message, self._ledger, feed)
