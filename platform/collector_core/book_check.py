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

REST and WS are sampled microseconds apart on a moving book, so exact equality would cry wolf;
the caller therefore compares against two live captures (before/after the REST call) and only
counts a mismatch present in both. The tolerances below are deliberately small -- revisit them
from the one-hour evidence in DATA_INTEGRITY_AUDIT.md, never loosen them to make a run pass.
"""

Level = tuple[float, float]  # (price, size)


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
