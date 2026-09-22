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
Feed tags (story 22.14): which WebSocket connection a message arrived on.

A `Feed` names one connection (`name`) and the group of connections that carry the same trades
(`group`: a primary socket and its optional trades-only twin, `trade_feeds = 2`). The core keeps
liveness, reconnect detection and first-copy arbitration per feed name; the one-sided-outage
alert compares feeds within a group. `trades_only` feeds carry no book, so they never count
towards the book-feed staleness gate (`_last_feed_message_ns`) or the silence detection.
"""

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any

from observability import error_ledger


@dataclass(frozen=True)
class Feed:
    name: str
    group: str
    trades_only: bool = False


MAIN_FEED = Feed("main", "main")
# The first-copy source name of a trade archived by the REST backfill. Never a WS feed: REST data
# never goes through `_on_data`, so it never counts as liveness.
REST_FEED_NAME = "rest"


async def optional_feed_step(feed: Feed, action: str, step: Awaitable[Any]) -> bool:
    """
    Run one step (connect, subscribe, ...) on a redundant trades-only socket; False, ledgered
    (`collector.trade_feed`), when it fails. The second feed exists to add redundancy, so its
    failure must never take the primary connection's data down with it.
    """
    try:
        await step
    except Exception as e:
        error_ledger.record("collector.trade_feed", f"{feed.name}: {action} failed", e)
        return False
    return True
