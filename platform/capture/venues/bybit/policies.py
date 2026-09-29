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
Bybit's capture policy (DATA-08, DDD spine AD-D6): the `u` sequence canary, as a pure value the
core's `LiveBook` runs. Was `BybitCollector._apply_deltas` until Story 26.1.

Bybit documents `u` as a per-topic sequence and `u == 1` as a service-restart snapshot, and it
was measured contiguous in a healthy stream: raw capture 2026-09-20 (`scripts/capture_hl_ws.py
--venue bybit`, 800 s, BTCUSDT), 31,862 `orderbook.50` frames, **31,860 of 31,860 delta steps
exactly +1**, zero gaps and zero regressions (DATA_INTEGRITY_AUDIT.md D-41). So a regress
(`u` <= previous: replayed or reordered) or a gap (`u` skips: lost) means the local book can no
longer be trusted: the core drops the message and the book, ledgers `collector.book_sequence`
and resyncs (DATA-03 fallback; a rising count is an open incident). Story 31.5 measured it on
every collected topic, spot included (the recorder's `orderbook.50` delta frames, 2026-09-29
12:59:19-16:12Z: linear BTC/ETH 481,524 / 507,908, spot BTC/ETH 403,606 / 312,005, 0 `u` breaks,
0 zero-level deltas; spot `u` also steps exactly +1), so a spot break is lost or reordered messages too
(audit D-98).

Zero levels (DATA-08, audit D-96): a zero-level *delta* never reaches Python -- the adapter's
`OrderBookDeltas::new_checked` refuses an empty vector (`crates/model/src/data/deltas.rs`) and
the Bybit client only `log::error!`s the failure (`crates/adapters/bybit/src/python/websocket.rs`)
-- so its `u` is missing here and the next delta reads as a gap: a `collector.book_sequence`
entry and a resync for a message that carried no change. Known limit: that false break cannot be
fixed on this side of the bindings (FORK-01); it is loud, never silent, and measured 0 times on
the wire (above). Upgrade path: the adapter forwards a zero-level delta as an empty-but-sequenced
message. A zero-level *snapshot* arrives as a lone `Clear` (no `u` to read) and still
re-baselines (`LiveBook._check_sequence`).
"""

from capture.domain.policies import SequenceVerdict
from nautilus_trader.model.data import OrderBookDeltas


def sequence_verdict(last_u: int | None, u: int, is_snapshot: bool) -> SequenceVerdict:
    """
    Classify a book message's update id `u` against the previous one. With no baseline
    (`last_u is None`) a non-snapshot cannot be judged: "ok".
    """
    if is_snapshot or u == 1:
        return "snapshot"
    if last_u is None:
        return "ok"
    if u <= last_u:
        return "regress"
    return "gap" if u - last_u > 1 else "ok"


def message_u(deltas: OrderBookDeltas) -> int | None:
    """
    Return the message's `u`, or None when it carries no level to read it from.

    Verified against crates/adapters/bybit/src/websocket/parse.rs (`parse_orderbook_deltas`,
    lines 232-316): `u` becomes `BookOrder.order_id` of *every* level delta of the message
    (one value per message); `seq` is `OrderBookDelta.sequence`; a `type=snapshot` message
    starts with a `Clear` delta (no order, hence skipped here); a message with zero levels
    carries no `u` at all.
    """
    for delta in deltas.deltas:
        if not delta.is_clear:
            return delta.order.order_id
    return None


class BybitSequenceCanary:
    """`SequenceCanary` over Bybit's `u`. Invariant: every checked message is +1 on the last."""

    def message_key(self, deltas: OrderBookDeltas) -> int | None:
        return message_u(deltas)

    def verdict(self, last: int | None, key: int, is_snapshot: bool) -> SequenceVerdict:
        return sequence_verdict(last, key, is_snapshot)
