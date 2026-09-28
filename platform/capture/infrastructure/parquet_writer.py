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
`ParquetArchiveWriter`: capture's `ArchiveWriter` over the shared `ParquetDataCatalog`
(DDD spine AD-D2/AD-D18, NAUT-02). Moved out of `capture.application.capture_service` in Story 26.1.

Every file this process writes through `ParquetDataCatalog.write_data()` is zstd-compressed: the
one patch, shared with `backfill_bars` (`kernel.parquet_compat` for why and its limit), applied
where the catalog is opened for writing.
"""

import asyncio
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import IO

import pyarrow.parquet as pq
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import CatalogFileSpan
from kernel.parquet_compat import apply_zstd_default

from capture.application import sites
from capture.application.ports import Ledger
from capture.infrastructure.capture_lock import acquire_capture_lock
from capture.infrastructure.gap_markers import record_gap
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_QUARANTINE_DIRNAME = "_quarantine"

apply_zstd_default()


class ParquetArchiveWriter:
    """
    The one live writer of a venue's catalog leaves (see `capture.application.ports.ArchiveWriter` for
    the invariant). `catalog` is the `ParquetDataCatalog` itself, for a reader in the same
    process (the capture tests read back what they wrote).
    """

    def __init__(self, catalog_path: str) -> None:
        root = Path(catalog_path).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self._path = str(root)
        self.catalog = ParquetDataCatalog(self._path)

    @property
    def catalog_path(self) -> str:
        return self._path

    def write(self, items: list) -> None:
        self.catalog.write_data(items)

    def write_instruments(self, instruments: list) -> None:
        self.catalog.write_data(instruments)

    def mark_gap(
        self, iid: str, from_ns: int, to_ns: int, reason: str, count: int, ledger: Ledger
    ) -> None:
        record_gap(self._path, iid, from_ns, to_ns, reason, count, ledger)

    def quarantine_corrupt(self, instrument_ids: Iterable[str], ledger: Ledger) -> None:
        quarantine_corrupt_parquet(self._path, instrument_ids, ledger)

    async def acquire_lock(
        self, venue: str, shutting_down: asyncio.Event, ledger: Ledger
    ) -> IO[str] | None:
        return await acquire_capture_lock(self._path, venue, shutting_down, ledger=ledger)


def quarantine_corrupt_parquet(
    catalog_path: str, instrument_ids: Iterable[str], ledger: Ledger
) -> None:
    """
    Move any unreadable .parquet file (e.g. left by a mid-write crash) for *this collector's*
    instruments out of the way.

    Runs once at process start, before any new writes. A half-written file from a killed
    process would otherwise sit forever next to good data and can break catalog reads or
    consolidation. Scoped to `data/<type>/<iid>/` for the given ids: every venue's collector
    shares one catalog root, and `ParquetDataCatalog` writes straight to the final path, so
    scanning a sibling's directories would quarantine a file that is merely mid-write. Only
    this process writes its own ids' directories, and it is not writing yet when this runs
    (the shared instrument-definition tables are left alone for the same reason).
    Known limit: full scan of this venue's files on every start; if that grows into the
    hundreds of thousands of files, switch to only checking files newer than the last clean
    shutdown.
    """
    root = Path(catalog_path).resolve()
    quarantine_root = root / _QUARANTINE_DIRNAME
    for iid in instrument_ids:
        for path in root.glob(f"data/*/{iid}/*.parquet"):
            if _is_readable_parquet(path):
                continue
            dest = quarantine_root / path.relative_to(root)
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dest))
            except OSError as e:
                # Must not escape: this runs before run_forever's restart loop.
                ledger(sites.CORRUPT_PARQUET, f"could not quarantine {path}", e)
                continue
            ledger(sites.CORRUPT_PARQUET, f"corrupt parquet file quarantined: {path} -> {dest}")
            if path.parent.parent.name == "trade_tick":
                _mark_quarantined_trades(str(root), iid, path, ledger)


def _mark_quarantined_trades(catalog_path: str, iid: str, path: Path, ledger: Ledger) -> None:
    """
    Record the quarantined trade file's span as an archive gap, so the nightly rebuild keeps the
    live values of the rows those trades were folded into. The name spans the batch's `ts_init`;
    a row folding them is sampled after arrival, so `to` is widened by the arrival margin. An
    unparseable name marks nothing and is ledgered: that file was never written by the collector.
    """
    try:
        span = CatalogFileSpan.from_path(path)
        from_ns, to_ns = span.start_ns, span.end_ns + MAX_TS_INIT_SKEW_NS
    except ValueError as e:
        ledger(sites.CORRUPT_PARQUET, f"no span in {path.name}; no gap marked", e)
        return
    record_gap(catalog_path, iid, from_ns, to_ns, "quarantined", 0, ledger)


def _is_readable_parquet(path: Path) -> bool:
    try:
        pq.ParquetFile(path)
    except Exception:
        return False
    return True
