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
Pure diff of a live book side against a REST snapshot of the same side (Story 22.5, DATA-02).

A REST snapshot and the live book are only comparable at the *same* venue state (audit D-64):
Hyperliquid's `l2Book` pushes every ~5 s while REST is live, and a venue-timed book trails the
wall clock by `1 + hold_back_seconds` by design, so two samples taken around the REST call
disagree on a correct book. The collector therefore aligns the samples on the venue's own key
(`BookSnapshot.sequence` for Bybit, `BookSnapshot.ts_event_ns` for Hyperliquid) and judges
them *exactly*; the tolerances below exist for callers that cannot align and must never be
loosened to make a run pass.
"""

from dataclasses import dataclass


Level = tuple[float, float]  # (price, size)

# Aligned samples are the same venue state, so only float representation may differ: a size
# parsed from a REST decimal string and one read back from a `Quantity` can differ in the last
# ulp. No price slack: a level present in one aligned sample and not the other is a finding.
EXACT_PRICE_TOLERANCE_LEVELS = 0
EXACT_SIZE_REL_TOLERANCE = 1e-9


@dataclass(frozen=True)
class BookSnapshot:
    """
    A venue's REST book with the key the live book can be aligned on.

    Invariant: at most one alignment key is set, and the caller compares only against a live
    capture whose key matches (`sequence`: the live capture bracketing this sequence;
    `ts_event_ns`: the live capture with exactly this `ts_event`). A snapshot with neither key
    cannot be judged and is skipped, never compared against the wall clock.
    """

    bids: list[Level]  # best first
    asks: list[Level]  # best first
    sequence: int | None = None  # Bybit `seq`, the same counter the WS stamps on every delta
    ts_event_ns: int | None = None  # Hyperliquid `time` (ms) as ns, the WS push's `ts_event`

    def __post_init__(self) -> None:
        if self.sequence is not None and self.ts_event_ns is not None:
            raise ValueError("BookSnapshot: sequence and ts_event_ns are mutually exclusive")


def _key(price: float) -> float:
    return round(price, 8)  # Price.as_double() and a parsed REST string can differ in the last ulp


def top_levels_mismatch(
    live: list[Level],
    rest: list[Level],
    *,
    depth: int = 20,
    price_tolerance_levels: int = 1,
    size_rel_tolerance: float = 0.05,
) -> list[str]:
    """
    Human-readable mismatches between one side's top `depth` levels (best first); [] = agree.

    Each message starts with a stable "<kind> <price>:" head (values follow the colon), so a
    caller can tell whether the *same* level is still wrong on a later comparison.

    Only the first `min(len(live), len(rest), depth)` live levels are judged, so a REST book
    shorter than `depth` is not a mismatch. Up to `price_tolerance_levels` live prices may be
    absent from REST (one level inserted/removed between the two samples shifts the tail).
    """
    live, rest = live[:depth], rest[:depth]
    if not live or not rest:
        return (
            [] if not live and not rest else [f"one side empty: live={len(live)} rest={len(rest)}"]
        )
    out: list[str] = []
    if _key(live[0][0]) != _key(rest[0][0]):
        out.append(f"best price: live={live[0][0]} rest={rest[0][0]}")
    rest_sizes = {_key(p): s for p, s in rest}
    judged = live[: min(len(live), len(rest))]
    missing = [p for p, _ in judged if _key(p) not in rest_sizes]
    if len(missing) > price_tolerance_levels:
        out.extend(
            f"absent from REST {price}: live level not in REST top {depth}" for price in missing
        )
    for price, size in judged:
        rest_size = rest_sizes.get(_key(price))
        if rest_size is not None and abs(size - rest_size) > size_rel_tolerance * max(
            size, rest_size
        ):
            out.append(f"size at {price}: live={size} rest={rest_size}")
    return out


def persistent(first: list[str], second: list[str]) -> list[str]:
    """`second`'s mismatches whose head (text before the first colon) was also in `first`."""
    heads = {m.split(":")[0] for m in first}
    return [m for m in second if m.split(":")[0] in heads]
