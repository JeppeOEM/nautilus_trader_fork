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
"""The retention process manager: this context's own hourly prune, not capture's."""

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Protocol

from observability import error_ledger


PRUNE_INTERVAL_SECONDS = 3600.0


class RetentionStore(Protocol):
    """
    A store whose short bars age out.

    Invariant (retention runs in the writer's process, once): only the process that holds the store
    read-write may delete from it, so the prune is a loop that process starts -- never a second
    service, and never a reader opening the file read-write to tidy up.
    """

    def prune(self, now_ms: int | None = None) -> None: ...


def loop(store: RetentionStore) -> Callable[[], Awaitable[None]]:
    """
    Build the zero-argument coroutine function a collector starts through `extra_loops`.

    Prunes once immediately (a process that restarts more often than hourly would otherwise never
    prune at all), then every hour. Runs until cancelled: `Collector.run`'s `finally` cancels every
    loop task, and the `await` is the cancellation point. A failed prune is loud and never stops the
    loop (DATA-07) -- the store simply keeps bars past their retention until the next attempt.

    The inner function's name (`prune_loop`) and its one free variable (`store`) are a contract:
    the three venue wiring tests identify the retention loop in `extra_loops` by that name and
    read the store out of the closure cell to prove each venue prunes the file it writes.
    Renaming either, or replacing the closure with a partial or a class, breaks those tests --
    update them together.
    """

    async def prune_loop() -> None:
        while True:
            try:
                store.prune()
            except Exception as e:
                error_ledger.record("collector.candle_store_prune", "candle store prune failed", e)
            await asyncio.sleep(PRUNE_INTERVAL_SECONDS)

    return prune_loop
