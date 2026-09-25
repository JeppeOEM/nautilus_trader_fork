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
"""Candles' side of capture's `SecondSink` port: the adapter a venue entrypoint injects."""

from collections.abc import Mapping
from collections.abc import Sequence

from kernel.second_snapshot import SecondRow

from candles.infrastructure.sqlite_store import CandleStore


class CandleSink:
    """
    Fold flushed seconds into one venue's candle store.

    Invariant (the store is never ahead of the archive): capture calls `apply` only with rows whose
    `ParquetDataCatalog.write_data` already returned, and drives `watermarks` at startup to backfill
    what a crash between a Parquet flush and its store write left behind. The command that would
    violate it is applying the buffer instead of the flushed batch -- which is why this is a port
    capture hands rows to, not a store capture reaches into.

    Structural, not inherited: this class never imports `collector_core.ports.SecondSink`, it merely
    has its shape, so no `candles` -> capture edge exists (spine AD-D2).

    One transaction per instrument, not one per flush (this replaced `apply_batch`): a crash
    mid-flush now leaves some instruments applied rather than none. Both states are recoverable --
    the watermark is per instrument and `python -m candles.rebuild` is idempotent -- and in exchange
    one instrument's failure no longer discards the whole batch.
    """

    def __init__(self, store: CandleStore) -> None:
        self._store = store

    def apply(self, instrument_id: str, rows: Sequence[SecondRow]) -> int:
        """Fold `rows` in, each second exactly once; returns the number of seconds applied."""
        return self._store.apply(instrument_id, rows)

    def watermarks(self) -> Mapping[str, int]:
        """instrument_id -> `ts_event` (ns) of the last second applied."""
        return self._store.watermarks()
