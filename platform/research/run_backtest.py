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
OFIStrategy backtest on 1s snapshots. Run from platform/:

    python -m research.run_backtest --start 2026-09-05 --end 2026-09-06
        [--symbol ETH-USD-PERP.DYDX] [--threshold 2.0]
"""

import argparse

from research.strategies.backtest_ofi import run


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTC-USD-PERP.DYDX")
    p.add_argument("--start", required=True, help="bounded window, e.g. 2026-09-05 (MEM-01)")
    p.add_argument("--end", required=True)
    p.add_argument("--threshold", type=float, default=2.0, help="OFI z-score entry threshold")
    p.add_argument("--warmup", type=int, default=600, help="seconds of data before trading")
    a = p.parse_args(argv)

    result = run(a.symbol, a.start, a.end, ofi_threshold=a.threshold, warmup_seconds=a.warmup)
    print(f"Events: {result.iterations}")
    print(f"PnL: {result.stats_pnls}")
    print(f"Returns: {result.stats_returns}")


if __name__ == "__main__":
    main()
