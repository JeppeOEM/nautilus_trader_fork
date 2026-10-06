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
The candle store's liquidation dedup outlives capture's startup replay (Story 33.3 review loop 2).

Capture re-applies the last `_CATCH_UP_MAX_NS` of archived liquidations at every start and relies on
the store's `liquidations_applied` table to skip those it already took. `candles` prunes that table
past `LIQUIDATIONS_APPLIED_RETAIN_DAYS`; were the retention shorter than the replay window plus a
flush interval, a replayed id would be pruned from the dedup first and counted twice. Neither
context may import the other, so the two constants are held to each other here.
"""

from candles.domain.fold import DAY_MS
from candles.infrastructure.sqlite_store import LIQUIDATIONS_APPLIED_RETAIN_DAYS
from capture.application.capture_service import _CATCH_UP_MAX_NS
from capture.application.config import CoreConfig


def test_the_applied_ids_outlive_the_catch_up_window_plus_a_flush() -> None:
    retained_ns = LIQUIDATIONS_APPLIED_RETAIN_DAYS * DAY_MS * 1_000_000
    flush_ns = CoreConfig(environment="mainnet", catalog_path="/").flush_interval_seconds * 10**9
    assert retained_ns > _CATCH_UP_MAX_NS + flush_ns
