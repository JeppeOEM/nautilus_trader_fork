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
r"""
Backfill historical venue klines (`Bar`) for Bybit and Hyperliquid into the Parquet catalog.

Usage:
    python -m archive.backfill_bars --catalog /app/catalog \\
        --instrument BTCUSDT-LINEAR.BYBIT [--instrument ETHUSDT-LINEAR.BYBIT ...] \\
        --start 2026-09-01 --end 2026-09-18 \\
        [--bar-spec 1-MINUTE-LAST] [--environment mainnet] [--apply]

**Report-only unless `--apply`**. What is written, the timestamp convention, coverage and every
Known limit are `archive.application.backfill_bars`'s docstring. The one offline `write_data()`
caller (`repair_catalog` rewrites through `CatalogFiles` since DW-204), listed in
`docs/DATA_DICTIONARY.md` section 6.

Known limit: the fetch keeps the PyO3 adapters' f64 kline path (D-52) instead of the
`VenueKlines` ACL `compare_klines` uses -- moving it would change the written `Bar` values.
Upgrade path: an exact-Decimal bar backfill over `archive.infrastructure.klines_*`.
"""

import argparse
import asyncio
import logging

from archive.application.backfill_bars import run
from archive.application.catalog_check import catalog_missing


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True, help="ParquetDataCatalog root")
    parser.add_argument(
        "--instrument", action="append", required=True, help="repeatable; full Nautilus id"
    )
    parser.add_argument("--start", required=True, help="YYYY-MM-DD (UTC, inclusive day)")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD (UTC, inclusive day)")
    parser.add_argument("--bar-spec", default="1-MINUTE-LAST", help="default: 1-MINUTE-LAST")
    parser.add_argument(
        "--environment",
        default="mainnet",
        choices=("mainnet", "testnet", "demo"),
        help="which venue endpoint to query; it cannot verify the catalog's own environment",
    )
    parser.add_argument("--apply", action="store_true", help="actually fetch and write")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the exit code (1 when any instrument failed or no catalog)."""
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if catalog_missing("backfill_bars", args.catalog):
        return 1  # never write a fresh catalog where a mount was meant to be
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
