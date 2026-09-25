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
The research context (DDD spine AD-D1 research row, Story 24.4): backtest strategies, their
runners, the watchlist client and the notebooks.

A pure consumer with no aggregates. It reads market-data rows only through the kernel's catalog
helpers (`kernel.catalog_files`) or Nautilus's streaming `BacktestDataConfig`, and the live
coin-set only over HTTP (`research.watchlist` -> data_api's `/api/rankings`). It computes no
rolling metric of its own: pct-change and volatility are ranking's, published through
`metrics.db` and the rankings API, never recomputed here. The one instrument-definition lookup
(`ParquetDataCatalog.instruments`) is metadata, and `strategies.snapshot_backtest` writes only a
throwaway catalog of derived quotes in a temporary directory.

Strategies are referenced by `ImportableStrategyConfig` string path
(`research.strategies.<module>:<Class>`, NAUT-03), so a sweep or a time-range change needs no
code change.

Dependency direction: in-repo, `research` imports only `kernel` and `observability` (beside
stdlib, `nautilus_trader` and pandas), never `views`, `data_api`, `ranking_engine` or
`ml_signals`; `platform/tests/test_boundaries.py` enforces the edges and
`research/tests/test_research_reads.py` the reads.
"""
