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
`FlushBatch`: the catalog write buffer, keyed by (data type, instrument id) (DDD spine AD-D7).

Invariant (the flush tie guard): a `TradeTick` batch is written only up to, not including, its
newest-`ts_init` group while that group is younger than `TRADE_CARRY_NS` or the ingest queue still
holds messages. Every adapter stamps one `ts_init` per WS message, so a message's trades can
straddle the flush, and `write_data` refuses a file whose `[first, last]` `ts_init` interval
touches an existing one -- the next flush would lose its whole batch on the tie. When a trade
group is carried, that instrument's snapshot rows sampled after the group's `ts_init` are carried
with it: they are the only rows that can hold those trades, so a crash before the next flush loses
both together -- an honest gap -- instead of leaving rows whose trades the archive never received
(the rebuild would zero them).

A `defaultdict` so the hot path's append is exactly the plain dict-of-lists it replaced (AD-D5).
"""

import bisect
from collections import defaultdict
from typing import Any

from kernel.second_snapshot import DydxSecondSnapshot

from nautilus_trader.model.data import TradeTick


TRADE_CARRY_NS: int = 5_000_000_000

BatchKey = tuple[type, str]


def split_open_ts_init_group(
    items: list[Any], now_ns: int, queue_pending: bool
) -> tuple[list[Any], list[Any]]:
    """
    Split a `ts_init`-sorted batch into (write now, carry to the next flush): the group sharing
    the newest `ts_init` is carried while it is younger than `TRADE_CARRY_NS`, or while the
    ingest queue still holds messages (under lag, the rest of that WS message can sit there for
    longer than any age bound).
    """
    newest = items[-1].ts_init
    if now_ns - newest >= TRADE_CARRY_NS and not queue_pending:
        return items, []
    cut = bisect.bisect_left([d.ts_init for d in items], newest)
    return items[:cut], items[cut:]


class FlushBatch(defaultdict[BatchKey, list[Any]]):
    """The buffer (see the module docstring); `take` swaps the writable part out."""

    def __init__(self) -> None:
        super().__init__(list)

    def take(
        self, now_ns: int, final: bool, queue_pending: bool
    ) -> list[tuple[BatchKey, list[Any]]]:
        """
        Swap every non-empty list out as a `ts_init`-sorted batch, leaving any carried items.
        `final` (shutdown): everything goes.
        """
        carried_from: dict[str, int] = {}
        batches: list[tuple[BatchKey, list[Any]]] = []
        # Trades first: their carry decides what the snapshot batches keep back.
        for key in sorted(self, key=lambda k: k[0] is not TradeTick):
            items = sorted(self[key], key=lambda d: d.ts_init)
            carry: list[Any] = []
            if key[0] is TradeTick and items and not final:
                items, carry = split_open_ts_init_group(items, now_ns, queue_pending)
                if carry:
                    carried_from[key[1]] = carry[0].ts_init
            elif key[0] is DydxSecondSnapshot and key[1] in carried_from:
                # By sample time: a venue row's ts_event is its exchange second.
                cut = bisect.bisect_right([d.ts_init for d in items], carried_from[key[1]])
                items, carry = items[:cut], items[cut:]
            self[key] = carry
            if items:
                batches.append((key, items))
        return batches
