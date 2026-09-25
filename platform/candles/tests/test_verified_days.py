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
The `VerifiedDays` adapters the archive tools get instead of a database connection (AD-D9).

The behaviour that matters to `prune_catalog`: an unknown day, an absent store and a store written
before the table existed must all read as `None` (unverified -> files kept), never as a pass and
never as an error.
"""

import sqlite3
from pathlib import Path

from candles.infrastructure.sqlite_store import CandleStore
from candles.infrastructure.sqlite_store import db_path_for_venue
from candles.infrastructure.verified_days import VerifiedDaysDir
from candles.infrastructure.verified_days import VerifiedDaysStore


_IID = "BTCUSDT-LINEAR.BYBIT"
_OTHER = "BTC-USD-PERP.DYDX"
_DAY = "2026-09-20"


def test_a_verdict_written_through_the_port_reads_back(tmp_path: Path) -> None:
    store = VerifiedDaysStore(str(tmp_path / "candles_bybit.db"))
    assert store.verified_status(_IID, _DAY) is None  # the file does not even exist yet
    store.mark_verified(_IID, _DAY, "fail", 3, 1_000)
    assert store.verified_status(_IID, _DAY) == "fail"
    store.mark_verified(_IID, _DAY, "pass", 0, 2_000)  # a rerun after a fix
    assert store.verified_status(_IID, _DAY) == "pass"
    assert store.verified_status(_IID, "2026-09-19") is None


def test_the_named_store_holds_no_connection_open_between_calls(tmp_path: Path) -> None:
    """The collector is the store's long-lived writer: a run must not hold a writer lock on it."""
    path = str(tmp_path / "candles_bybit.db")
    verified = VerifiedDaysStore(path)
    verified.mark_verified(_IID, _DAY, "pass", 0, 1_000)
    collector = CandleStore(path)  # would block on a held write transaction
    collector.mark_verified(_OTHER, _DAY, "fail", 1, 2_000)
    collector.close()
    assert verified.verified_status(_OTHER, _DAY) == "fail"


def test_a_store_that_predates_the_table_reads_as_unverified(tmp_path: Path) -> None:
    path = tmp_path / "candles_bybit.db"
    sqlite3.connect(path).execute("CREATE TABLE candles (t INTEGER)").connection.commit()
    assert VerifiedDaysStore(str(path)).verified_status(_IID, _DAY) is None


def test_the_directory_adapter_resolves_the_file_from_the_instrument_id(tmp_path: Path) -> None:
    bybit = CandleStore(db_path_for_venue(tmp_path, "BYBIT"))
    bybit.mark_verified(_IID, _DAY, "pass", 0, 1_000)
    bybit.close()
    dydx = CandleStore(db_path_for_venue(tmp_path, "DYDX"))
    dydx.mark_verified(_OTHER, _DAY, "fail", 2, 1_000)
    dydx.close()
    with VerifiedDaysDir(tmp_path) as verified:
        assert verified.verified_status(_IID, _DAY) == "pass"
        assert verified.verified_status(_OTHER, _DAY) == "fail"


def test_a_venue_with_no_store_reads_as_unverified(tmp_path: Path) -> None:
    with VerifiedDaysDir(tmp_path) as verified:
        assert verified.verified_status(_IID, _DAY) is None


def test_the_directory_adapter_can_also_write_its_venues_verdict(tmp_path: Path) -> None:
    with VerifiedDaysDir(tmp_path) as verified:
        verified.mark_verified(_IID, _DAY, "pass", 0, 1_000)
    assert Path(db_path_for_venue(tmp_path, "BYBIT")).exists()
    assert VerifiedDaysStore(db_path_for_venue(tmp_path, "BYBIT")).verified_status(_IID, _DAY) == (
        "pass"
    )
