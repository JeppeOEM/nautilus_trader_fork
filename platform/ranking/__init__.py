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
The ranking context (DDD spine AD-D10, Story 25.2): the sole computer and publisher of the coin
ranking (architecture AD-9).

- `domain/` -- `RankingBoard` (the mode, one `InstrumentMetrics` per instrument, the volume book and
  the `RankingsPublisher`) and ranking's own math: the pct-change/volatility formula
  (`metrics.price_stats_from_series`), the long-window price series and the cross-sectional
  volatility tracker. No I/O, no clock, no ledger.
- `application/` -- the ports (`VolumeSource`, `PriceHistory`, `RankingHistory`, `LivePublisher`),
  `RankingEngine` (the four loops) and `queries` (`history`/`nearest`, the read service `views`
  calls for `metrics.db`).
- `infrastructure/` -- Redis, the `metrics.db` store, the catalog price read and one volume source
  per venue, all constructed by `__main__` (`python3 -m ranking`).

No module here holds mutable runtime state (`platform/tests/test_boundaries.py`): every piece of
state lives on the board or the engine instance that `__main__` builds.
"""
