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
Update the pinned instrument list in config.toml.

Takes the top 40 instruments by 24h volume, merges with any already-pinned
instruments, removes excluded instruments, and rewrites the [[instruments]]
section. The collector hot-reloads config every 30s, so no restart needed.

Usage (from troll/ directory):
    python3 -m dydx_collector.update_pinned [--top N] [--dry-run]
"""

import argparse
import re
import sys
from pathlib import Path

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork

from dydx_collector.config import load_config
from dydx_collector.open_interest import _fetch_markets_json

CONFIG_PATH = Path(__file__).parent.parent / "config.toml"
TOP_N = 40


def top_by_volume(markets_json: dict, n: int, exclude: frozenset[str]) -> list[str]:
    rows = []
    for m in markets_json.get("markets", {}).values():
        ticker = m.get("ticker")
        if not ticker:
            continue
        iid = f"{ticker}-PERP.DYDX"
        if iid in exclude:
            continue
        try:
            vol = float(m.get("volume24H") or 0)
        except (ValueError, TypeError):
            vol = 0.0
        rows.append((iid, vol))
    rows.sort(key=lambda x: -x[1])
    return [iid for iid, _ in rows[:n]]


def rewrite_instruments(config_path: Path, new_pinned: list[str]) -> None:
    text = config_path.read_text()
    # Remove all existing [[instruments]] blocks
    text = re.sub(r"\n*\[\[instruments\]\][^\[]*", "", text, flags=re.DOTALL)
    text = text.rstrip() + "\n"
    # Append new blocks
    for iid in new_pinned:
        text += f'\n[[instruments]]\nid = "{iid}"\n'
    config_path.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Update pinned instruments to top N by 24h volume")
    parser.add_argument("--top", type=int, default=TOP_N, help=f"Number of top instruments (default {TOP_N})")
    parser.add_argument("--dry-run", action="store_true", help="Print new list without writing")
    args = parser.parse_args()

    config = load_config(CONFIG_PATH)
    current_pinned = {e.id for e in config.instruments}
    exclude = config.exclude

    print("Fetching market data from dYdX...")
    markets_json = _fetch_markets_json(config.network)

    top = top_by_volume(markets_json, args.top, exclude)

    # New pinned = top N ∪ existing pinned (existing stay even if below threshold)
    merged = list(dict.fromkeys(top + sorted(current_pinned - set(top))))

    print(f"\nNew pinned list ({len(merged)} instruments):")
    for i, iid in enumerate(merged, 1):
        tag = " [existing]" if iid in current_pinned and iid not in top else ""
        tag = tag or (" [new]" if iid not in current_pinned else "")
        print(f"  {i:>3}. {iid}{tag}")

    added = set(merged) - current_pinned
    removed_from_top = current_pinned - set(top)
    print(f"\n  Added:   {len(added)} new coins")
    print(f"  Kept:    {len(removed_from_top)} existing coins no longer in top {args.top}")

    if args.dry_run:
        print("\n[dry-run] No changes written.")
        return

    rewrite_instruments(CONFIG_PATH, merged)
    print(f"\nWrote {len(merged)} [[instruments]] entries to {CONFIG_PATH}")
    print("Collector will pick up changes within 30s (config_reload_seconds).")


if __name__ == "__main__":
    main()
