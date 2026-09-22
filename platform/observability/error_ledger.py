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
One place where "our code malfunctioned and we carried on" is made impossible to miss (DATA-07).

Every site that must continue past a failure (a dropped message, a skipped coin, an unreadable
file) calls `record()` instead of a bare log line: it logs at ERROR with the traceback AND counts
the site, so `GET /api/errors` (and the frontend's error bar) can show that something is wrong
without anyone reading Dozzle. Counts are per-process and reset on restart -- they say "this
process has seen N failures at this site", never "the failure went away".

Known limit: the ledger is per process, so `GET /api/errors` shows only `data_api`'s own sites;
every other process's sites are visible in its log only. Upgrade path: an
`observability/infrastructure/redis_ledger.py` publishing an `errors:ledger` channel that
`/api/errors` aggregates (spine AD-D16; story 23.3 makes the ledger durable first). That adapter
is the one sanctioned non-stdlib import inside `observability/`: the story adding it widens
`tests/test_boundaries.py`'s stdlib-only rule to that one subpackage, deliberately, nowhere else.
"""

import logging
import threading
from collections import Counter


logger = logging.getLogger(__name__)

_lock = threading.Lock()
_counts: Counter[str] = Counter()
_last: dict[str, str] = {}


def record(site: str, detail: str = "", exc: BaseException | None = None) -> None:
    """Log at ERROR (with traceback when `exc` is given or an exception is being handled) and count."""
    with _lock:
        _counts[site] += 1
        _last[site] = detail or (repr(exc) if exc else "")
    logger.error("[%s] %s", site, detail, exc_info=exc if exc is not None else True)


def counts() -> dict[str, int]:
    with _lock:
        return dict(_counts)


def last_details() -> dict[str, str]:
    with _lock:
        return dict(_last)


def reset() -> None:
    """Clear every count and detail (tests only)."""
    with _lock:
        _counts.clear()
        _last.clear()
