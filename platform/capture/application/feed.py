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
Feed tags (story 22.14): which WebSocket connection a message arrived on, and the redundant
trades-only socket's failure handling for the venue clients.

`Feed`/`MAIN_FEED` are `capture.domain.feed_group`'s (the `FeedGroup` aggregate keeps
liveness, reconnect detection and arbitration per feed since Story 26.1), and `REST_FEED_NAME`
is `capture.domain.trade_intake`'s; this module is the clients' import point for them.
"""

from collections.abc import Awaitable
from typing import Any

from capture.application import sites
from capture.application.ports import Ledger
from capture.domain.feed_group import MAIN_FEED
from capture.domain.feed_group import Feed
from capture.domain.trade_intake import REST_FEED_NAME


__all__ = [
    "MAIN_FEED",
    "REST_FEED_NAME",
    "Feed",
    "OptionalStepFailed",
    "optional_feed_send",
    "optional_feed_step",
]


async def optional_feed_step(feed: Feed, action: str, step: Awaitable[Any], ledger: Ledger) -> bool:
    """
    Run one step (connect, subscribe, ...) on a redundant trades-only socket; False, ledgered
    (`collector.trade_feed`, through the collector's `ledger`), when it fails. The second feed
    exists to add redundancy, so its failure must never take the primary connection's data down.
    """
    try:
        await step
    except Exception as e:
        ledger(sites.TRADE_FEED, f"{feed.name}: {action} failed", e)
        return False
    return True


class OptionalStepFailed(Exception):
    """
    A trades-only socket step failed and was already ledgered (`optional_feed_send`). Raised so a
    wire bookkeeping call (`wire_channels.WireChannels`) does not count the channel held and runs
    its undo; the client then suppresses it, since that socket's failure is never fatal.
    """


async def optional_feed_send(feed: Feed, action: str, step: Awaitable[Any], ledger: Ledger) -> None:
    """`optional_feed_step`, raising `OptionalStepFailed` (after the ledger entry) on failure."""
    if not await optional_feed_step(feed, action, step, ledger):
        raise OptionalStepFailed(f"{feed.name}: {action} failed")
