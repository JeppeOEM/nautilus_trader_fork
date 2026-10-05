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
Backtest `CandlePatternStrategy` on the collectors' raw trade archive (Story 27.8): one bounded
window through the `BacktestRunner` port (`NodeRunner`: `BacktestNode` + `BacktestDataConfig` of
`TradeTick`, the strategy by string path, NAUT-03), bars aggregated by Nautilus from the trades
(`1-MINUTE-LAST-INTERNAL` unless `bar_type` says otherwise).

    python -m research.strategies.backtest_candle_pattern   # from platform/
"""

from research.application.backtest_runner import NodeRunner
from research.application.ports import RunResult
from research.application.ports import RunSpec
from research.strategies.catalog_location import default_catalog_path


_S = "research.strategies.candle_pattern_strategy:"


def run(
    symbol: str = "BTC-USD-PERP.DYDX",
    start: str = "2026-09-05",
    end: str = "2026-09-06",
    catalog_path: str | None = None,
    **params: object,
) -> RunResult:
    """
    `params` are `CandlePatternStrategyConfig` fields, e.g. `long_patterns=["HAMMER"]`,
    `trend_condition="any"`, `trade_size="0.01"`; an unknown one fails the build.
    """
    params.setdefault("trade_size", "0.01")
    if catalog_path is None:
        catalog_path = default_catalog_path()
    spec = RunSpec(
        catalog_path=catalog_path,
        instrument_ids=(symbol,),
        start=start,
        end=end,
        strategy_path=_S + "CandlePatternStrategy",
        config_path=_S + "CandlePatternStrategyConfig",
        params=params,
        data="trades",
    )
    return NodeRunner().run(spec)


if __name__ == "__main__":
    result = run(long_patterns=["HAMMER", "ENGULFING"], trend_condition="above")
    print(f"Events processed: {result.iterations:,}; closed trades: {len(result.trades)}")
    for name, value in result.metrics.as_table():
        print(f"{name:>24}: {value}")
