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
One-off evidence for story 33.2 (not shipped in the image, not run in production): settles the
two Hyperliquid liquidation hypotheses of the epic on a `capture_hl_ws.py --topic trades` capture.

(a) A market-liquidation fill is system-generated, so its public trade carries a non-transaction
    (all-zero) `hash`, and the liquidated address is one of its `users`. Measured three ways:
    * candidates: every address of every all-zero-hash trade is cross-queried through the public
      `userFillsByTime` (one call per address per `--bucket-minutes`), and each trade is
      classified by the fills found for its `tid` (a `liquidation` marker confirms it);
    * control: a seeded random sample of ordinary-hash trades, queried the same way (a
      liquidation found there is a false negative of (a));
    * census: the latest fills of a seeded random sample of the capture's addresses, every
      `liquidation`-marked fill among them classified by its own `hash` -- the direct recall of
      the rule, on far more liquidations than an hour's control sample can meet.
(b) The liquidator vaults' fills (`method: backstop`) give the backstop subset: every HLP child
    vault's fills over the capture window, each backstop fill looked up among the captured
    public trades; plus the vaults' recent history (backstop fills per day) and, for a bounded
    sample of it, the liquidated user's own fill of the same `tid` (the confirmation).
Samples: on seeded samples of ordinary and all-zero-hash trades, whether a fill's `hash` is its
public trade's (so the census's fill hashes test (a)'s trade hash) and whether an all-zero-hash
trade's absent side is a TWAP slice (`userTwapSliceFills`).

A hypothesis is adopted only with >= 99 % confirmed on the capture and a measured false-negative
rate (the epic's rule); `verdicts` in the report applies it. The report is JSON on stdout;
`docs/DATA_DICTIONARY.md` §1.26 records it. Weight follows Hyperliquid's documented `/info` limit
(1200 per minute per IP; `userFills`/`userFillsByTime` cost 20 plus 1 per 20 items returned),
paced under it here.

Known limit: `userFillsByTime` serves only an address's 10,000 most recent fills (Hyperliquid's
API docs), so a very busy address's fill from the capture can be out of reach by the time it is
queried; it then reads as absent (`one_fill_ordinary`/`no_fill_found`). Upgrade path: run the
probe while the capture is recorded, or a node's fill stream.

    PYTHONPATH=. python scripts/capture_hl_ws.py --coin BTC,ETH --topic trades \
        --seconds 3600 --out /tmp/hl_trades.jsonl
    PYTHONPATH=. python scripts/hl_liquidation_probe.py /tmp/hl_trades.jsonl --coins BTC,ETH \
        > /tmp/probe.json
"""

import argparse
import http.client
import json
import random
import sys
import time
import urllib.error
from collections import Counter
from collections import defaultdict

from kernel.venue_http import http_json
from kernel.venue_http import hyperliquid_info_url
from kernel.venue_http import post_json_request


HLP_VAULT = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"
NO_TX_HASH = "0x" + "0" * 64
WEIGHT_PER_MINUTE = 1200  # documented
WEIGHT_BUDGET = 900  # paced under the limit, so a concurrent collector keeps headroom
FILLS_WEIGHT = 20
PAGE = 2000  # most fills one `userFills`/`userFillsByTime` response holds
MINUTE_MS = 60_000
DAY_MS = 86_400_000
ADOPT_SHARE = 0.99


class Info:
    """`/info` POSTs with weight accounting and pacing."""

    def __init__(self) -> None:
        self.url = hyperliquid_info_url("mainnet")
        self.weight = 0
        self.calls = 0
        self.truncated_pages = 0
        self._window: list[tuple[float, int]] = []

    def post(self, body: dict, base_weight: int = FILLS_WEIGHT) -> list | dict:
        attempts = 5
        for attempt in range(attempts):
            self._pace(base_weight)
            try:
                result = http_json(post_json_request(self.url, body))
                break
            except RETRIED as error:
                if isinstance(error, urllib.error.HTTPError) and not _transient(error.code):
                    raise  # a refused request (bad body or address) is not retried
                # A failed attempt may still have been charged: count its base weight.
                self._window.append((time.monotonic(), base_weight))
                if attempt == attempts - 1:
                    raise RuntimeError(f"/info failed {attempts} times: {body}") from error
                print(f"retry {attempt}: {error!r}", file=sys.stderr)
                time.sleep(10 * (attempt + 1))
        cost = base_weight + (len(result) // 20 if isinstance(result, list) else 0)
        self.weight += cost
        self.calls += 1
        self._window.append((time.monotonic(), cost))
        return result

    def _pace(self, upcoming: int) -> None:
        while True:
            now = time.monotonic()
            self._window = [(t, w) for t, w in self._window if now - t < 60]
            if sum(w for _, w in self._window) + upcoming <= WEIGHT_BUDGET:
                return
            time.sleep(1)

    def fills(self, user: str, start_ms: int, end_ms: int) -> list[dict]:
        """
        Every fill of `user` in [start, end], paged. A full page restarts at its newest
        millisecond, not after it, so fills sharing that millisecond are not skipped; the overlap
        is dropped by `(coin, tid, side)` (a self-trade's two fills share the `tid`). A full page
        that adds nothing (over `PAGE` fills in one millisecond) cannot be paged past: counted in
        `truncated_pages`, which the report carries.
        """
        out: dict[tuple, dict] = {}
        while True:
            body = {
                "type": "userFillsByTime",
                "user": user,
                "startTime": start_ms,
                "endTime": end_ms,
                "aggregateByTime": False,
            }
            page = _fill_list(self.post(body), body)
            before = len(out)
            out.update(((f["coin"], f["tid"], f["side"]), f) for f in page)
            if len(page) < PAGE:
                return list(out.values())
            if len(out) == before:
                self.truncated_pages += 1
                print(
                    f"{user}: over {PAGE} fills at {start_ms}, the rest unreachable",
                    file=sys.stderr,
                )
                return list(out.values())
            start_ms = max(f["time"] for f in page)

    def latest_fills(self, user: str) -> list[dict]:
        """Return the user's latest fills, newest first (sorted here, not assumed of the venue)."""
        body = {"type": "userFills", "user": user, "aggregateByTime": False}
        fills = _fill_list(self.post(body), body)
        return sorted(fills, key=lambda f: f["time"], reverse=True)


# Transport failures worth a retry; an `HTTPError` (a `URLError`) is retried only if transient.
RETRIED = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    http.client.HTTPException,
    json.JSONDecodeError,
)


def _transient(status: int) -> bool:
    return status == 429 or status >= 500


def _fill_list(result: list | dict, body: dict) -> list[dict]:
    """Return a fills response, refusing any other shape (an error object, a changed API)."""
    if not isinstance(result, list):
        raise RuntimeError(f"/info {body['type']} returned {result!r}, not a list of fills")
    return result


def _rows(path: str) -> list[dict]:
    """Return the capture's rows; a truncated last line (an interrupted capture) is skipped."""
    rows = []
    with open(path) as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"skipped an undecodable line of {path}", file=sys.stderr)
    if not rows:
        raise SystemExit(f"{path}: no capture rows")
    return rows


def load_trades(path: str) -> tuple[list[dict], int, int]:
    """
    Return the capture's live trades (deduplicated by `(coin, tid)`) and its window in ms. A
    coin's live trades start at its own first `trades` frame: the subscribe reply before it
    replays older trades (DATA-06).
    """
    rows = _rows(path)
    first_ms: dict[str, int] = {}
    seen: dict[tuple, dict] = {}
    for row in rows:
        if row["raw"].get("channel") != "trades" or not row["raw"]["data"]:
            continue
        for trade in row["raw"]["data"]:
            start = first_ms.setdefault(trade["coin"], row["recv_ns"] // 1_000_000)
            if trade["time"] >= start:
                seen.setdefault((trade["coin"], trade["tid"]), trade)
    return list(seen.values()), rows[0]["recv_ns"] // 1_000_000, rows[-1]["recv_ns"] // 1_000_000


def resolve(info: Info, trades: list[dict], bucket_ms: int) -> dict[tuple, list[dict]]:
    """Each trade's fills by its two addresses: one `userFillsByTime` per address per bucket."""
    buckets: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for trade in trades:
        for user in set(trade["users"]):  # a self-trade is one address, queried once
            buckets[(user, trade["time"] // bucket_ms)].append(trade)
    found: dict[tuple, list[dict]] = defaultdict(list)
    for n, ((user, bucket), members) in enumerate(sorted(buckets.items())):
        if n % 100 == 0:
            print(f"resolve {n}/{len(buckets)} weight {info.weight}", file=sys.stderr)
        fills = info.fills(user, bucket * bucket_ms, (bucket + 1) * bucket_ms - 1)
        by_key: dict[tuple, list[dict]] = defaultdict(list)
        for fill in fills:
            by_key[(fill["coin"], fill["tid"])].append(fill)
        for trade in members:
            key = (trade["coin"], trade["tid"])
            found[key].extend({**fill, "user": user} for fill in by_key.get(key, []))
    return found


def classify(fills: list[dict]) -> str:
    if any(f.get("liquidation") for f in fills):
        return "liquidation"
    if len(fills) > 2:
        return "unexpected_multi_fill"
    if len(fills) == 2:
        return "both_fills_ordinary"
    if len(fills) == 1:
        # The other side's fill is absent from userFills: a TWAP slice's taker fill is listed
        # by `userTwapSliceFills` instead (measured, docs/DATA_DICTIONARY.md §1.26).
        return "one_fill_ordinary"
    return "no_fill_found"


def liquidation_record(trade: dict, fills: list[dict]) -> dict:
    marked = [f for f in fills if f.get("liquidation")]
    keys = ("user", "side", "dir", "crossed", "liquidation", "hash")
    return {
        "coin": trade["coin"],
        "tid": trade["tid"],
        "hash": trade["hash"],
        "time": trade["time"],
        "fills": [{k: f.get(k) for k in keys} for f in marked],
    }


def trade_side(info: Info, trades: list[dict], bucket_ms: int) -> dict:
    """Classify trades by the fills of their addresses, keeping the liquidations found."""
    fills = resolve(info, trades, bucket_ms)
    classes = Counter(classify(fills[(t["coin"], t["tid"])]) for t in trades)
    liquidations = [
        liquidation_record(t, fills[(t["coin"], t["tid"])])
        for t in trades
        if classify(fills[(t["coin"], t["tid"])]) == "liquidation"
    ]
    # Hypothesis (a) is about the public trade's hash; the census reads fill hashes, so this
    # measures that the two are the same field.
    same_hash = Counter(
        f["hash"] == t["hash"] for t in trades for f in fills[(t["coin"], t["tid"])]
    )
    return {
        "size": len(trades),
        "classes": classes,
        "liquidations": liquidations,
        "fill_hash_equals_trade_hash": same_hash,
    }


def census(info: Info, trades: list[dict], size: int, seed: int, window: tuple[int, int]) -> dict:
    """Every liquidation-marked fill in the latest fills of a sample of the capture's addresses."""
    addresses = sorted({u for t in trades for u in t["users"]})
    sample = random.Random(seed).sample(  # noqa: S311 (a reproducible sample, not crypto)
        addresses, min(size, len(addresses))
    )
    by_key = {(t["coin"], t["tid"]): t for t in trades}
    coins = {t["coin"] for t in trades}
    fills: list[dict] = []
    for n, user in enumerate(sample):
        if n % 50 == 0:
            print(f"census {n}/{len(sample)} weight {info.weight}", file=sys.stderr)
        for fill in info.latest_fills(user):
            if (liq := fill.get("liquidation")) is None:
                continue
            public = by_key.get((fill["coin"], fill["tid"]))
            in_window = window[0] <= fill["time"] <= window[1] and fill["coin"] in coins
            fills.append(
                {
                    "user_is_liquidated": user == liq["liquidatedUser"],
                    "method": liq["method"],
                    "crossed": fill["crossed"],
                    "fill_hash_is_tx": fill["hash"] != NO_TX_HASH,
                    "in_capture_window": in_window,
                    "public_trade_hash_is_tx": None
                    if public is None
                    else public["hash"] != NO_TX_HASH,
                    "coin": fill["coin"],
                    "tid": fill["tid"],
                    "time": fill["time"],
                }
            )
    # One liquidation shows on both counterparties' fills: classify each (coin, tid) once, from
    # all of its fills, so the result never depends on which fill was seen last.
    trades_seen: dict[tuple, list[dict]] = defaultdict(list)
    for fill in fills:
        trades_seen[(fill["coin"], fill["tid"])].append(fill)
    return {
        "addresses_sampled": len(sample),
        "liquidation_fills": len(fills),
        "distinct_liquidation_trades": len(trades_seen),
        "by_method_and_hash": Counter(_census_class(group) for group in trades_seen.values()),
        "in_capture_window": [g[0] for g in trades_seen.values() if g[0]["in_capture_window"]],
    }


def _census_class(group: list[dict]) -> str:
    """`method:hash class` of one liquidation trade; `mixed` where its fills disagree."""
    methods = {f["method"] for f in group}
    hashes = {f["fill_hash_is_tx"] for f in group}
    method = methods.pop() if len(methods) == 1 else "mixed"
    if len(hashes) > 1:
        return f"{method}:mixed_hash"
    return f"{method}:{'tx_hash' if hashes.pop() else 'no_tx_hash'}"


def _confirmed(info: Info, fill: dict) -> bool:
    """Whether the liquidated user's own fills hold the same `tid` with the marker."""
    own = info.fills(
        fill["liquidation"]["liquidatedUser"], fill["time"] - 1000, fill["time"] + 1000
    )
    return any(f["tid"] == fill["tid"] and f.get("liquidation") for f in own)


def backstop(info: Info, trades: list[dict], window: tuple[int, int], confirm: int) -> dict:
    """Hypothesis (b): the HLP child vaults' backstop fills, in the window and in history."""
    vaults = info.post({"type": "vaultDetails", "vaultAddress": HLP_VAULT})
    relationship = (vaults.get("relationship") or {}) if isinstance(vaults, dict) else {}
    children = (relationship.get("data") or {}).get("childAddresses") or []
    if not children:
        print(f"vaultDetails names no child vault: {vaults}", file=sys.stderr)
    by_key = {(t["coin"], t["tid"]): t for t in trades}
    in_window, history = [], []
    left = confirm
    for vault in children:
        for fill in info.fills(vault, *window):
            if (liq := fill.get("liquidation")) is not None:
                in_window.append(
                    {
                        "vault": vault,
                        "coin": fill["coin"],
                        "tid": fill["tid"],
                        "method": liq["method"],
                        "confirmed": _confirmed(info, fill),
                        "in_public_trades": (fill["coin"], fill["tid"]) in by_key,
                    }
                )
        latest = info.latest_fills(vault)
        marked = [f for f in latest if f.get("liquidation")]
        checked = marked[:left]
        left -= len(checked)
        history.append(
            {
                "vault": vault,
                "latest_fills": len(latest),
                "newest_fill_age_days": round((window[1] - latest[0]["time"]) / DAY_MS, 2)
                if latest
                else None,
                "newest_liquidation_age_days": round((window[1] - marked[0]["time"]) / DAY_MS, 2)
                if marked
                else None,
                "span_days": round((latest[0]["time"] - latest[-1]["time"]) / DAY_MS, 2)
                if len(latest) > 1
                else 0,
                "liquidation_fills": len(marked),
                "methods": Counter(f["liquidation"]["method"] for f in marked),
                "checked": len(checked),
                "confirmed": sum(_confirmed(info, f) for f in checked),
            }
        )
    return {"vaults": children, "in_window": in_window, "history": history}


def samples(info: Info, trades: list[dict], size: int, seed: int) -> dict:
    """
    Two spot checks on seeded samples of ordinary and all-zero-hash trades (each address's fills
    within 1 s of the trade): a fill's `hash` equals its public trade's, and the side of an
    all-zero-hash trade with no fill is found in `userTwapSliceFills` (a TWAP slice).
    """
    rng = random.Random(seed)  # noqa: S311 (reproducible)
    ordinary = [t for t in trades if t["hash"] != NO_TX_HASH]
    zero = [t for t in trades if t["hash"] == NO_TX_HASH]
    picked = {
        "ordinary": rng.sample(ordinary, min(size, len(ordinary))),
        "zero": rng.sample(zero, min(size, len(zero))),
    }
    same_hash: Counter = Counter()
    twap: Counter = Counter()
    for kind, sample in picked.items():
        for trade in sample:
            for user in set(trade["users"]):
                _sample_check(info, kind, trade, user, same_hash, twap)
    return {
        "per_kind": {kind: len(sample) for kind, sample in picked.items()},
        "fill_hash_equals_trade_hash": same_hash,
        "zero_hash_absent_side_in_twap_slice_fills": twap,
    }


def _sample_check(
    info: Info,
    kind: str,
    trade: dict,
    user: str,
    same_hash: Counter,
    twap: Counter,
) -> None:
    key = (trade["coin"], trade["tid"])
    own = [
        f
        for f in info.fills(user, trade["time"] - 1000, trade["time"] + 1000)
        if (f["coin"], f["tid"]) == key
    ]
    same_hash.update(f"{kind}:{f['hash'] == trade['hash']}" for f in own)
    if kind == "zero" and not own:
        slices = info.post({"type": "userTwapSliceFills", "user": user})
        twap[any((s["fill"]["coin"], s["fill"]["tid"]) == key for s in slices)] += 1


def verdicts(report: dict) -> dict:
    """Apply the epic's rule: >= 99 % confirmed on the capture, with a measured false-negative rate."""
    cand = report["candidates"]
    confirmed_a = cand["classes"].get("liquidation", 0)
    distinct = report["census"]["by_method_and_hash"]
    # A control-sample liquidation is an ordinary-hash one: a false negative of (a) too.
    missed_by_control = len(report["control"]["liquidations"])
    total = sum(distinct.values()) + missed_by_control
    caught = sum(n for k, n in distinct.items() if k.endswith(":no_tx_hash"))
    recall_a = caught / total if total else None
    window_b = [f for f in report["backstop"]["in_window"] if f["method"] == "backstop"]
    seen_b = len(window_b)
    return {
        "a": {
            "candidates": cand["size"],
            "confirmed_liquidations": confirmed_a,
            "confirmed_share": confirmed_a / cand["size"] if cand["size"] else None,
            "census_liquidations": total - missed_by_control,
            "control_liquidations": missed_by_control,
            "census_with_no_tx_hash": caught,
            "false_negative_rate": None if recall_a is None else 1 - recall_a,
            "adopted": recall_a is not None
            and recall_a >= ADOPT_SHARE
            and confirmed_a / max(cand["size"], 1) >= ADOPT_SHARE,
        },
        "b": {
            "backstop_fills_in_window": seen_b,
            "confirmed_in_window": sum(f["confirmed"] for f in window_b),
            "census_backstop_liquidations": sum(
                n for k, n in distinct.items() if k.startswith("backstop:")
            ),
            "adopted": seen_b > 0
            and sum(f["confirmed"] for f in window_b) / len(window_b) >= ADOPT_SHARE,
        },
    }


def _positive(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be positive: {text}")
    return value


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--coins", help="comma-separated; default every captured coin")
    ap.add_argument("--control", type=_positive, default=200)
    ap.add_argument("--census", type=_positive, default=150, help="addresses whose fills are read")
    ap.add_argument("--confirm", type=_positive, default=20, help="backstop fills to confirm")
    ap.add_argument("--seed", type=int, default=332)
    ap.add_argument("--bucket-minutes", type=_positive, default=10)
    ap.add_argument("--sample", type=_positive, default=40, help="trades per kind in `samples`")
    ap.add_argument("--sample-seed", type=int, default=7)
    args = ap.parse_args()
    trades, start_ms, end_ms = load_trades(args.capture)
    if args.coins:
        coins = {coin.strip() for coin in args.coins.split(",")}
        trades = [t for t in trades if t["coin"] in coins]
    if not trades:
        raise SystemExit("no live trades for the selected coins: nothing to measure")
    bucket_ms = args.bucket_minutes * MINUTE_MS
    info = Info()
    started = time.monotonic()
    candidates = [t for t in trades if t["hash"] == NO_TX_HASH]
    ordinary = [t for t in trades if t["hash"] != NO_TX_HASH]
    control = random.Random(args.seed).sample(  # noqa: S311 (reproducible)
        ordinary, min(args.control, len(ordinary))
    )
    report = {
        "window_ms": [start_ms, end_ms],
        "trades": len(trades),
        "trades_per_coin": Counter(t["coin"] for t in trades),
        "candidates": trade_side(info, candidates, bucket_ms),
        "control": trade_side(info, control, bucket_ms),
        "census": census(info, trades, args.census, args.seed, (start_ms, end_ms)),
        "backstop": backstop(info, trades, (start_ms, end_ms), args.confirm),
        "samples": samples(info, trades, args.sample, args.sample_seed),
    }
    report["verdicts"] = verdicts(report)
    report["info"] = {
        "calls": info.calls,
        "weight": info.weight,
        "truncated_pages": info.truncated_pages,
        "elapsed_hours": round((time.monotonic() - started) / 3600, 3),
        "documented_limit_weight_per_minute": WEIGHT_PER_MINUTE,
    }
    json.dump(report, sys.stdout, indent=1, default=str)


if __name__ == "__main__":
    main()
