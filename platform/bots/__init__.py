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
The bots context (DDD spine AD-D15, Story 25.3): the one sanctioned `TradingNode`/`Strategy`
runtime in `platform/` (architecture AD-8's amendment), exposed to the rest of the platform only
through its Redis Open Host Service -- `bots:status`, `bots:control`, `bots:history:*` and
`bots:incidents:*` (AD-10) -- and the `fills.db` store it alone writes.

- `domain/` -- the paper/non-paper split as two aggregate types (`PaperFleet`, `ExecBot`, over the
  `PaperConfig`/`ExecConfig`/`BotConfig` value objects), `Bot` (id == Nautilus `order_id_tag`, the
  bounded incident log, heartbeat state) and `FillLedger` (per-fill realized PnL and the
  day/week/month/all history windows). Stdlib, `kernel` and Nautilus value types only.
- `application/` -- the ports (`BotRuntime`, `FillsStore`, `BusConnection`), `supervise` (status
  heartbeat and `bots:control`) and `history` (fill recording and the history refresh).
- `infrastructure/` -- the Nautilus anti-corruption layer: `nautilus_host` (the only module that
  imports `TradingNode`, asserted by `platform/tests/test_boundaries.py`), `cache_reader` (every
  read strategy-scoped), plus `fills_store`, `redis` and the two config loaders (`config`).
- `strategies/` -- `DummyStrategy`, framework code attached by `nautilus_host`.

`__main__` (`python3 -m bots`, compose service `live-paper`) is the composition root. No module
here holds mutable runtime state: every piece of state lives on an instance `__main__` builds.
"""
