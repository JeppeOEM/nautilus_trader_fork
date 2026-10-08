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
The `DerivsHistory` adapter (Story 33.4): one instrument's archived open interest and liquidations,
through the kernel's column-projected readers `query_open_interest`/`query_liquidations` -- the
same reads the candle fold and the derivatives read model use, never a second Parquet decoder.
"""

from kernel.catalog_files import query_liquidations
from kernel.catalog_files import query_open_interest
from kernel.liquidation import Liquidation
from kernel.open_interest import OpenInterest
from observability import error_ledger


class CatalogDerivsHistory:
    """
    `DerivsHistory` over the Parquet catalog (read-only).

    Invariant: rows come back exactly as the kernel readers return them -- deduplicated, sorted,
    and a disagreeing duplicate raised, never resolved here.
    """

    def __init__(self, catalog_path: str) -> None:
        self._catalog_path = catalog_path

    def open_interest(self, instrument_id: str, start_ns: int, end_ns: int) -> list[OpenInterest]:
        return query_open_interest(
            self._catalog_path, instrument_id, start_ns, end_ns, on_foreign=error_ledger.record
        )

    def liquidations(self, instrument_id: str, start_ns: int, end_ns: int) -> list[Liquidation]:
        return query_liquidations(
            self._catalog_path, instrument_id, start_ns, end_ns, on_foreign=error_ledger.record
        )
