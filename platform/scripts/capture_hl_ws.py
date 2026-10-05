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
r"""
Raw-frame WS capture harness (not run in production): raw Hyperliquid / Bybit / any other venue's
WS frames from an independent client (aiohttp, zero shared code with the collector), each stamped
with its arrival `time.time_ns()`, as JSONL. Written for story 22.5's evidence: (a) does the trade
channel replay history on subscribe, (b) the real Hyperliquid `l2Book` push cadence, (c) is
Bybit's `u` contiguous. The Rust clients cannot supply this: Cargo.toml's
`release_max_level_debug` compiles the Bybit handler's `log::trace!` raw-frame line out of release
builds, and Hyperliquid has none.

It is also the step-1 harness of platform/CLAUDE.md's "Adding a venue": `--venue other` captures
a venue this script does not know, given its `--url` and one or more literal `--subscribe` JSON
payloads (each validated as JSON, then sent as the exact text given, in order; a JSON string such
as '"ping"' goes out as its plain-text content; nothing is templated in, so put the symbol in the
JSON, and `--coin` is refused alongside).
`--subscribe` given with `hyperliquid`/`bybit` replaces that venue's default payloads. Bybit spot
uses the same `publicTrade`/`orderbook.50` topics, so it needs only the spot `--url`. A text frame
that is not JSON is kept as `text`, a binary one as `raw_b64`; a close or error frame ends the
capture and says so on stderr, as does a send the venue refuses. `--out` must not exist yet (an
earlier capture is never overwritten; a capture that fails before writing a frame removes its
empty file, so the same command can be retried).
`--summarize` uses `--venue`'s summary when given, else guesses from the frames: always pass
`--venue other` for a venue this script does not know.
Story 33.1 added `--topic` (repeatable; Bybit topic prefixes such as `allLiquidation` or
`publicTrade`, Hyperliquid subscription types such as `trades`; default: the trade and book topics
above) and comma-separated `--coin`, for the liquidation wire investigation
(`docs/DATA_DICTIONARY.md` §1.26); `--summarize` then reports the `allLiquidation` frames too
(that summary reads the Bybit definitions through `kernel.venue_http`, so run it from `platform/`
with `PYTHONPATH=.`). Story 33.2: for Hyperliquid `trades`, `--summarize` also counts each coin's
live trades carrying the all-zero, non-transaction `hash` (`scripts/hl_liquidation_probe.py`
settles what they are).

    python scripts/capture_hl_ws.py --coin BTC --seconds 900 --out /tmp/hl_btc.jsonl
    python scripts/capture_hl_ws.py --venue bybit --coin BTCUSDT --seconds 900 --out /tmp/by.jsonl
    python scripts/capture_hl_ws.py --venue bybit --coin BTCUSDT \
        --url wss://stream.bybit.com/v5/public/spot --out /tmp/by_spot.jsonl
    python scripts/capture_hl_ws.py --venue other --url wss://ws.example.com/v1 \
        --subscribe '{"op": "subscribe", "channel": "trades", "symbol": "BTC-USD"}' \
        --subscribe '{"op": "subscribe", "channel": "book", "symbol": "BTC-USD"}' --out /tmp/x.jsonl
    python scripts/capture_hl_ws.py --summarize /tmp/hl_btc.jsonl   # venue detected from frames
    python scripts/capture_hl_ws.py --summarize /tmp/x.jsonl --venue other   # generic summary
    python scripts/capture_hl_ws.py --venue bybit --coin BTCUSDT,ETHUSDT \
        --topic allLiquidation --topic publicTrade --seconds 1800 --out /tmp/by_liq.jsonl
    PYTHONPATH=. python scripts/capture_hl_ws.py --summarize /tmp/by_liq.jsonl
"""

import argparse
import asyncio
import base64
import itertools
import json
import math
import os
import statistics
import sys
import time
from collections import Counter
from decimal import Decimal
from typing import Any
from typing import TextIO

import aiohttp


_BYBIT_URL = "wss://stream.bybit.com/v5/public/linear"
_HYPERLIQUID_URL = "wss://api.hyperliquid.xyz/ws"
_VENUES = ("hyperliquid", "bybit", "other")
_HL_CHANNELS = frozenset({"trades", "l2Book"})
_COIN = "BTC"
_BYBIT_TOPICS = ("publicTrade", "orderbook.50")
_HYPERLIQUID_TOPICS = ("trades", "l2Book")
# Bybit refuses a subscribe request of more than 10 args on a public stream.
_BYBIT_ARGS_PER_REQUEST = 10
# Bybit drops a public socket idle for 10 min; a capture pings it this often.
_BYBIT_PING_SECONDS = 20


def subscribe_plan(
    venue: str,
    coin: str,
    url: str | None,
    subs: list[Any] | None,
    topics: list[str] | None = None,
) -> tuple[str, list[Any]]:
    """
    Return the `(url, subscribe payloads)` a capture of `venue` uses. A known venue falls back to
    its default URL and payloads, built for every comma-separated `coin` and every `topics` entry
    (default: the trade and book topics); non-empty `subs` replace the payloads. `other` has no
    defaults: a missing `url` or empty `subs` is a `ValueError` (the CLI refuses it before
    connecting).
    """
    if venue == "other":
        if not url or not subs:
            raise ValueError("--venue other needs --url and at least one --subscribe")
        return url, list(subs)
    coins = coin.split(",")
    if venue == "bybit":
        default_url = _BYBIT_URL
        args = [f"{topic}.{c}" for topic in topics or _BYBIT_TOPICS for c in coins]
        step = _BYBIT_ARGS_PER_REQUEST
        default_subs: list[Any] = [
            {"op": "subscribe", "args": args[i : i + step]} for i in range(0, len(args), step)
        ]
    elif venue == "hyperliquid":
        default_url = _HYPERLIQUID_URL
        default_subs = [
            {"method": "subscribe", "subscription": {"type": topic, "coin": c}}
            for topic in topics or _HYPERLIQUID_TOPICS
            for c in coins
        ]
    else:
        raise ValueError(f"unknown venue {venue!r}: expected one of {_VENUES}")
    return url or default_url, list(subs) if subs else default_subs


def capture(
    venue: str,
    coin: str,
    seconds: float,
    out: str,
    url: str | None,
    subs: list[Any] | None = None,
    topics: list[str] | None = None,
) -> None:
    """
    Capture `seconds` of raw frames to the JSONL file `out`, created exclusively so an earlier
    capture is never overwritten. An early end (the venue closed the socket) is reported on stderr.
    """
    ws_url, payloads = subscribe_plan(venue, coin, url, subs, topics)
    with open(out, "x") as f:
        try:
            frames, ended = asyncio.run(
                _stream(ws_url, payloads, seconds, f, ping=venue == "bybit")
            )
        except BaseException:
            # A failed connect leaves no evidence worth keeping: drop the empty file so a retry
            # is not refused as an overwrite. A file holding any frame is kept.
            if f.tell() == 0:
                os.remove(out)
            raise
    print(f"{frames} frame(s) written to {out}; {ended}", file=sys.stderr)


async def _stream(
    url: str,
    subs: list[Any],
    seconds: float,
    f: TextIO,
    *,
    ping: bool = False,
) -> tuple[int, str]:
    deadline = time.monotonic() + seconds
    async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
        return await _pump(ws, subs, deadline, f, ping=ping)


async def _pump(
    ws: aiohttp.ClientWebSocketResponse,
    subs: list[Any],
    deadline: float,
    f: TextIO,
    *,
    ping: bool = False,
) -> tuple[int, str]:
    """
    Send `subs` (a `str` goes out as exactly that text, any other value as JSON), then record until
    `deadline`. A send the venue refuses by closing the socket ends the capture with that reason.
    `ping` (Bybit) sends `{"op": "ping"}` every `_BYBIT_PING_SECONDS` so a quiet topic such as
    `allLiquidation` does not let the venue drop the socket mid-capture.
    """
    for sub in subs:
        try:
            await (ws.send_str(sub) if isinstance(sub, str) else ws.send_json(sub))
        except (ConnectionError, aiohttp.ClientError) as e:
            return 0, f"ENDED EARLY: send of {sub!r} failed ({e!r}, close code {ws.close_code})"
    frames = 0
    ping_at = time.monotonic() + _BYBIT_PING_SECONDS
    while (remaining := deadline - time.monotonic()) > 0:
        if ping and time.monotonic() >= ping_at:
            await ws.send_json({"op": "ping"})
            ping_at = time.monotonic() + _BYBIT_PING_SECONDS
        try:
            msg = await ws.receive(timeout=min(remaining, 5) if ping else remaining)
        except TimeoutError:
            if ping:
                continue
            break
        row = frame_row(msg.type, msg.data)
        if row is None:
            return frames, f"ENDED EARLY: {msg.type.name} (data {msg.data!r}, extra {msg.extra!r})"
        f.write(json.dumps({"recv_ns": time.time_ns(), **row}) + "\n")
        frames += 1
    return frames, "ran to the deadline"


def frame_row(kind: aiohttp.WSMsgType, data: Any) -> dict[str, Any] | None:
    """
    Return the stored form of one received frame: `raw` (parsed JSON), `text` (a text frame that
    is not JSON, e.g. a plain `pong`) or `raw_b64` (a binary frame, bytes kept exactly); None for
    a close/error frame, which ends the capture.
    """
    if kind == aiohttp.WSMsgType.TEXT:
        try:
            return {"raw": json.loads(data)}
        except json.JSONDecodeError:
            return {"text": data}
    if kind == aiohttp.WSMsgType.BINARY:
        return {"raw_b64": base64.b64encode(data).decode("ascii")}
    return None


def _dict_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if isinstance(r.get("raw"), dict)]


def _print_replay_ages(label: str, ages: list[float]) -> None:
    if ages:
        print(f"first {label} frame: {len(ages)} trades, age {ages[0]:.1f}s..{ages[-1]:.1f}s")
    else:
        print(f"first {label} frame: 0 trades (nothing replayed)")


def _bybit_precisions() -> dict[str, tuple[int, int]]:
    """
    Every linear symbol's (price, size) precision from the definitions' tick and qty step, as the
    Bybit adapter derives it: `Price::from_str(tickSize)` keeps the text's own digits ("0.10" is
    precision 2), so the text is read as written, never `normalize()`d.
    """
    # Imported here: only this summary needs the platform on the path (`PYTHONPATH=.`).
    from kernel.venue_http import bybit_url
    from kernel.venue_http import get_request
    from kernel.venue_http import http_json

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
    rows = _dict_rows(rows)
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


def summarize_bybit(rows: list[dict[str, Any]]) -> None:
    rows = _dict_rows(rows)
    if any(str(r["raw"].get("topic", "")).startswith("allLiquidation") for r in rows):
        summarize_liquidations(rows)
        return
    trades = [r for r in rows if str(r["raw"].get("topic", "")).startswith("publicTrade")]
    if trades:
        head = trades[0]
        ages = sorted((head["recv_ns"] / 1e6 - t["T"]) / 1000 for t in head["raw"]["data"])
        _print_replay_ages("publicTrade", ages)
        print(f"publicTrade frames: {len(trades)}")
    books = [r["raw"] for r in rows if str(r["raw"].get("topic", "")).startswith("orderbook")]
    us = [(b["type"], b["data"]["u"]) for b in books]
    deltas = [u for t, u in us if t == "delta"]
    steps = [b - a for a, b in itertools.pairwise(deltas)]
    print(
        f"orderbook: {len(books)} frames, {len(us) - len(deltas)} snapshot(s); delta u steps: "
        f"+1 x{steps.count(1)}, gaps(>1) x{sum(1 for s in steps if s > 1)}, "
        f"regress(<=0) x{sum(1 for s in steps if s <= 0)}; max step {max(steps, default=0)}"
    )


def summarize_hyperliquid(rows: list[dict[str, Any]]) -> None:
    first = rows[0]["recv_ns"] if rows else 0
    rows = _dict_rows(rows)
    trades = [r for r in rows if r["raw"].get("channel") == "trades"]
    books = [r for r in rows if r["raw"].get("channel") == "l2Book"]
    if trades:
        # The subscribe reply is the first `trades` frame: its age spread is the replay bound.
        head = trades[0]
        ages = sorted((head["recv_ns"] / 1e6 - t["time"]) / 1000 for t in head["raw"]["data"])
        _print_replay_ages("trades", ages)
        print(f"trades frames: {len(trades)}; first at +{(head['recv_ns'] - first) / 1e9:.2f}s")
        # Only a capture of this script's own `trades` frames carries the fields the count reads.
        if all({"coin", "tid", "hash"} <= t.keys() for f in trades for t in f["raw"]["data"]):
            summarize_hashes(trades)
    gaps = [(b["recv_ns"] - a["recv_ns"]) / 1e9 for a, b in itertools.pairwise(books)]
    if gaps:
        changed = sum(
            1
            for a, b in itertools.pairwise(books)
            if a["raw"]["data"]["levels"] != b["raw"]["data"]["levels"]
        )
        print(
            f"l2Book: {len(books)} frames, gap median {statistics.median(gaps):.2f}s "
            f"min {min(gaps):.2f}s max {max(gaps):.2f}s; levels changed in {changed}/{len(gaps)}"
        )
    if not trades and not gaps:
        print(
            f"no trades frame and {len(books)} l2Book frame(s) in {len(rows)} JSON object frame(s): "
            "nothing to summarise as Hyperliquid (try --venue other)"
        )


def summarize_generic(rows: list[dict[str, Any]]) -> None:
    """
    Frame count and arrival-gap cadence over every frame of the capture together (subscribe acks,
    heartbeats and all channels mixed): a liveness/cadence first look, not the per-channel,
    per-instrument messages/s that a venue-specific summary measures.
    """
    objects = len(_dict_rows(rows))
    print(f"frames: {len(rows)} ({objects} JSON object, {len(rows) - objects} other)")
    gaps = [(b["recv_ns"] - a["recv_ns"]) / 1e9 for a, b in itertools.pairwise(rows)]
    if gaps:
        print(
            f"arrival gap median {statistics.median(gaps):.2f}s "
            f"min {min(gaps):.2f}s max {max(gaps):.2f}s over {len(gaps)} gap(s)"
        )


def detect_venue(rows: list[dict[str, Any]]) -> str:
    """
    `bybit` if any object frame has a `topic`, `hyperliquid` if one has an HL channel, else
    `other`. Only a guess for this script's own captures: another venue's frames can carry the
    same keys, so summarize a new venue's capture with an explicit `--venue other`.
    """
    frames = [r["raw"] for r in _dict_rows(rows)]
    if any("topic" in raw for raw in frames):
        return "bybit"
    if any(raw.get("channel") in _HL_CHANNELS for raw in frames):
        return "hyperliquid"
    return "other"


def read_capture(path: str) -> tuple[list[dict[str, Any]], int]:
    """
    Return the rows and the count of non-blank lines that are not a capture row: not JSON (a
    cut-off last line), or not an object carrying an integer `recv_ns` (a line from another tool).
    """
    rows = []
    unparseable = 0
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                unparseable += 1
                continue
            if isinstance(row, dict) and type(row.get("recv_ns")) is int:
                rows.append(row)
            else:
                unparseable += 1
    return rows, unparseable


def summarize(path: str, venue: str | None = None) -> None:
    """Summarise a capture as `venue`'s; with no `venue` the kind is detected from the frames."""
    rows, unparseable = read_capture(path)
    if unparseable:
        print(f"WARNING: {unparseable} unparseable line(s) in {path} not summarised")
    kind = venue or detect_venue(rows)
    if kind == "bybit":
        summarize_bybit(rows)
    elif kind == "hyperliquid":
        summarize_hyperliquid(rows)
    else:
        summarize_generic(rows)


def _json_payload(value: str) -> str:
    """
    Validate `value` as JSON and return the text to send: `value` itself (byte-exact, never
    re-serialised), or a JSON string's decoded content, which goes out as plain text.
    """
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"not valid JSON: {value!r} ({e})") from e
    return parsed if isinstance(parsed, str) else value


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Capture raw venue WS frames as JSONL.")
    ap.add_argument(
        "--coin",
        help=f"symbol, or a comma-separated list, for a known venue's default payloads ({_COIN})",
    )
    ap.add_argument(
        "--topic",
        action="append",
        help="repeatable: a known venue's topic in place of its trade and book defaults",
    )
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--out", default="hl_capture.jsonl", help="must not exist yet")
    ap.add_argument(
        "--venue",
        choices=_VENUES,
        help="capture: default hyperliquid; --summarize: default detected from the frames",
    )
    ap.add_argument("--url")
    ap.add_argument(
        "--subscribe",
        action="append",
        type=_json_payload,
        metavar="JSON",
        help="literal JSON payload, sent as given; repeatable; replaces a venue's defaults",
    )
    ap.add_argument("--summarize", metavar="JSONL")
    return ap


def _check_capture_args(ap: argparse.ArgumentParser, args: argparse.Namespace, venue: str) -> None:
    """Refuse (exit 2, naming the flag) a capture whose flags cannot all be honoured."""
    if venue == "other" and not args.url:
        ap.error("--venue other requires --url")
    if venue == "other" and not args.subscribe:
        ap.error("--venue other requires at least one --subscribe")
    if args.coin is not None and args.subscribe:
        ap.error("--coin is not used with --subscribe: put the symbol in the payload")
    if args.topic and args.subscribe:
        ap.error("--topic is not used with --subscribe: put the topic in the payload")
    if args.topic and venue == "other":
        ap.error("--topic needs a known venue: use --subscribe with --venue other")
    if args.coin is not None and not all(c.strip() for c in args.coin.split(",")):
        ap.error("--coin must not be empty (nor hold an empty list entry)")
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        ap.error(f"--seconds must be positive and finite, got {args.seconds}")
    if os.path.exists(args.out):
        ap.error(f"--out {args.out} already exists: refusing to overwrite a capture")


def main(argv: list[str] | None = None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.summarize:
        capture_flags = ("coin", "topic", "url", "subscribe")
        ignored = [flag for flag in capture_flags if getattr(args, flag) is not None]
        if ignored:
            ap.error(f"--summarize reads a capture: --{ignored[0]} is a capture-only flag")
        summarize(args.summarize, args.venue)
        return
    venue = args.venue or "hyperliquid"
    _check_capture_args(ap, args, venue)
    extra = {"topics": args.topic} if args.topic else {}
    capture(venue, args.coin or _COIN, args.seconds, args.out, args.url, args.subscribe, **extra)


if __name__ == "__main__":
    main()
