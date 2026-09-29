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
Proof that the Story 23.2 move changed no published byte (spine AD-D12, MR1).

The fixtures were recorded with the pre-move code (the two modules' previous home in the collector
core, run in the `story-23-1/collector` image at `7bd64952fd`; both re-export shims were deleted in
Story 24.2):

- `pre_move_catalog/`: `ParquetDataCatalog.write_data` (with the collector's zstd patch applied)
  of `_snapshot_with_trades()` and `_open_interest()` below, one call each;
- `snapshots_raw.json`: `json.dumps([DydxSecondSnapshot.to_dict(s) for s in snapshots])`, the
  collector's `publish_snapshot_batch` encoding (`capture/infrastructure/redis_stream.py`), of `_snapshot_with_trades()` and
  `_snapshot_without_trades()`;
- `archive_gap_line.jsonl`: `record_gap(catalog, "BTC-USD-PERP.DYDX", T, T + 300 s,
  "quarantined", 7)`.

Parquet is compared on Arrow schema (with metadata) and table content, not raw bytes: the writer
embeds its `created_by` version.

Story 30.2 changed the snapshot's stored and wire layout on purpose (floats -> exact integers), so
for `DydxSecondSnapshot` these fixtures are now the float layout every reader must refuse loudly
(`LegacySnapshotLayoutError`, naming `archive.tools.migrate_snapshot_ints`), and the new layout
must decode to exactly the floats the old one stored. `OpenInterest` is still byte-for-byte proof.
"""

import json
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from kernel import archive_markers
from kernel.archive_markers import ArchiveGap
from kernel.open_interest import OpenInterest
from kernel.parquet_compat import apply_zstd_default
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import LegacySnapshotLayoutError
from kernel.tests.snapshot_factory import make_snapshot
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_FIXTURES = Path(__file__).parent / "fixtures"
_PRE_MOVE = _FIXTURES / "pre_move_catalog"
_T = 1_758_000_000_000_000_000
_DYDX = InstrumentId.from_str("BTC-USD-PERP.DYDX")
_BYBIT = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")


def _snapshot_with_trades() -> DydxSecondSnapshot:
    return make_snapshot(
        _DYDX,
        bid_prices=[100.5, 100.0],
        bid_sizes=[1.25, 2.0],
        ask_prices=[101.0, 101.5],
        ask_sizes=[0.5, 3.0],
        buy_volume=1.5,
        sell_volume=0.25,
        buy_count=3,
        sell_count=1,
        ts_event=_T,
        ts_init=_T + 1_500_000_000,
        open_price=100.75,
        high_price=101.0,
        low_price=100.5,
        close_price=100.9,
        price_precision=2,
        size_precision=2,
    )


def _snapshot_without_trades() -> DydxSecondSnapshot:
    return make_snapshot(
        _DYDX,
        bid_prices=[100.5],
        bid_sizes=[1.0],
        ask_prices=[101.0],
        ask_sizes=[0.5],
        ts_event=_T + 1_000_000_000,
        ts_init=_T + 2_000_000_000,
        price_precision=2,
        size_precision=2,
    )


# The float view a pre-30.2 row held under each wire key.
_FLOAT_KEYS = (
    "bid_prices",
    "bid_sizes",
    "ask_prices",
    "ask_sizes",
    "buy_volume",
    "sell_volume",
    "buy_count",
    "sell_count",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
)


def _float_view(s: DydxSecondSnapshot) -> dict:
    return {key: getattr(s, key) for key in _FLOAT_KEYS}


def _open_interest() -> OpenInterest:
    return OpenInterest(_BYBIT, Decimal("12345.678"), _T, _T + 3_000_000_000)


def _files(root: Path) -> dict[str, Path]:
    return {str(p.relative_to(root)): p for p in sorted(root.rglob("*.parquet"))}


def test_pre_move_rows_read_back_unchanged() -> None:
    catalog = ParquetDataCatalog(str(_PRE_MOVE))
    interests = catalog.query(OpenInterest, identifiers=[_BYBIT.value])
    unwrapped = [r.data if hasattr(r, "data") else r for r in interests]
    assert OpenInterest.to_dict(unwrapped[0]) == OpenInterest.to_dict(_open_interest())


def test_a_float_layout_snapshot_file_is_refused_never_read_as_floats() -> None:
    catalog = ParquetDataCatalog(str(_PRE_MOVE))
    with pytest.raises(LegacySnapshotLayoutError, match="migrate_snapshot_ints"):
        catalog.query(DydxSecondSnapshot, identifiers=[_DYDX.value])


def test_the_integer_layout_decodes_to_the_floats_the_float_layout_stored() -> None:
    row = pq.read_table(next(_PRE_MOVE.rglob("custom_dydx_second_snapshot/*/*.parquet")))
    stored = {key: row.column(key)[0].as_py() for key in _FLOAT_KEYS}
    assert _float_view(_snapshot_with_trades()) == stored


def test_post_move_write_matches_the_pre_move_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The writers install the patch at import; installing it here is undone after the test, so
    # no later test's Parquet compression depends on whether this one ran first.
    monkeypatch.setattr(pq, "write_table", pq.write_table)
    apply_zstd_default()
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([_snapshot_with_trades()])
    catalog.write_data([_open_interest()])
    before, after = _files(_PRE_MOVE), _files(tmp_path)
    assert list(after) == list(before)  # same directory names and file names
    snapshot_file = next(name for name in after if "custom_dydx_second_snapshot" in name)
    written = pq.read_table(after[snapshot_file])
    # Names, not `equals`: Parquet names a list's child `element` where Arrow says `item`.
    assert written.schema.names == DydxSecondSnapshot.schema().names
    assert written.schema.metadata == DydxSecondSnapshot.schema().metadata
    (row,) = written.to_pylist()
    assert row == DydxSecondSnapshot.to_dict(_snapshot_with_trades())
    for name, old_path in before.items():
        if name == snapshot_file:
            continue  # Story 30.2: a new layout on purpose (see the module docstring)
        old, new = pq.read_table(old_path), pq.read_table(after[name])
        assert new.schema.equals(old.schema, check_metadata=True), name
        assert new.equals(old), name
        assert pq.ParquetFile(after[name]).metadata.row_group(0).column(0).compression == "ZSTD"


def test_a_float_snapshots_raw_payload_is_refused() -> None:
    for entry in json.loads((_FIXTURES / "snapshots_raw.json").read_text()):
        with pytest.raises(LegacySnapshotLayoutError):
            DydxSecondSnapshot.from_dict(entry)


def test_the_integer_payload_decodes_to_the_floats_the_float_payload_carried() -> None:
    snapshots = [_snapshot_with_trades(), _snapshot_without_trades()]
    payload = json.dumps([DydxSecondSnapshot.to_dict(s) for s in snapshots])
    decoded = [DydxSecondSnapshot.from_dict(entry) for entry in json.loads(payload)]
    assert [DydxSecondSnapshot.to_dict(s) for s in decoded] == json.loads(payload)
    old = json.loads((_FIXTURES / "snapshots_raw.json").read_text())
    assert [_float_view(s) for s in decoded] == [
        {key: entry[key] for key in _FLOAT_KEYS} for entry in old
    ]


def test_archive_gap_line_is_byte_identical() -> None:
    recorded = (_FIXTURES / "archive_gap_line.jsonl").read_text()
    gap = ArchiveGap("BTC-USD-PERP.DYDX", _T, _T + 300_000_000_000, "quarantined", 7)
    assert archive_markers.encode(gap) + "\n" == recorded
    assert archive_markers.decode(recorded.strip()) == gap
