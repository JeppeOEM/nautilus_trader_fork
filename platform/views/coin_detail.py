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
The single-coin detail read model's data reads (SSOT-05): the inputs the web coin page reads for
one coin.

- `metrics_history`/`metrics_nearest` -- the ranking context's `metrics.db` history reads
  (its query service `ranking.application.queries`), and `catalog_snapshot_rows` -- the archived seconds of one
  coin as the kernel's stored/wire dicts (`DydxSecondSnapshot.to_dict`: exact integer units, the
  two precisions, gap-encoded book prices; Story 30.2), never re-encoded as floats.

Moved out of `data_api/routes/metrics.py` and `data_api/app.py` (Story 24.2). Story 25.1a deleted
the metric groups (`COIN_DETAIL_GROUPS`), the rank-row lookup and the `snapshots:raw` decode that
came from bot_tui's Coin-detail view: that view was their only reader, and it went web-only.
"""

from kernel.second_snapshot import DydxSecondSnapshot
from ranking.application.queries import HISTORY_MAX_DAYS
from ranking.application.queries import history
from ranking.application.queries import nearest

from views.catalog_reads import query_second_snapshots


# The `days` ceiling `metrics_history` serves: metrics.db's own retention, re-exposed here because
# the interfaces read ranking only through views (AD-D2), so data_api bounds its query by this.
METRICS_HISTORY_MAX_DAYS = HISTORY_MAX_DAYS


def metrics_history(symbol: str, db_path: str, days: int = METRICS_HISTORY_MAX_DAYS) -> list[dict]:
    """One coin's `metrics.db` rows over the last `days` days, oldest first (ranking's own read)."""
    return history(symbol, db_path, days)


def metrics_nearest(symbol: str, ts_ns: int, db_path: str) -> dict | None:
    """Return the `metrics.db` row of one coin closest to `ts_ns`; None if never stored."""
    return nearest(symbol, ts_ns, db_path)


def catalog_snapshot_rows(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[dict]:
    """
    Return the archived seconds of one coin in `[start_ns, end_ns]` as the kernel's wire dicts
    (integers and precisions; `docs/DATA_DICTIONARY.md` §1 says how to decode them by hand).
    """
    return [
        DydxSecondSnapshot.to_dict(s)
        for s in query_second_snapshots(catalog_path, instrument_id, start_ns, end_ns)
    ]
