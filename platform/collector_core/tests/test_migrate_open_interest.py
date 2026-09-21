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
"""Migration of legacy per-venue open-interest directories into custom_open_interest/."""

from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from collector_core import migrate_open_interest
from collector_core.open_interest import OpenInterest
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_LEGACY = {
    "BTC-USD-PERP.DYDX": ("custom_dydx_open_interest", "DydxOpenInterest"),
    "BTCUSDT-LINEAR.BYBIT": ("custom_bybit_open_interest", "BybitOpenInterest"),
    "BTC-USD-PERP.HYPERLIQUID": ("custom_hyperliquid_open_interest", "HyperliquidOpenInterest"),
}


def _legacy_catalog(root: Path) -> None:
    """Write via OpenInterest, then make it look like production: old dir name, old type metadata."""
    catalog = ParquetDataCatalog(str(root))
    catalog.write_data(
        [OpenInterest(InstrumentId.from_str(i), Decimal("5.5"), 1, 1) for i in _LEGACY]
    )
    data = root / "data"
    for iid, (old_dir, old_type) in _LEGACY.items():
        (data / old_dir).mkdir()
        (data / "custom_open_interest" / iid).rename(data / old_dir / iid)
        for f in (data / old_dir / iid).glob("*.parquet"):
            table = pq.read_table(f)
            pq.write_table(
                table.replace_schema_metadata(
                    {**table.schema.metadata, b"type": old_type.encode()}
                ),
                f,
            )
    (data / "custom_open_interest").rmdir()


def _files(root: Path, dirname: str) -> list[Path]:
    return sorted((root / "data" / dirname).glob("*/*.parquet"))


def test_report_changes_nothing(tmp_path: Path) -> None:
    _legacy_catalog(tmp_path)
    assert migrate_open_interest.main(["--catalog", str(tmp_path)]) == 0
    assert not (tmp_path / "data" / "custom_open_interest").exists()
    assert len(_files(tmp_path, "custom_dydx_open_interest")) == 1


def test_apply_moves_rewrites_backs_up_and_is_idempotent(tmp_path: Path) -> None:
    catalog_dir, backup = tmp_path / "cat", tmp_path / "bak"
    _legacy_catalog(catalog_dir)
    args = ["--catalog", str(catalog_dir), "--backup-dir", str(backup), "--apply"]
    assert migrate_open_interest.main(args) == 0
    catalog = ParquetDataCatalog(str(catalog_dir))
    for iid in _LEGACY:
        (oi,) = catalog.query(OpenInterest, identifiers=[iid])
        assert oi.data.open_interest == Decimal("5.5")
    assert all(not (catalog_dir / "data" / old).exists() for old, _ in _LEGACY.values())
    assert len(list(backup.rglob("*.parquet"))) == 3
    assert migrate_open_interest.main(args) == 0  # second run: nothing to do
    assert len(_files(catalog_dir, "custom_open_interest")) == 3


def test_existing_target_aborts_without_touching_anything(tmp_path: Path) -> None:
    _legacy_catalog(tmp_path)
    src = _files(tmp_path, "custom_dydx_open_interest")[0]
    target = tmp_path / "data" / "custom_open_interest" / src.parent.name / src.name
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    args = ["--catalog", str(tmp_path), "--backup-dir", str(tmp_path / "bak"), "--apply"]
    assert migrate_open_interest.main(args) == 1
    assert target.read_bytes() == b"x"
    assert len(_files(tmp_path, "custom_bybit_open_interest")) == 1
    assert not (tmp_path / "bak").exists()


def test_apply_requires_backup_dir(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        migrate_open_interest.main(["--catalog", str(tmp_path), "--apply"])


def test_resumes_after_crash_between_replace_and_unlink(tmp_path: Path) -> None:
    _legacy_catalog(tmp_path)
    src = _files(tmp_path, "custom_dydx_open_interest")[0]
    target = tmp_path / "data" / "custom_open_interest" / src.parent.name / src.name
    target.parent.mkdir(parents=True)
    table = pq.read_table(src)
    pq.write_table(
        table.replace_schema_metadata({**table.schema.metadata, b"type": b"OpenInterest"}), target
    )
    args = ["--catalog", str(tmp_path), "--backup-dir", str(tmp_path / "bak"), "--apply"]
    assert migrate_open_interest.main(args) == 0
    assert not src.exists()
    assert len(_files(tmp_path, "custom_open_interest")) == 3
