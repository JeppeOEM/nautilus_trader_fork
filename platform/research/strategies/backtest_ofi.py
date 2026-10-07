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

With `forced_flow_filter` or a `liquidation_cascade_mode` other than `"off"` (Story 33.13) the run
also needs the archived liquidations, which `snapshot_backtest` does not stream: it goes through
the `BacktestRunner` port instead (`NodeRunner`, `RunSpec(data="seconds_liquidations")`: the
derived quotes, the snapshots and the `custom_liquidation` rows) and returns its `RunResult`. Both
options need a Bybit LINEAR `symbol` (the only liquidation feed; anything else is refused).

That path runs other execution models than the default one: `NodeRunner` puts the spec's fixed
300 ms order latency (`DEFAULT_LATENCY_MS`) on the venue, `snapshot_backtest` none, so a
forced-flow run and a default `run()` differ by more than the option. Compare the variants with
each other on one path -- `research.application.gallery.ofi_specs` runs the baseline and the three
variants through `NodeRunner` alike (notebook 08) -- never against this module's default run.

    python -m research.strategies.backtest_ofi   # from platform/
"""

from decimal import Decimal

from nautilus_trader.backtest.results import BacktestResult
from research.application.backtest_runner import NodeRunner
from research.application.ports import RunResult
from research.application.ports import RunSpec
from research.strategies.cascade_rules import MODE_OFF
from research.strategies.catalog_location import default_catalog_path
from research.strategies.snapshot_backtest import run as run_snapshot_backtest


_S = "research.strategies.ofi_strategy:"


def run(
    symbol: str = "BTC-USD-PERP.DYDX",
    start: str = "2026-09-05",
    end: str = "2026-09-06",
    catalog_path: str | None = None,
    **params: object,
) -> BacktestResult | RunResult:
    """
    `params` are OFIStrategyConfig fields, e.g. ofi_threshold=2.0, trade_size=Decimal("0.01").
    A `BacktestResult` (`snapshot_backtest`), or the `RunResult` of a `seconds_liquidations` run
    when either forced-flow option is on (the module docstring). `forced_flow_filter` must be a
    real `bool` (`TypeError` otherwise: the string `"false"` is truthy and would route the run).
    """
    flag = params.get("forced_flow_filter", False)
    if not isinstance(flag, bool):
        raise TypeError(f"forced_flow_filter must be a bool, got {flag!r}")
    params.setdefault("trade_size", Decimal("0.01"))
    if catalog_path is None:
        catalog_path = default_catalog_path()
    if flag or params.get("liquidation_cascade_mode", MODE_OFF) != MODE_OFF:
        spec = RunSpec(
            catalog_path=catalog_path,
            instrument_ids=(symbol,),
            start=start,
            end=end,
            strategy_path=_S + "OFIStrategy",
            config_path=_S + "OFIStrategyConfig",
            params={**params, "trade_size": str(params["trade_size"])},
            data="seconds_liquidations",
        )
        return NodeRunner().run(spec)
    return run_snapshot_backtest(
        catalog_path, symbol, start, end, _S + "OFIStrategy", _S + "OFIStrategyConfig", params
    )


def snapshot_result(result: BacktestResult | RunResult) -> BacktestResult:
    """
    Return `result` as the default path's `BacktestResult`; `TypeError` for a `RunResult` (a
    forced-flow run), which a caller reading `stats_pnls` cannot take.
    """
    if not isinstance(result, BacktestResult):
        raise TypeError(
            f"expected the snapshot runner's BacktestResult, got {type(result).__name__}"
        )
    return result


if __name__ == "__main__":
    result = snapshot_result(run(ofi_threshold=2.0, warmup_seconds=600))
    print(f"Events processed: {result.iterations:,}")
    print(f"PnL: {result.stats_pnls}")
    print(f"Returns: {result.stats_returns}")
