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
Deprecated shim package (Story 26.2): capture moved to `capture/` (DDD spine Structural Seed).

- `collector_core.domain.*` -> `capture.domain.*`
- `collector_core.{ports,sites,book_check,feed}` -> `capture.application.*`
- `collector_core.collector` (`Collector`) -> `capture.application.capture_service`
  (`CaptureService`)
- `collector_core.application.trade_backfill` -> `capture.application.trade_backfill`
- `collector_core.config` -> `capture.application.config` (thresholds),
  `capture.infrastructure.config` (the loader) and `capture.venues.<v>.config`
- `collector_core.infrastructure.*`, `collector_core.{capture_lock,gap_markers}` ->
  `capture.infrastructure.*`
- `collector_core.tests` -> `capture.tests`

Every module here is a pure re-export removed after `26-3-closeout-shims-gone-spines-reconciled`.
"""
