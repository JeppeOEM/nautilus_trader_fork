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
The research domain layer (Story 27.1, DDD spine AD-D1 research row, AD-D2): typed analysis results.

Value objects for the numbers every notebook and backtest report shows -- `ReturnSeries`,
`EquityCurve` (+ `DrawdownEpisode`), `TradeLedger` (+ `ClosedTrade`), `MetricReport`,
`AlignedReturns`/`CorrelationMatrix` -- each constructed only in a state that satisfies the invariant
its docstring names, so a Sharpe ratio or a drawdown is computed one way everywhere.

Statistics are never reimplemented here: `MetricReport` and `ReturnSeries.rolling_sharpe` call
`kernel.performance_metrics` (Nautilus's `PortfolioStatistic`s, SSOT-02). What is computed here is
the arithmetic those statistics do not cover (compounding, drawdown episodes, pairwise-complete
correlation, lead-lag, single-linkage clustering).

Layering (AD-D2): imports only the standard library, numpy (pure array arithmetic, no I/O --
admitted for `research/domain` by the spine and `platform/tests/test_boundaries.py`), `kernel` and
`nautilus_trader.model`/`core`. No pandas, no catalog, no file or network I/O: DataFrames and
readers live in `research.application`.
"""
