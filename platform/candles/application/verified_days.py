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
"""The day-status port: how the archive tools read and write a reconciliation verdict."""

from typing import Protocol


class VerifiedDays(Protocol):
    """
    Read and write one instrument-day's kline-reconciliation verdict.

    Invariant (one store of day status, AD-D9): `verified_days` lives in the candle store and is the
    only record of whether a day was proven against the venue's own klines. `prune_catalog` deletes
    a day's raw trades only once that record says "pass", so a second, divergent copy of the status
    -- or an archive tool opening the SQLite file itself and reading a stale connection's view --
    would decide a deletion on an unproven day. The commands that could violate it are
    `compare_klines`'s verdict write and `prune_catalog`'s retention read; both go through this port
    and never through a database connection.

    `verified_status` returns `None` both for a day never verified and for a store that does not
    exist yet or predates the table -- an unverified day, whose files are kept.
    """

    def mark_verified(
        self, instrument_id: str, day: str, status: str, mismatches: int, checked_at_ms: int
    ) -> None:
        """Upsert one instrument-day's verdict; `status` is "pass" or "fail", `day` YYYY-MM-DD."""
        ...

    def verified_status(self, instrument_id: str, day: str) -> str | None:
        """Return that instrument-day's verdict ("pass"/"fail"), or None when never verified."""
        ...
