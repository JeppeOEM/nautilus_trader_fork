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
What the reference recorder subscribes and polls, and which channel file every received frame is
filed under -- written from the venues' published wire formats, never from the collectors' clients
(the reference side never imports the code it checks).

Invariant: one plan yields exactly one set of endpoints (WebSocket connections), REST polls and
channel names, so the recorder, its fixture tool and every later comparator agree on where a
frame of a given kind lives. Channels:

- Bybit: `<category>.<topic kind>` (`linear.orderbook.50`, `linear.publicTrade`, `linear.tickers`,
  `spot.orderbook.50`, `spot.publicTrade`) -- the category is part of the name because linear and
  spot share topic names -- plus `<category>.control` (subscribe and ping replies) and
  `<category>.rest.<request>` for the REST polls.
- Hyperliquid: the wire `channel` (`l2Book`, `trades`, `activeAssetCtx`), `control`
  (`subscriptionResponse`, `pong`, `error`) and `rest.<request type>`.
- Both: `connection` (every open/close/error), `unparsed` (not JSON) and `unknown` (JSON the
  tables below do not name).

Known limit (common mode): the wire symbols and coins come from `kernel.venues` (`bybit_category`,
`market_suffix`, `base_symbol`) and every URL from `kernel.venue_http` -- the same two modules the
collectors use. A bug there (a wrong symbol, a wrong host) would misdirect both sides alike, and
the comparison would agree on wrong data. The REST cross-polls catch it only partly: they are
built from the same helpers, but their responses name the symbol the venue actually served
(`result.symbol` / `coin`), which a comparator can check against the instrument id. Upgrade path:
a second, independently written id-to-wire table in this context, cross-checked against the
kernel's in a test.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from typing import NamedTuple

from kernel.venues import base_symbol
from kernel.venues import bybit_category
from kernel.venues import market_kind
from kernel.venues import market_suffix

from verification.domain.plan_file import RecordingPlan


BYBIT = "BYBIT"
HYPERLIQUID = "HYPERLIQUID"
VENUES = (BYBIT, HYPERLIQUID)

CONNECTION_CHANNEL = "connection"
UNPARSED_CHANNEL = "unparsed"
UNKNOWN_CHANNEL = "unknown"

# Frame anomalies: each is filed (never dropped) and ledgered by the recorder.
ANOMALY_UNPARSED = "unparsed"
ANOMALY_UNKNOWN = "unknown"
ANOMALY_VENUE_ERROR = "venue_error"

# Bybit's documented keepalive: a `{"op":"ping"}` every 20 s. Spot refuses a subscribe message
# carrying more than 10 args, so every category is chunked to 10.
BYBIT_PING = '{"op": "ping"}'
BYBIT_PING_SECONDS = 20.0
BYBIT_ARGS_PER_MESSAGE = 10
BYBIT_BOOK_DEPTH = 50
# Known limit: only the two categories the collectors record (Story 29.3) are recorded; an inverse
# or option id is refused rather than subscribed with topics nobody verified on the wire. Upgrade
# path: capture that category's frames first (platform/CLAUDE.md "Adding a venue", step 1).
BYBIT_CATEGORIES = ("linear", "spot")
_BYBIT_LINEAR_KINDS = (f"orderbook.{BYBIT_BOOK_DEPTH}", "publicTrade", "tickers")
_BYBIT_SPOT_KINDS = (f"orderbook.{BYBIT_BOOK_DEPTH}", "publicTrade")
_BYBIT_TOPIC_KINDS = frozenset(_BYBIT_LINEAR_KINDS)

# Hyperliquid's documented keepalive: `{"method":"ping"}`, answered on channel `pong`; the server
# drops a connection silent for 60 s, so every 30 s.
HYPERLIQUID_PING = '{"method": "ping"}'
HYPERLIQUID_PING_SECONDS = 30.0
HYPERLIQUID_ENDPOINT = "ws"
HYPERLIQUID_REST_ENDPOINT = "info"
HYPERLIQUID_KINDS = ("l2Book", "trades", "activeAssetCtx")
_HYPERLIQUID_CONTROL = frozenset({"subscriptionResponse", "pong"})

# Data silence that forces a reconnect. Keepalive replies do not count: a socket that still
# answers pings while its subscriptions have died must be caught. Bybit's linear book pushes every
# 20 ms and its ticker every 100 ms; Hyperliquid's l2Book pushed every 5.38 s median (max 5.96 s)
# in the Story 22.5 capture (capture/venues/hyperliquid/config.toml) and activeAssetCtx every few
# seconds -- so 30 s of silence on every data channel of an endpoint is a dead feed on both.
# Known limit: staleness is judged per endpoint, not per topic, so one topic the venue silently
# stops pushing while its siblings on the same socket keep flowing is not detected here (a refused
# subscribe *is*: Bybit's `success: false` and Hyperliquid's `error` channel are ledgered at
# `verification.recorder.venue_error`). The comparators of Stories 31.4-31.6 see such a topic as a
# gap in the reference. Upgrade path: a per-topic watchdog with a per-topic bound.
STALE_FEED_SECONDS = 30.0


@dataclass(frozen=True)
class Endpoint:
    """
    One WebSocket connection: its URL, what it subscribes (`subscriptions`, the labels written on
    its `open` line; `subscribe_messages`, the frames sent), its keepalive and the data channels a
    connection line is also filed into, so any one channel file shows its own gaps.
    """

    name: str
    url: str
    subscriptions: tuple[str, ...]
    subscribe_messages: tuple[str, ...]
    ping_message: str
    ping_seconds: float
    stale_seconds: float
    data_channels: tuple[str, ...]


@dataclass(frozen=True)
class RestPoll:
    """One REST request repeated every `every_seconds`: a GET path+query or a POST JSON body."""

    endpoint: str
    name: str
    method: str
    request: str
    every_seconds: float
    channel: str


class Classified(NamedTuple):
    """Where a frame is filed, and the anomaly the recorder ledgers for it (None when normal)."""

    channel: str
    anomaly: str | None


def _chunks(items: tuple[str, ...], size: int) -> list[tuple[str, ...]]:
    return [items[start : start + size] for start in range(0, len(items), size)]


def bybit_symbol(instrument_id: str) -> str:
    """
    Return the Bybit wire symbol of a Nautilus Bybit id: the symbol minus its category segment
    (`BTCUSDT-LINEAR.BYBIT` -> `BTCUSDT`, `BTCUSDT-25SEP26-LINEAR.BYBIT` -> `BTCUSDT-25SEP26`).
    """
    bybit_category(instrument_id)  # refuses a malformed or non-Bybit id
    suffix = market_suffix(instrument_id) or ""
    symbol = instrument_id.rpartition(".")[0]
    return symbol[: -(len(suffix) + 1)]


def _bybit_kinds(category: str) -> tuple[str, ...]:
    return _BYBIT_LINEAR_KINDS if category == "linear" else _BYBIT_SPOT_KINDS


def _bybit_symbols_by_category(plan: RecordingPlan) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for iid in plan.instruments:
        category = bybit_category(iid)
        if category not in BYBIT_CATEGORIES:
            raise ValueError(f"{iid}: Bybit category {category!r} is not recorded")
        grouped.setdefault(category, []).append(bybit_symbol(iid))
    return grouped


def _bybit_endpoint(category: str, symbols: list[str], url: str) -> Endpoint:
    kinds = _bybit_kinds(category)
    topics = tuple(f"{kind}.{symbol}" for symbol in symbols for kind in kinds)
    return Endpoint(
        name=category,
        url=url,
        subscriptions=topics,
        subscribe_messages=tuple(
            json.dumps({"op": "subscribe", "args": list(chunk)})
            for chunk in _chunks(topics, BYBIT_ARGS_PER_MESSAGE)
        ),
        ping_message=BYBIT_PING,
        ping_seconds=BYBIT_PING_SECONDS,
        stale_seconds=STALE_FEED_SECONDS,
        data_channels=tuple(f"{category}.{kind}" for kind in kinds),
    )


def hyperliquid_coin(instrument_id: str) -> str:
    """Return the Hyperliquid wire coin of a perp id (`SOL-USD-PERP.HYPERLIQUID` -> `SOL`)."""
    if market_kind(instrument_id) != "perp":
        raise ValueError(f"{instrument_id}: only Hyperliquid perps are recorded")
    return base_symbol(instrument_id)


def _hyperliquid_endpoint(plan: RecordingPlan, url: str) -> Endpoint:
    coins = tuple(hyperliquid_coin(iid) for iid in plan.instruments)
    pairs = [(kind, coin) for coin in coins for kind in HYPERLIQUID_KINDS]
    return Endpoint(
        name=HYPERLIQUID_ENDPOINT,
        url=url,
        subscriptions=tuple(f"{kind}.{coin}" for kind, coin in pairs),
        subscribe_messages=tuple(
            json.dumps({"method": "subscribe", "subscription": {"type": kind, "coin": coin}})
            for kind, coin in pairs
        ),
        ping_message=HYPERLIQUID_PING,
        ping_seconds=HYPERLIQUID_PING_SECONDS,
        stale_seconds=STALE_FEED_SECONDS,
        data_channels=HYPERLIQUID_KINDS,
    )


def ws_endpoints(plan: RecordingPlan, url_for: Callable[[str], str]) -> tuple[Endpoint, ...]:
    """
    Return the plan's WebSocket endpoints, `url_for(endpoint name)` giving each one's URL (the
    composition root passes `kernel.venue_http`'s builders; a test passes a local server). An empty
    plan has none.
    """
    if not plan.instruments:
        return ()
    if plan.venue == HYPERLIQUID:
        return (_hyperliquid_endpoint(plan, url_for(HYPERLIQUID_ENDPOINT)),)
    if plan.venue != BYBIT:
        raise ValueError(f"no reference recorder for venue {plan.venue!r}")
    grouped = _bybit_symbols_by_category(plan)
    return tuple(
        _bybit_endpoint(category, grouped[category], url_for(category))
        for category in BYBIT_CATEGORIES
        if category in grouped
    )


# `recent-trade`'s `limit` maximum per category (Bybit v5 docs: spot 60, linear 1000). A sample of
# the latest trades for spot-checking ids and prices, never a completeness oracle: at a busy
# moment more than `limit` trades can happen between two polls 30 s apart -- the WS `publicTrade`
# stream is the complete record.
BYBIT_RECENT_TRADE_LIMITS = MappingProxyType({"linear": 1000, "spot": 60})


def _bybit_poll(category: str, name: str, query: str, every_seconds: int) -> RestPoll:
    return RestPoll(
        endpoint=category,
        name=name,
        method="GET",
        request=f"/v5/market/{name}?{query}",
        every_seconds=every_seconds,
        channel=f"{category}.rest.{name}",
    )


def _bybit_polls_of(instrument_id: str) -> list[RestPoll]:
    category, symbol = bybit_category(instrument_id), bybit_symbol(instrument_id)
    base = f"category={category}&symbol={symbol}"
    polls = [_bybit_poll(category, "instruments-info", base, 30)]
    if category == "linear":
        oi_query = f"{base}&intervalTime=5min&limit=1"
        polls.append(_bybit_poll(category, "open-interest", oi_query, 30))
        # The collector's open interest source (`capture/venues/bybit/open_interest.py`) is this
        # endpoint's `openInterest`, polled category-wide (`tickers?category=linear`); the
        # `open-interest` history endpoint above is the venue's second, independent view.
        # Known limit: polled per symbol, not category-wide as the collector does -- the same
        # endpoint and row, but the category-wide list is ~650 KB (~100 KB zstd) per poll, about
        # 300 MB/day at 30 s for rows nothing compares. Upgrade path: the category-wide request,
        # if a comparator ever needs the collector's exact response.
        polls.append(_bybit_poll(category, "tickers", base, 30))
    limit = BYBIT_RECENT_TRADE_LIMITS[category]
    polls.append(_bybit_poll(category, "recent-trade", f"{base}&limit={limit}", 30))
    polls.append(_bybit_poll(category, "orderbook", f"{base}&limit={BYBIT_BOOK_DEPTH}", 60))
    return polls


def _hyperliquid_poll(body: dict[str, Any], every_seconds: int) -> RestPoll:
    return RestPoll(
        endpoint=HYPERLIQUID_REST_ENDPOINT,
        name=str(body["type"]),
        method="POST",
        request=json.dumps(body),
        every_seconds=every_seconds,
        channel=f"rest.{body['type']}",
    )


def rest_polls(plan: RecordingPlan) -> tuple[RestPoll, ...]:
    """
    Return the plan's REST polls: Bybit `instruments-info`, `open-interest` and `tickers` (both
    linear only) and `recent-trade` every 30 s and `orderbook` every 60 s per instrument; Hyperliquid
    `metaAndAssetCtxs` every 30 s (one request covers every asset) and `l2Book` every 60 s per coin.
    """
    if not plan.instruments:
        return ()
    if plan.venue == BYBIT:
        return tuple(poll for iid in plan.instruments for poll in _bybit_polls_of(iid))
    if plan.venue != HYPERLIQUID:
        raise ValueError(f"no reference recorder for venue {plan.venue!r}")
    books = [
        _hyperliquid_poll({"type": "l2Book", "coin": hyperliquid_coin(iid)}, 60)
        for iid in plan.instruments
    ]
    return (_hyperliquid_poll({"type": "metaAndAssetCtxs"}, 30), *books)


def _classify_bybit(endpoint: str, message: dict[str, Any]) -> Classified:
    topic = message.get("topic")
    if isinstance(topic, str):
        kind = topic.rpartition(".")[0]
        if kind in _BYBIT_TOPIC_KINDS:
            return Classified(f"{endpoint}.{kind}", None)
        return Classified(UNKNOWN_CHANNEL, ANOMALY_UNKNOWN)
    if "op" in message:
        refused = message.get("success") is False
        return Classified(f"{endpoint}.control", ANOMALY_VENUE_ERROR if refused else None)
    return Classified(UNKNOWN_CHANNEL, ANOMALY_UNKNOWN)


def _classify_hyperliquid(message: dict[str, Any]) -> Classified:
    channel = message.get("channel")
    if not isinstance(channel, str):  # absent, or a list/object a set lookup could not hash
        return Classified(UNKNOWN_CHANNEL, ANOMALY_UNKNOWN)
    if channel in HYPERLIQUID_KINDS:
        return Classified(str(channel), None)
    if channel in _HYPERLIQUID_CONTROL:
        return Classified("control", None)
    if channel == "error":
        return Classified("control", ANOMALY_VENUE_ERROR)
    return Classified(UNKNOWN_CHANNEL, ANOMALY_UNKNOWN)


def classify_frame(venue: str, endpoint: str, text: str) -> Classified:
    """
    File one received text frame: its channel, and the anomaly when it is not JSON (`unparsed`),
    not a shape the tables name (`unknown`) or a refusal by the venue (`venue_error`).
    """
    try:
        message = json.loads(text)
    except ValueError:
        return Classified(UNPARSED_CHANNEL, ANOMALY_UNPARSED)
    if not isinstance(message, dict):
        return Classified(UNKNOWN_CHANNEL, ANOMALY_UNKNOWN)
    if venue == BYBIT:
        return _classify_bybit(endpoint, message)
    return _classify_hyperliquid(message)


def rest_refusal(venue: str, status: int, text: str | None) -> str | None:
    """
    Why a REST response is a failed poll, or None when it is a good one: a non-2xx status, a body
    that is not UTF-8 JSON, a JSON `null` (Hyperliquid answers some bad requests with 200 and
    `null`), Bybit's in-band refusal (`retCode` other than 0 under HTTP 200), or a Bybit
    `result.list` that is empty -- Bybit's answer for a symbol it does not list (a delisted or
    mistyped id), which every recorded instrument must never get.
    """
    if not 200 <= status < 300:
        return f"HTTP {status}"
    if text is None:
        return "body is not UTF-8"
    try:
        body = json.loads(text)
    except ValueError:
        return "body is not JSON"
    if body is None:
        return "body is JSON null"
    if venue == BYBIT:
        code = body.get("retCode") if isinstance(body, dict) else None
        if code != 0:
            return f"retCode {code!r}"
        result = body.get("result")
        if isinstance(result, dict) and result.get("list") == []:
            return "result.list is empty"
    return None
