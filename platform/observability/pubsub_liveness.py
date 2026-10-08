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
The one heartbeat-silence receive loop for a Redis pub/sub subscriber (DW-283).

A half-open connection never errors: `listen()` blocks forever and the subscriber's cache freezes
until the process restarts. On a channel whose publisher sends on a heartbeat, silence is
therefore the only liveness signal, so `receive_until_silent` raises once a channel has been
quiet past its threshold and the caller's own reconnect loop resubscribes. The caller keeps its
channel, threshold, poll timeout and reconnect loop; only the loop itself lives here.

Deliberate exception: a channel that can be legitimately silent (its publisher sometimes has
nothing to send) must not use this helper, which would reconnect a healthy connection every
window. Such a subscriber probes with a PING after silence and raises only on an unanswered one;
each such subscriber says so in its own `_receive`.
"""

import time
from collections.abc import Callable
from typing import Any
from typing import NoReturn
from typing import Protocol


class MessagePoller(Protocol):
    """
    The one call `receive_until_silent` makes on a pub/sub (`redis.asyncio`'s `PubSub` satisfies
    it structurally).

    Invariant: a poll returns within `timeout` seconds whether or not a frame arrived, which is
    what lets the silence check run on a connection that has stopped delivering; a poller that
    blocks without a timeout would defeat it.
    """

    async def get_message(self, *, ignore_subscribe_messages: bool, timeout: float) -> Any: ...


async def receive_until_silent(
    pubsub: MessagePoller,
    channel: str,
    silence_seconds: float,
    ingest: Callable[[str], None],
    *,
    poll_seconds: float,
    after_poll: Callable[[], None] | None = None,
) -> NoReturn:
    """
    Pass every `"message"` frame's data to `ingest` until `silence_seconds` pass without one, then
    raise `ConnectionError("no <channel> message for <silence_seconds>s")`.

    Only a `"message"` frame refreshes the window: any other frame (a pong, say) proves the socket
    answers but not that the publisher's heartbeat still arrives. `after_poll`, when given, runs
    once after every poll, before the silence check.

    Precondition: the client decodes responses (`decode_responses=True`, so `ingest` gets `str`)
    and subscribed with `subscribe`, whose frames are typed `"message"` (`psubscribe`'s are
    `"pmessage"` and would read as silence). The clock is `time.monotonic`, so a wall-clock step
    never fires or masks the check; it is read through the `time` module so a test's patched
    clock reaches it.
    """
    heard = time.monotonic()
    while True:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=poll_seconds)
        is_data = message is not None and message["type"] == "message"
        if is_data:
            heard = time.monotonic()
            ingest(message["data"])
        if after_poll is not None:
            after_poll()
        if not is_data and time.monotonic() - heard > silence_seconds:
            raise ConnectionError(f"no {channel} message for {silence_seconds:.0f}s")
