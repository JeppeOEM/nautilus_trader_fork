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
Story 33.3: the per-bar order-flow and liquidation columns, folded once.

Every expected value below is computed by hand in its comment (TEST-01): integer units at the
stated precisions, `pv` in `10^-(price_precision + size_precision)`. The fold, the merge, the store
(migration aside: `test_schema_is_frozen.py`) and the rebuild over a real catalog.
"""

import sqlite3
from pathlib import Path

import pytest
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.second_snapshot import SecondOHLC
from kernel.tests.snapshot_factory import make_snapshot
from kernel.tests.snapshot_factory import second_of

from candles.application import queries
from candles.application.rebuild import rebuild_instrument
from candles.domain.candle import is_valid_candle
from candles.domain.fold import AGGREGATE_KEYS
from candles.domain.fold import CandleOverflowError
from candles.domain.fold import FoldedBucket
from candles.domain.fold import check_storable
from candles.domain.fold import fold_rows
from candles.domain.fold import merge_buckets
from candles.infrastructure import sqlite_store
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_LINEAR = "BTCUSDT-LINEAR.BYBIT"  # has the liquidation feed
_SPOT = "BTCUSDT-SPOT.BYBIT"  # has none
_DAY0_MS = 20_000 * 86_400_000
_S = 1_000_000_000
_T0 = _DAY0_MS * 1_000_000


def _sec(
    s: int,
    close_units: int | None,
    buy: int,
    sell: int,
    buy_n: int,
    sell_n: int,
    precisions: tuple[int, int] = (1, 3),
) -> SecondOHLC:
    """One second `s` after the day's start, from units at `(price, size)` precisions."""
    pp, sp = precisions
    close = None if close_units is None else close_units / 10**pp
    return SecondOHLC(
        _T0 + s * _S,
        close,
        close,
        close,
        close,
        buy / 10**sp,
        sell / 10**sp,
        pp,
        sp,
        close_units,
        buy,
        sell,
        buy_n,
        sell_n,
    )


def _liq(
    key: str,
    s: int,
    size_units: int,
    side: LiquidatedSide = LiquidatedSide.LONG,
    sp: int = 3,
    iid: str = _LINEAR,
) -> Liquidation:
    ts = _T0 + s * _S
    return Liquidation(InstrumentId.from_str(iid), side, size_units, 600_000, 1, sp, key, ts, ts)


def _minute(folded: dict, t_ms: int = _DAY0_MS) -> FoldedBucket:
    return folded[(60, t_ms)]


# -- the fold ------------------------------------------------------------------------------------


def test_the_golden_bucket_folds_exactly() -> None:
    """
    The spec's golden fold (price precision 1, size precision 3):
    s0 close 1000 buys 2000 in 1 trade; s1 close 1001 sells 3000 in 2; one LONG liquidation of 500.
    pv = 1000 x 2000 + 1001 x 3000 = 5_003_000; v = 5.0 as the float sum, 5000 units.
    """
    rows = [_sec(0, 1000, 2000, 0, 1, 0), _sec(1, 1001, 0, 3000, 0, 2)]
    bucket = _minute(fold_rows(rows, liquidations=[_liq("a", 1, 500)], bars=(60,)))
    assert bucket.aggregates() == {
        "buy_v": 2000,
        "sell_v": 3000,
        "buy_n": 1,
        "sell_n": 2,
        "pv": 5_003_000,
        "liq_long_v": 500,
        "liq_short_v": 0,
        "liq_n": 1,
        "price_precision": 1,
        "size_precision": 3,
    }
    assert bucket.v == 5.0
    assert (bucket.o, bucket.c, bucket.seconds_observed) == (100.0, 100.1, 2)


def test_the_traded_bucket_of_the_matrix() -> None:
    """3 s: buy 2 units in 1 trade, sell 3 in 2, one quiet second; 1 LONG liquidation of 4."""
    rows = [_sec(0, 10, 2, 0, 1, 0), _sec(1, None, 0, 0, 0, 0), _sec(2, 11, 0, 3, 0, 2)]
    bucket = _minute(fold_rows(rows, liquidations=[_liq("a", 2, 4)], bars=(60,)))
    assert (bucket.buy_v, bucket.sell_v, bucket.buy_n, bucket.sell_n) == (2, 3, 1, 2)
    assert (bucket.liq_long_v, bucket.liq_short_v, bucket.liq_n) == (4, 0, 1)
    assert (bucket.buy_v or 0) + (bucket.sell_v or 0) == round(bucket.v * 10**3) == 5
    assert bucket.pv == 10 * 2 + 11 * 3


def test_no_feed_is_null_liquidations_and_a_quiet_feed_is_zero() -> None:
    rows = [_sec(0, 10, 2, 0, 1, 0)]
    no_feed = _minute(fold_rows(rows, bars=(60,)))
    quiet = _minute(fold_rows(rows, liquidations=[], bars=(60,)))
    assert (no_feed.liq_long_v, no_feed.liq_short_v, no_feed.liq_n) == (None, None, None)
    assert (quiet.liq_long_v, quiet.liq_short_v, quiet.liq_n) == (0, 0, 0)
    assert no_feed.buy_v == quiet.buy_v == 2


def test_a_mid_bucket_precision_change_rescales_to_the_finer() -> None:
    """s1 at size precision 2 buys 5 units (0.05), s2 at 3 buys 7 (0.007): 50 + 7 = 57 at 3."""
    rows = [_sec(0, 10, 5, 0, 1, 0, (1, 2)), _sec(1, 10, 7, 0, 1, 0, (1, 3))]
    bucket = _minute(fold_rows(rows, bars=(60,)))
    assert (bucket.buy_v, bucket.size_precision) == (57, 3)
    assert bucket.pv == 10 * 5 * 10 + 10 * 7  # each second's pv rescaled to 10^-(1+3)


def test_a_short_liquidation_at_a_coarser_precision_is_rescaled() -> None:
    rows = [_sec(0, 10, 2, 0, 1, 0, (1, 3))]
    liq = _liq("a", 0, 4, LiquidatedSide.SHORT, sp=2)  # 0.04 = 40 units at precision 3
    bucket = _minute(fold_rows(rows, liquidations=[liq], bars=(60,)))
    assert (bucket.liq_long_v, bucket.liq_short_v, bucket.liq_n) == (0, 40, 1)


def test_a_liquidation_without_a_second_makes_an_unobserved_bucket() -> None:
    bucket = _minute(fold_rows([], liquidations=[_liq("a", 5, 9)], bars=(60,)))
    assert (bucket.o, bucket.v, bucket.seconds_observed) == (None, 0.0, 0)
    assert (bucket.buy_v, bucket.pv, bucket.liq_long_v, bucket.liq_n) == (0, 0, 9, 1)
    assert (bucket.price_precision, bucket.size_precision) == (0, 3)


def test_a_bucket_is_known_only_from_the_feed_start_and_a_straddling_one_is_null() -> None:
    """
    Feed start 120 s (the first archived liquidation, 7 units LONG): minutes 0 and 1 start before
    it and read null, minute 2 starts exactly at it and counts it, minute 3 has none and reads 0.
    The 5-minute bucket [0, 300 s) starts before the start it holds: null, never 7.
    """
    rows = [_sec(s, 10, 1, 0, 1, 0) for s in (0, 60, 121, 180)]
    folded = fold_rows(
        rows,
        liquidations=[_liq("a", 120, 7)],
        liquidations_since_ns=_T0 + 120 * _S,
        bars=(60, 300),
    )
    liq = {t: folded[(60, _DAY0_MS + t)].liq_n for t in (0, 60_000, 120_000, 180_000)}
    assert liq == {0: None, 60_000: None, 120_000: 1, 180_000: 0}
    assert _minute(folded, _DAY0_MS + 60_000).buy_v == 1  # the flow is known throughout
    assert folded[(300, _DAY0_MS)].liq_long_v is None


def test_a_liquidation_inside_a_straddling_bucket_is_not_counted_nor_makes_a_row() -> None:
    """
    Feed start 90 s: minute 1 [60, 120 s) straddles it, so its liquidation at 90 s (that row is the
    start) neither counts nor creates a liquidation-only bucket; minute 2's at 130 s counts.
    """
    folded = fold_rows(
        [], liquidations=[_liq("a", 90, 3), _liq("b", 130, 5)], liquidations_since_ns=_T0 + 90 * _S
    )
    assert (60, _DAY0_MS + 60_000) not in folded
    assert folded[(60, _DAY0_MS + 120_000)].liq_long_v == 5
    assert all(bar == 60 for bar, _t in folded)  # every wider bucket straddles: no row


def test_a_row_older_than_the_given_bound_moves_the_bound_never_raises() -> None:
    """Bound 61 s, a liquidation at 60 s: the start is 60 s, so minute 1 is known and counts it."""
    folded = fold_rows(
        [], liquidations=[_liq("a", 60, 9)], liquidations_since_ns=_T0 + 61 * _S, bars=(60,)
    )
    assert _minute(folded, _DAY0_MS + 60_000).liq_long_v == 9


def test_a_sub_millisecond_feed_start_leaves_its_bucket_unknown() -> None:
    """A start 1 ns after minute 1's start: minute 1 starts before it (straddles), 2 is known."""
    folded = fold_rows(
        [_sec(60, 10, 1, 0, 1, 0), _sec(120, 10, 1, 0, 1, 0)],
        liquidations=[],
        liquidations_since_ns=_T0 + 60 * _S + 1,
        bars=(60,),
    )
    assert _minute(folded, _DAY0_MS + 60_000).liq_n is None
    assert _minute(folded, _DAY0_MS + 120_000).liq_n == 0


def test_a_read_time_fold_over_int64_is_exact_never_refused() -> None:
    """
    2^62 + 2^62 = 2^63 = 9_223_372_036_854_775_808, one past int64: a read-time fold (a 1W bar,
    a `raw_1s` page) never reaches SQLite, so it keeps the exact Python int; `pv` = close 10 x
    (2^62 + 2^62) = 10 x 2^63.
    """
    rows = [_sec(0, 10, 2**62, 0, 1, 0), _sec(1, 10, 2**62, 0, 1, 0)]
    minute = _minute(fold_rows(rows, bars=(60,)))
    assert minute.buy_v == 2**63
    assert minute.pv == 10 * 2**63


def test_a_store_write_over_int64_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    """The same two seconds through the live sink's write: refused before binding, rolled back."""
    db = _db(tmp_path)
    rows = [_sec(0, 10, 2**62, 0, 1, 0), _sec(1, 10, 2**62, 0, 1, 0)]
    with pytest.raises(CandleOverflowError, match="buy_v"):
        sqlite_store.apply_seconds(db, _SPOT, rows)
    assert _stored(db, _SPOT) == []
    assert sqlite_store.watermarks(db) == {}


def test_a_sum_inside_int64_on_the_exact_path_is_kept() -> None:
    """Past the float estimate's bound the fold recomputes in Python ints: right at int64 max."""
    rows = [_sec(0, None, 2**62, 0, 1, 0), _sec(1, None, 2**62 - 1, 0, 1, 0)]
    assert _minute(fold_rows(rows, bars=(60,))).buy_v == 2**63 - 1


# -- the merge -----------------------------------------------------------------------------------


def _bucket(buy_v: int, size_p: int, liq_n: int | None = 0) -> FoldedBucket:
    liq = None if liq_n is None else liq_n
    return FoldedBucket(
        10.0, 10.0, 10.0, 10.0, 1.0, 1, buy_v, 0, 1, 0, buy_v * 100, liq, liq, liq, 1, size_p
    )


def test_merging_rescales_the_coarser_part_and_adds() -> None:
    """Stored row at size precision 2 (buy 5), fragment at 3 (buy 7): 5 x 10 + 7 = 57 at 3."""
    merged = merge_buckets(_bucket(5, 2), _bucket(7, 3))
    assert (merged.buy_v, merged.size_precision, merged.pv) == (57, 3, 500 * 10 + 700)
    assert (merged.v, merged.seconds_observed) == (2.0, 2)


def test_a_pre_migration_part_keeps_the_merged_groups_null() -> None:
    old = FoldedBucket(10.0, 11.0, 9.0, 10.5, 3.0, 40)  # every Story 33.3 column unknown
    merged = merge_buckets(old, _bucket(7, 3))
    assert merged.aggregates() == dict.fromkeys(AGGREGATE_KEYS)
    assert (merged.o, merged.h, merged.l, merged.c, merged.v) == (10.0, 11.0, 9.0, 10.0, 4.0)


def test_a_no_feed_part_keeps_the_liquidations_null_and_the_flow() -> None:
    merged = merge_buckets(_bucket(5, 3, liq_n=None), _bucket(7, 3))
    assert (merged.buy_v, merged.liq_n) == (12, None)


def test_a_merge_over_int64_is_exact_and_only_the_store_refuses_it() -> None:
    """2^62 + 2^62 = 2^63: the pure merge keeps it; `check_storable` (the store write) refuses."""
    merged = merge_buckets(_bucket(2**62, 3), _bucket(2**62, 3))
    assert merged.buy_v == 2**63
    with pytest.raises(CandleOverflowError, match="buy_v"):
        check_storable(merged)


# -- is_valid_candle -----------------------------------------------------------------------------

_OK = {
    "o": 10.0,
    "h": 12.0,
    "l": 9.0,
    "c": 11.0,
    "v": 5.0,
    "buy_v": 2000,
    "sell_v": 3000,
    "buy_n": 1,
    "sell_n": 2,
    "pv": 1,
    "liq_long_v": 0,
    "liq_short_v": 0,
    "liq_n": 0,
    "price_precision": 1,
    "size_precision": 3,
}


@pytest.mark.parametrize(
    ("change", "valid"),
    [
        ({}, True),
        (dict.fromkeys(AGGREGATE_KEYS), True),  # a pre-migration bar
        ({"liq_long_v": None, "liq_short_v": None, "liq_n": None}, True),  # no feed
        ({"buy_v": 2001}, False),  # buy_v + sell_v is not v in units
        ({"buy_v": -1, "sell_v": 5001}, False),
        ({"sell_n": -1}, False),
        ({"pv": None}, False),  # flow partly null
        ({"liq_long_v": 4}, False),  # a liquidated volume with no liquidation
        ({"liq_n": 1}, False),  # a liquidation with no volume
        ({"liq_long_v": 4, "liq_n": 1}, True),
        ({"liq_n": None}, False),  # the liquidation group partly null
    ],
)
def test_is_valid_candle_judges_the_order_flow_columns(change: dict, valid: bool) -> None:
    assert is_valid_candle({**_OK, **change}) is valid


def test_an_old_shape_without_the_keys_is_still_valid() -> None:
    assert is_valid_candle({k: _OK[k] for k in ("o", "h", "l", "c", "v")})


# -- the store -----------------------------------------------------------------------------------


def _db(tmp_path: Path) -> sqlite_store.sqlite3.Connection:
    return sqlite_store.connect_rw(str(tmp_path / "c.db"))


def _stored(db: sqlite_store.sqlite3.Connection, iid: str, bar: int = 60) -> list[dict]:
    return queries.window(db, iid, bar, 1 << 62, 10_000)


def test_a_feed_instruments_seconds_store_zero_from_its_feed_start_and_a_spot_ones_null(
    tmp_path: Path,
) -> None:
    """
    A liquidation at 60 s records the feed start: minute 1 (starting at it) and minute 2 read 0
    once their seconds arrive, minute 0 (applied before any start was known) and spot read null.
    """
    db = _db(tmp_path)
    sqlite_store.apply_seconds(db, _LINEAR, [_sec(0, 10, 2, 0, 1, 0)])
    assert sqlite_store.feed_since(db, _LINEAR) is None
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("a", 60, 4)])
    assert sqlite_store.feed_since(db, _LINEAR) == _T0 + 60 * _S
    sqlite_store.apply_seconds(db, _LINEAR, [_sec(61, 10, 2, 0, 1, 0), _sec(120, 10, 2, 0, 1, 0)])
    sqlite_store.apply_seconds(db, _SPOT, [_sec(0, 10, 2, 0, 1, 0)])
    assert [bar["liq_n"] for bar in _stored(db, _LINEAR)] == [None, 1, 0]
    (spot,) = _stored(db, _SPOT)
    assert spot["liq_n"] is None
    assert spot["buy_v"] == 2


def test_the_persisted_feed_start_only_moves_earlier(tmp_path: Path) -> None:
    db = _db(tmp_path)
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("a", 120, 4)])
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("b", 300, 4)])
    assert sqlite_store.feed_since(db, _LINEAR) == _T0 + 120 * _S
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("c", 60, 4)])
    assert sqlite_store.feed_since(db, _LINEAR) == _T0 + 60 * _S


def test_a_liquidation_of_another_instrument_is_refused(tmp_path: Path) -> None:
    db = _db(tmp_path)
    eth = _liq("a", 60, 4, iid="ETHUSDT-LINEAR.BYBIT")
    with pytest.raises(ValueError, match="applied under"):
        sqlite_store.apply_liquidations(db, _LINEAR, [eth])
    assert db.execute("SELECT COUNT(*) FROM liquidations_applied").fetchone() == (0,)


def test_two_flushes_of_different_precision_merge_exactly(tmp_path: Path) -> None:
    db = _db(tmp_path)
    sqlite_store.apply_seconds(db, _SPOT, [_sec(0, 10, 5, 0, 1, 0, (1, 2))])
    sqlite_store.apply_seconds(db, _SPOT, [_sec(1, 10, 7, 0, 1, 0, (1, 3))])
    (bar,) = _stored(db, _SPOT)
    assert (bar["buy_v"], bar["size_precision"], bar["buy_n"]) == (57, 3, 2)
    assert is_valid_candle(bar)


def test_a_replayed_liquidation_counts_once(tmp_path: Path) -> None:
    """The feed starts at the day's first ns, so every width's bucket starts at it: all known."""
    db = _db(tmp_path)
    batch = [_liq("a", 0, 4), _liq("b", 1, 6, LiquidatedSide.SHORT)]
    assert sqlite_store.apply_liquidations(db, _LINEAR, batch) == 2
    sqlite_store.apply_seconds(db, _LINEAR, [_sec(0, 10, 2, 0, 1, 0)])
    assert sqlite_store.apply_liquidations(db, _LINEAR, batch) == 0  # catch-up overlap
    assert sqlite_store.apply_liquidations(db, _LINEAR, [batch[0], batch[0]]) == 0
    for bar in (60, 300, 3600, 86_400):
        (stored,) = _stored(db, _LINEAR, bar)
        assert (stored["liq_long_v"], stored["liq_short_v"], stored["liq_n"]) == (4, 6, 2)


def test_a_liquidation_before_its_seconds_is_not_coverage(tmp_path: Path) -> None:
    """The liquidation at 60 s is the feed start and minute 1 starts at it: a known row, unseen."""
    db = _db(tmp_path)
    assert sqlite_store.apply_liquidations(db, _LINEAR, [_liq("a", 60, 4)]) == 1
    assert db.execute("SELECT COUNT(*) FROM candles").fetchone() == (1,)  # the 1m row only
    assert _stored(db, _LINEAR) == []  # no trade: never served
    assert queries.oldest_t(db, _LINEAR, 60, traded_only=False) is None
    assert queries.newest_t(db, _LINEAR, 60) is None
    assert queries.bucket_starts(db, _LINEAR, 60, 0, 1 << 62) == []
    sqlite_store.apply_seconds(db, _LINEAR, [_sec(61, 10, 2, 0, 1, 0)])
    (bar,) = _stored(db, _LINEAR)
    assert (bar["seconds_observed"], bar["liq_long_v"], bar["liq_n"], bar["buy_v"]) == (1, 4, 1, 2)
    assert queries.bucket_starts(db, _LINEAR, 60, 0, 1 << 62) == [_DAY0_MS + 60_000]
    assert bar["price_precision"] == 1  # the liquidation-only row's 0 rescaled up


def test_liquidations_for_an_instrument_without_the_feed_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no liquidation feed"):
        sqlite_store.apply_liquidations(_db(tmp_path), _SPOT, [_liq("a", 0, 4)])


def test_a_rebuild_folds_the_days_liquidations_and_owns_their_ids(tmp_path: Path) -> None:
    db = _db(tmp_path)
    rows = [_sec(0, 10, 2, 0, 1, 0), _sec(61, 11, 0, 3, 0, 1)]
    liqs = [_liq("a", 0, 4), _liq("b", 65, 6, LiquidatedSide.SHORT)]
    day = (_DAY0_MS, _DAY0_MS + 86_400_000)
    for _ in range(2):  # idempotent
        sqlite_store.rebuild(db, _LINEAR, rows, *day, liquidations=liqs, liquidations_since_ns=_T0)
        first, second = _stored(db, _LINEAR)
        assert (first["liq_long_v"], first["liq_n"]) == (4, 1)
        assert (second["liq_short_v"], second["liq_n"]) == (6, 1)
    assert sqlite_store.apply_liquidations(db, _LINEAR, liqs) == 0  # the rebuild owns them
    with pytest.raises(ValueError, match="has a liquidation feed"):
        sqlite_store.rebuild(db, _LINEAR, rows, *day)
    with pytest.raises(ValueError, match="no liquidation feed"):
        sqlite_store.rebuild(db, _SPOT, rows, *day, liquidations=[])
    with pytest.raises(ValueError, match="no liquidation feed"):
        sqlite_store.rebuild(db, _SPOT, rows, *day, liquidations_since_ns=_T0)
    assert sqlite_store.feed_since(db, _LINEAR) == _T0  # the rebuild persisted the start


def test_a_rebuild_missing_a_liquidation_the_live_sink_applied_is_refused(tmp_path: Path) -> None:
    """
    The rebuild read the archive before "b" was flushed; the collector applied "b" live in between.
    Replacing the ids would drop "b" from the bar for good, so the rebuild refuses and rolls back:
    the live minute keeps both (liq_long_v 4 + 6 = 10, liq_n 2) and "b" stays claimed.
    """
    db = _db(tmp_path)
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("a", 0, 4), _liq("b", 30, 6)])
    sqlite_store.apply_seconds(db, _LINEAR, [_sec(1, 10, 2, 0, 1, 0)])
    day = (_DAY0_MS, _DAY0_MS + 86_400_000)
    with pytest.raises(sqlite_store.RebuildRaceError, match="'b'"):
        sqlite_store.rebuild(
            db,
            _LINEAR,
            [_sec(1, 10, 2, 0, 1, 0)],
            *day,
            liquidations=[_liq("a", 0, 4)],
            liquidations_since_ns=_T0,
        )
    (bar,) = _stored(db, _LINEAR)
    assert (bar["liq_long_v"], bar["liq_n"]) == (10, 2)
    assert sqlite_store.apply_liquidations(db, _LINEAR, [_liq("b", 30, 6)]) == 0
    assert not db.in_transaction


def test_a_failed_commit_rolls_back_so_the_connection_takes_the_next_write(
    tmp_path: Path,
) -> None:
    """A COMMIT that raises leaves SQLite's transaction open; `_immediate` rolls it back."""

    class _FailOnce(sqlite_store.sqlite3.Connection):
        fail = True

        def commit(self) -> None:
            if _FailOnce.fail:
                _FailOnce.fail = False
                raise sqlite_store.sqlite3.OperationalError("disk I/O error")
            super().commit()

    db = sqlite_store.sqlite3.connect(str(tmp_path / "c.db"), factory=_FailOnce)
    db.execute("CREATE TABLE t(x INTEGER)")
    with (
        pytest.raises(sqlite_store.sqlite3.OperationalError, match="disk I/O"),
        sqlite_store._immediate(db),
    ):
        db.execute("INSERT INTO t VALUES(1)")
    assert not db.in_transaction
    with sqlite_store._immediate(db):  # would raise "cannot start a transaction within one"
        db.execute("INSERT INTO t VALUES(2)")
    assert db.execute("SELECT x FROM t").fetchall() == [(2,)]


def test_a_rebuild_given_a_row_older_than_the_archive_bound_moves_the_bound(
    tmp_path: Path,
) -> None:
    """Archive bound 120 s, a liquidation at 60 s: no error, the start is 60 s (minute 1 known)."""
    db = _db(tmp_path)
    rows = [_sec(61, 10, 2, 0, 1, 0)]
    day = (_DAY0_MS, _DAY0_MS + 86_400_000)
    sqlite_store.rebuild(
        db,
        _LINEAR,
        rows,
        *day,
        liquidations=[_liq("a", 60, 4)],
        liquidations_since_ns=_T0 + 120 * _S,
    )
    (bar,) = _stored(db, _LINEAR)
    assert (bar["liq_long_v"], bar["liq_n"]) == (4, 1)
    assert sqlite_store.feed_since(db, _LINEAR) == _T0 + 60 * _S


def test_a_rebuild_bounds_by_the_stores_persisted_start_when_earlier(tmp_path: Path) -> None:
    """The live sink saw a liquidation at 0 s (start persisted); the archive's bound is 120 s."""
    db = _db(tmp_path)
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("a", 0, 4)])
    rows = [_sec(1, 10, 2, 0, 1, 0)]
    day = (_DAY0_MS, _DAY0_MS + 86_400_000)
    sqlite_store.rebuild(
        db,
        _LINEAR,
        rows,
        *day,
        liquidations=[_liq("a", 0, 4)],
        liquidations_since_ns=_T0 + 120 * _S,
    )
    (bar,) = _stored(db, _LINEAR)
    assert bar["liq_n"] == 1


def test_a_feed_rebuild_with_nothing_archived_stores_null_never_zero(tmp_path: Path) -> None:
    db = _db(tmp_path)
    rows = [_sec(0, 10, 2, 0, 1, 0)]
    sqlite_store.rebuild(db, _LINEAR, rows, _DAY0_MS, _DAY0_MS + 86_400_000, liquidations=[])
    (bar,) = _stored(db, _LINEAR)
    assert (bar["liq_long_v"], bar["liq_short_v"], bar["liq_n"], bar["buy_v"]) == (
        None,
        None,
        None,
        2,
    )


def test_prune_drops_applied_ids_past_the_catch_up_horizon(tmp_path: Path) -> None:
    db = _db(tmp_path)
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("a", 0, 4)])
    sqlite_store.prune(db, _DAY0_MS + 2 * 86_400_000)
    assert db.execute("SELECT COUNT(*) FROM liquidations_applied").fetchone() == (1,)
    sqlite_store.prune(db, _DAY0_MS + 2 * 86_400_000 + 1)
    assert db.execute("SELECT COUNT(*) FROM liquidations_applied").fetchone() == (0,)


def _three_minutes(db: sqlite3.Connection) -> None:
    """
    Minute 0 at (price, size) precision (1, 2): close 1.0, buys 5, sells 2 (pv 10 x 7 = 70 at
    10^-3); minute 1 at (2, 3): close 1.00, buys 7, sells 9 (pv 100 x 16 = 1600 at 10^-5); minute 2
    at (1, 3): close 1.0, buys 100 (pv 10 x 100 = 1000 at 10^-4).
    """
    sqlite_store.apply_seconds(db, _SPOT, [_sec(0, 10, 5, 2, 1, 1, (1, 2))])
    sqlite_store.apply_seconds(db, _SPOT, [_sec(60, 100, 7, 9, 1, 1, (2, 3))])
    sqlite_store.apply_seconds(db, _SPOT, [_sec(120, 10, 100, 0, 1, 0, (1, 3))])


def test_flow_totals_sum_mixed_precisions_exactly(tmp_path: Path) -> None:
    """
    Minutes 0 and 1, rescaled to size precision 3 and price precision 2: delta 30 - 2 = 28,
    volume 70 + 16 = 86, pv 70 x 10^2 + 1600 = 8600 at 10^-5 (a VWAP of 8600 / (86 x 10^2) = 1.0).
    """
    db = _db(tmp_path)
    _three_minutes(db)
    totals = queries.flow_totals(db, _SPOT, 60, _DAY0_MS + 120_000)
    assert totals == queries.FlowTotals(28, 86, 3, 8600, 5)
    assert queries.flow_totals(db, _SPOT, 60, _DAY0_MS) is None
    assert queries.flow_totals(db, _SPOT, 300, 1 << 62) == queries.FlowTotals(128, 186, 3, 18600, 5)


def test_flow_totals_since_bounds_the_range_below_inclusively(tmp_path: Path) -> None:
    """Minutes 1 and 2 only: delta -2 + 100, volume 16 + 100, pv 1600 + 1000 x 10 at 10^-5."""
    db = _db(tmp_path)
    _three_minutes(db)
    totals = queries.flow_totals(db, _SPOT, 60, 1 << 62, since_ms=_DAY0_MS + 60_000)
    assert totals == queries.FlowTotals(98, 116, 3, 11600, 5)
    assert queries.flow_totals(db, _SPOT, 60, 1 << 62, since_ms=_DAY0_MS + 180_000) is None


def test_flow_totals_add_up_across_utc_days(tmp_path: Path) -> None:
    """The per-day grouping only splits the SQLite sums: two days still total as one range."""
    db = _db(tmp_path)
    sqlite_store.apply_seconds(db, _SPOT, [_sec(0, 10, 5, 2, 1, 1, (1, 3))])
    sqlite_store.apply_seconds(db, _SPOT, [_sec(86_400, 10, 1, 4, 1, 1, (1, 3))])
    assert queries.flow_totals(db, _SPOT, 60, 1 << 62) == queries.FlowTotals(0, 12, 3, 120, 4)


def test_flow_totals_ignore_a_liquidation_only_row(tmp_path: Path) -> None:
    """
    Minute 0 observed at size precision 2: buys 5, sells 2, delta 3. A liquidation-only row at
    minute 5 (`seconds_observed` 0, flow 0, size precision 4) observed nothing: the totals stay at
    size precision 2, not 4.
    """
    db = _db(tmp_path)
    sqlite_store.apply_seconds(db, _LINEAR, [_sec(0, 10, 5, 2, 1, 1, (1, 2))])
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("a", 300, 4, sp=4)])
    assert queries.flow_totals(db, _LINEAR, 60, 1 << 62) == queries.FlowTotals(3, 7, 2, 70, 3)


@pytest.mark.parametrize("column", ["size_precision", "price_precision", "pv"])
def test_flow_totals_refuse_known_flow_without_a_precision_or_pv(
    tmp_path: Path, column: str
) -> None:
    """A corrupt row (flow known, a precision or `pv` null: impossible by the fold) is named."""
    db = _db(tmp_path)
    sqlite_store.apply_seconds(db, _SPOT, [_sec(0, 10, 5, 2, 1, 1, (1, 2))])
    db.execute(f"UPDATE candles SET {column} = NULL WHERE bar_seconds = 60")  # noqa: S608
    with pytest.raises(ValueError, match="no precision or no pv"):
        queries.flow_totals(db, _SPOT, 60, 1 << 62)


# -- the rebuild over a real catalog ---------------------------------------------------------------


def test_rebuild_instrument_folds_the_archived_liquidations(tmp_path: Path) -> None:
    """
    A Bybit linear day: two traded seconds (10 s, 70 s) and two archived liquidations (60 s, 72 s),
    rebuilt from disk. The feed starts at 60 s: minute 0 starts before it (null), minute 1 at it.
    """
    catalog = tmp_path / "catalog"
    writer = ParquetDataCatalog(str(catalog))
    snapshots = [
        make_snapshot(
            _LINEAR,
            bid_prices=[99.0],
            bid_sizes=[1.0],
            ask_prices=[101.0],
            ask_sizes=[1.0],
            buy_volume=0.002,
            buy_count=1,
            open_price=100.0,
            high_price=100.0,
            low_price=100.0,
            close_price=100.0,
            ts_event=_T0 + s * _S,
            price_precision=1,
            size_precision=3,
        )
        for s in (10, 70)
    ]
    writer.write_data(snapshots)
    writer.write_data([_liq("a", 60, 4), _liq("b", 72, 6, LiquidatedSide.SHORT)])
    db_path = str(tmp_path / "candles_bybit.db")
    applied = rebuild_instrument(db_path, str(catalog), _LINEAR, _T0, _T0 + 86_399 * _S)
    assert applied == 2
    db = sqlite_store.connect_rw(db_path)
    first, second = _stored(db, _LINEAR)
    assert (first["buy_v"], first["liq_n"]) == (2, None)
    assert (second["buy_v"], second["liq_long_v"], second["liq_short_v"], second["liq_n"]) == (
        2,
        4,
        6,
        2,
    )
    assert second == {**second, "pv": 1000 * 2, "price_precision": 1, "size_precision": 3}
    assert [second_of(s).buy_volume_units for s in snapshots] == [2, 2]


def _linear_second(s: int) -> object:
    """Return a traded Bybit linear second `s` after day 0's start: buys 2 units at 100.0."""
    return make_snapshot(
        _LINEAR,
        bid_prices=[99.0],
        bid_sizes=[1.0],
        ask_prices=[101.0],
        ask_sizes=[1.0],
        buy_volume=0.002,
        buy_count=1,
        open_price=100.0,
        high_price=100.0,
        low_price=100.0,
        close_price=100.0,
        ts_event=_T0 + s * _S,
        price_precision=1,
        size_precision=3,
    )


# 15:00:30 on day 1, the feed's first archived liquidation, and the times around it (s after day 0).
_DAY = 86_400
_FIRST = _DAY + 15 * 3600 + 30


def _feed_start_day() -> tuple[list[object], list[Liquidation]]:
    """
    Day 0 trades at 00:00:10 with no liquidation archived. Day 1 trades at 00:00:10, 15:00:10,
    15:00:50, 15:01:10 and 15:03:20; its liquidations are the feed's first at 15:00:30 (4 units
    LONG) and one at 15:01:20 (6 units SHORT).
    """
    seconds = [_linear_second(s) for s in (10, _DAY + 10, _FIRST - 20, _FIRST + 20)]
    seconds += [_linear_second(s) for s in (_FIRST + 40, _FIRST + 170)]
    liquidations = [_liq("first", _FIRST, 4), _liq("next", _FIRST + 50, 6, LiquidatedSide.SHORT)]
    return seconds, liquidations


def test_the_feed_starts_mid_minute_so_its_minute_hour_and_day_are_null(tmp_path: Path) -> None:
    """
    Hand-computed (feed start 15:00:30 on day 1): the 1m bucket 15:00 starts before it and is null,
    so its liquidation is not counted; 15:01 counts the SHORT 6 (liq_n 1); 15:03 is a known 0;
    day 1's 00:00 minute, the 15:00 hour and both 1D bars start before it and are null, never 0.
    """
    catalog = tmp_path / "catalog"
    writer = ParquetDataCatalog(str(catalog))
    seconds, liquidations = _feed_start_day()
    writer.write_data(seconds)
    writer.write_data(liquidations)
    db_path = str(tmp_path / "candles_bybit.db")
    applied = rebuild_instrument(db_path, str(catalog), _LINEAR, _T0, _T0 + (2 * _DAY - 1) * _S)
    assert applied == 6
    db = sqlite_store.connect_rw(db_path)
    minutes = {
        (bar["t"] - _DAY0_MS) // 1000: (bar["liq_short_v"], bar["liq_n"])
        for bar in _stored(db, _LINEAR)
    }
    m1500 = _FIRST - 30
    assert minutes == {
        0: (None, None),
        _DAY: (None, None),
        m1500: (None, None),
        m1500 + 60: (6, 1),
        m1500 + 180: (0, 0),
    }
    hours = {(bar["t"] - _DAY0_MS) // 1000: bar["liq_n"] for bar in _stored(db, _LINEAR, 3600)}
    assert hours == {0: None, _DAY: None, m1500: None}
    days = {(bar["t"] - _DAY0_MS) // 1000: bar["liq_n"] for bar in _stored(db, _LINEAR, _DAY)}
    assert days == {0: None, _DAY: None}
    flow = {(bar["t"] - _DAY0_MS) // 1000: bar["buy_v"] for bar in _stored(db, _LINEAR)}
    assert flow == {0: 2, _DAY: 2, m1500: 4, m1500 + 60: 2, m1500 + 180: 2}  # known throughout


def _aggregates(db: sqlite_store.sqlite3.Connection) -> list[tuple]:
    columns = ", ".join(("bar_seconds", "t", "seconds_observed", *AGGREGATE_KEYS))
    query = f"SELECT {columns} FROM candles ORDER BY bar_seconds, t"  # noqa: S608 -- constant
    return db.execute(query).fetchall()


def test_the_live_sink_and_a_rebuild_of_the_same_day_store_the_same_rows(tmp_path: Path) -> None:
    """
    The same seconds and liquidations applied live (capture's order per flush: liquidations, then
    seconds) and then rebuilt from the archive: every bucket of every width, its liquidation, flow
    and coverage columns, identical -- the feed start straddling 15:00 null on both paths.
    """
    seconds, liquidations = _feed_start_day()
    rows = [second_of(s) for s in seconds]
    live = sqlite_store.connect_rw(str(tmp_path / "live.db"))
    flushes = ((rows[:2], []), (rows[2:4], liquidations[:1]), (rows[4:], liquidations[1:]))
    for flush_rows, flush_liquidations in flushes:
        sqlite_store.apply_liquidations(live, _LINEAR, flush_liquidations)
        sqlite_store.apply_seconds(live, _LINEAR, flush_rows)
    catalog = tmp_path / "catalog"
    writer = ParquetDataCatalog(str(catalog))
    writer.write_data(seconds)
    writer.write_data(liquidations)
    rebuilt_path = str(tmp_path / "rebuilt.db")
    rebuild_instrument(rebuilt_path, str(catalog), _LINEAR, _T0, _T0 + (2 * _DAY - 1) * _S)
    rebuilt = sqlite_store.connect_rw(rebuilt_path)
    assert _aggregates(live) == _aggregates(rebuilt)
    assert sqlite_store.feed_since(live, _LINEAR) == sqlite_store.feed_since(rebuilt, _LINEAR)


def test_a_second_connection_cannot_write_inside_a_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The read-merge-write is one `BEGIN IMMEDIATE` transaction: between its SELECT and its REPLACE
    another connection's write is refused (`database is locked`), never interleaved.
    """
    path = str(tmp_path / "c.db")
    db = sqlite_store.connect_rw(path)
    other = sqlite_store.sqlite3.connect(path, timeout=0)
    attempts: list[str] = []
    read = sqlite_store._stored_buckets

    def read_then_race(*args: object) -> dict:
        stored = read(*args)  # type: ignore[arg-type]
        try:
            other.execute("INSERT INTO built_through VALUES('race', 1)")
            other.commit()
            attempts.append("interleaved")
        except sqlite_store.sqlite3.OperationalError as exc:
            attempts.append(str(exc))
        return stored

    monkeypatch.setattr(sqlite_store, "_stored_buckets", read_then_race)
    sqlite_store.apply_seconds(db, _SPOT, [_sec(0, 10, 2, 0, 1, 0)])
    assert set(attempts) == {"database is locked"}
    assert other.execute(
        "SELECT COUNT(*) FROM built_through WHERE instrument_id = 'race'"
    ).fetchone() == (0,)
    other.close()


def test_write_buckets_outside_a_transaction_is_refused(tmp_path: Path) -> None:
    db = _db(tmp_path)
    folded = fold_rows([_sec(0, 10, 2, 0, 1, 0)], bars=(60,))
    with pytest.raises(RuntimeError, match="BEGIN IMMEDIATE"):
        sqlite_store.write_buckets(db, _SPOT, folded)


def test_the_feed_start_query_reads_the_persisted_row_and_none_without_one(
    tmp_path: Path,
) -> None:
    """
    One indexed SELECT: the row `apply_liquidations` lowered; None for an unknown id or a file
    without the table (not migrated yet: a read-only reader cannot migrate it).
    """
    db = _db(tmp_path)
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("a", 30, 4)])
    assert queries.liquidation_feed_since(db, _LINEAR) == _T0 + 30 * _S
    assert queries.liquidation_feed_since(db, "ETHUSDT-LINEAR.BYBIT") is None
    unmigrated = sqlite_store.sqlite3.connect(":memory:")
    assert queries.liquidation_feed_since(unmigrated, _LINEAR) is None


# -- the liquidation read (Story 33.4) -------------------------------------------------------------


def test_liquidation_window_serves_untraded_buckets_and_newest_row_t_sees_them(
    tmp_path: Path,
) -> None:
    """
    The feed starts at minute 0 (a liquidation at 0 s): minute 0 traded and had a liquidation,
    minute 1 only a liquidation (no trade, `o` null: `window` never serves it, D-162), minute 2 only
    a trade (a known 0). All three are served, oldest first, inside `[start_ms, end_ms)`.
    """
    db = _db(tmp_path)
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("a", 0, 4)])
    sqlite_store.apply_seconds(db, _LINEAR, [_sec(1, 10, 2, 0, 1, 0)])
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("b", 70, 6, LiquidatedSide.SHORT)])
    sqlite_store.apply_seconds(db, _LINEAR, [_sec(121, 10, 2, 0, 1, 0)])
    rows = queries.liquidation_window(db, _LINEAR, 60, _DAY0_MS, _DAY0_MS + 180_000)
    assert [(r["t"] - _DAY0_MS, r["liq_long_v"], r["liq_short_v"], r["liq_n"]) for r in rows] == [
        (0, 4, 0, 1),
        (60_000, 0, 6, 1),
        (120_000, 0, 0, 0),
    ]
    assert [r["size_precision"] for r in rows] == [3, 3, 3]
    assert [b["t"] - _DAY0_MS for b in _stored(db, _LINEAR)] == [0, 120_000]  # traded only
    assert queries.liquidation_window(db, _LINEAR, 60, _DAY0_MS + 60_000, _DAY0_MS + 120_000)[0][
        "t"
    ] == (_DAY0_MS + 60_000)
    assert queries.newest_row_t(db, _LINEAR, 60, _DAY0_MS + 120_000) == _DAY0_MS + 60_000
    assert queries.newest_row_t(db, _LINEAR, 60, _DAY0_MS) is None


def test_liquidation_window_of_an_instrument_without_the_feed_reads_null(tmp_path: Path) -> None:
    db = _db(tmp_path)
    sqlite_store.apply_seconds(db, _SPOT, [_sec(0, 10, 2, 0, 1, 0)])
    (row,) = queries.liquidation_window(db, _SPOT, 60, _DAY0_MS, _DAY0_MS + 60_000)
    assert (row["liq_long_v"], row["liq_short_v"], row["liq_n"]) == (None, None, None)


def _ranged(s: int, high: float, low: float, close: float) -> SecondOHLC:
    """One traded second at (price, size) precision (1, 3) with its own high and low."""
    return SecondOHLC(
        _T0 + s * _S, close, high, low, close, 0.001, 0.0, 1, 3, round(close * 10), 1, 0, 1, 0
    )


def test_session_hlc_is_the_highest_high_lowest_low_and_last_traded_close(tmp_path: Path) -> None:
    """
    Minute 0: h 10.5, l 9.8, c 10.0; minute 1: h 11.0, l 10.1, c 10.9; minute 2 only a liquidation
    (no trade, `o` null): over the three minutes H 11.0, L 9.8 and the last *traded* close 10.9.
    """
    db = _db(tmp_path)
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("a", 0, 4)])
    sqlite_store.apply_seconds(db, _LINEAR, [_ranged(1, 10.5, 9.8, 10.0)])
    sqlite_store.apply_seconds(db, _LINEAR, [_ranged(61, 11.0, 10.1, 10.9)])
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("b", 130, 6)])
    end = _DAY0_MS + 180_000
    assert queries.session_hlc(db, _LINEAR, 60, _DAY0_MS, end) == (11.0, 9.8, 10.9)
    assert queries.session_hlc(db, _LINEAR, 60, _DAY0_MS + 60_000, end) == (11.0, 10.1, 10.9)
    assert queries.session_hlc(db, _LINEAR, 60, _DAY0_MS, _DAY0_MS + 60_000) == (10.5, 9.8, 10.0)


def test_session_hlc_of_a_range_without_a_trade_is_none(tmp_path: Path) -> None:
    db = _db(tmp_path)
    sqlite_store.apply_liquidations(db, _LINEAR, [_liq("a", 0, 4)])
    sqlite_store.apply_seconds(db, _LINEAR, [_ranged(61, 11.0, 10.1, 10.9)])
    assert queries.session_hlc(db, _LINEAR, 60, _DAY0_MS, _DAY0_MS + 60_000) is None
    assert queries.session_hlc(db, _SPOT, 60, _DAY0_MS, _DAY0_MS + 180_000) is None
