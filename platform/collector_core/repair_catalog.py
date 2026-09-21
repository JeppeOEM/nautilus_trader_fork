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
Find and repair second snapshots whose trade OHLC is impossible given their own book.

Usage:
    python -m collector_core.repair_catalog --catalog /app/catalog [--instrument X ...]      # report only
    python -m collector_core.repair_catalog --catalog /app/catalog --apply                   # rewrite

Detection is `integrity.ohlc_outside_book` -- see there for why it is sound. It finds the
rows written by the pre-fix collector, which counted dYdX's subscribed-reply trade history
as live trades (before the `stale_trade_seconds` filter in `collector_core/config.py`).

--apply, per flagged second: replace the snapshot with a copy whose trade fields are
cleared (OHLC None, volumes/counts 0 -- the real trades in that second cannot be told
apart from the replayed ones, so "no trade recorded" is the honest value, DATA-01), delete
and (with --candles-db) rebuild the candle-store days it touched from the corrected raw 1s.
Without --candles-db the store still holds the spike: run `build_candles` for those days. The book fields
are untouched. Manually run, like prune_catalog.py; reads raw 1s in day chunks (MEM-01).

Never run it on a day `rebuild_seconds` has rebuilt (story 22.13): a rebuilt second holds the
trades whose *exchange* time falls in it, while its book is still sampled at mid-second on
arrival time, so a fast market can put a real trade outside that book range -- this tool would
then clear real trades. The rebuild itself is the repair for rows written after the trade
archive existed; this tool is for pre-archive rows only.
"""

import argparse
import logging

from ml_signals.catalog_stats import query_second_snapshots

from collector_core.build_candles import all_instruments
from collector_core.build_candles import data_range_ns
from collector_core.build_candles import day_chunks
from collector_core.build_candles import rebuild_instrument
from collector_core.integrity import ohlc_outside_book
from collector_core.second_snapshot import DydxSecondSnapshot
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)


def find_impossible_snapshots(
    catalog_path: str, iid: str, start_ns: int, end_ns: int
) -> list[DydxSecondSnapshot]:
    return [
        snap
        for a, b in day_chunks(start_ns, end_ns)
        for snap in query_second_snapshots(catalog_path, iid, a, min(b, end_ns))
        if ohlc_outside_book(snap)
    ]


def _cleared_copy(snap: DydxSecondSnapshot) -> DydxSecondSnapshot:
    values = DydxSecondSnapshot.to_dict(snap)
    values.update(
        buy_volume=0.0,
        sell_volume=0.0,
        buy_count=0,
        sell_count=0,
        open_price=None,
        high_price=None,
        low_price=None,
        close_price=None,
    )
    return DydxSecondSnapshot.from_dict(values)


def repair_instrument(
    catalog: ParquetDataCatalog,
    catalog_path: str,
    iid: str,
    flagged: list[DydxSecondSnapshot],
    candles_db_path: str | None = None,
) -> None:
    # Build every replacement first so a bad row fails before anything is deleted.
    replacements = [(snap, _cleared_copy(snap)) for snap in flagged]
    for snap, cleared in replacements:
        catalog.delete_data_range(DydxSecondSnapshot, iid, snap.ts_event, snap.ts_event)
        catalog.write_data([cleared])
    if candles_db_path is not None:
        first, last = min(s.ts_event for s in flagged), max(s.ts_event for s in flagged)
        rebuild_instrument(candles_db_path, catalog_path, iid, first, last)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--instrument", action="append", help="repeatable; default: all")
    parser.add_argument(
        "--apply", action="store_true", help="rewrite the catalog (default: report only)"
    )
    parser.add_argument(
        "--candles-db", help="candles.db to rebuild the repaired days in (closed days only)"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    catalog = ParquetDataCatalog(args.catalog)
    for iid in args.instrument or all_instruments(args.catalog):
        span = data_range_ns(args.catalog, iid)
        if span is None:
            continue
        flagged = find_impossible_snapshots(args.catalog, iid, *span)
        if not flagged:
            continue
        logger.info("%s: %d impossible snapshot(s)", iid, len(flagged))
        for snap in flagged:
            logger.info(
                "  ts=%d high=%s low=%s vol=%.4f",
                snap.ts_event,
                snap.high_price,
                snap.low_price,
                snap.buy_volume + snap.sell_volume,
            )
        if args.apply:
            repair_instrument(catalog, args.catalog, iid, flagged, args.candles_db)
            logger.info("  repaired")


if __name__ == "__main__":
    main()
