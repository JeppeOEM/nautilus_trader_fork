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
Records the verification tests' wire fixtures from the live venues with the real recorder, then
trims them to a small committed set (Story 31.1):

    python3 -m verification.tools.record_fixtures --venue BYBIT \
        --seconds 180 --reconnect-after 90 --out verification/tests/fixtures

It runs the production `Recorder` (the same wiring as `python3 -m verification.recorder`, reading
the venue's `config.toml`) into a scratch directory for `--seconds`, closes every connection from
the client side once at `--reconnect-after` so the real reconnect path runs, stops with a clean
shutdown, and writes the trimmed lines -- in the recorder's own format, through the same
`RawStore` -- under `<out>/raw/<venue>/`. The trim keeps every connection, control and anomaly
line, the first `--book-frames` frames of each book topic and the first `--frames` of every other
data topic after each `open`, and the first `--rest-lines` responses of each REST channel, so the
Bybit book fixture holds a snapshot plus a contiguous delta run per topic and connection.
Never fabricate a fixture, and never lose a good one: before anything under `--out` is touched the
scratch recording is checked (`recording_problems`: a data frame on every data channel, a good
response on every REST channel, the forced reconnect on every endpoint); a short one exits non-zero
and the committed fixtures stay as they were. The trim itself is staged and swapped in by renames.
"""

import argparse
import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import aiohttp
from observability import error_ledger

from verification.application.recorder import FORCED_RECONNECT
from verification.application.recorder import Recorder
from verification.application.recorder import Timing
from verification.application.venue_urls import kernel_endpoints
from verification.application.venue_urls import kernel_wiring
from verification.domain.plan_file import RecordingPlan
from verification.domain.subscriptions import VENUES
from verification.domain.subscriptions import rest_polls
from verification.infrastructure.aiohttp_io import AiohttpConnector
from verification.infrastructure.aiohttp_io import AiohttpHttp
from verification.infrastructure.raw_store import FILE_SUFFIX
from verification.infrastructure.raw_store import RawStore
from verification.infrastructure.raw_store import iter_records
from verification.infrastructure.raw_store import venue_dir
from verification.recorder import config_path
from verification.recorder import read_plan_file


# Fixtures are rewritten whole by this tool, never pruned by age.
_FIXTURE_RETAIN_DAYS = 36500
_KEEP_ALL_CHANNELS = frozenset({"connection", "unparsed", "unknown"})
_BOOK_CHANNELS = ("orderbook.50", "l2Book")


async def record(venue: str, root: Path, seconds: float, reconnect_after: float) -> None:
    """Run the production recorder for `seconds`, forcing one client-side reconnect."""
    plan_path = config_path(venue, {})
    plan = read_plan_file(plan_path, venue)
    stop = asyncio.Event()
    store = RawStore(root, venue, _FIXTURE_RETAIN_DAYS, error_ledger.record)
    async with aiohttp.ClientSession() as session:
        recorder = Recorder(
            plan,
            lambda: read_plan_file(plan_path, venue),
            kernel_wiring(),
            AiohttpConnector(session),
            AiohttpHttp(session),
            store,
            error_ledger.record,
            Timing(),
        )
        running = asyncio.create_task(recorder.run(stop))
        await asyncio.sleep(reconnect_after)
        await recorder.reconnect_all(FORCED_RECONNECT)
        await asyncio.sleep(max(0.0, seconds - reconnect_after))
        stop.set()
        await running


def _line_ts(line: dict[str, object]) -> int:
    value = line.get("recv_ns", line.get("ts_ns"))
    if not isinstance(value, int):
        raise ValueError(f"a raw line without a timestamp: {line}")
    return value


def _hyperliquid_coin(message: dict[str, object]) -> str | None:
    """Return the coin of a Hyperliquid data frame (`data.coin`, or its first trade's), or None."""
    data = message.get("data")
    if isinstance(data, list) and data:
        data = data[0]
    coin = data.get("coin") if isinstance(data, dict) else None
    return coin if isinstance(coin, str) else None


def _topic_key(channel: str, line: dict[str, object]) -> str:
    """
    Return the trim's per-segment key, one per symbol: Bybit's `topic`, Hyperliquid's channel and
    coin, else the channel -- so one symbol's frames never use up another's share.
    """
    raw = line.get("raw")
    try:
        message = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:  # an `unparsed` frame: kept whole, keyed by its channel
        message = None
    if not isinstance(message, dict):
        return channel
    topic = message.get("topic")
    if isinstance(topic, str):
        return topic
    coin = _hyperliquid_coin(message)
    return channel if coin is None else f"{channel}.{coin}"


class Trim:
    """What the trim keeps per channel: see the module docstring."""

    def __init__(self, book_frames: int, frames: int, rest_lines: int) -> None:
        self._book_frames = book_frames
        self._frames = frames
        self._rest_lines = rest_lines

    def kept(self, channel: str, lines: Iterator[dict[str, object]]) -> Iterator[dict[str, object]]:
        """Yield the lines of one channel (in file order) the fixture keeps."""
        counts: dict[str, int] = {}
        for line in lines:
            if line.get("kind") == "connection":  # always kept, never counted against a topic
                if line.get("event") == "open":
                    counts = {}
                yield line
                continue
            key = _topic_key(channel, line) if line.get("kind") == "frame" else channel
            counts[key] = counts.get(key, 0) + 1
            limit = self._limit(channel, line)
            if limit is None or counts[key] <= limit:
                yield line

    def _limit(self, channel: str, line: dict[str, object]) -> int | None:
        """How many lines of this key one segment keeps; None keeps them all."""
        if channel in _KEEP_ALL_CHANNELS:
            return None
        if channel.endswith("control"):
            return None
        if line.get("kind") == "rest":
            return self._rest_lines
        return self._book_frames if channel.endswith(_BOOK_CHANNELS) else self._frames


def _channel_lines(channel_dir: Path) -> Iterator[dict[str, object]]:
    for path in sorted(channel_dir.glob(f"*{FILE_SUFFIX}")):
        yield from iter_records(path)


def _frames(channel_dir: Path) -> int:
    return sum(1 for line in _channel_lines(channel_dir) if line["kind"] == "frame")


def _good_responses(channel_dir: Path) -> int:
    return sum(
        1
        for line in _channel_lines(channel_dir)
        if line["kind"] == "rest" and line.get("status") == 200 and "refusal" not in line
    )


def _reconnected(lines: list[dict[str, object]], endpoint: str) -> bool:
    events = [(l["event"], l["reason"]) for l in lines if l.get("endpoint") == endpoint]
    closed = ("close", FORCED_RECONNECT)
    return closed in events and ("open", FORCED_RECONNECT) in events[events.index(closed) :]


def recording_problems(plan: RecordingPlan, work: Path) -> list[str]:
    """
    List what the scratch recording lacks for a fixture set: a data frame on every data channel, a good
    response on every REST channel, and the forced reconnect on every endpoint. Empty when good.
    """
    root = venue_dir(work, plan.venue)
    try:
        connection = list(_channel_lines(root / "connection"))
        problems = [
            f"{channel}: no data frame"
            for endpoint in kernel_endpoints(plan)
            for channel in endpoint.data_channels
            if _frames(root / channel) == 0
        ]
        problems += [
            f"{poll.channel}: no good response"
            for poll in rest_polls(plan)
            if _good_responses(root / poll.channel) == 0
        ]
    except (OSError, ValueError) as exc:  # an unreadable scratch file (`TruncatedTail`, ...)
        return [f"the scratch recording is unreadable: {exc!r}"]
    problems += [
        f"{endpoint.name}: no forced reconnect recorded"
        for endpoint in kernel_endpoints(plan)
        if not _reconnected(connection, endpoint.name)
    ]
    return sorted(set(problems))


def trim(venue: str, work: Path, out: Path, rule: Trim) -> dict[str, int]:
    """
    Rewrite `<out>/raw/<venue>/` as the trimmed recording; return lines kept per channel. The trim
    is written to a staging directory first and swapped in by renames, so a failure midway leaves
    the committed fixtures as they were.
    """
    staging = Path(tempfile.mkdtemp(prefix=".fixtures-staging-", dir=out))
    try:
        store = RawStore(staging, venue, _FIXTURE_RETAIN_DAYS, error_ledger.record)
        kept: dict[str, int] = {}
        for channel_dir in sorted(p for p in venue_dir(work, venue).iterdir() if p.is_dir()):
            channel = channel_dir.name
            for line in rule.kept(channel, _channel_lines(channel_dir)):
                store.write(channel, _line_ts(line), line)
                kept[channel] = kept.get(channel, 0) + 1
        store.close()
        _swap_in(venue_dir(staging, venue), venue_dir(out, venue))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return kept


def _swap_in(new: Path, target: Path) -> None:
    old = target.with_name(target.name + ".old")
    if old.exists() and not target.exists():  # an earlier swap died between its two renames
        os.replace(old, target)
    shutil.rmtree(old, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        os.replace(target, old)
    try:
        os.replace(new, target)
    except OSError:
        if old.exists():
            os.replace(old, target)  # the committed fixtures go back where they were
        raise
    shutil.rmtree(old, ignore_errors=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python3 -m verification.tools.record_fixtures")
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument("--reconnect-after", type=float, default=90.0)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--book-frames", type=int, default=260)
    parser.add_argument("--frames", type=int, default=40)
    parser.add_argument("--rest-lines", type=int, default=2)
    args = parser.parse_args(argv)
    if not 0 < args.reconnect_after < args.seconds:  # else no reconnect is ever recorded
        parser.error("--reconnect-after must be above 0 and below --seconds")
    if min(args.book_frames, args.frames, args.rest_lines) < 1:  # else a data-less trim
        parser.error("--book-frames, --frames and --rest-lines must be at least 1")
    plan = read_plan_file(config_path(args.venue, {}), args.venue)
    args.out.mkdir(parents=True, exist_ok=True)  # the trim stages inside it, after the recording
    work = Path(tempfile.mkdtemp(prefix="verification-fixtures-"))
    try:
        asyncio.run(record(args.venue, work, args.seconds, args.reconnect_after))
        problems = recording_problems(plan, work)
        if problems:
            raise SystemExit(f"not a fixture set, the committed fixtures are kept: {problems}")
        rule = Trim(args.book_frames, args.frames, args.rest_lines)
        kept = trim(args.venue, work, args.out, rule)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({"venue": args.venue, "kept": kept, "ledger": error_ledger.counts()}))


if __name__ == "__main__":
    main()
