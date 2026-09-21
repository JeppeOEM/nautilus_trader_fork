"""
Independent reference dYdX WS client -- completely separate code path from
nautilus_trader/crates (pure Python + aiohttp, talks to dYdX's public indexer
WS protocol directly per https://docs.dydx.trade/developers/indexer/websockets).

Purpose: settle whether the "ghost price" desync seen in dydx-collector is a
genuine loss upstream of our pipeline (dYdX/network -- would also show up here)
or a bug specific to our own collector/Rust client (would NOT show up here).

Runs two things concurrently:
  1. Its own v4_orderbook subscription for BTC-USD and ETH-USD, maintaining a
     naive book (dict[Decimal, Decimal] per side) from raw JSON, with a rolling
     buffer of the last 300 raw messages per instrument for forensic dumps.
  2. A tail of `docker logs -f dydx-collector`, watching for its "Crossed book"
     lines. On one, immediately logs this reference client's current best
     bid/ask for the same instrument + whether ANY raw message in the rolling
     buffer ever touched the exact frozen price, to a dedicated per-episode file.

Zero shared code with the collector under test -- if this client shows the same
stale price at the same moment, the loss is upstream of both of us.
"""

import asyncio
import json
import re
import subprocess
import time
from collections import deque
from decimal import Decimal
from pathlib import Path

import aiohttp


WS_URL = "wss://indexer.dydx.trade/v4/ws"
INSTRUMENTS = ["BTC-USD", "ETH-USD"]
OUT_DIR = Path(__file__).parent / "reference_check_out"
OUT_DIR.mkdir(exist_ok=True)

# iid as it appears in our collector's logs -> dYdX indexer ticker
IID_TO_TICKER = {"BTC-USD-PERP.DYDX": "BTC-USD", "ETH-USD-PERP.DYDX": "ETH-USD"}

books: dict[str, dict[str, dict[Decimal, Decimal]]] = {
    t: {"bids": {}, "asks": {}} for t in INSTRUMENTS
}
raw_history: dict[str, deque] = {t: deque(maxlen=300) for t in INSTRUMENTS}

_crossed_re = re.compile(
    r"Crossed book for (?P<iid>[A-Z0-9-]+\.DYDX) \(bid=(?P<bid>[\d.]+) >= ask=(?P<ask>[\d.]+)\)"
)


def best_bid(ticker: str) -> Decimal | None:
    live = {p: s for p, s in books[ticker]["bids"].items() if s > 0}
    return max(live) if live else None


def best_ask(ticker: str) -> Decimal | None:
    live = {p: s for p, s in books[ticker]["asks"].items() if s > 0}
    return min(live) if live else None


def apply_side(ticker: str, side: str, levels: list) -> None:
    # Snapshot ("subscribed") levels are {"price":.., "size":..} dicts; incremental
    # ("channel_data") levels are [price_str, size_str] pairs. Same wire data, two shapes.
    book_side = books[ticker][side]
    for level in levels:
        if isinstance(level, dict):
            price_str, size_str = level["price"], level["size"]
        else:
            price_str, size_str = level
        price = Decimal(price_str)
        size = Decimal(size_str)
        if size == 0:
            book_side.pop(price, None)
        else:
            book_side[price] = size


async def ws_reader() -> None:
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL, heartbeat=30) as ws:
            for ticker in INSTRUMENTS:
                await ws.send_json({"type": "subscribe", "channel": "v4_orderbook", "id": ticker})
            print(f"[{time.strftime('%H:%M:%S')}] reference client subscribed: {INSTRUMENTS}", flush=True)

            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                ticker = data.get("id")
                if ticker not in INSTRUMENTS:
                    continue

                now_ns = time.time_ns()
                raw_history[ticker].append((now_ns, data))

                try:
                    msg_type = data.get("type")
                    contents = data.get("contents", {})
                    if msg_type == "subscribed":
                        books[ticker] = {"bids": {}, "asks": {}}
                        apply_side(ticker, "bids", contents.get("bids", []))
                        apply_side(ticker, "asks", contents.get("asks", []))
                    elif msg_type == "channel_data":
                        apply_side(ticker, "bids", contents.get("bids", []))
                        apply_side(ticker, "asks", contents.get("asks", []))
                except Exception as e:
                    print(f"Failed to apply message for {ticker}: {e}", flush=True)


async def periodic_snapshot_log() -> None:
    log_path = OUT_DIR / "reference_snapshots.log"
    while True:
        await asyncio.sleep(1.0)
        ts = time.strftime("%H:%M:%S")
        with log_path.open("a") as f:
            for ticker in INSTRUMENTS:
                f.write(f"{ts} {ticker} bid={best_bid(ticker)} ask={best_ask(ticker)}\n")


def dump_episode(iid: str, our_bid: str, our_ask: str) -> None:
    ticker = IID_TO_TICKER.get(iid)
    if ticker is None:
        return
    ts = time.strftime("%H:%M:%S")
    ref_bid = best_bid(ticker)
    ref_ask = best_ask(ticker)

    frozen_prices = {Decimal(our_bid), Decimal(our_ask)}
    touching = [
        (t, d) for t, d in raw_history[ticker]
        if any(
            Decimal(p) in frozen_prices
            for side in ("bids", "asks")
            for p, _ in d.get("contents", {}).get(side, [])
        )
    ]

    episode_file = OUT_DIR / f"episode_{ticker}_{int(time.time())}.json"
    with episode_file.open("w") as f:
        json.dump(
            {
                "collector_claim": {"iid": iid, "bid": our_bid, "ask": our_ask},
                "reference_book": {"bid": str(ref_bid), "ask": str(ref_ask)},
                "messages_touching_frozen_prices": [
                    {"ts_ns": t, "data": d} for t, d in touching
                ],
            },
            f,
            indent=2,
            default=str,
        )

    summary = (
        f"{ts} EPISODE {ticker} | collector claims bid={our_bid} ask={our_ask} | "
        f"REFERENCE book bid={ref_bid} ask={ref_ask} | "
        f"{len(touching)} raw msgs touched either frozen price -> {episode_file.name}"
    )
    print(summary, flush=True)
    with (OUT_DIR / "episodes_summary.log").open("a") as f:
        f.write(summary + "\n")


async def docker_log_watcher() -> None:
    proc = subprocess.Popen(
        ["docker", "logs", "-f", "--since", "0s", "dydx-collector"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, proc.stdout.readline)
        if not line:
            break
        m = _crossed_re.search(line)
        if m and m.group("iid") in IID_TO_TICKER:
            dump_episode(m.group("iid"), m.group("bid"), m.group("ask"))


async def main() -> None:
    await asyncio.gather(ws_reader(), periodic_snapshot_log(), docker_log_watcher())


if __name__ == "__main__":
    asyncio.run(main())
