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
import re
import sqlite3
import time
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from kernel.second_snapshot import SecondOHLC
from kernel.second_snapshot import unit_float

from candles.application import queries
from candles.application.forming import bars_from_rows
from candles.domain.candle import PARTIAL_OBSERVED_FRACTION
from candles.domain.candle import is_valid_candle
from candles.domain.fold import AGGREGATE_KEYS
from candles.domain.fold import BAR_SECONDS
from candles.domain.fold import DAY_MS
from candles.domain.fold import RETAIN_DAYS
from candles.infrastructure import sqlite_store
from candles.infrastructure.sqlite_store import CandleStore


_IID = "BTC-USD-PERP.DYDX"
_DAY0_MS = 20_000 * 86_400_000  # a UTC midnight well in the past (so `rebuild` may touch it)
_SEC_NS = 1_000_000_000
_PRICE_P = 2
_SIZE_P = 3


def _units(value: float, precision: int) -> int:
    """Return a decimal literal in units, exactly (`Decimal(str(...))`: never a float product)."""
    scaled = Decimal(str(value)).scaleb(precision)
    assert scaled == scaled.to_integral_value(), (value, precision)
    return int(scaled)


def _second(
    sec: int, price: float | None, volume: float = 1.0, size_precision: int = _SIZE_P
) -> SecondOHLC:
    """
    `sec` counts from _DAY0_MS; price None = a second with a book but no trade. A traded second
    spans `price - 0.5 .. price + 0.5`, closes at `price + 0.1`, buys `volume` in one trade and
    sells 0.25 in one; the floats are decoded from the units exactly as the catalog read decodes.
    """
    ts_event = _DAY0_MS * 1_000_000 + sec * _SEC_NS
    if price is None:
        return SecondOHLC(
            ts_event, None, None, None, None, 0.0, 0.0, _PRICE_P, size_precision, None, 0, 0, 0, 0
        )
    p = _units(price, _PRICE_P)
    buy, sell = _units(volume, size_precision), _units(0.25, size_precision)
    o, h, low, c = p, p + 50, p - 50, p + 10
    return SecondOHLC(
        ts_event,
        unit_float(o, _PRICE_P),
        unit_float(h, _PRICE_P),
        unit_float(low, _PRICE_P),
        unit_float(c, _PRICE_P),
        unit_float(buy, size_precision),
        unit_float(sell, size_precision),
        _PRICE_P,
        size_precision,
        c,
        buy,
        sell,
        1,
        1,
    )


def _fixture() -> list[SecondOHLC]:
    """Six hours of seconds: ~5% traded, and a 40-minute hole with no rows at all."""
    rng = random.Random(7)  # noqa: S311 -- a deterministic fixture, not cryptography
    rows = []
    for sec in range(6 * 3600):
        if 7200 <= sec < 9600:
            continue  # collector down
        traded = rng.random() < 0.05
        price = rng.randint(9_000, 11_000) / 100 if traded else None
        rows.append(_second(sec, price, rng.randint(10, 500) / 100))
    return rows


def _stored(db: sqlite3.Connection, bar: int) -> list[dict]:
    return queries.window(db, _IID, bar, 1 << 62, 10_000)


def _check_matches_raw_aggregation(db: sqlite3.Connection, rows: list[SecondOHLC]) -> None:
    for bar in BAR_SECONDS:
        got = _stored(db, bar)
        want = bars_from_rows(rows, bar)
        assert [c["t"] for c in got] == [c["t"] for c in want]
        for g, w in zip(got, want, strict=True):
            for k in ("o", "h", "l", "c"):
                assert g[k] == w[k], (bar, k, g, w)
            assert abs(g["v"] - w["v"]) < 1e-9
            # The integer columns are exact whatever the flush split: equal, not close.
            assert {k: g[k] for k in AGGREGATE_KEYS} == {k: w[k] for k in AGGREGATE_KEYS}
            assert is_valid_candle(g), g  # buy_v + sell_v is v in units
            seconds = sum(
                1 for r in rows if r.ts_event // 1_000_000 // (bar * 1000) * (bar * 1000) == g["t"]
            )
            assert g["seconds_observed"] == seconds
            assert g["partial"] == (seconds < PARTIAL_OBSERVED_FRACTION * bar)


def test_apply_in_flush_sized_batches_matches_raw_aggregation(tmp_path: Path) -> None:
    with closing(sqlite_store.connect_rw(str(tmp_path / "c.db"))) as db:
        rows = _fixture()
        for i in range(0, len(rows), 90):  # a batch per flush; batches split buckets mid-way
            assert sqlite_store.apply_seconds(db, _IID, rows[i : i + 90]) == len(rows[i : i + 90])
        _check_matches_raw_aggregation(db, rows)


def test_many_instruments_applied_one_by_one_match_raw_aggregation(tmp_path: Path) -> None:
    """The live feed's shape since Story 24.1: the sink applies each instrument on its own."""
    with closing(CandleStore(str(tmp_path / "c.db"))) as store:
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
    with closing(sqlite_store.connect_rw(str(tmp_path / "c.db"))) as db:
        rows = _fixture()
        sqlite_store.apply_seconds(db, _IID, rows)
        assert sqlite_store.apply_seconds(db, _IID, rows) == 0  # a re-delivered flush
        assert sqlite_store.apply_seconds(db, _IID, rows[100:5000]) == 0
        _check_matches_raw_aggregation(db, rows)


def test_rebuild_repairs_a_hole_and_is_idempotent(tmp_path: Path) -> None:
    with closing(sqlite_store.connect_rw(str(tmp_path / "c.db"))) as db:
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
    with closing(sqlite_store.connect_rw(str(tmp_path / "c.db"))) as db:
        today_ms = int(time.time() * 1000) // 86_400_000 * 86_400_000
        now_row = _second((today_ms - _DAY0_MS) // 1000, 1.0)
        assert sqlite_store.rebuild(db, _IID, [now_row], today_ms, today_ms + 86_400_000) == 0
        assert (
            sqlite_store.rebuild(
                db, _IID, [now_row], today_ms, today_ms + 86_400_000, allow_open_day=True
            )
            == 1
        )


def test_prune_drops_old_short_bars_but_keeps_wide_ones(tmp_path: Path) -> None:
    with closing(sqlite_store.connect_rw(str(tmp_path / "c.db"))) as db:
        sqlite_store.apply_seconds(db, _IID, _fixture())
        sqlite_store.prune(db, _DAY0_MS + 400 * 86_400_000)
        assert queries.oldest_t(db, _IID, 60) is None
        assert queries.oldest_t(db, _IID, 300) is None
        assert queries.oldest_t(db, _IID, 14400) is not None
        assert queries.newest_t(db, _IID, 60) is None
        assert queries.newest_t(db, _IID, 14400) is not None


def _plan(db: sqlite3.Connection, sql: str, params: tuple) -> str:
    return " ".join(row[3] for row in db.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall())


def test_prune_plan_is_an_index_search_not_a_table_scan(tmp_path: Path) -> None:
    with closing(sqlite_store.connect_rw(str(tmp_path / "c.db"))) as db:
        plan = _plan(db, sqlite_store._PRUNE, (60, 0))
    # SQLite before 3.36 words it "SEARCH TABLE candles"
    assert re.search(r"SEARCH (TABLE )?candles", plan)
    assert "INDEX candles_by_bar_seconds_t (bar_seconds=? AND t<?)" in plan
    assert "SCAN" not in plan


def test_per_instrument_reads_and_rebuild_deletes_keep_the_primary_key(tmp_path: Path) -> None:
    """The added index must not steal the plans that lead with `instrument_id`."""
    window = (
        "SELECT t FROM candles WHERE instrument_id = ? AND bar_seconds = ? "
        "AND o IS NOT NULL AND t < ? ORDER BY t DESC LIMIT ?"
    )
    rebuild_delete = "DELETE FROM candles WHERE instrument_id = ? AND t >= ? AND t < ?"
    with closing(sqlite_store.connect_rw(str(tmp_path / "c.db"))) as db:
        window_plan = _plan(db, window, (_IID, 60, 0, 10))
        rebuild_plan = _plan(db, rebuild_delete, (_IID, 0, 1))
    assert "PRIMARY KEY (instrument_id=? AND bar_seconds=? AND t<?)" in window_plan
    assert "PRIMARY KEY (instrument_id=?)" in rebuild_plan


def test_the_other_per_instrument_reads_keep_the_primary_key(tmp_path: Path) -> None:
    """
    `queries.oldest_t`/`newest_t`/`bucket_starts` and the verifier's day read
    (`verification.infrastructure.catalog_scan`), whose `ORDER BY bar_seconds, t` matches the new
    index's key order.
    """
    reads = {
        "SELECT MIN(t) FROM candles WHERE instrument_id = ? AND bar_seconds = ? "
        "AND o IS NOT NULL": ((_IID, 60), "PRIMARY KEY (instrument_id=? AND bar_seconds=?)"),
        "SELECT MAX(t) FROM candles WHERE instrument_id = ? AND bar_seconds = ?": (
            (_IID, 60),
            "PRIMARY KEY (instrument_id=? AND bar_seconds=?)",
        ),
        "SELECT t FROM candles WHERE instrument_id = ? AND bar_seconds = ? AND t >= ? AND t < ? "
        "ORDER BY t": (
            (_IID, 60, 0, 1),
            "PRIMARY KEY (instrument_id=? AND bar_seconds=? AND t>? AND t<?)",
        ),
        "SELECT bar_seconds, t, o, h, l, c, v, seconds_observed FROM candles "
        "WHERE instrument_id = ? AND t >= ? AND t < ? ORDER BY bar_seconds, t": (
            (_IID, 0, 1),
            "PRIMARY KEY (instrument_id=?)",
        ),
    }
    with closing(sqlite_store.connect_rw(str(tmp_path / "c.db"))) as db:
        for sql, (params, expected) in reads.items():
            plan = _plan(db, sql, params)
            assert expected in plan, (sql, plan)
            assert "candles_by_bar_seconds_t" not in plan, (sql, plan)


# The Story 24.1 baseline DDL: before DW-195's index and before Story 33.3's columns and tables.
_PRE_INDEX_SCHEMA = Path(__file__).parent / "fixtures" / "candle_store_schema_pre_33_3.sql"
_NOW_MS = _DAY0_MS + 100 * DAY_MS


def _cutoff(bar: int) -> int:
    return _NOW_MS - RETAIN_DAYS[bar] * DAY_MS


# Per pruned width: rows just past retention (deleted) and rows at or inside it (kept).
_EXPIRED = {bar: (_cutoff(bar) - DAY_MS, _cutoff(bar) - bar * 1000) for bar in RETAIN_DAYS}
_LIVE = {bar: (_cutoff(bar), _NOW_MS - bar * 1000) for bar in RETAIN_DAYS}
_WIDE_BAR = 14_400  # never pruned, however old
_WIDE_TS = (_NOW_MS - 1_000 * DAY_MS,)


def _seeded() -> dict[int, tuple[int, ...]]:
    seeded = {bar: (*_EXPIRED[bar], *_LIVE[bar]) for bar in RETAIN_DAYS}
    seeded[_WIDE_BAR] = _WIDE_TS
    return seeded


def _pre_index_store(path: Path) -> None:
    """Create a real store with the pre-DW-195 DDL, holding expired, live and wide rows."""
    with closing(sqlite3.connect(path)) as old:
        old.executescript(_PRE_INDEX_SCHEMA.read_text())
        for bar, stamps in _seeded().items():
            for t in stamps:
                old.execute(
                    "INSERT INTO candles VALUES(?, ?, ?, 1.0, 2.0, 0.5, 1.5, 3.0, 60)",
                    (_IID, bar, t),
                )
        old.commit()


def _candle_indexes(db: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'candles' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]


def _stamps_by_bar(db: sqlite3.Connection) -> dict[int, tuple[int, ...]]:
    stamps: dict[int, tuple[int, ...]] = {}
    for bar, t in db.execute("SELECT bar_seconds, t FROM candles ORDER BY bar_seconds, t"):
        stamps[bar] = (*stamps.get(bar, ()), t)
    return stamps


def test_pre_index_store_has_no_index(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _pre_index_store(path)
    with closing(sqlite3.connect(path)) as db:
        assert _candle_indexes(db) == []


def test_pre_index_fixture_is_the_recorded_schema_without_its_later_additions() -> None:
    """
    The fixtures cannot drift apart: the old shape is the recorded one minus DW-195's index and
    minus Story 33.3's aggregate column lines and its two liquidation tables.
    """
    recorded = (_PRE_INDEX_SCHEMA.parent / "candle_store_schema.sql").read_text()
    additions = [
        "CREATE INDEX IF NOT EXISTS candles_by_bar_seconds_t ON candles(bar_seconds, t);\n",
        "    buy_v INTEGER, sell_v INTEGER, buy_n INTEGER, sell_n INTEGER, pv INTEGER,\n",
        "    liq_long_v INTEGER, liq_short_v INTEGER, liq_n INTEGER,\n",
        "    price_precision INTEGER, size_precision INTEGER,\n",
        re.search(
            r"CREATE TABLE IF NOT EXISTS liquidations_applied \(.*?\) WITHOUT ROWID;\n",
            recorded,
            re.DOTALL,
        )[0],
        re.search(
            r"CREATE TABLE IF NOT EXISTS liquidation_feed_since \(.*?\n\);\n", recorded, re.DOTALL
        )[0],
    ]
    stripped = recorded
    for addition in additions:
        assert addition in stripped
        stripped = stripped.replace(addition, "")
    assert _PRE_INDEX_SCHEMA.read_text() == stripped


def test_pre_index_store_gains_the_index_on_writer_open_with_rows_intact(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _pre_index_store(path)
    store = CandleStore(str(path))
    try:
        assert _candle_indexes(store.connection) == ["candles_by_bar_seconds_t"]
        assert store.connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'candles_by_bar_seconds_t'"
        ).fetchone() == ("CREATE INDEX candles_by_bar_seconds_t ON candles(bar_seconds, t)",)
        assert _stamps_by_bar(store.connection) == _seeded()
    finally:
        store.close()


def test_migrated_and_analyzed_store_still_prunes_through_the_index(tmp_path: Path) -> None:
    """The plan on a populated, migrated store with planner statistics, not only an empty one."""
    path = tmp_path / "old.db"
    _pre_index_store(path)
    with closing(sqlite3.connect(path)) as old:
        rows = [
            (f"X{i}-PERP.BYBIT", bar, _NOW_MS - k * bar * 1000)
            for i in range(50)
            for bar in (*RETAIN_DAYS, _WIDE_BAR)
            for k in range(1, 40)
        ]
        old.executemany("INSERT INTO candles VALUES(?, ?, ?, 1.0, 2.0, 0.5, 1.5, 3.0, 60)", rows)
        old.commit()
    with closing(sqlite_store.connect_rw(str(path))) as db:
        db.execute("ANALYZE")
        for bar in RETAIN_DAYS:
            plan = _plan(db, sqlite_store._PRUNE, (bar, _cutoff(bar)))
            assert "INDEX candles_by_bar_seconds_t (bar_seconds=? AND t<?)" in plan, plan
            assert "SCAN" not in plan, plan


def test_pre_index_store_prunes_exactly_the_expired_rows_after_migration(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _pre_index_store(path)
    store = CandleStore(str(path))
    try:
        store.prune(now_ms=_NOW_MS)
        assert _stamps_by_bar(store.connection) == {**_LIVE, _WIDE_BAR: _WIDE_TS}
    finally:
        store.close()


def test_writer_open_twice_is_idempotent_with_exactly_one_index(tmp_path: Path) -> None:
    path = str(tmp_path / "c.db")
    sqlite_store.connect_rw(path).close()
    with closing(sqlite_store.connect_rw(path)) as db:
        assert _candle_indexes(db) == ["candles_by_bar_seconds_t"]


def test_bucket_starts_lists_every_observed_bucket_and_skips_the_outage(tmp_path: Path) -> None:
    with closing(sqlite_store.connect_rw(str(tmp_path / "c.db"))) as db:
        sqlite_store.apply_seconds(db, _IID, _fixture())
        starts = queries.bucket_starts(db, _IID, 60, _DAY0_MS, _DAY0_MS + 6 * 3_600_000)
        outage = set(range(_DAY0_MS + 7_200_000, _DAY0_MS + 9_600_000, 60_000))
        expected = [t for t in range(_DAY0_MS, _DAY0_MS + 6 * 3_600_000, 60_000) if t not in outage]
        assert starts == expected  # untraded minutes included, the collector-down minutes absent


def test_read_only_reader_sees_writer_and_missing_store_is_none(tmp_path: Path) -> None:
    path = str(tmp_path / "c.db")
    with sqlite_store.connect_ro(path) as db:
        assert db is None
    with closing(sqlite_store.connect_rw(path)) as writer:
        sqlite_store.apply_seconds(writer, _IID, [_second(0, 100.0)])
        with sqlite_store.connect_ro(path) as db:
            assert db is not None
            assert len(queries.window(db, _IID, 60, 1 << 62, 5)) == 1


def test_verified_days_upsert_and_read_back(tmp_path: Path) -> None:
    with closing(sqlite_store.connect_rw(str(tmp_path / "candles.db"))) as db:
        assert sqlite_store.verified_status(db, _IID, "2026-09-20") is None
        sqlite_store.mark_verified(db, _IID, "2026-09-20", "fail", 3, 1_000)
        sqlite_store.mark_verified(db, _IID, "2026-09-20", "pass", 0, 2_000)  # a rerun after a fix
        assert sqlite_store.verified_status(db, _IID, "2026-09-20") == "pass"
        assert sqlite_store.verified_status(db, _IID, "2026-09-19") is None
        assert db.execute("SELECT checked_at, mismatches FROM verified_days").fetchall() == [
            (2_000, 0)
        ]


def test_verified_status_on_a_store_that_predates_the_table(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as old:
        old.execute("CREATE TABLE candles (t INTEGER)")
        old.commit()
    with sqlite_store.connect_ro(str(path)) as ro:
        assert ro is not None
        assert sqlite_store.verified_status(ro, _IID, "2026-09-20") is None
