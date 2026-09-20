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
One-off evidence capture for story 22.5 (not run in production): raw Hyperliquid / Bybit WS
frames from an independent client (aiohttp, zero shared code with the collector), each stamped
with its arrival `time.time_ns()`, as JSONL. Answers (a) does the trade channel replay history
on subscribe, (b) the real Hyperliquid `l2Book` push cadence, (c) is Bybit's `u` contiguous.
The Rust clients cannot supply this: Cargo.toml's `release_max_level_debug` compiles the
Bybit handler's `log::trace!` raw-frame line out of release builds, and Hyperliquid has none.

    python scripts/capture_hl_ws.py --coin BTC --seconds 900 --out /tmp/hl_btc.jsonl
    python scripts/capture_hl_ws.py --venue bybit --coin BTCUSDT --seconds 900 --out /tmp/by.jsonl
    python scripts/capture_hl_ws.py --summarize /tmp/hl_btc.jsonl   # venue detected from frames
"""

import argparse
import asyncio
import json
import statistics
import time

import aiohttp


_BYBIT_URL = "wss://stream.bybit.com/v5/public/linear"


async def capture(venue: str, coin: str, seconds: float, out: str, url: str | None) -> None:
    if venue == "bybit":
        url = url or _BYBIT_URL
        subs = [{"op": "subscribe", "args": [f"publicTrade.{coin}", f"orderbook.50.{coin}"]}]
    else:
        url = url or "wss://api.hyperliquid.xyz/ws"
        subs = [
            {"method": "subscribe", "subscription": {"type": "trades", "coin": coin}},
            {"method": "subscribe", "subscription": {"type": "l2Book", "coin": coin}},
        ]
    deadline = time.monotonic() + seconds
    with open(out, "w") as f:
        async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
            await _pump(ws, subs, deadline, f)


async def _pump(ws: aiohttp.ClientWebSocketResponse, subs: list, deadline: float, f) -> None:
    for sub in subs:
        await ws.send_json(sub)
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            msg = await ws.receive(timeout=remaining)
        except TimeoutError:
            break
        if msg.type != aiohttp.WSMsgType.TEXT:
            break
        f.write(json.dumps({"recv_ns": time.time_ns(), "raw": json.loads(msg.data)}) + "\n")


def summarize_bybit(rows: list) -> None:
    trades = [r for r in rows if str(r["raw"].get("topic", "")).startswith("publicTrade")]
    if trades:
        head = trades[0]
        ages = sorted((head["recv_ns"] / 1e6 - t["T"]) / 1000 for t in head["raw"]["data"])
        print(f"first publicTrade frame: {len(ages)} trades, age {ages[0]:.1f}s..{ages[-1]:.1f}s")
        print(f"publicTrade frames: {len(trades)}")
    books = [r["raw"] for r in rows if str(r["raw"].get("topic", "")).startswith("orderbook")]
    us = [(b["type"], b["data"]["u"]) for b in books]
    deltas = [u for t, u in us if t == "delta"]
    steps = [b - a for a, b in zip(deltas, deltas[1:], strict=False)]
    print(
        f"orderbook: {len(books)} frames, {len(us) - len(deltas)} snapshot(s); delta u steps: "
        f"+1 x{steps.count(1)}, gaps(>1) x{sum(1 for s in steps if s > 1)}, "
        f"regress(<=0) x{sum(1 for s in steps if s <= 0)}; max step {max(steps, default=0)}"
    )


def summarize(path: str) -> None:
    rows = [json.loads(line) for line in open(path)]
    if any("topic" in r["raw"] for r in rows):
        return summarize_bybit(rows)
    first = rows[0]["recv_ns"] if rows else 0
    trades = [r for r in rows if r["raw"].get("channel") == "trades"]
    books = [r for r in rows if r["raw"].get("channel") == "l2Book"]
    if trades:
        # The subscribe reply is the first `trades` frame: its age spread is the replay bound.
        head = trades[0]
        ages = sorted((head["recv_ns"] / 1e6 - t["time"]) / 1000 for t in head["raw"]["data"])
        print(f"first trades frame: {len(ages)} trades, age {ages[0]:.1f}s..{ages[-1]:.1f}s")
        print(f"trades frames: {len(trades)}; first at +{(head['recv_ns'] - first) / 1e9:.2f}s")
    gaps = [(b["recv_ns"] - a["recv_ns"]) / 1e9 for a, b in zip(books, books[1:], strict=False)]
    if gaps:
        changed = sum(
            1
            for a, b in zip(books, books[1:], strict=False)
            if a["raw"]["data"]["levels"] != b["raw"]["data"]["levels"]
        )
        print(
            f"l2Book: {len(books)} frames, gap median {statistics.median(gaps):.2f}s "
            f"min {min(gaps):.2f}s max {max(gaps):.2f}s; levels changed in {changed}/{len(gaps)}"
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--coin", default="BTC")
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--out", default="hl_capture.jsonl")
    ap.add_argument("--venue", choices=("hyperliquid", "bybit"), default="hyperliquid")
    ap.add_argument("--url")
    ap.add_argument("--summarize", metavar="JSONL")
    args = ap.parse_args()
    if args.summarize:
        summarize(args.summarize)
    else:
        asyncio.run(capture(args.venue, args.coin, args.seconds, args.out, args.url))
