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
Deprecated shim package (Story 26.2): this venue moved to `capture/venues/dydx/`.

- `dydx_collector.client` -> `capture.venues.dydx.client`
- `dydx_collector.trade_history` -> `capture.venues.dydx.trade_history`
- `dydx_collector.policies` -> `capture.venues.dydx.policies`
- `dydx_collector.open_interest` -> `capture.venues.dydx.open_interest`

`dydx_collector.collector` moved to `capture.venues.dydx.__main__` (`python3 -m
capture.venues.dydx`, `build_capture`). It has no shim on purpose: its class has no successor
of that shape, and a stale `python3 -m dydx_collector.collector` must fail loudly, never exit 0
into a restart loop.

`config.toml` stays: it is the 0-byte placeholder the compose bind mount covers at the
container path `/app/dydx_collector/config.toml` (AD-D12, frozen; Story 26.3 decides where it
goes).

Every module here is a pure re-export removed after `26-3-closeout-shims-gone-spines-reconciled`.
"""
