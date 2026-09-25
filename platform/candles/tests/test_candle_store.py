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
The SQLite candle store must agree exactly with the same fold read straight off the rows
(`application.forming.bars_from_rows`, the archive-side path): same buckets and OHLCV;
`seconds_observed` counts every second present, traded or not. Plain rows with the snapshot fields,
a real SQLite file (platform/CLAUDE.md TEST-03).
"""

import random
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

from candles.application import queries
from candles.application.forming import bars_from_rows
from candles.domain.candle import PARTIAL_OBSERVED_FRACTION
from candles.domain.fold import BAR_SECONDS
from candles.infrastructure import sqlite_store
from candles.infrastructure.sqlite_store import CandleStore


_IID = "BTC-USD-PERP.DYDX"
_DAY0_MS = 20_000 * 86_400_000  # a UTC midnight well in the past (so `rebuild` may touch it)
_SEC_NS = 1_000_000_000


def _second(sec: int, price: float | None, volume: float = 1.0) -> SimpleNamespace:
    """`sec` counts from _DAY0_MS; price None = a second with a book but no trade."""
    return SimpleNamespace(
        ts_event=_DAY0_MS * 1_000_000 + sec * _SEC_NS,
        open_price=price,
        high_price=None if price is None else price + 0.5,
        low_price=None if price is None else price - 0.5,
        close_price=None if price is None else price + 0.1,
        buy_volume=0.0 if price is None else volume,
        sell_volume=0.0 if price is None else 0.25,
    )


def _fixture() -> list[SimpleNamespace]:
    """Six hours of seconds: ~5% traded, and a 40-minute hole with no rows at all."""
    rng = random.Random(7)  # noqa: S311 -- a deterministic fixture, not cryptography
    rows = []
    for sec in range(6 * 3600):
        if 7200 <= sec < 9600:
            continue  # collector down
        rows.append(
            _second(sec, rng.uniform(90, 110) if rng.random() < 0.05 else None, rng.uniform(0.1, 5))
        )
    return rows


def _stored(db: sqlite3.Connection, bar: int) -> list[dict]:
    return queries.window(db, _IID, bar, 1 << 62, 10_000)


def _check_matches_raw_aggregation(db: sqlite3.Connection, rows: list[SimpleNamespace]) -> None:
    for bar in BAR_SECONDS:
        got = _stored(db, bar)
        want = bars_from_rows(rows, bar)
        assert [c["t"] for c in got] == [c["t"] for c in want]
        for g, w in zip(got, want, strict=True):
            for k in ("o", "h", "l", "c"):
                assert g[k] == w[k], (bar, k, g, w)
            assert abs(g["v"] - w["v"]) < 1e-9
            seconds = sum(
                1 for r in rows if r.ts_event // 1_000_000 // (bar * 1000) * (bar * 1000) == g["t"]
            )
            assert g["seconds_observed"] == seconds
            assert g["partial"] == (seconds < PARTIAL_OBSERVED_FRACTION * bar)


def test_apply_in_flush_sized_batches_matches_raw_aggregation(tmp_path: Path) -> None:
    db = sqlite_store.connect_rw(str(tmp_path / "c.db"))
    rows = _fixture()
    for i in range(0, len(rows), 90):  # a batch per flush; batches split buckets mid-way
        assert sqlite_store.apply_seconds(db, _IID, rows[i : i + 90]) == len(rows[i : i + 90])
    _check_matches_raw_aggregation(db, rows)


def test_many_instruments_applied_one_by_one_match_raw_aggregation(tmp_path: Path) -> None:
    """The live feed's shape since Story 24.1: the sink applies each instrument on its own."""
    store = CandleStore(str(tmp_path / "c.db"))
    rows = _fixture()
    other = "ETH-USD-PERP.DYDX"
    for i in range(0, len(rows), 1800):  # a feed every 30 min, both coins
        chunk = rows[i : i + 1800]
        assert [store.apply(_IID, chunk), store.apply(other, chunk)] == [len(chunk)] * 2
    _check_matches_raw_aggregation(store.connection, rows)
    assert queries.window(store.connection, other, 3600, 1 << 62, 10) == queries.window(
        store.connection, _IID, 3600, 1 << 62, 10
    )


def test_replayed_seconds_are_not_double_counted(tmp_path: Path) -> None:
    db = sqlite_store.connect_rw(str(tmp_path / "c.db"))
    rows = _fixture()
    sqlite_store.apply_seconds(db, _IID, rows)
    assert sqlite_store.apply_seconds(db, _IID, rows) == 0  # a re-delivered flush
    assert sqlite_store.apply_seconds(db, _IID, rows[100:5000]) == 0
    _check_matches_raw_aggregation(db, rows)


def test_rebuild_repairs_a_hole_and_is_idempotent(tmp_path: Path) -> None:
    db = sqlite_store.connect_rw(str(tmp_path / "c.db"))
    rows = _fixture()
    sqlite_store.apply_seconds(
        db,
        _IID,
        [r for r in rows if not 3000 <= (r.ts_event - _DAY0_MS * 1_000_000) // _SEC_NS < 3600],
    )
    hour0 = next(c for c in _stored(db, 3600) if c["t"] == _DAY0_MS)
    assert (
        hour0["seconds_observed"] == 3000
    )  # the missed 10 minutes are visibly absent before the rebuild
    for _ in range(2):  # the second run must change nothing
        sqlite_store.rebuild(db, _IID, rows, _DAY0_MS, _DAY0_MS + 86_400_000)
        _check_matches_raw_aggregation(db, rows)


def test_rebuild_refuses_the_open_day_unless_told(tmp_path: Path) -> None:
    db = sqlite_store.connect_rw(str(tmp_path / "c.db"))
    today_ms = int(time.time() * 1000) // 86_400_000 * 86_400_000
    now_row = SimpleNamespace(
        ts_event=today_ms * 1_000_000,
        open_price=1.0,
        high_price=1.0,
        low_price=1.0,
        close_price=1.0,
        buy_volume=1.0,
        sell_volume=0.0,
    )
    assert sqlite_store.rebuild(db, _IID, [now_row], today_ms, today_ms + 86_400_000) == 0
    assert (
        sqlite_store.rebuild(
            db, _IID, [now_row], today_ms, today_ms + 86_400_000, allow_open_day=True
        )
        == 1
    )


def test_prune_drops_old_short_bars_but_keeps_wide_ones(tmp_path: Path) -> None:
    db = sqlite_store.connect_rw(str(tmp_path / "c.db"))
    sqlite_store.apply_seconds(db, _IID, _fixture())
    sqlite_store.prune(db, _DAY0_MS + 400 * 86_400_000)
    assert queries.oldest_t(db, _IID, 60) is None
    assert queries.oldest_t(db, _IID, 300) is None
    assert queries.oldest_t(db, _IID, 14400) is not None


def test_read_only_reader_sees_writer_and_missing_store_is_none(tmp_path: Path) -> None:
    path = str(tmp_path / "c.db")
    with sqlite_store.connect_ro(path) as db:
        assert db is None
    writer = sqlite_store.connect_rw(path)
    sqlite_store.apply_seconds(writer, _IID, [_second(0, 100.0)])
    with sqlite_store.connect_ro(path) as db:
        assert db is not None
        assert len(queries.window(db, _IID, 60, 1 << 62, 5)) == 1


def test_verified_days_upsert_and_read_back(tmp_path: Path) -> None:
    db = sqlite_store.connect_rw(str(tmp_path / "candles.db"))
    assert sqlite_store.verified_status(db, _IID, "2026-09-20") is None
    sqlite_store.mark_verified(db, _IID, "2026-09-20", "fail", 3, 1_000)
    sqlite_store.mark_verified(db, _IID, "2026-09-20", "pass", 0, 2_000)  # a rerun after a fix
    assert sqlite_store.verified_status(db, _IID, "2026-09-20") == "pass"
    assert sqlite_store.verified_status(db, _IID, "2026-09-19") is None
    assert db.execute("SELECT checked_at, mismatches FROM verified_days").fetchall() == [(2_000, 0)]


def test_verified_status_on_a_store_that_predates_the_table(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    sqlite3.connect(path).execute("CREATE TABLE candles (t INTEGER)").connection.commit()
    with sqlite_store.connect_ro(str(path)) as ro:
        assert ro is not None
        assert sqlite_store.verified_status(ro, _IID, "2026-09-20") is None
