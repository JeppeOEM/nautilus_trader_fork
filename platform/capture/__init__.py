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
The market-data capture context (DDD spine AD-D1/AD-D6; Story 22.1's collector core, moved here
from `collector_core/` and the three `<venue>_collector/` packages in Story 26.2): one
ingest/flush/sample/write path for every venue.

- `domain/`: the gate's aggregates (`LiveBook`, `TradeIntake`, `FeedGroup`), the pure
  `SecondSampler`, verdicts, events and the core-default policies; no I/O.
- `application/`: the ports, the ledger sites, the thresholds (`CoreConfig`) and the one
  `CaptureService` that runs the loops.
- `infrastructure/`: the adapters (Parquet archive, Redis live stream, capture lock, archive-gap
  markers) and the one venue `config.toml` loader; imported only by the composition roots.
- `venues/<v>/`: a venue is its client, trade history, policies, optional open-interest poll or
  book snapshot, config and `__main__.py` composition root (`python3 -m capture.venues.<v>`).
"""
