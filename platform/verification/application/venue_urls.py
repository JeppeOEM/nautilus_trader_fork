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
The production URLs of a plan's endpoints and REST polls, all built by `kernel.venue_http` (the
one place venue URLs live, AD-D3). Tests hand the recorder a local server's URLs instead.
"""

from kernel.venue_http import bybit_url
from kernel.venue_http import bybit_ws_url
from kernel.venue_http import hyperliquid_info_url
from kernel.venue_http import hyperliquid_ws_url

from verification.application.recorder import VenueWiring
from verification.domain.plan_file import RecordingPlan
from verification.domain.subscriptions import BYBIT
from verification.domain.subscriptions import Endpoint
from verification.domain.subscriptions import RestPoll
from verification.domain.subscriptions import rest_polls
from verification.domain.subscriptions import ws_endpoints


def ws_url(plan: RecordingPlan, endpoint: str) -> str:
    """Return the WebSocket URL of one of the plan's endpoints (`linear`/`spot`, or `ws`)."""
    if plan.venue == BYBIT:
        return bybit_ws_url(plan.environment, endpoint)
    return hyperliquid_ws_url(plan.environment)


def kernel_endpoints(plan: RecordingPlan) -> tuple[Endpoint, ...]:
    """Return the plan's endpoints at their production URLs (refuses an unknown environment)."""
    return ws_endpoints(plan, lambda name: ws_url(plan, name))


def kernel_rest_url(plan: RecordingPlan, poll: RestPoll) -> str:
    """Return a REST poll's production URL: Bybit's path on its host, Hyperliquid's `info`."""
    if plan.venue == BYBIT:
        return bybit_url(plan.environment, poll.request)
    return hyperliquid_info_url(plan.environment)


def kernel_wiring() -> VenueWiring:
    """Return the production wiring: `kernel.venue_http` URLs and the domain's poll table."""
    return VenueWiring(kernel_endpoints, rest_polls, kernel_rest_url)
