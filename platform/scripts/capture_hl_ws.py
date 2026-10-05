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

    python scripts/capture_hl_ws.py --coin BTC --seconds 900 --out /tmp/hl_btc.jsonl
    python scripts/capture_hl_ws.py --venue bybit --coin BTCUSDT --seconds 900 --out /tmp/by.jsonl
    python scripts/capture_hl_ws.py --venue bybit --coin BTCUSDT \
        --url wss://stream.bybit.com/v5/public/spot --out /tmp/by_spot.jsonl
    python scripts/capture_hl_ws.py --venue other --url wss://ws.example.com/v1 \
        --subscribe '{"op": "subscribe", "channel": "trades", "symbol": "BTC-USD"}' \
        --subscribe '{"op": "subscribe", "channel": "book", "symbol": "BTC-USD"}' --out /tmp/x.jsonl
    python scripts/capture_hl_ws.py --summarize /tmp/hl_btc.jsonl   # venue detected from frames
    python scripts/capture_hl_ws.py --summarize /tmp/x.jsonl --venue other   # generic summary
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
from typing import Any
from typing import TextIO

import aiohttp


_BYBIT_URL = "wss://stream.bybit.com/v5/public/linear"
_HYPERLIQUID_URL = "wss://api.hyperliquid.xyz/ws"
_VENUES = ("hyperliquid", "bybit", "other")
_HL_CHANNELS = frozenset({"trades", "l2Book"})
_COIN = "BTC"


def subscribe_plan(
    venue: str,
    coin: str,
    url: str | None,
    subs: list[Any] | None,
) -> tuple[str, list[Any]]:
    """
    Return the `(url, subscribe payloads)` a capture of `venue` uses. A known venue falls back to
    its default URL and payloads; non-empty `subs` replace the payloads. `other` has no defaults:
    a missing `url` or empty `subs` is a `ValueError` (the CLI refuses it before connecting).
    """
    if venue == "other":
        if not url or not subs:
            raise ValueError("--venue other needs --url and at least one --subscribe")
        return url, list(subs)
    if venue == "bybit":
        default_url = _BYBIT_URL
        default_subs: list[Any] = [
            {"op": "subscribe", "args": [f"publicTrade.{coin}", f"orderbook.50.{coin}"]},
        ]
    elif venue == "hyperliquid":
        default_url = _HYPERLIQUID_URL
        default_subs = [
            {"method": "subscribe", "subscription": {"type": "trades", "coin": coin}},
            {"method": "subscribe", "subscription": {"type": "l2Book", "coin": coin}},
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
) -> None:
    """
    Capture `seconds` of raw frames to the JSONL file `out`, created exclusively so an earlier
    capture is never overwritten. An early end (the venue closed the socket) is reported on stderr.
    """
    ws_url, payloads = subscribe_plan(venue, coin, url, subs)
    with open(out, "x") as f:
        try:
            frames, ended = asyncio.run(_stream(ws_url, payloads, seconds, f))
        except BaseException:
            # A failed connect leaves no evidence worth keeping: drop the empty file so a retry
            # is not refused as an overwrite. A file holding any frame is kept.
            if f.tell() == 0:
                os.remove(out)
            raise
    print(f"{frames} frame(s) written to {out}; {ended}", file=sys.stderr)


async def _stream(url: str, subs: list[Any], seconds: float, f: TextIO) -> tuple[int, str]:
    deadline = time.monotonic() + seconds
    async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
        return await _pump(ws, subs, deadline, f)


async def _pump(
    ws: aiohttp.ClientWebSocketResponse,
    subs: list[Any],
    deadline: float,
    f: TextIO,
) -> tuple[int, str]:
    """
    Send `subs` (a `str` goes out as exactly that text, any other value as JSON), then record until
    `deadline`. A send the venue refuses by closing the socket ends the capture with that reason.
    """
    for sub in subs:
        try:
            await (ws.send_str(sub) if isinstance(sub, str) else ws.send_json(sub))
        except (ConnectionError, aiohttp.ClientError) as e:
            return 0, f"ENDED EARLY: send of {sub!r} failed ({e!r}, close code {ws.close_code})"
    frames = 0
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            msg = await ws.receive(timeout=remaining)
        except TimeoutError:
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


def summarize_bybit(rows: list[dict[str, Any]]) -> None:
    rows = _dict_rows(rows)
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
    ap.add_argument("--coin", help=f"symbol for a known venue's default payloads ({_COIN})")
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


def main(argv: list[str] | None = None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.summarize:
        ignored = [flag for flag in ("coin", "url", "subscribe") if getattr(args, flag) is not None]
        if ignored:
            ap.error(f"--summarize reads a capture: --{ignored[0]} is a capture-only flag")
        summarize(args.summarize, args.venue)
        return
    venue = args.venue or "hyperliquid"
    if venue == "other" and not args.url:
        ap.error("--venue other requires --url")
    if venue == "other" and not args.subscribe:
        ap.error("--venue other requires at least one --subscribe")
    if args.coin is not None and args.subscribe:
        ap.error("--coin is not used with --subscribe: put the symbol in the payload")
    if args.coin is not None and not args.coin.strip():
        ap.error("--coin must not be empty")
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        ap.error(f"--seconds must be positive and finite, got {args.seconds}")
    if os.path.exists(args.out):
        ap.error(f"--out {args.out} already exists: refusing to overwrite a capture")
    capture(venue, args.coin or _COIN, args.seconds, args.out, args.url, args.subscribe)


if __name__ == "__main__":
    main()
