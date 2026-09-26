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
`Bot`: one running bot's identity, heartbeat state and feed-health incident log (AD-10, AD-D15).

The incident log is `bots:incidents:{bot_id}` (operator request: a WS/feed-health log viewable
from bot_tui without digging through logs): a bounded list of `{type, started_at, ended_at}` spans
-- `data_stale` (the strategy's last quote silent for `DATA_STALE_NS`, the OBS-01 doctrine applied
to one bot's own feed, since no typed reconnect event exists to hook) and `process_start` (a
zero-duration marker, once per process life, so a restart is never invisible just because no
staleness incident accompanied it). The list entries are the published wire shape verbatim.

Whether the strategy is running is deliberately not cached here: the Nautilus strategy's
`is_running` is the one truth, handed to `observe` on every heartbeat tick.
"""

# 30 s of silence on a live instrument's quote feed is a pipeline failure, never a quiet market
# (OBS-01) -- the same threshold capture's own watchdog applies, here to one bot's feed.
DATA_STALE_NS: int = 30_000_000_000

# Bounded incident history (MEM-01): oldest entries drop off first.
MAX_INCIDENTS: int = 50

type BotId = str
"""A bot's identity: its config `bot_id`, pinned as its strategy's Nautilus `order_id_tag`."""


def incident_transition(
    now: float, is_stale: bool, incidents: list[dict]
) -> tuple[list[dict], bool]:
    """
    One step of the staleness state machine: open a `data_stale` span on a stale start, close the
    open one on recovery, otherwise leave the log alone. Returns `(incidents, changed)`, so the
    log is written only on an actual transition, never on every heartbeat tick.
    """
    open_incident = incidents[-1] if incidents and incidents[-1]["ended_at"] is None else None
    if is_stale:
        if open_incident is None:
            new_incident = {"type": "data_stale", "started_at": now, "ended_at": None}
            return [*incidents, new_incident], True
        return incidents, False
    if open_incident is not None:
        closed = {**open_incident, "ended_at": now}
        return [*incidents[:-1], closed], True
    return incidents, False


def close_orphaned_incident(incidents: list[dict], now: float) -> list[dict]:
    """
    Close a span a previous process life left open: the loop that would have closed it is gone,
    and the log must not show a permanently "ongoing" incident from a process no longer running.
    """
    if not incidents or incidents[-1]["ended_at"] is not None:
        return incidents
    closed = {**incidents[-1], "ended_at": now, "note": "closed by restart"}
    return [*incidents[:-1], closed]


def record_process_start(incidents: list[dict], now: float) -> list[dict]:
    """Append a zero-duration marker: a restart must show in the same log as the stale spans."""
    return [*incidents, {"type": "process_start", "started_at": now, "ended_at": now}]


def trim_incidents(incidents: list[dict]) -> list[dict]:
    return incidents[-MAX_INCIDENTS:]


def is_feed_stale(last_data_ns: int, started_at: float, now_ns: int) -> bool:
    """
    Return True once `DATA_STALE_NS` passed with no data -- measured from the last quote if one ever
    arrived, otherwise from process start, so a feed that never connects at all still opens a
    `data_stale` incident (`last_data_ns == 0` would otherwise look like "healthy, no tick yet").
    """
    reference_ns = last_data_ns if last_data_ns != 0 else int(started_at * 1e9)
    return (now_ns - reference_ns) > DATA_STALE_NS


class Bot:
    """
    One bot of the process, addressed by `BotId` on every `bots:*` key and channel.

    Invariants: the incident log holds at most `MAX_INCIDENTS` entries, and at most one open
    (`ended_at is None`) span, always the last; a stopped bot never opens a `data_stale` span (a
    deliberate stop is not a feed outage); `process_start` is recorded once per process life
    (`start`), never per Redis reconnect. Commands: `start`, `observe`.
    """

    def __init__(self, bot_id: BotId, mode: str, started_at: float) -> None:
        self.id = bot_id
        self.mode = mode
        self.started_at = started_at
        self._incidents: list[dict] = []
        self._started = False

    @property
    def incidents(self) -> list[dict]:
        return list(self._incidents)

    @property
    def started(self) -> bool:
        return self._started

    def start(self, prior: list[dict], now: float) -> list[dict]:
        """
        Adopt the previous life's log: close its orphaned span, mark this start, trim. Once per
        process life: a second call would log a restart that never happened.
        """
        if self._started:
            raise RuntimeError(f"bot {self.id} already recorded this process's start")
        # Built before the flag is set: a malformed prior log raises here and leaves the bot
        # unstarted, so the caller can still start it from an empty log that marks this life.
        incidents = trim_incidents(record_process_start(close_orphaned_incident(prior, now), now))
        self._incidents = incidents
        self._started = True
        return self.incidents

    def observe(self, now: float, now_ns: int, last_data_ns: int, running: bool) -> bool:
        """One heartbeat tick of feed health; True when the log changed and must be republished."""
        is_stale = running and is_feed_stale(last_data_ns, self.started_at, now_ns)
        new_incidents, changed = incident_transition(now, is_stale, self._incidents)
        if changed:
            self._incidents = trim_incidents(new_incidents)
        return changed
