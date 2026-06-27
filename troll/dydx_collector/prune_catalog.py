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
Prune old Parquet files from specific catalog data types.

Parquet filenames in the catalog encode their time range:
  <start_ts>_<end_ts>.parquet

This script deletes files whose end timestamp is older than `--days` days.
Run as a daily cron job to enforce rolling retention on expensive data types.

Usage:
    python prune_catalog.py --catalog troll/dydx_collector/catalog \\
                            --types order_book_deltas \\
                            --days 14 \\
                            [--dry-run]
"""

import argparse
import re
import time
from pathlib import Path


# Timestamp is nanoseconds since epoch encoded in the filename.
_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T[\d\-]+Z)_(\d{4}-\d{2}-\d{2}T[\d\-]+Z)\.parquet$")


def _filename_end_ns(path: Path) -> int | None:
    """Parse the end timestamp from a catalog Parquet filename, return nanoseconds."""
    m = _TS_RE.search(path.name)
    if not m:
        return None
    # Convert ISO-ish filename format back to a comparable timestamp via string sort
    # (catalog filenames sort lexicographically = chronologically, so comparing
    # the string directly to a formatted cutoff is sufficient).
    return m.group(2)  # type: ignore[return-value]  # returns str, compared below


def prune(catalog_path: str, data_types: list[str], retain_days: int, dry_run: bool) -> None:
    cutoff_ns = time.time_ns() - retain_days * 86_400 * 1_000_000_000
    # Format cutoff as the same ISO-like string the catalog uses in filenames.
    import datetime
    cutoff_str = (
        datetime.datetime.fromtimestamp(cutoff_ns / 1e9, tz=datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H-%M-%S")
    )

    root = Path(catalog_path) / "data"
    total_freed = 0

    for data_type in data_types:
        type_dir = root / data_type
        if not type_dir.exists():
            print(f"  {data_type}: directory not found, skipping")
            continue

        for parquet_file in sorted(type_dir.rglob("*.parquet")):
            end_str = _filename_end_ns(parquet_file)
            if end_str is None:
                continue
            if end_str < cutoff_str:
                size = parquet_file.stat().st_size
                total_freed += size
                if dry_run:
                    print(f"  [dry-run] would delete {parquet_file} ({size // 1024} KB)")
                else:
                    parquet_file.unlink()
                    print(f"  deleted {parquet_file} ({size // 1024} KB)")

    action = "would free" if dry_run else "freed"
    print(f"\n{action} {total_freed / 1024 / 1024:.1f} MB")


def prune_instrument(catalog_path: str, instrument_id: str, retain_hours: float) -> int:
    """
    Delete all Parquet files for `instrument_id` whose end timestamp is older than
    `retain_hours` hours ago, across every data type directory in the catalog.

    Returns the total bytes freed.
    """
    cutoff_ns = time.time_ns() - int(retain_hours * 3_600 * 1_000_000_000)
    import datetime
    cutoff_str = (
        datetime.datetime.fromtimestamp(cutoff_ns / 1e9, tz=datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H-%M-%S")
    )
    freed = 0
    data_root = Path(catalog_path) / "data"
    if not data_root.exists():
        return 0
    for type_dir in data_root.iterdir():
        iid_dir = type_dir / instrument_id
        if not iid_dir.is_dir():
            continue
        for f in sorted(iid_dir.glob("*.parquet")):
            end_str = _filename_end_ns(f)
            if end_str and end_str < cutoff_str:
                freed += f.stat().st_size
                f.unlink()
    return freed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="troll/dydx_collector/catalog")
    parser.add_argument("--types", nargs="+", default=["order_book_deltas"])
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Pruning {args.types} older than {args.days} days from {args.catalog}")
    if args.dry_run:
        print("(dry run — nothing will be deleted)")
    prune(args.catalog, args.types, args.days, args.dry_run)
