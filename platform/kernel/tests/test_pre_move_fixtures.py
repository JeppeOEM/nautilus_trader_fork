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

The fixtures were recorded with the pre-move code (`collector_core.second_snapshot` /
`collector_core.open_interest`, run in the `story-23-1/collector` image at `7bd64952fd`):

- `pre_move_catalog/`: `ParquetDataCatalog.write_data` (with the collector's zstd patch applied)
  of `_snapshot_with_trades()` and `_open_interest()` below, one call each;
- `snapshots_raw.json`: `json.dumps([DydxSecondSnapshot.to_dict(s) for s in snapshots])`, the
  collector's `_publish_snapshot_batch` encoding, of `_snapshot_with_trades()` and
  `_snapshot_without_trades()`;
- `archive_gap_line.jsonl`: `record_gap(catalog, "BTC-USD-PERP.DYDX", T, T + 300 s,
  "quarantined", 7)`.

Parquet is compared on Arrow schema (with metadata) and table content, not raw bytes: the writer
embeds its `created_by` version.
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
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_FIXTURES = Path(__file__).parent / "fixtures"
_PRE_MOVE = _FIXTURES / "pre_move_catalog"
_T = 1_758_000_000_000_000_000
_DYDX = InstrumentId.from_str("BTC-USD-PERP.DYDX")
_BYBIT = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")


def _snapshot_with_trades() -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        _DYDX,
        [100.5, 100.0],
        [1.25, 2.0],
        [101.0, 101.5],
        [0.5, 3.0],
        1.5,
        0.25,
        3,
        1,
        _T,
        _T + 1_500_000_000,
        100.75,
        101.0,
        100.5,
        100.9,
    )


def _snapshot_without_trades() -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        _DYDX,
        [100.5],
        [1.0],
        [101.0],
        [0.5],
        0.0,
        0.0,
        0,
        0,
        _T + 1_000_000_000,
        _T + 2_000_000_000,
    )


def _open_interest() -> OpenInterest:
    return OpenInterest(_BYBIT, Decimal("12345.678"), _T, _T + 3_000_000_000)


def _files(root: Path) -> dict[str, Path]:
    return {str(p.relative_to(root)): p for p in sorted(root.rglob("*.parquet"))}


def test_pre_move_rows_read_back_unchanged() -> None:
    catalog = ParquetDataCatalog(str(_PRE_MOVE))
    snapshots = catalog.query(DydxSecondSnapshot, identifiers=[_DYDX.value])
    interests = catalog.query(OpenInterest, identifiers=[_BYBIT.value])
    unwrapped = [r.data if hasattr(r, "data") else r for r in snapshots + interests]
    assert [type(r).__name__ for r in unwrapped] == ["DydxSecondSnapshot", "OpenInterest"]
    assert DydxSecondSnapshot.to_dict(unwrapped[0]) == DydxSecondSnapshot.to_dict(
        _snapshot_with_trades()
    )
    assert OpenInterest.to_dict(unwrapped[1]) == OpenInterest.to_dict(_open_interest())


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
    for name, old_path in before.items():
        old, new = pq.read_table(old_path), pq.read_table(after[name])
        assert new.schema.equals(old.schema, check_metadata=True), name
        assert new.equals(old), name
        assert pq.ParquetFile(after[name]).metadata.row_group(0).column(0).compression == "ZSTD"


def test_snapshots_raw_payload_is_byte_identical() -> None:
    snapshots = [_snapshot_with_trades(), _snapshot_without_trades()]
    payload = json.dumps([DydxSecondSnapshot.to_dict(s) for s in snapshots])
    assert payload == (_FIXTURES / "snapshots_raw.json").read_text()
    decoded = [DydxSecondSnapshot.from_dict(entry) for entry in json.loads(payload)]
    assert [DydxSecondSnapshot.to_dict(s) for s in decoded] == json.loads(payload)


def test_archive_gap_line_is_byte_identical() -> None:
    recorded = (_FIXTURES / "archive_gap_line.jsonl").read_text()
    gap = ArchiveGap("BTC-USD-PERP.DYDX", _T, _T + 300_000_000_000, "quarantined", 7)
    assert archive_markers.encode(gap) + "\n" == recorded
    assert archive_markers.decode(recorded.strip()) == gap
