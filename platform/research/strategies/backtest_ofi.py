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
Backtest OFIStrategy on locally collected dYdX 1s snapshots. See snapshot_backtest.py.

    python -m research.strategies.backtest_ofi   # from platform/
"""

from decimal import Decimal

from nautilus_trader.backtest.results import BacktestResult
from research.strategies.catalog_location import default_catalog_path
from research.strategies.snapshot_backtest import run as run_snapshot_backtest


_S = "research.strategies.ofi_strategy:"


def run(
    symbol: str = "BTC-USD-PERP.DYDX",
    start: str = "2026-09-05",
    end: str = "2026-09-06",
    catalog_path: str | None = None,
    **params: object,
) -> BacktestResult:
    """`params` are OFIStrategyConfig fields, e.g. ofi_threshold=2.0, trade_size=Decimal("0.01")."""
    params.setdefault("trade_size", Decimal("0.01"))
    if catalog_path is None:
        catalog_path = default_catalog_path()
    return run_snapshot_backtest(
        catalog_path, symbol, start, end, _S + "OFIStrategy", _S + "OFIStrategyConfig", params
    )


if __name__ == "__main__":
    result = run(ofi_threshold=2.0, warmup_seconds=600)
    print(f"Events processed: {result.iterations:,}")
    print(f"PnL: {result.stats_pnls}")
    print(f"Returns: {result.stats_returns}")
