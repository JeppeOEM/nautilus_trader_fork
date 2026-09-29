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
The collection-control context (DDD spine AD-D17, Story 25.4): what a venue *intends* to collect,
kept apart from what capture actually subscribed.

A venue's collected set is one aggregate, `CollectionPlan` (the intent). Capture applies each
change through `capture.application.capture_service.CaptureService.apply` and reports the result as `Applied`
(the fact), and `collector:status` shows both -- an instrument planned but not applied carries
`"pending": true`. Control never touches a book, a gate or a catalog file: `archive.RetentionPolicy`
stays the only deleter, and reads the plan's retention attributes through `TomlPlanStore`.

Published language (frozen, AD-D12; fields only ever appended): `collector:status` (one row per
planned instrument, the per-venue plan aggregate -- `unpinned_ids`, then since Story 29.2 `venue`,
`cap`, `accepts_commands`, `min_liquidity_usd`, `last_apply` -- and the `removed` tombstone) and
`collector:control` (`{action, id}` with `start`, `unpin`, `stop`, `pin_top_liquid`, then since
Story 29.4 `venue` -- absent means dYdX), both read and written by `bot_tui`.

- `domain/` -- `CollectionPlan` (instruments, exclude, cap, the liquidity threshold and the
  dropped-instrument retention) whose commands `add`/`remove`/`pin`/`unpin`/`exclude`/`reload`
  return a `PlanDiff`; `InstrumentEntry`; `LiquidityTier` and the pure `classify_liquidity` that
  alone admits a pin (USD volume, OBS-03). Stdlib and `kernel` only.
- `application/` -- the ports (`Capture`, `PlanStore`, `StatusBus`, `ControlChannel`,
  `MarketsSource`), `ControlService` (`collector:control`: save, then apply, then publish),
  `StatusPublisher` (`collector:status`) and the plan-file `reload_loop`.
- `infrastructure/` -- `TomlPlanStore` over the venue `config.toml` through capture's one loader
  (`capture.infrastructure.config`), the Redis bus and channel, and `DydxMarkets` (the indexer's markets).

Every venue publishes status and takes control (Bybit and Hyperliquid since Story 29.4; Story
29.2 wired only their status loop). Each venue's composition root,
`capture/venues/<venue>/__main__.py`'s `build_capture_from_file`, hands the three loops (reload,
status, control) to the capture service through `add_loops`, and each `ControlService` acts only
on the `collector:control` messages addressed to its venue. No module here holds mutable runtime
state: every piece of state lives on an instance the composition root builds.
"""
