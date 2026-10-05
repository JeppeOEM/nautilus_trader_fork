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
Story 33.1 added `--topic` (repeatable; Bybit topic prefixes such as `allLiquidation` or
`publicTrade`, Hyperliquid subscription types such as `trades`; default: the trade and book topics
above) and comma-separated `--coin`, for the liquidation wire investigation
(`docs/DATA_DICTIONARY.md` §1.26); `--summarize` then reports the `allLiquidation` frames too.
Story 33.2: for Hyperliquid `trades`, `--summarize` also counts each coin's live trades carrying
the all-zero, non-transaction `hash` (`scripts/hl_liquidation_probe.py` settles what they are).
The Rust clients cannot supply this: Cargo.toml's `release_max_level_debug` compiles the
Bybit handler's `log::trace!` raw-frame line out of release builds, and Hyperliquid has none.

Run from `platform/` with `PYTHONPATH=.` (the definitions fetch goes through `kernel.venue_http`):

    PYTHONPATH=. python scripts/capture_hl_ws.py --coin BTC --seconds 900 --out /tmp/hl_btc.jsonl
    PYTHONPATH=. python scripts/capture_hl_ws.py --venue bybit --coin BTCUSDT --seconds 900 \
        --out /tmp/by.jsonl
    PYTHONPATH=. python scripts/capture_hl_ws.py --summarize /tmp/hl_btc.jsonl  # venue from frames
    PYTHONPATH=. python scripts/capture_hl_ws.py --venue bybit --coin BTCUSDT,ETHUSDT \
        --topic allLiquidation --topic publicTrade --seconds 1800 --out /tmp/by_liq.jsonl
"""

import argparse
import asyncio
import json
import statistics
import time
from collections import Counter
from decimal import Decimal

import aiohttp
from kernel.venue_http import bybit_url
from kernel.venue_http import get_request
from kernel.venue_http import http_json


_BYBIT_URL = "wss://stream.bybit.com/v5/public/linear"
_BYBIT_TOPICS = ("publicTrade", "orderbook.50")
_HYPERLIQUID_TOPICS = ("trades", "l2Book")
# Bybit refuses a subscribe request of more than 10 args on a public stream.
_BYBIT_ARGS_PER_REQUEST = 10


def subscriptions(venue: str, coins: list[str], topics: list[str] | None) -> list[dict]:
    if venue == "bybit":
        args = [f"{topic}.{coin}" for topic in topics or _BYBIT_TOPICS for coin in coins]
        step = _BYBIT_ARGS_PER_REQUEST
        return [{"op": "subscribe", "args": args[i : i + step]} for i in range(0, len(args), step)]
    return [
        {"method": "subscribe", "subscription": {"type": topic, "coin": coin}}
        for topic in topics or _HYPERLIQUID_TOPICS
        for coin in coins
    ]


async def capture(
    venue: str,
    coins: list[str],
    topics: list[str] | None,
    seconds: float,
    out: str,
    url: str | None,
) -> None:
    default_url = _BYBIT_URL if venue == "bybit" else "wss://api.hyperliquid.xyz/ws"
    url = url or default_url
    subs = subscriptions(venue, coins, topics)
    deadline = time.monotonic() + seconds
    with open(out, "w") as f:
        async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
            await _pump(ws, subs, deadline, f)


async def _pump(ws: aiohttp.ClientWebSocketResponse, subs: list, deadline: float, f) -> None:
    for sub in subs:
        await ws.send_json(sub)
    ping_at = time.monotonic() + 20
    while (remaining := deadline - time.monotonic()) > 0:
        if "op" in subs[0] and time.monotonic() >= ping_at:
            await ws.send_json({"op": "ping"})  # Bybit drops a public socket idle for 10 min
            ping_at = time.monotonic() + 20
        try:
            msg = await ws.receive(timeout=min(remaining, 5))
        except TimeoutError:
            continue
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


def _bybit_precisions() -> dict[str, tuple[int, int]]:
    """
    Every linear symbol's (price, size) precision from the definitions' tick and qty step, as the
    Bybit adapter derives it: `Price::from_str(tickSize)` keeps the text's own digits ("0.10" is
    precision 2), so the text is read as written, never `normalize()`d.
    """
    out: dict[str, tuple[int, int]] = {}
    cursor = ""
    while True:
        query = f"/v5/market/instruments-info?category=linear&limit=1000&cursor={cursor}"
        result = http_json(get_request(bybit_url("mainnet", query)))["result"]
        for item in result["list"]:
            tick = item["priceFilter"]["tickSize"]
            step = item["lotSizeFilter"]["qtyStep"]
            out[item["symbol"]] = (_digits(tick), _digits(step))
        cursor = result.get("nextPageCursor") or ""
        if not cursor:
            return out


def _digits(text: str) -> int:
    exponent = Decimal(text).as_tuple().exponent
    assert isinstance(exponent, int), text
    return max(0, -exponent)


def _finer(text: str, precision: int) -> bool:
    """Whether the wire value carries a non-zero digit beyond the definition's precision."""
    value = Decimal(text)
    return value != round(value, precision)


def _key(entry: dict) -> tuple:
    return tuple(entry[k] for k in ("T", "s", "S", "v", "p"))


def summarize_liquidations(rows: list) -> None:
    """Print the story 33.1 figures: cadence, entries, windows, duplicates, precision."""
    frames = [r for r in rows if str(r["raw"].get("topic", "")).startswith("allLiquidation")]
    acks = [r["raw"] for r in rows if r["raw"].get("op") == "subscribe"]
    print(f"subscribe acks: {Counter((a.get('success'), a.get('ret_msg')) for a in acks)}")
    if not frames:
        print("allLiquidation: no frames")
        return
    span_min = (frames[-1]["recv_ns"] - rows[0]["recv_ns"]) / 60e9
    entries = [e for f in frames for e in f["raw"]["data"]]
    per_frame = Counter(len(f["raw"]["data"]) for f in frames)
    print(
        f"allLiquidation: {len(frames)} frames over {span_min:.1f} min "
        f"({len(frames) / max(span_min, 1e-9):.2f}/min), {len(entries)} entries, "
        f"entries per frame {sorted(per_frame.items())}, types {Counter(f['raw'].get('type') for f in frames)}"
    )
    windows = Counter((f["raw"]["topic"], f["raw"]["ts"] // 500) for f in frames)
    distinct_t = Counter(len({e["T"] for e in f["raw"]["data"]}) for f in frames)
    print(f"frames per (topic, 500 ms window): {sorted(Counter(windows.values()).items())}")
    print(f"distinct T per frame: {sorted(distinct_t.items())}")
    keys = [_key(e) for e in entries]
    in_frame = sum(len(f["raw"]["data"]) - len({_key(e) for e in f["raw"]["data"]}) for f in frames)
    cross = len(keys) - len(set(keys)) - in_frame
    print(f"duplicate (T,s,S,v,p): {in_frame} inside one frame, {cross} across frames")
    lags = sorted((f["recv_ns"] / 1e6 - e["T"]) for f in frames for e in f["raw"]["data"])
    print(
        f"arrival - T (ms): min {lags[0]:.0f} median {statistics.median(lags):.0f} max {lags[-1]:.0f}"
    )
    first = frames[0]
    print(
        f"first frame age: {(first['recv_ns'] / 1e6 - min(e['T'] for e in first['raw']['data'])) / 1000:.1f}s"
    )
    print(f"sides: {Counter(e['S'] for e in entries)}")
    precisions = _bybit_precisions()
    unknown = sorted({e["s"] for e in entries} - set(precisions))
    fine_p = sum(
        1 for e in entries if e["s"] in precisions and _finer(e["p"], precisions[e["s"]][0])
    )
    fine_v = sum(
        1 for e in entries if e["s"] in precisions and _finer(e["v"], precisions[e["s"]][1])
    )
    print(
        f"p finer than price_precision: {fine_p}/{len(entries)}; v finer than size_precision: "
        f"{fine_v}/{len(entries)}; symbols without a definition: {unknown}"
    )
    _match_trades(rows, entries)


def _match_trades(rows: list, entries: list) -> None:
    """Share of liquidations with a same-size trade on the forced side within +-2 s."""
    trades: dict[str, list[dict]] = {}
    for r in rows:
        if str(r["raw"].get("topic", "")).startswith("publicTrade"):
            for t in r["raw"]["data"]:
                trades.setdefault(t["s"], []).append(t)
    if not trades:
        return
    matched = 0
    for e in entries:
        forced = "Sell" if e["S"] == "Buy" else "Buy"  # a long liquidated is a forced sell
        matched += any(
            t["S"] == forced and Decimal(t["v"]) == Decimal(e["v"]) and abs(t["T"] - e["T"]) <= 2000
            for t in trades.get(e["s"], ())
        )
    print(f"same-size forced-side trade within 2 s: {matched}/{len(entries)}")


def summarize_hashes(frames: list) -> None:
    """
    Story 33.2: live trades per coin and how many carry the all-zero, non-transaction `hash`
    (deduplicated by `(coin, tid)`; a coin's live trades start at its own first `trades` frame,
    the subscribe reply that replays older trades, DATA-06). `scripts/hl_liquidation_probe.py`
    cross-queries those against the fills.
    """
    first_ns: dict[str, int] = {}
    live: dict[tuple, dict] = {}
    for frame in frames:
        for trade in frame["raw"]["data"]:
            start_ns = first_ns.setdefault(trade["coin"], frame["recv_ns"])
            if trade["time"] * 1_000_000 >= start_ns:
                live.setdefault((trade["coin"], trade["tid"]), trade)
    no_tx = "0x" + "0" * 64
    per_coin = Counter(coin for coin, _ in live)
    zero = Counter(t["coin"] for t in live.values() if t["hash"] == no_tx)
    for coin in sorted(per_coin):
        print(f"{coin}: {per_coin[coin]} live trades, {zero[coin]} with a non-transaction hash")


def summarize(path: str) -> None:
    rows = [json.loads(line) for line in open(path)]
    if any(str(r["raw"].get("topic", "")).startswith("allLiquidation") for r in rows):
        return summarize_liquidations(rows)
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
        summarize_hashes(trades)
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
    ap.add_argument("--coin", default="BTC", help="one coin, or a comma-separated list")
    ap.add_argument("--topic", action="append", help="repeatable; default: trades and book")
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--out", default="hl_capture.jsonl")
    ap.add_argument("--venue", choices=("hyperliquid", "bybit"), default="hyperliquid")
    ap.add_argument("--url")
    ap.add_argument("--summarize", metavar="JSONL")
    args = ap.parse_args()
    if args.summarize:
        summarize(args.summarize)
    else:
        coins = args.coin.split(",")
        asyncio.run(capture(args.venue, coins, args.topic, args.seconds, args.out, args.url))
