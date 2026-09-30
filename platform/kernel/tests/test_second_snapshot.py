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
`kernel.second_snapshot`: the `SecondRow` protocol (Story 24.1) and the one encoder/decoder of the
exact integer layout (Story 30.2).

`SecondRow` is the one name capture and candles share for a duck-typed second: both shapes that
cross the `SecondSink` port must satisfy it. The layout tests prove the encoder refuses every value
it cannot hold exactly, the decoder is strict, and encode -> Parquet -> decode (and the Redis JSON
route) returns identical `Price`/`Quantity` values over a seeded generator of books. The columnar
batch encoder (Story 28.2) is proven equal to the dict path: the same table, byte-identical Parquet
files through `ParquetDataCatalog`, the same refusals.
"""

import functools
import hashlib
import json
import random
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kernel.second_snapshot import INT64_MAX
from kernel.second_snapshot import INT64_MIN
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import LegacySnapshotLayoutError
from kernel.second_snapshot import SecondOHLC
from kernel.second_snapshot import SecondRow
from kernel.second_snapshot import Side
from kernel.second_snapshot import SnapshotEncodingError
from kernel.second_snapshot import SnapshotTradeUnits
from kernel.second_snapshot import decode_book_prices
from kernel.second_snapshot import encode_book_prices
from kernel.second_snapshot import snapshots_to_record_batch
from kernel.second_snapshot import unit_float
from kernel.second_snapshot import unit_floats
from kernel.second_snapshot import units_of
from kernel.tests.snapshot_factory import make_snapshot
from kernel.tests.snapshot_factory import wire
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import FIXED_PRECISION
from nautilus_trader.model.objects import PRICE_MAX
from nautilus_trader.model.objects import QUANTITY_MAX
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.serialization.arrow import serializer
from nautilus_trader.serialization.arrow.serializer import ArrowSerializer
from nautilus_trader.serialization.arrow.serializer import make_dict_deserializer
from nautilus_trader.serialization.arrow.serializer import make_dict_serializer


_IID = "BTC-USD-PERP.DYDX"


def _ohlc() -> SecondOHLC:
    return SecondOHLC(1_000_000_000, 100.0, 101.0, 99.0, 100.5, 1.0, 0.5)


def _snapshot() -> DydxSecondSnapshot:
    return make_snapshot(
        _IID,
        bid_prices=[99.0],
        bid_sizes=[1.0],
        ask_prices=[101.0],
        ask_sizes=[1.0],
        buy_volume=1.0,
        sell_volume=0.5,
        buy_count=1,
        sell_count=1,
        ts_event=1_000_000_000,
        open_price=100.0,
        high_price=101.0,
        low_price=99.0,
        close_price=100.5,
    )


def _volume(row: SecondRow) -> float:
    """Typed against the protocol, so mypy checks every call site below satisfies it."""
    return row.buy_volume + row.sell_volume


def test_second_ohlc_satisfies_the_protocol() -> None:
    assert isinstance(_ohlc(), SecondRow)
    assert _volume(_ohlc()) == 1.5


def test_the_live_snapshot_satisfies_the_protocol() -> None:
    assert isinstance(_snapshot(), SecondRow)
    assert _volume(_snapshot()) == 1.5


def test_the_two_shapes_carry_the_same_per_second_values() -> None:
    fields = ("ts_event", "open_price", "high_price", "low_price", "close_price")
    assert [getattr(_ohlc(), f) for f in fields] == [getattr(_snapshot(), f) for f in fields]


def test_a_row_missing_a_field_does_not_satisfy_the_protocol() -> None:
    """A stand-in that forgot `sell_volume` is caught here, not at the flush that folds it."""
    assert not isinstance(SimpleNamespace(ts_event=0, buy_volume=1.0), SecondRow)


# -- units ---------------------------------------------------------------------------------------


def test_units_are_the_raw_scaled_down_exactly() -> None:
    assert units_of(Price.from_str("85891.9").raw, 1) == 858_919
    assert units_of(Price.from_str("-0.5").raw, 1) == -5
    assert units_of(Quantity.from_str("0.001").raw, 3) == 1
    assert units_of(Quantity.from_str("7").raw, 0) == 7


def test_a_raw_finer_than_the_precision_is_refused_never_rounded() -> None:
    with pytest.raises(SnapshotEncodingError, match="not exact at precision 1"):
        units_of(Price.from_str("85891.95").raw, 1)


def test_a_precision_outside_this_build_is_refused() -> None:
    for precision in (-1, FIXED_PRECISION + 1):
        with pytest.raises(SnapshotEncodingError, match="precision"):
            units_of(0, precision)


def test_units_outside_int64_are_refused() -> None:
    raw = (INT64_MAX + 1) * 10 ** (FIXED_PRECISION - 9)
    with pytest.raises(SnapshotEncodingError, match="int64"):
        units_of(raw, 9)
    assert units_of(INT64_MAX * 10 ** (FIXED_PRECISION - 9), 9) == INT64_MAX


def test_one_float_definition_for_the_scalar_and_the_array_path() -> None:
    rng = random.Random(3002)  # noqa: S311 -- a deterministic fixture, not cryptography
    values = [rng.randint(-(10**15), 10**15) for _ in range(2_000)]
    precisions = [rng.randint(0, 9) for _ in values]
    scalar = [unit_float(u, p) for u, p in zip(values, precisions, strict=True)]
    array = unit_floats(np.array(values), np.array(precisions))
    assert scalar == array.tolist()
    assert unit_float(858_919, 1) == 85891.9  # no float noise: the nearest double to the decimal


# -- the gap layout ------------------------------------------------------------------------------


def test_the_spec_example_encodes_to_gaps() -> None:
    row = wire(
        bid_prices=[100.5, 100.3, 99.9],
        bid_sizes=[1, 1, 1],
        ask_prices=[100.7, 101.0],
        ask_sizes=[1, 1],
        price_precision=1,
        size_precision=0,
    )
    assert (row["bid_prices"], row["ask_prices"]) == ([1005, 2, 4], [1007, 3])
    assert decode_book_prices([1005, 2, 4], "bid") == [1005, 1003, 999]
    assert decode_book_prices([1007, 3], "ask") == [1007, 1010]


def test_an_empty_side_is_empty_both_ways() -> None:
    assert encode_book_prices([], "bid") == []
    assert decode_book_prices([], "ask") == []
    row = DydxSecondSnapshot.from_dict(wire(ask_prices=[1.0], ask_sizes=[1.0]))
    assert (row.bid_prices, row.bid_price_units, row.exact.bid_prices) == ([], [], ())


@pytest.mark.parametrize(
    ("units", "side"),
    [([100, 100], "bid"), ([100, 101], "bid"), ([100, 99], "ask"), ([5, 5], "ask")],
)
def test_a_non_positive_gap_is_refused(units: list[int], side: Side) -> None:
    with pytest.raises(SnapshotEncodingError, match="non-positive gap"):
        encode_book_prices(units, side)


def test_an_unsorted_book_cannot_even_be_built() -> None:
    with pytest.raises(SnapshotEncodingError):
        make_snapshot(bid_prices=[99.0, 100.0], bid_sizes=[1, 1])


def test_a_stored_non_positive_gap_is_refused_on_decode() -> None:
    row = wire(bid_prices=[100.0, 99.0], bid_sizes=[1, 1])
    row["bid_prices"] = [1_000_000, 0]
    with pytest.raises(ValueError, match="non-positive stored gap"):
        DydxSecondSnapshot.from_dict(row)


# -- strict decode ---------------------------------------------------------------------------------


def test_a_row_without_precisions_is_a_legacy_row_naming_the_migration() -> None:
    row = wire(bid_prices=[1.0], bid_sizes=[1.0])
    for key in ("price_precision", "size_precision"):
        legacy = {k: v for k, v in row.items() if k != key}
        with pytest.raises(LegacySnapshotLayoutError, match="migrate_snapshot_ints"):
            DydxSecondSnapshot.from_dict(legacy)
    with pytest.raises(LegacySnapshotLayoutError):
        DydxSecondSnapshot.from_dict({**row, "price_precision": None})


_WIRE_KEYS = tuple(wire(bid_prices=[1.0], bid_sizes=[1.0]))


@pytest.mark.parametrize("key", _WIRE_KEYS)
def test_every_field_is_required_on_decode(key: str) -> None:
    """Story 31.2: `from_dict` defaults nothing -- a pre-OHLC file is migrated before any read."""
    row = wire(bid_prices=[1.0], bid_sizes=[1.0])
    missing = {k: v for k, v in row.items() if k != key}
    with pytest.raises((KeyError, LegacySnapshotLayoutError)):
        DydxSecondSnapshot.from_dict(missing)


def test_the_required_fields_are_the_whole_wire_row() -> None:
    """The parametrization above covers every key the encoder writes."""
    assert set(_WIRE_KEYS) == set(DydxSecondSnapshot.to_dict(_snapshot()))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("buy_volume", 1.0),
        ("open_price", 100.0),
        ("buy_count", True),
        ("ts_event", 1.5),
        ("price_precision", 4.0),
        ("bid_prices", [10000.0]),
        ("ask_sizes", [True]),
    ],
)
def test_a_float_or_bool_in_an_integer_field_is_refused(key: str, value: object) -> None:
    row = wire(bid_prices=[1.0], bid_sizes=[1.0], ask_prices=[2.0], ask_sizes=[1.0])
    with pytest.raises(ValueError):
        DydxSecondSnapshot.from_dict({**row, key: value})


def test_a_missing_key_is_refused() -> None:
    row = wire(bid_prices=[1.0], bid_sizes=[1.0])
    del row["close_price"]
    with pytest.raises(KeyError):
        DydxSecondSnapshot.from_dict(row)


def test_sizes_must_match_their_prices_and_never_be_negative() -> None:
    with pytest.raises(SnapshotEncodingError, match="prices but"):
        make_snapshot(bid_prices=[1.0], bid_sizes=[])
    row = wire(bid_prices=[1.0], bid_sizes=[1.0])
    with pytest.raises(SnapshotEncodingError, match="negative"):
        DydxSecondSnapshot.from_dict({**row, "bid_sizes": [-1]})


def test_a_trade_count_past_its_uint32_column_is_refused_at_encode() -> None:
    row = wire(bid_prices=[1.0], bid_sizes=[1.0])
    DydxSecondSnapshot.from_dict({**row, "buy_count": 2**32 - 1})
    with pytest.raises(SnapshotEncodingError, match="uint32"):
        DydxSecondSnapshot.from_dict({**row, "buy_count": 2**32})


def test_a_stored_precision_outside_this_build_is_refused_on_the_array_path() -> None:
    with pytest.raises(ValueError, match="precision"):
        unit_floats(np.array([1]), np.array([FIXED_PRECISION + 1]))


# -- decoded values --------------------------------------------------------------------------------


def test_decoded_floats_and_exact_values() -> None:
    row = DydxSecondSnapshot.from_dict(
        wire(
            bid_prices=[85891.9],
            bid_sizes=[0.125],
            ask_prices=[85892.0],
            ask_sizes=[3],
            buy_volume=0.3,
            close_price=85891.9,
            price_precision=1,
            size_precision=3,
        )
    )
    assert row.bid_prices == [85891.9]  # never 85891.90000000001
    assert row.buy_volume == 0.3
    assert (row.open_price, row.close_price) == (None, 85891.9)
    assert row.exact.bid_prices == (Price.from_str("85891.9"),)
    assert row.exact.bid_sizes == (Quantity.from_str("0.125"),)
    assert row.exact.close_price == Price.from_str("85891.9")
    assert row.exact.sell_volume == Quantity.from_str("0.000")
    assert row.exact is row.exact  # built once


def test_as_floats_is_the_indicator_view() -> None:
    row = _snapshot()
    view = row.as_floats()
    assert view["bid_prices"] == [99.0]
    assert (view["buy_volume"], view["close_price"], view["buy_count"]) == (1.0, 100.5, 1)


def _catalog_round_trip(tmp_path: Path, rows: list[DydxSecondSnapshot]) -> list[DydxSecondSnapshot]:
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data(rows)
    read = catalog.query(DydxSecondSnapshot, identifiers=[_IID])
    return [r.data if hasattr(r, "data") else r for r in read]


def test_parquet_and_redis_decode_identically(tmp_path: Path) -> None:
    rows = [
        make_snapshot(
            _IID,
            bid_prices=[100.5, 100.25],
            bid_sizes=[1.5, 2],
            ask_prices=[100.75],
            ask_sizes=[0.001],
            buy_volume=2.5,
            buy_count=2,
            open_price=100.5,
            high_price=100.75,
            low_price=100.5,
            close_price=100.75,
            ts_event=1_000,
            price_precision=2,
            size_precision=3,
        )
    ]
    from_parquet = _catalog_round_trip(tmp_path, rows)
    from_redis = [
        DydxSecondSnapshot.from_dict(entry)
        for entry in json.loads(json.dumps([DydxSecondSnapshot.to_dict(r) for r in rows]))
    ]
    assert [DydxSecondSnapshot.to_dict(r) for r in from_parquet] == [
        DydxSecondSnapshot.to_dict(r) for r in from_redis
    ]
    assert from_parquet[0].exact == from_redis[0].exact == rows[0].exact


# -- the property test: seeded generated books ------------------------------------------------------

_ITERATIONS = 400


def _level_prices(rng: random.Random, n: int, best: int, step: int) -> list[int]:
    out, level = [], best
    for _ in range(n):
        out.append(level)
        level += step * rng.randint(1, 1_000)
    return out


def _size_units(rng: random.Random, precision: int) -> int:
    """Up to QUANTITY_MAX (and so its raw) where int64 units allow it, else up to int64."""
    ceiling = min(int(QUANTITY_MAX) * 10**precision, INT64_MAX)
    return rng.choice((rng.randint(0, 10**6), rng.randint(0, ceiling)))


def _generated(rng: random.Random, i: int) -> DydxSecondSnapshot:
    pp, sp = rng.randint(0, 9), rng.randint(0, 9)
    top = min(int(PRICE_MAX) * 10**pp, 10**15)
    n_bids, n_asks = rng.randint(1, 50), rng.randint(1, 50)
    best_bid = rng.randint(n_bids * 1_000 + 1, top // 2)
    bids = _level_prices(rng, n_bids, best_bid, -1)
    asks = _level_prices(rng, n_asks, best_bid + rng.randint(1, 100), 1)

    def level(price_units: int) -> tuple[Price, Quantity]:
        return (
            Price.from_raw(price_units * 10 ** (FIXED_PRECISION - pp), pp),
            Quantity.from_raw(_size_units(rng, sp) * 10 ** (FIXED_PRECISION - sp), sp),
        )

    traded = rng.random() < 0.7
    close = asks[0] if traded else None
    trades = SnapshotTradeUnits(
        close, close, close, close, _size_units(rng, sp), _size_units(rng, sp), 3, 4
    )
    return DydxSecondSnapshot.from_levels(
        InstrumentId.from_str(_IID),
        pp,
        sp,
        [level(p) for p in bids],
        [level(p) for p in asks],
        trades,
        ts_event=i,
        ts_init=i,
    )


def test_generated_books_round_trip_exactly_through_parquet(tmp_path: Path) -> None:
    rng = random.Random(30_2)  # noqa: S311 -- a deterministic generator, not cryptography
    rows = [_generated(rng, i) for i in range(_ITERATIONS)]
    batch = make_dict_serializer(DydxSecondSnapshot.schema())(rows)
    path = tmp_path / "generated.parquet"
    pq.write_table(pa.Table.from_batches([batch]), path)
    decoded = make_dict_deserializer(DydxSecondSnapshot)(pq.read_table(path))
    assert len(decoded) == len(rows)
    for want, got in zip(rows, decoded, strict=True):
        assert _raws(got) == _raws(want)
        assert got.bid_prices == [unit_float(u, want.price_precision) for u in want.bid_price_units]


def test_generated_books_reach_fifty_levels_and_every_precision() -> None:
    rng = random.Random(30_2)  # noqa: S311
    rows = [_generated(rng, i) for i in range(_ITERATIONS)]
    assert max(len(r.bid_prices) for r in rows) == 50
    assert {r.price_precision for r in rows} == set(range(10))
    assert {r.size_precision for r in rows} == set(range(10))


def _raws(row: DydxSecondSnapshot) -> list:
    exact = row.exact
    values = [*exact.bid_prices, *exact.bid_sizes, *exact.ask_prices, *exact.ask_sizes]
    values += [exact.open_price, exact.close_price, exact.buy_volume, exact.sell_volume]
    return [None if v is None else (v.raw, v.precision) for v in values]


def test_a_size_whose_units_exceed_int64_is_refused() -> None:
    """QUANTITY_MAX at size precision 9 is 3.4e22 units: past int64 (the Known limit)."""
    huge = Quantity.from_raw(int(QUANTITY_MAX) * 10**FIXED_PRECISION, 0)
    assert units_of(huge.raw, 0) == int(QUANTITY_MAX)  # fits at precision 0
    with pytest.raises(SnapshotEncodingError, match="int64"):
        DydxSecondSnapshot.from_levels(
            InstrumentId.from_str(_IID),
            2,
            9,
            [(Price.from_str("1.00"), huge)],
            [],
            SnapshotTradeUnits(None, None, None, None, 0, 0, 0, 0),
            ts_event=0,
            ts_init=0,
        )


# -- the columnar batch encoder (Story 28.2) --------------------------------------------------------

_BATCH_IDS = ("BTC-USD-PERP.DYDX", "ETHUSDT-LINEAR.BYBIT", "SOL-USD-PERP.HYPERLIQUID")


def _batch_row(n: int, iid: str) -> DydxSecondSnapshot:
    """Row `n` of `iid`: depth 0..20 a side (an empty side included), alternating set/None OHLC."""
    bids, asks = n % 21, (n * 7) % 21
    traded = n % 2 == 0
    price = 50_000 + n if traded else None
    return DydxSecondSnapshot(
        InstrumentId.from_str(iid),
        price_precision=1 + n % 3,
        size_precision=n % 5,
        bid_price_units=[49_990 - 3 * k for k in range(bids)],
        bid_size_units=[k * n for k in range(bids)],
        ask_price_units=[50_010 + 2 * k for k in range(asks)],
        ask_size_units=[k + n for k in range(asks)],
        buy_volume_units=7 * n if traded else 0,
        sell_volume_units=INT64_MAX if n == 5 else 0,
        buy_count=2**32 - 1 if n == 3 else n,
        sell_count=n % 4,
        ts_event=n * 1_000_000_000 + 500_000_000,
        ts_init=n * 1_000_000_000 + 1_200_000_000,
        open_price_units=price,
        high_price_units=None if price is None else price + 9,
        low_price_units=None if price is None else INT64_MIN,
        close_price_units=price,
    )


def _batch_rows() -> list[DydxSecondSnapshot]:
    """Several instruments interleaved, as a flush hands them over (ts_init ascending per id)."""
    return [_batch_row(n, iid) for n in range(40) for iid in _BATCH_IDS]


def _dict_path_table(rows: list[DydxSecondSnapshot]) -> pa.Table:
    """Build what `serialize_batch` built before Story 28.2: one `to_dict` batch per row."""
    batches = [ArrowSerializer.serialize(row, DydxSecondSnapshot) for row in rows]
    return pa.Table.from_batches(batches, schema=batches[0].schema)


@pytest.mark.parametrize("source", ["fixture", "generated"])
def test_the_batch_encoder_builds_the_dict_paths_table(source: str) -> None:
    rng = random.Random(28_2)  # noqa: S311 -- a deterministic generator, not cryptography
    rows = (
        _batch_rows() if source == "fixture" else [_generated(rng, i) for i in range(_ITERATIONS)]
    )
    batch = snapshots_to_record_batch(rows)
    reference = _dict_path_table(rows)
    assert batch.schema.equals(reference.schema, check_metadata=True)
    # Equal values; the dict path's one-entry dictionary per row chunk is unified by combining.
    assert pa.Table.from_batches([batch]).combine_chunks().equals(reference.combine_chunks())


def test_serialize_batch_takes_the_registered_batch_encoder() -> None:
    table = ArrowSerializer.serialize_batch(_batch_rows(), DydxSecondSnapshot)
    assert table.num_rows == len(_batch_rows())
    assert table.column("instrument_id").num_chunks == 1  # one batch, not one per row


def _write_catalog(root: Path, rows: list[DydxSecondSnapshot]) -> dict[str, str]:
    """Write `rows` through a fresh `ParquetDataCatalog`; return each file's sha256 by path."""
    ParquetDataCatalog(str(root)).write_data(rows)
    files = sorted(p for p in root.rglob("*.parquet"))
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


@pytest.mark.parametrize("compression", [None, "zstd"])
def test_the_batch_encoder_writes_byte_identical_parquet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compression: str | None
) -> None:
    """`None`: this process's `pq.write_table` default; `zstd`: what capture's writer sets."""
    if compression is not None:
        monkeypatch.setattr(
            pq, "write_table", functools.partial(pq.write_table, compression=compression)
        )
    rows = _batch_rows()
    columnar = _write_catalog(tmp_path / "columnar", rows)
    monkeypatch.delitem(serializer._ARROW_BATCH_ENCODERS, DydxSecondSnapshot)  # the dict path
    reference = _write_catalog(tmp_path / "dict", rows)
    assert len(columnar) == len(_BATCH_IDS)
    assert columnar == reference


def test_the_batch_encoded_files_decode_to_the_rows(tmp_path: Path) -> None:
    rows = _batch_rows()
    _write_catalog(tmp_path, rows)
    decoded = [
        row
        for path in sorted(tmp_path.rglob("*.parquet"))
        for row in ArrowSerializer.deserialize(DydxSecondSnapshot, pq.read_table(path))
    ]
    by_id = sorted(rows, key=lambda r: (r.instrument_id.value, r.ts_init))
    assert [DydxSecondSnapshot.to_dict(r) for r in decoded] == [
        DydxSecondSnapshot.to_dict(r) for r in by_id
    ]


def test_an_empty_batch_is_the_schema_with_no_rows() -> None:
    batch = snapshots_to_record_batch([])
    assert batch.num_rows == 0
    assert batch.schema.equals(DydxSecondSnapshot.schema(), check_metadata=True)


def _mutated(**attributes: object) -> DydxSecondSnapshot:
    """Return a valid row, attributes overwritten after `__init__`'s checks (what the encoder sees)."""
    row = _batch_row(4, _IID)
    for name, value in attributes.items():
        setattr(row, name, value)
    return row


@pytest.mark.parametrize(
    ("attributes", "match"),
    [
        ({"buy_count": 2**32}, "fit its column"),
        ({"sell_volume_units": INT64_MAX + 1}, "fit its column"),
        ({"price_precision": 256}, "fit its column"),
        ({"size_precision": -1}, "fit its column"),
        ({"bid_price_units": [1, 2]}, "non-positive gap"),
        ({"bid_price_units": [INT64_MAX, INT64_MIN]}, "outside int64"),
        ({"ask_price_units": [INT64_MAX, INT64_MIN]}, "non-positive gap"),
    ],
)
def test_the_batch_encoder_refuses_what_the_dict_path_refuses(
    attributes: dict[str, object], match: str
) -> None:
    row = _mutated(**attributes)
    with pytest.raises(SnapshotEncodingError, match=match):
        snapshots_to_record_batch([_batch_row(2, _IID), row])
    # The dict path refuses too: by raising, or by Nautilus's `dicts_to_record_batch` returning
    # no batch at all (it prints the error), never by encoding the value.
    try:
        refused = make_dict_serializer(DydxSecondSnapshot.schema())([row]) is None
    except SnapshotEncodingError:
        refused = True
    assert refused


def test_the_batch_encoder_refuses_more_instruments_than_the_dictionary_index_holds() -> None:
    """129 distinct ids widen pyarrow's int8 dictionary index; the schema's int8 refuses it, named."""
    rows = [_batch_row(1, f"C{n}-USD-PERP.DYDX") for n in range(129)]
    assert snapshots_to_record_batch(rows[:128]).num_rows == 128
    with pytest.raises(SnapshotEncodingError, match="fit its column"):
        snapshots_to_record_batch(rows)
    # The dict path refuses the same batch (Nautilus prints the error and returns no batch).
    assert make_dict_serializer(DydxSecondSnapshot.schema())(rows) is None
