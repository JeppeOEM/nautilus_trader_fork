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
Ranking's query service (DDD spine AD-D11): the `metrics.db` reads other contexts make. `views`
calls exactly these (`platform/tests/test_boundaries.py`'s `VIEWS_QUERY_SERVICES`); the ranking
metrics themselves (pct-change, volatility) are read from here or `rankings:live`, never recomputed.
"""

from ranking.infrastructure.metrics_store import RETAIN_DAYS
from ranking.infrastructure.metrics_store import read_history
from ranking.infrastructure.metrics_store import read_nearest


# The widest `days` window `history` can answer truthfully: metrics.db prunes older rows on every
# write, so a caller asking further back would get a silently shortened answer.
HISTORY_MAX_DAYS = RETAIN_DAYS


def history(instrument_id: str, db_path: str, days: int = HISTORY_MAX_DAYS) -> list[dict]:
    """Return one instrument's `metrics.db` rows over the last `days` days, ordered by ts."""
    return read_history(db_path, instrument_id, days)


def nearest(instrument_id: str, ts: int, db_path: str) -> dict | None:
    """Return the `metrics.db` row of one instrument closest to `ts` (ns); None if never stored."""
    return read_nearest(db_path, instrument_id, ts)
