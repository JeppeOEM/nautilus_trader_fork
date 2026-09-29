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
"""
The live mechanics check behind `make bots-churn-check` (Story 29.6): watch one bot's
`bots:status` and pass only when it shows, in order,

1. a protected long -- `stop_loss`, `take_profit`, `entry_price`, `mark_price` and
   `position_qty` all set, one stop-loss and one take-profit order, stop < entry < take-profit;
2. flat with no exits, `closed_trades` above (1)'s, and no order of the bot left open (no
   orphaned reduce-only leg);
3. a second protected long whose exits or entry differ from (1)'s (fresh exits).

`ChurnChecker` is the pure sequence checker (fed payload dicts, tested by
`test_churn_check.py`); `main` subscribes to Redis with redis-py and feeds it until it passes or
the timeout runs out. Run from `platform/`:
`python3 bots/tests/churn_check.py --redis-url URL --timeout 900 --out PATH` (exit 0 = passed,
1 = a check failed or was not reached, 2 = Redis unreachable).
"""

import argparse
import json
import sys
import time
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path

import redis


STATUS_CHANNEL = "bots:status"
CHECKS = (
    "protected long",
    "flat with no orphaned order",
    "second protected long with fresh exits",
)
_PRICE_FIELDS = ("stop_loss", "take_profit", "entry_price", "mark_price", "position_qty")
_PROGRESS_SECONDS = 60.0


def protected_long_problems(status: dict) -> list[str]:
    """Why `status` is not a protected long (empty when it is one)."""
    problems = [] if status.get("position_side") == "long" else ["position_side is not long"]
    problems += [f"{key} is null" for key in _PRICE_FIELDS if status.get(key) is None]
    for key in ("stop_loss_orders", "take_profit_orders"):
        if status.get(key) != 1:
            problems.append(f"{key} is {status.get(key)!r}, not 1")
    prices = {key: status.get(key) for key in ("stop_loss", "entry_price", "take_profit")}
    if all(value is not None for value in prices.values()):
        try:
            stop, entry, take = (Decimal(v) for v in prices.values() if v is not None)
        except (InvalidOperation, TypeError, ValueError):
            return [*problems, f"a price is not a number: {prices!r}"]
        if not stop < entry < take:
            problems.append("not stop_loss < entry_price < take_profit")
    return problems


def flat_problems(status: dict, protected: dict) -> list[str]:
    """Why `status` is not the clean flat that must follow `protected` (empty when it is)."""
    problems = [] if status.get("position_side") == "flat" else ["position_side is not flat"]
    problems += [
        f"{key} is not null" for key in ("stop_loss", "take_profit") if status.get(key) is not None
    ]
    if not status.get("closed_trades", 0) > protected.get("closed_trades", 0):
        problems.append(
            f"closed_trades {status.get('closed_trades')!r} is not above "
            f"{protected.get('closed_trades')!r}"
        )
    if status.get("open_orders") != 0:
        problems.append(f"open_orders is {status.get('open_orders')!r}, not 0 (orphaned order)")
    return problems


def fresh_long_problems(status: dict, protected: dict) -> list[str]:
    """Why `status` is not a second protected long with exits of its own (empty when it is)."""
    problems = protected_long_problems(status)
    keys = ("stop_loss", "take_profit", "entry_price")
    if not problems and all(status.get(key) == protected.get(key) for key in keys):
        problems.append("exits and entry are identical to the first long's (not fresh)")
    return problems


class ChurnChecker:
    """
    Walks one bot's `bots:status` payloads through `CHECKS` in order. Invariant: a check is only
    ever passed by a payload arriving after the one that passed the check before it.
    """

    def __init__(self, bot_id: str) -> None:
        self.bot_id = bot_id
        self.passed: list[dict] = []
        self.messages = 0
        self.last_problems: list[str] = []
        self.last_status: dict | None = None

    @property
    def done(self) -> bool:
        return len(self.passed) == len(CHECKS)

    def feed(self, status: dict) -> bool:
        """Consume one payload; True when it passed the next check."""
        if self.done or status.get("bot_id") != self.bot_id:
            return False
        self.messages += 1
        self.last_status = status
        problems = self._problems(status)
        self.last_problems = problems
        if problems:
            return False
        self.passed.append(status)
        return True

    def _problems(self, status: dict) -> list[str]:
        stage = len(self.passed)
        if stage == 0:
            return protected_long_problems(status)
        if stage == 1:
            return flat_problems(status, self.passed[0])
        return fresh_long_problems(status, self.passed[0])

    def report(self) -> str:
        """One line: the pass, or the check not reached and why the last payload missed it."""
        if self.done:
            return f"{self.bot_id}: all {len(CHECKS)} checks passed"
        check = f"check {len(self.passed) + 1} ({CHECKS[len(self.passed)]})"
        if not self.messages:
            return f"{self.bot_id}: {check} not reached: no {STATUS_CHANNEL} message received"
        return (
            f"{self.bot_id}: {check} not reached after {self.messages} messages; the last one "
            f"failed on: {'; '.join(self.last_problems)}"
        )

    def captured(self) -> dict:
        return {
            "bot_id": self.bot_id,
            "passed": self.done,
            "report": self.report(),
            "checks": dict(zip(CHECKS, self.passed, strict=False)),
            "last_status": self.last_status,
        }


def _status(data: bytes | str) -> dict | None:
    """Return a `bots:status` message as a dict; None (reported, skipped) for anything else."""
    try:
        status = json.loads(data)
    except ValueError:
        status = None
    if not isinstance(status, dict):
        print(f"ignoring a malformed {STATUS_CHANNEL} message: {data!r}", file=sys.stderr)
        return None
    return status


def _watch(checker: ChurnChecker, redis_url: str, timeout: float) -> None:
    client = redis.Redis.from_url(redis_url, socket_timeout=5.0)
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(STATUS_CHANNEL)
    deadline = time.monotonic() + timeout
    next_progress = time.monotonic() + _PROGRESS_SECONDS
    try:
        while not checker.done and time.monotonic() < deadline:
            message = pubsub.get_message(timeout=1.0)
            status = _status(message["data"]) if message is not None else None
            if status is not None and checker.feed(status):
                passed = len(checker.passed)
                print(f"passed check {passed}: {CHECKS[passed - 1]}", flush=True)
            if time.monotonic() >= next_progress:
                print(f"waiting: {checker.report()}", flush=True)
                next_progress += _PROGRESS_SECONDS
    finally:
        pubsub.close()
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bot-id", default="churn-01")
    args = parser.parse_args(argv)
    checker = ChurnChecker(args.bot_id)
    try:
        _watch(checker, args.redis_url, args.timeout)
    except (OSError, redis.RedisError) as exc:
        print(f"cannot watch {STATUS_CHANNEL} on {args.redis_url}: {exc}", file=sys.stderr)
        return 2
    finally:
        # Written even when Redis drops mid-run, so the checks already passed are not lost.
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(checker.captured(), indent=2))
    print(checker.report())
    return 0 if checker.done else 1


if __name__ == "__main__":
    sys.exit(main())
