# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software distributed under the
#  License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
#  either express or implied.  See the License for the specific language governing permissions
#  and limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
The DW-266 memory canary (operator decision 2026-10-05: "read cgroup memory each flush and
ledger above threshold -- Memory canary ledger"; the notification push added by the operator,
2026-10-09): the collector's own container memory, read from cgroup v2, warned *before* the
cgroup OOM-kill the compose `mem_limit` makes. Without it the only signal of a plan grown at
runtime past what the limit was measured for is `OOMKilled=true` after the restart
(`docker-compose.yml`'s Known limit, DEPLOY_CHECKLIST section 7).

`memory_usage` reads the container's own `memory.current`/`memory.max` (the same pair
DEPLOY_CHECKLIST's re-measure step reads); `MemoryCanary` is the once-per-crossing policy: a
slow creep sends one ledger line and one notification, not one per flush.

Known limit: cgroup v2 only (every Docker host this stack runs on is v2); a v1 host reads as
unlimited, so the canary is off there. Upgrade path: the v1 `memory.usage_in_bytes`/
`memory.limit_in_bytes` pair under `memory/`, should a v1 host ever run a collector.
Known limit: `memory.max` of `max` (no limit: the dev box outside Docker, or the test stack's
lifted limit) and an unreadable base mean no canary: an environment property, never a fault,
so they are never ledgered.
"""

from pathlib import Path


# The cgroup v2 root inside a container: the container's own `memory.current`/`memory.max`,
# not the host's (Docker mounts the container's subtree here).
CGROUP_BASE = Path("/sys/fs/cgroup")

# The canary's levels, as fractions of `memory.max`. A `mem_limit` is the measured peak x 1.5
# (docker-compose.yml, DEPLOY_CHECKLIST section 7), so a collector running at its measured
# peak sits at ~2/3 of its own limit: fire at 0.9 (1.35x the peak, the last ~10% before the
# cgroup kill -- about two added instruments of headroom at the ~16 MiB scaling) and re-arm
# at 0.8 (1.2x the peak, so an excursion above the fire level that has not fallen back does
# not re-arm the canary mid-elevation).
MEMORY_PRESSURE_FIRE = 0.9
MEMORY_PRESSURE_REARM = 0.8


def memory_usage(base: Path) -> tuple[int, int] | None:
    """
    Return the container's `(memory.current, memory.max)` bytes from the cgroup v2 files under
    `base`, or None with no limit (`memory.max` is `max`) or on any read/parse failure: an
    environment property (no cgroup, a v1 host), never a fault to ledger.
    """
    try:
        current = int((base / "memory.current").read_text().strip())
        limit_text = (base / "memory.max").read_text().strip()
    except (OSError, ValueError):
        return None
    if limit_text == "max":
        return None
    try:
        limit = int(limit_text)
    except ValueError:
        return None
    if limit <= 0:
        return None
    return current, limit


class MemoryCanary:
    """
    The once-per-crossing policy over `memory_usage`'s samples (DW-266's decision: warn,
    never refuse a plan change).

    Invariant: `check` returns True at most once per crossing of the fire level -- after a
    fire it stays False until a sample falls below the re-arm level, so usage oscillating
    around the fire level sends one message, not one per minute (the notification has no
    ledger cap to absorb a flap). A None sample (no canary this host) changes nothing: the
    armed state survives a missing canary exactly as it survives a normal sample.
    """

    def __init__(
        self,
        fire: float = MEMORY_PRESSURE_FIRE,
        rearm: float = MEMORY_PRESSURE_REARM,
    ) -> None:
        self._fire = fire
        self._rearm = rearm
        self._armed = True

    def check(self, usage: tuple[int, int] | None) -> bool:
        """Return True exactly once per crossing of the fire level (the class invariant)."""
        if usage is None:
            return False
        current, limit = usage
        fraction = current / limit
        if not self._armed:
            if fraction < self._rearm:
                self._armed = True
            return False
        if fraction >= self._fire:
            self._armed = False
            return True
        return False
