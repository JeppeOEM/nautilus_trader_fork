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
Backtest `LiquidationCascadeStrategy` on the archive (Story 33.14): one bounded window through the
`BacktestRunner` port (`NodeRunner`: `BacktestNode` + `BacktestDataConfig`, the strategy by string
path, NAUT-03) with `RunSpec(data="liquidations")` -- quotes derived from the 1 s snapshots' top of
book (the market the simulated exchange fills against) plus the archived `custom_liquidation`
rows. Bybit LINEAR ids only (`kernel.liquidation.has_liquidation_feed`).

    python -m research.strategies.backtest_liquidation_cascade   # from platform/
"""

from research.application.backtest_runner import NodeRunner
from research.application.ports import RunResult
from research.application.ports import RunSpec
from research.strategies.catalog_location import default_catalog_path
from research.strategies.liquidation_cascade_strategy import DEFAULT_STOP_PCT


_S = "research.strategies.liquidation_cascade_strategy:"


def run(
    symbol: str = "BTCUSDT-LINEAR.BYBIT",
    start: str | int = "2026-10-05",
    end: str | int = "2026-10-06",
    catalog_path: str | None = None,
    **params: object,
) -> RunResult:
    """
    `params` are `LiquidationCascadeStrategyConfig` fields, e.g. `mode="fade"`,
    `sides=["long"]`, `stop_pct=0.02`, `trade_size="0.001"`; an unknown one fails the build.

    The config requires exactly one stop field. When `params` names neither `stop_pct` nor
    `stop_atr_multiple`, this wrapper adds `stop_pct=DEFAULT_STOP_PCT` (the strategy module's one
    default, 1 %, the gallery's too) -- the only parameter it injects.
    """
    if "stop_pct" not in params and "stop_atr_multiple" not in params:
        params["stop_pct"] = DEFAULT_STOP_PCT
    if catalog_path is None:
        catalog_path = default_catalog_path()
    spec = RunSpec(
        catalog_path=catalog_path,
        instrument_ids=(symbol,),
        start=start,
        end=end,
        strategy_path=_S + "LiquidationCascadeStrategy",
        config_path=_S + "LiquidationCascadeStrategyConfig",
        params=params,
        data="liquidations",
    )
    return NodeRunner().run(spec)


if __name__ == "__main__":
    result = run()
    print(f"Events processed: {result.iterations:,}; closed trades: {len(result.trades)}")
    for name, value in result.metrics.as_table():
        print(f"{name:>24}: {value}")
