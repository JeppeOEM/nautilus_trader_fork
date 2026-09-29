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
One-off evidence tool for story 29.4 (not run in production): measures each venue's public
WebSocket subscribe limits live, from an independent client (aiohttp, zero shared code with the
collector's Rust clients), and prints one JSON summary. The numbers it printed on 2026-09-28/29 are
recorded in `docs/DATA_DICTIONARY.md` §1.14 and set `BYBIT_WS_FRAMES_PER_SECOND` /
`HYPERLIQUID_WS_FRAMES_PER_SECOND`.

Bybit (`linear` and `spot`): (a) how many topics one subscribe request may carry (one request of
N `publicTrade` args per fresh connection); (b) a burst of single-topic subscribe requests sent
back to back on one connection, then the matching unsubscribe burst: acks, failures, close.

Hyperliquid: (a) a burst of subscribe frames sent back to back on one connection; (b) a ramp at
a gentle rate (under the documented 2000 sent messages per minute per IP) across `trades`,
`l2Book`, `activeAssetCtx`, `bbo` and three `candle` intervals for every coin, until the first `error` reply.

Never run `--venue hyperliquid` from a host whose IP also runs `hyperliquid_collector` or
`live-paper` (the VPS): the ramp takes all 1000 channels that IP may hold for as long as it runs,
so the collector's own subscribes are refused meanwhile. Run it from a workstation.

    PYTHONPATH=. python scripts/measure_ws_limits.py --venue bybit > /tmp/bybit_limits.json
    PYTHONPATH=. python scripts/measure_ws_limits.py --venue hyperliquid > /tmp/hl_limits.json
"""

import argparse
import asyncio
import json
import sys
import time
from typing import Any

import aiohttp
from kernel.venue_http import bybit_url
from kernel.venue_http import get_request
from kernel.venue_http import http_json
from kernel.venue_http import hyperliquid_info_url
from kernel.venue_http import post_json_request


_BYBIT_WS = "wss://stream.bybit.com/v5/public/"
_HL_WS = "wss://api.hyperliquid.xyz/ws"
_BYBIT_ARG_COUNTS = (10, 11, 20, 50)
_BYBIT_BURST = 200
_HL_BURST = 100
_HL_RAMP_PER_SECOND = 20.0  # 1200/min: under Hyperliquid's documented 2000 sent messages/min
_HL_RAMP_MAX = 1100  # past the documented 1000 subscriptions per IP
_HL_COOLDOWN_SECONDS = 65.0  # lets the burst's frames leave the per-minute window first
_REPLY_TIMEOUT = 15.0


def _log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def bybit_symbols(category: str) -> list[str]:
    """Every trading symbol of the category, from Bybit's REST instruments-info."""
    path = f"/v5/market/instruments-info?category={category}&limit=1000"
    body = http_json(get_request(bybit_url("mainnet", path)))
    return [row["symbol"] for row in body["result"]["list"] if row["status"] == "Trading"]


async def _bybit_replies(
    ws: aiohttp.ClientWebSocketResponse, wanted: set[str], timeout: float
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Collect the replies whose `req_id` is wanted; also report whether the socket closed."""
    replies: dict[str, dict[str, Any]] = {}
    deadline = time.monotonic() + timeout
    while wanted - replies.keys() and (remaining := deadline - time.monotonic()) > 0:
        try:
            msg = await ws.receive(timeout=remaining)
        except TimeoutError:
            break
        if msg.type != aiohttp.WSMsgType.TEXT:
            return replies, True
        frame = json.loads(msg.data)
        if frame.get("req_id") in wanted:
            replies[frame["req_id"]] = frame
    return replies, False


async def bybit_args_per_request(
    session: aiohttp.ClientSession, url: str, symbols: list[str]
) -> list[dict[str, Any]]:
    """One subscribe request of N topics per fresh connection: accepted or refused, and why."""
    results = []
    for count in _BYBIT_ARG_COUNTS:
        args = [f"publicTrade.{symbol}" for symbol in symbols[:count]]
        async with session.ws_connect(url) as ws:
            await ws.send_json({"op": "subscribe", "req_id": f"args{count}", "args": args})
            replies, closed = await _bybit_replies(ws, {f"args{count}"}, _REPLY_TIMEOUT)
        reply = replies.get(f"args{count}", {})
        results.append(
            {
                "args": count,
                "success": reply.get("success"),
                "ret_msg": reply.get("ret_msg"),
                "closed": closed,
            }
        )
    return results


async def _bybit_burst(
    ws: aiohttp.ClientWebSocketResponse, op: str, symbols: list[str]
) -> dict[str, Any]:
    ids = [f"{op}{i}" for i in range(len(symbols))]
    started = time.monotonic()
    for req_id, symbol in zip(ids, symbols, strict=True):
        await ws.send_json({"op": op, "req_id": req_id, "args": [f"publicTrade.{symbol}"]})
    sent_s = time.monotonic() - started
    replies, closed = await _bybit_replies(ws, set(ids), _REPLY_TIMEOUT)
    failures = [r.get("ret_msg") for r in replies.values() if not r.get("success")]
    return {
        "op": op,
        "requests": len(ids),
        "send_seconds": round(sent_s, 4),
        "acks": len(replies),
        "success": len(replies) - len(failures),
        "failed": len(failures),
        "fail_messages": sorted(set(map(str, failures))),
        "acked_within_seconds": round(time.monotonic() - started, 3),
        "closed": closed,
    }


async def bybit_request_burst(
    session: aiohttp.ClientSession, url: str, symbols: list[str]
) -> dict[str, Any]:
    """K single-topic subscribes back to back, then the K unsubscribes, then a ping."""
    burst = symbols[:_BYBIT_BURST]
    async with session.ws_connect(url) as ws:
        subscribe = await _bybit_burst(ws, "subscribe", burst)
        unsubscribe = await _bybit_burst(ws, "unsubscribe", burst)
        await ws.send_json({"op": "ping", "req_id": "alive"})
        pong, _ = await _bybit_replies(ws, {"alive"}, _REPLY_TIMEOUT)
    return {"subscribe": subscribe, "unsubscribe": unsubscribe, "alive_after": bool(pong)}


async def measure_bybit() -> dict[str, Any]:
    summary: dict[str, Any] = {
        "venue": "bybit",
        "measured_at": time.strftime("%FT%TZ", time.gmtime()),
    }
    async with aiohttp.ClientSession() as session:
        for category in ("linear", "spot"):
            symbols = bybit_symbols(category)
            url = _BYBIT_WS + category
            _log(f"bybit {category}: {len(symbols)} symbols")
            summary[category] = {
                "symbols": len(symbols),
                "args_per_request": await bybit_args_per_request(session, url, symbols),
                "request_burst": await bybit_request_burst(session, url, symbols),
            }
    return summary


def hyperliquid_coins() -> list[str]:
    """Every listed perp coin, from Hyperliquid's REST `meta`."""
    body = http_json(post_json_request(hyperliquid_info_url("mainnet"), {"type": "meta"}))
    return [row["name"] for row in body["universe"] if not row.get("isDelisted")]


def _hl_subscriptions(coins: list[str]) -> list[dict[str, Any]]:
    kinds: list[dict[str, Any]] = []
    for coin in coins:
        kinds += [
            {"type": "trades", "coin": coin},
            {"type": "l2Book", "coin": coin},
            {"type": "activeAssetCtx", "coin": coin},
            {"type": "bbo", "coin": coin},
            {"type": "candle", "coin": coin, "interval": "1m"},
            {"type": "candle", "coin": coin, "interval": "5m"},
            {"type": "candle", "coin": coin, "interval": "15m"},
        ]
    return kinds


class _HlTally:
    """Counts subscribe acks and `error` replies read off one socket, with the first error's index."""

    def __init__(self) -> None:
        self.acks = 0
        self.errors: list[str] = []
        self.first_error_after_sent: int | None = None
        self.closed = False
        self.sent = 0

    async def read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                break
            frame = json.loads(msg.data)
            channel = frame.get("channel")
            if channel == "subscriptionResponse":
                self.acks += 1
            elif channel == "error":
                if self.first_error_after_sent is None:
                    self.first_error_after_sent = self.sent
                self.errors.append(str(frame.get("data")))
        self.closed = True

    def summary(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "acks": self.acks,
            "errors": len(self.errors),
            "first_error_after_sent": self.first_error_after_sent,
            "error_samples": sorted(set(self.errors))[:5],
            "closed": self.closed,
        }


async def _hl_send(
    session: aiohttp.ClientSession, subs: list[dict[str, Any]], interval: float
) -> dict[str, Any]:
    tally = _HlTally()
    async with session.ws_connect(_HL_WS, heartbeat=20) as ws:
        reader = asyncio.create_task(tally.read(ws))
        started = time.monotonic()
        for sub in subs:
            if tally.closed or len(tally.errors) >= 10:
                break
            await ws.send_json({"method": "subscribe", "subscription": sub})
            tally.sent += 1
            if interval:
                await asyncio.sleep(interval)
        sent_s = time.monotonic() - started
        await asyncio.sleep(_REPLY_TIMEOUT)
        closed_by_venue = tally.closed
        await ws.close()
        await reader
    return {**tally.summary(), "closed": closed_by_venue, "send_seconds": round(sent_s, 3)}


async def measure_hyperliquid() -> dict[str, Any]:
    coins = hyperliquid_coins()
    _log(f"hyperliquid: {len(coins)} coins")
    subs = _hl_subscriptions(coins)
    burst = [s for s in subs if s["type"] in ("trades", "l2Book")][:_HL_BURST]
    async with aiohttp.ClientSession() as session:
        burst_result = await _hl_send(session, burst, 0.0)
        _log(f"burst: {burst_result}; cooling down {_HL_COOLDOWN_SECONDS}s")
        await asyncio.sleep(_HL_COOLDOWN_SECONDS)
        ramp = await _hl_send(session, subs[:_HL_RAMP_MAX], 1 / _HL_RAMP_PER_SECOND)
    return {
        "venue": "hyperliquid",
        "measured_at": time.strftime("%FT%TZ", time.gmtime()),
        "coins": len(coins),
        "burst": burst_result,
        "ramp": {**ramp, "per_second": _HL_RAMP_PER_SECOND},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", choices=("bybit", "hyperliquid"), required=True)
    venue = ap.parse_args().venue
    measure = measure_bybit if venue == "bybit" else measure_hyperliquid
    print(json.dumps(asyncio.run(measure()), indent=2))
