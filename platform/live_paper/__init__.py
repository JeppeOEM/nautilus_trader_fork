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
Deprecated package (Story 25.3): `live_paper` became the bots context, `platform/bots/`
(`python3 -m bots`). Its old modules map to:

- `live_paper.config` -> `bots.domain.config` (the value objects, re-exported by the shim) and
  `bots.infrastructure.config` (the loaders, which now return the `PaperFleet`/`ExecBot`
  aggregates -- a changed shape, so they are not re-exported).
- `live_paper.strategy` -> `bots.strategies.dummy` (re-exported by the shim).
- `live_paper.node` -> `bots.__main__.main` (re-exported by the shim) and
  `bots.infrastructure.nautilus_host.build_node` (changed shape: it returns the hosted bots and
  schedules no task).
- `live_paper.venues` -> `bots.infrastructure.nautilus_host.VENUES` (client factories) and
  `bots.domain.config.VENUE_RULES` (the pure venue facts).
- `live_paper.bot_status` -> `bots.application.supervise` (`Supervisor`, `build_status`) and
  `bots.domain.bot` (`Bot`, the incident log).
- `live_paper.trade_history` -> `bots.application.history` (`HistoryPublisher`) and
  `bots.domain.fill_ledger` (`FillLedger`, the history windows).
- `live_paper.fills_store` -> `bots.infrastructure.fills_store.SqliteFillsStore`.

The package itself defines nothing; its shim modules are removed after Story 26.1.
"""
