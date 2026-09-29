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
The ranking context's ports (DDD spine AD-D10): every input and output of `RankingEngine` crosses
one of these, implemented in `ranking.infrastructure` and wired by `ranking/__main__.py`.
"""

from typing import Protocol


# The published channel names (AD-D12: frozen for the whole migration).
SNAPSHOTS_CHANNEL = "snapshots:raw"
CONTROL_CHANNEL = "ranking:control"
RANKINGS_CHANNEL = "rankings:live"
# Story 29.5: every venue's market names (no metric), one message per venue per volume cycle.
MARKETS_CHANNEL = "markets:live"


class VolumeSource(Protocol):
    """
    One venue source of USD 24 h volume per instrument (`name`: "dydx", "bybit-linear", ...).

    Invariant: `fetch` returns only finite, non-negative USD figures keyed by the instrument id
    exactly as that venue's adapter builds it; an unparseable value is ledgered and left out, never
    returned as 0 (DATA-01); a failed or malformed response raises, so the engine keeps the last
    good values instead of replacing them with nothing.
    """

    name: str

    async def fetch(self) -> dict[str, float]: ...


class PriceHistory(Protocol):
    """
    The archived per-second prices of one instrument, for the one-time price-series backfill.

    Invariant: an ascending `(ts_event, price)` series from `start_ns` on; a second with no trade
    contributes nothing (never a zero price).
    """

    def series(self, instrument_id: str, start_ns: int) -> list[tuple[int, float]]: ...


class RankingHistory(Protocol):
    """
    `metrics.db`, the persisted per-minute metric rows ranking is the sole writer of.

    Invariant: one row per (ts, instrument_id) with exactly the frozen column set; `price_near_days_ago`
    returns only a price stored within tolerance of the target, never a padded or zero one.
    """

    def write(self, rows: list[dict], retain_days: int = 31) -> None: ...

    def latest(self) -> list[dict]: ...

    def history(self, instrument_id: str, days: int = 31) -> list[dict]: ...

    def nearest(self, instrument_id: str, ts: int) -> dict | None: ...

    def price_near_days_ago(self, days: float, tolerance_s: float = 3600.0) -> dict[str, float]: ...


class LivePublisher(Protocol):
    """
    One published channel (`rankings:live`, and since Story 29.5 `markets:live`). Invariant:
    `message` is published verbatim -- the engine builds the frozen wire payload, the adapter only
    transports it.
    """

    async def publish(self, message: str) -> None: ...
