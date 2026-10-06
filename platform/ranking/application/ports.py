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

from kernel.liquidation import Liquidation
from kernel.open_interest import OpenInterest

from ranking.domain.price_series import PricePoint


# The published channel names (AD-D12: frozen for the whole migration).
SNAPSHOTS_CHANNEL = "snapshots:raw"
CONTROL_CHANNEL = "ranking:control"
RANKINGS_CHANNEL = "rankings:live"
# Story 29.5: every venue's market names (no metric), one message per venue per volume cycle.
MARKETS_CHANNEL = "markets:live"
# Story 33.4: capture's published derivatives rows (`kernel.derivs_wire`) and liquidations
# (`Liquidation.to_dict` rows), each one JSON array per message; spelled here because ranking may
# not import `capture` (`capture.infrastructure.redis_stream` holds the publisher's constants).
DERIVS_CHANNEL = "derivs:raw"
LIQUIDATIONS_CHANNEL = "liquidations:raw"


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
    The archived traded seconds of one instrument, for the one-time price-series backfill.

    Invariant: an ascending `PricePoint` series from `start_ns` on, each second's float close plus
    its exact close and traded volume (Story 33.4: the hourly volume's backfill rides this one
    read); a second with no trade contributes nothing (never a zero price).
    """

    def series(self, instrument_id: str, start_ns: int) -> list[PricePoint]: ...


class DerivsHistory(Protocol):
    """
    The archived open interest and liquidations of one instrument (Story 33.4), read once per
    instrument at its first backfill and never retried.

    Invariant: every row with `ts_event` in the inclusive `[start_ns, end_ns]`, ascending, each
    stored row once; a row stored twice with different values raises (the engine ledgers it),
    never one copy picked. MEM-01: the caller bounds the window (25 h, 1 h).
    """

    def open_interest(
        self, instrument_id: str, start_ns: int, end_ns: int
    ) -> list[OpenInterest]: ...

    def liquidations(self, instrument_id: str, start_ns: int, end_ns: int) -> list[Liquidation]: ...


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
