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
The collection-control context's ports (DDD spine AD-D2/AD-D17): every input and output of
`ControlService` and `StatusPublisher` crosses one of these, implemented in
`collection_control.infrastructure` (and, for `Capture`, by capture's `Collector`) and wired by
the composition root, `dydx_collector/collector.py`'s `build_collector`. The channel names are the
published language (frozen by AD-D12).
"""

from collections.abc import AsyncIterator
from typing import Any
from typing import Protocol

from collector_core.ports import Applied
from collector_core.ports import CaptureStatus
from collector_core.ports import PlanDiff

from collection_control.domain.plan import CollectionPlan


STATUS_CHANNEL = "collector:status"
CONTROL_CHANNEL = "collector:control"


class Capture(Protocol):
    """
    What control needs from capture: apply a plan change, and read what is actually collected.

    Invariant (AD-D17): the plan is the intent, the applied set is the fact -- control reports an
    instrument as collected only from `capture_status()`, never from its own plan, and changes the
    wire only through `apply`, whose `Applied` names what failed instead of raising. Implemented by
    `collector_core.collector.Collector` (structurally; capture never imports this module).
    """

    async def apply(self, diff: PlanDiff) -> Applied: ...

    def capture_status(self) -> CaptureStatus: ...


class PlanStore(Protocol):
    """
    Where a venue's plan is persisted (the venue `config.toml`).

    Invariant: `load` returns only a plan that passed the one loader's validation (every
    `CollectionPlan` invariant, every config key known), and `save` writes only a plan that reads
    back equal through that same loader -- so a saved plan is always one the collector would start
    from. Either raises rather than write or return anything else.
    """

    def load(self) -> CollectionPlan: ...

    def save(self, plan: CollectionPlan) -> None: ...


class StatusBus(Protocol):
    """
    The `collector:status` publisher. Invariant: publishes each message string verbatim, in call
    order, on `STATUS_CHANNEL` only (the bytes are the published language, replay-tested).
    """

    async def publish(self, message: str) -> None: ...

    async def aclose(self) -> None: ...


class ControlChannel(Protocol):
    """
    The `collector:control` subscription. Invariant: `listen` yields every message's payload string
    in arrival order and raises (never ends quietly) when the connection is lost, so the caller can
    ledger and reconnect instead of silently stopping to take commands.
    """

    def listen(self) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...


class MarketsSource(Protocol):
    """
    The venue's markets snapshot a liquidity classification is made from (dYdX's indexer
    `perpetualMarkets`). Invariant: returns the venue's response as parsed JSON, or raises -- never
    an empty stand-in, which would read as "no market is liquid".
    """

    async def fetch(self) -> dict[str, Any]: ...
