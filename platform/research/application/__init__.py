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
The research application layer (Story 27.1, DDD spine AD-D2): the ports a notebook uses and their
implementations.

`ports.py` declares `MarketFrames`, `RankingHistory` and `BacktestRunner` as `typing.Protocol`s
plus the `RunSpec`/`RunResult` values; `frames.CatalogFrames` reads time-bounded frames from the
catalog and the candle store (`candles.application.queries`, the one research -> candles edge),
`ranking_history.HttpRankingHistory` reads ranking's published history over data_api's HTTP API,
`backtest_runner.NodeRunner` runs `BacktestNode` sweeps, and `quotes.derived_quotes` seeds a
snapshot backtest's simulated market. pandas lives here, never in
`research.domain`; analysis arithmetic lives in `research.domain`, never here.
"""
