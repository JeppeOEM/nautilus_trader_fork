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
Seeded adversarial cases for the derived-signal comparisons (Story 31.3): books of 0-50 levels
with empty sides, zero top sizes, one-sided and crossed tops; price and size precisions 0..8;
`ts_event` gaps (under, at and over the 3 s OFI rule), duplicate seconds and week-boundary stamps;
float series with NaN/None gaps. Every generator takes a `random.Random(seed)` and nothing else
random.

One case feeds both sides from the same integers: `stored_row` writes the §1.7 stored layout with
this module's own gap encoder, the production side decodes it with `DydxSecondSnapshot.from_dict`
and the reference with `RefBook.from_stored` -- so the two decoders are compared too.
"""

import gzip
import json
import random
from dataclasses import dataclass
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from kernel.second_snapshot import DydxSecondSnapshot

from verification.domain.reference_signals import RefBook
from verification.domain.signal_compare import Agreement
from verification.domain.signal_compare import relative


NS_PER_S = 1_000_000_000
# 2026-09-28 00:00 UTC, a Monday: series start near here so 1W and 1D boundaries are crossed.
MONDAY_NS = 1_790_553_600 * NS_PER_S
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "snapshots"
FIXTURE_IDS = (
    "BTCUSDT-LINEAR.BYBIT",
    "BTCUSDT-SPOT.BYBIT",
    "ETHUSDT-LINEAR.BYBIT",
    "ETHUSDT-SPOT.BYBIT",
    "SOL-USD-PERP.HYPERLIQUID",
)
CASE_IID = "CASE-USD-PERP.HYPERLIQUID"


@dataclass(frozen=True)
class BookCase:
    """One second as absolute integer units (level prices best first), before any encoding."""

    ts_event: int
    price_precision: int
    size_precision: int
    bid_units: tuple[int, ...]
    bid_size_units: tuple[int, ...]
    ask_units: tuple[int, ...]
    ask_size_units: tuple[int, ...]
    buy_volume: int = 0
    sell_volume: int = 0
    buy_count: int = 0
    sell_count: int = 0
    ohlc: tuple[int | None, int | None, int | None, int | None] = (None, None, None, None)


def _gaps(units: tuple[int, ...], side: str) -> list[int]:
    """Encode §1.7 here, independently: the best price, then each positive gap to the level above."""
    out = list(units[:1])
    for i in range(1, len(units)):
        out.append(units[i - 1] - units[i] if side == "bid" else units[i] - units[i - 1])
    return out


def stored_row(case: BookCase, instrument_id: str = CASE_IID) -> dict[str, Any]:
    """Return the stored/wire row of a case (the integer layout, gap-encoded book)."""
    o, h, low, c = case.ohlc
    return {
        "instrument_id": instrument_id,
        "price_precision": case.price_precision,
        "size_precision": case.size_precision,
        "bid_prices": _gaps(case.bid_units, "bid"),
        "bid_sizes": list(case.bid_size_units),
        "ask_prices": _gaps(case.ask_units, "ask"),
        "ask_sizes": list(case.ask_size_units),
        "buy_volume": case.buy_volume,
        "sell_volume": case.sell_volume,
        "buy_count": case.buy_count,
        "sell_count": case.sell_count,
        "open_price": o,
        "high_price": h,
        "low_price": low,
        "close_price": c,
        "ts_event": case.ts_event,
        "ts_init": case.ts_event,
    }


def both(rows: list[dict[str, Any]]) -> tuple[list[DydxSecondSnapshot], list[RefBook]]:
    """Decode the same stored rows on both sides: production's `from_dict`, the reference's own."""
    return [DydxSecondSnapshot.from_dict(r) for r in rows], [RefBook.from_stored(r) for r in rows]


def seeded(seed: int) -> random.Random:
    """Return the one generator source of every case: a seeded `random.Random`."""
    return random.Random(seed)  # noqa: S311 -- deterministic test data, not cryptography


def load_fixture(instrument_id: str) -> list[dict[str, Any]]:
    """Return the committed real rows of one soak instrument (`cut_snapshot_fixtures`)."""
    with gzip.open(FIXTURE_DIR / f"{instrument_id}.jsonl.gz", "rt", encoding="utf-8") as lines:
        return [json.loads(line) for line in lines]


# --- the adversarial book generator ------------------------------------------------------------


@dataclass
class _Walk:
    """The evolving book of one generated series (mutable: one per series, never shared)."""

    best_bid: int
    spread: int
    bid_gaps: list[int]
    ask_gaps: list[int]
    bid_sizes: list[int]
    ask_sizes: list[int]


def _size(rng: random.Random) -> int:
    return 0 if rng.random() < 0.05 else rng.randint(1, 10 ** rng.randint(0, 6))


def _new_walk(rng: random.Random, max_levels: int) -> _Walk:
    bids, asks = rng.randint(1, max_levels), rng.randint(1, max_levels)
    return _Walk(
        best_bid=rng.randint(10**3, 10**7),
        spread=rng.randint(1, 20),
        bid_gaps=[rng.randint(1, 5) for _ in range(bids - 1)],
        ask_gaps=[rng.randint(1, 5) for _ in range(asks - 1)],
        bid_sizes=[_size(rng) for _ in range(bids)],
        ask_sizes=[_size(rng) for _ in range(asks)],
    )


def _resize(rng: random.Random, gaps: list[int], sizes: list[int], max_levels: int) -> None:
    """Grow or shrink a side by a level now and then; keep most sizes, redraw some."""
    if rng.random() < 0.1 and len(sizes) < max_levels:
        gaps.append(rng.randint(1, 5))
        sizes.append(_size(rng))
    elif rng.random() < 0.1 and len(sizes) > 1:
        gaps.pop()
        sizes.pop()
    for i in range(len(sizes)):
        if rng.random() < 0.4:
            sizes[i] = _size(rng)


def _step(rng: random.Random, walk: _Walk, max_levels: int) -> None:
    walk.best_bid = max(walk.best_bid + rng.choice((-2, -1, 0, 0, 0, 1, 2)), 10**3)
    if rng.random() < 0.1:
        walk.spread = rng.randint(1, 20)
    _resize(rng, walk.bid_gaps, walk.bid_sizes, max_levels)
    _resize(rng, walk.ask_gaps, walk.ask_sizes, max_levels)


def _ladder(best: int, gaps: list[int], side: str) -> tuple[int, ...]:
    levels = [best]
    for gap in gaps:
        levels.append(levels[-1] - gap if side == "bid" else levels[-1] + gap)
    return tuple(levels)


def _trades(rng: random.Random, low: int, high: int) -> dict[str, Any]:
    """Return a traded second (volume, counts, OHLC inside the book's reach) or an untraded one."""
    if rng.random() < 0.4:
        return {}
    buy = rng.randint(0, 10**6) if rng.random() < 0.8 else 0
    sell = rng.randint(0, 10**6) if rng.random() < 0.8 else 0
    if buy == 0 and sell == 0:
        return {}
    o, c = rng.randint(low, high), rng.randint(low, high)
    hi, lo = max(o, c) + rng.randint(0, 3), max(min(o, c) - rng.randint(0, 3), 1)
    return {
        "buy_volume": buy,
        "sell_volume": sell,
        "buy_count": rng.randint(1, 20) if buy else 0,
        "sell_count": rng.randint(1, 20) if sell else 0,
        "ohlc": (o, hi, lo, c),
    }


def _top(rng: random.Random, walk: _Walk) -> tuple[int, int, list[int], list[int]]:
    """Return the best prices and top sizes of this second, with the rare adversarial top."""
    best_ask = walk.best_bid + walk.spread
    if rng.random() < 0.03:  # crossed or touched: computed as written, never clamped
        best_ask = walk.best_bid - rng.randint(0, 3)
    bid_sizes, ask_sizes = list(walk.bid_sizes), list(walk.ask_sizes)
    if rng.random() < 0.03:  # zero top sizes: microprice undefined
        bid_sizes[0] = ask_sizes[0] = 0
    return walk.best_bid, best_ask, bid_sizes, ask_sizes


def _case(rng: random.Random, walk: _Walk, ts: int, precisions: tuple[int, int]) -> BookCase:
    best_bid, best_ask, bid_sizes, ask_sizes = _top(rng, walk)
    bids = _ladder(best_bid, walk.bid_gaps, "bid")
    asks = _ladder(best_ask, walk.ask_gaps, "ask")
    case = BookCase(ts, *precisions, bids, tuple(bid_sizes), asks, tuple(ask_sizes))
    case = replace(case, **_trades(rng, max(best_bid - 5, 1), best_ask + 5))
    roll = rng.random()
    if roll < 0.02:
        return replace(case, bid_units=(), bid_size_units=())
    if roll < 0.04:
        return replace(case, ask_units=(), ask_size_units=())
    if roll < 0.05:
        return replace(case, bid_units=(), bid_size_units=(), ask_units=(), ask_size_units=())
    return case


def next_ts(rng: random.Random, ts: int) -> int:
    """1 s mostly; now and then a gap under, at or over the 3 s OFI rule, or a duplicate second."""
    roll = rng.random()
    if roll < 0.03:
        return ts + 200_000_000  # a second row inside the same floor second
    if roll < 0.06:
        return ts + rng.choice((2, 3, 3, 4, 7, 30)) * NS_PER_S  # 3 s exactly is no gap (strict >)
    if roll < 0.065:
        return ts + 3 * NS_PER_S + 1  # one nanosecond over the rule
    return ts + NS_PER_S


def book_series(
    rng: random.Random, rows: int, max_levels: int = 50, start_ns: int | None = None
) -> list[BookCase]:
    """One instrument's seconds: fixed precisions p, s in 0..8, an evolving book, gaps."""
    precisions = (rng.randint(0, 8), rng.randint(0, 8))
    walk = _new_walk(rng, max_levels)
    ts = (start_ns if start_ns is not None else MONDAY_NS - 30 * NS_PER_S) + NS_PER_S // 2
    cases = []
    for _ in range(rows):
        cases.append(_case(rng, walk, ts, precisions))
        _step(rng, walk, max_levels)
        ts = next_ts(rng, ts)
    return cases


def two_sided(cases: list[BookCase]) -> list[BookCase]:
    return [c for c in cases if c.bid_units and c.ask_units]


# --- sparse rows over weeks, for the candle fold ---------------------------------------------


def sparse_series(rng: random.Random, rows: int, span_days: int) -> list[BookCase]:
    """
    Rows spread over `span_days` from a Monday, stamps sorted with duplicates and exact bucket
    boundaries (Monday/midnight/hour starts) among them; one book shape, trades varying.
    """
    precisions = (rng.randint(0, 8), rng.randint(0, 8))
    walk = _new_walk(rng, 5)
    stamps = sorted(
        MONDAY_NS
        + rng.randrange(-2 * 86_400, span_days * 86_400) * NS_PER_S
        + rng.choice((0, NS_PER_S // 2))
        for _ in range(rows)
    )
    stamps[len(stamps) // 2 : len(stamps) // 2] = [stamps[len(stamps) // 2]] * 2  # duplicates
    cases = []
    for ts in stamps:
        cases.append(_case(rng, walk, ts, precisions))
        _step(rng, walk, 5)
    return cases


# --- float series with gaps, for the research functions -----------------------------------------


def price_path(rng: random.Random, points: int, gap_rate: float = 0.05) -> list[float | None]:
    """Return a positive float price path (a decoded unit value per point) with None gaps."""
    precision = rng.randint(0, 6)
    units = rng.randint(10**4, 10**7)
    path: list[float | None] = []
    for _ in range(points):
        units = max(units + rng.randint(-500, 500), 1_000)
        path.append(None if rng.random() < gap_rate else units / 10**precision)
    return path


def float_series(rng: random.Random, points: int, nan_rate: float = 0.08) -> list[float]:
    """Return returns-like floats with NaN gaps."""
    return [
        float("nan") if rng.random() < nan_rate else rng.gauss(0.0, 1e-3) for _ in range(points)
    ]


def carried(values: list[Decimal | None]) -> list[Decimal | None]:
    """Apply the §2.3 Known limit as a stateful OBI publishes it: None keeps the last defined value."""
    out: list[Decimal | None] = []
    for value in values:
        out.append(value if value is not None else (out[-1] if out else None))
    return out


def pinned_zero(prod: float | None, expected: float | None) -> Agreement:
    """
    Pinned Known limit (§2.2): an undefined z-score (under 2 readings, or all equal) is published
    as 0.0 by `RollingZScore`; the reference says None. Anything else is judged `relative`.
    """
    if expected is None and prod == 0.0:
        return Agreement.BOTH_UNDEFINED
    return relative(prod, expected)
