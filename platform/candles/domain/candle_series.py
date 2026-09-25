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
"""One instrument's derived bar series: the watermark that makes a second count exactly once."""

from collections.abc import Iterable

from kernel.second_snapshot import SecondRow

from candles.domain.fold import fold_rows


class CandleSeries:
    """
    One instrument's bars, all widths, guarded by a single `ts_event` watermark.

    Three invariants:

    * **Exactly once.** A second at or below `through_ns` was already folded in, so `accept` drops
      it. The commands that would violate it are a re-delivered flush and a startup catch-up that
      overlaps what the last run applied -- both routine. It matters because the store's `_UPSERT`
      *accumulates* `v` and `seconds_observed`: a second applied twice inflates the bucket's volume
      and can push a genuinely partial bucket over the `partial` threshold, and nothing downstream
      could tell. The watermark is the only thing preventing that.
    * **Never ahead of the archive.** `accept` is called only with rows whose
      `ParquetDataCatalog.write_data` already succeeded (capture's `SecondSink` port), so a bar can
      never contain a second the archive does not hold.
    * **Rebuildable from seconds.** The watermark only ever moves forward on `accept`, so a hole the
      live path left is never "sealed": `application.rebuild` recomputes whole UTC days from the
      archive and is idempotent.

    A hole filled into the archive *behind* the watermark is deliberately not re-applied here (that
    would double-count the seconds around it); `python -m candles.rebuild --day D` is its repair.

    Coverage: every accepted second counts toward its bucket's `seconds_observed`, traded or not, so
    `domain.candle.is_partial` can mark a bucket the collector only partly saw.
    """

    def __init__(self, instrument_id: str, through_ns: int = -1) -> None:
        self._instrument_id = instrument_id
        self._through_ns = through_ns

    @property
    def instrument_id(self) -> str:
        return self._instrument_id

    @property
    def through_ns(self) -> int:
        """`ts_event` (ns) of the newest second folded in; -1 when nothing has been."""
        return self._through_ns

    def accept(self, rows: Iterable[SecondRow]) -> tuple[list[SecondRow], int]:
        """
        Return `(the seconds not yet folded in, oldest first; the new watermark)`.

        Advances this series' own watermark, so a second `accept` over the same rows returns
        `([], unchanged)`. `rows` may arrive in any order and may overlap what was applied before.
        """
        fresh = sorted((r for r in rows if r.ts_event > self._through_ns), key=lambda r: r.ts_event)
        if not fresh:
            return [], self._through_ns
        self._through_ns = fresh[-1].ts_event
        return fresh, self._through_ns

    def buckets(self, rows: Iterable[SecondRow]) -> dict[tuple[int, int], list]:
        """
        Fold accepted seconds into `(bar_seconds, bucket_start_ms) -> [o, h, l, c, v, observed]`.

        The one aggregation (`domain.fold.fold_arrays`), over exactly the rows given.

        It does not consult the watermark: exactly-once is `accept`'s job, so a caller folding a
        batch for the store passes `accept`'s output, never its own rows. Folding unjudged rows is
        legitimate for a read (the forming bar re-folds the current bucket every tick).
        """
        return fold_rows(rows)
