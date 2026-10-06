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
import threading
from collections.abc import Iterable
from collections.abc import Sequence
from pathlib import Path
from typing import IO

import pyarrow.parquet as pq
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_S
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.clocks import CatalogFileSpan
from kernel.parquet_compat import apply_zstd_default

from capture.application import sites
from capture.application.ports import Ledger
from capture.application.ports import RecentTrades
from capture.infrastructure.capture_lock import acquire_capture_lock
from capture.infrastructure.coverage_file import append_lines
from capture.infrastructure.coverage_file import coverage_path
from capture.infrastructure.coverage_file import ensure_file
from capture.infrastructure.coverage_file import repair_torn_tail
from capture.infrastructure.gap_markers import record_gap
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_QUARANTINE_DIRNAME = "_quarantine"
_TRADE_DIR = "trade_tick"
_SNAPSHOT_DIR = "custom_dydx_second_snapshot"

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
        # Coverage appends run in worker threads: a cancelled flush's thread can still be writing
        # when the final flush appends, so one lock serializes them (whole lines, in order).
        self._coverage_lock = threading.Lock()
        # The paths whose tail this process checked, and repaired bytes not yet reported (an
        # append that failed after its repair reports them on the next success).
        self._coverage_checked: set[Path] = set()
        self._coverage_repaired: dict[Path, int] = {}

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

    def recent_trades(self, iid: str, start_ns: int, end_ns: int, ledger: Ledger) -> RecentTrades:
        """
        Read only the `trade_id`, `ts_init` and `ts_event` columns of the files whose name span
        (`ts_init`) meets `[start_ns, end_ns]`, and keep the rows inside it (MEM-01: one horizon
        of ids): the ids with their `ts_init`, and the newest `ts_event` of those rows. A file
        whose name is not a catalog span, or that vanished or cannot be read, is ledgered
        (`collector.dedup_seed`) and skipped; the other files still seed.
        """
        found: list[tuple[str, int]] = []
        newest: int | None = None
        for path in (Path(self._path) / "data" / _TRADE_DIR / iid).glob("*.parquet"):
            try:
                ids, file_newest = _trades_in_span(path, start_ns, end_ns)
            except (ValueError, OSError) as e:
                ledger(sites.DEDUP_SEED, f"{iid}: {path.name} skipped by the dedup seed", e)
                continue
            found.extend(ids)
            if file_newest is not None and (newest is None or file_newest > newest):
                newest = file_newest
        return RecentTrades(found, newest)

    def last_snapshot_second(self, iid: str) -> int | None:
        """
        Return the newest snapshot row's `ts_event // 1 s`, reading the newest file(s) only: the
        one with the latest name span, plus any other whose span ends within `READ_SPAN_MARGIN_NS`
        of it (a row's `ts_event` sits at most that far from its `ts_init`, the span's clock).
        """
        spans = _spanned_files(self._path, _SNAPSHOT_DIR, iid)
        if not spans:
            return None
        newest_end = max(span.end_ns for span, _ in spans)
        newest: int | None = None
        for span, path in spans:
            if span.end_ns < newest_end - READ_SPAN_MARGIN_NS:
                continue
            stamps = pq.read_table(path, columns=["ts_event"]).column("ts_event").to_pylist()
            if stamps:
                newest = max(int(max(stamps)), newest or 0)
        return None if newest is None else newest // NS_PER_S

    def append_coverage(self, venue: str, lines: Sequence[str]) -> int:
        path = coverage_path(self._path, venue)
        with self._coverage_lock:
            if path not in self._coverage_checked:
                cut = repair_torn_tail(path)
                self._coverage_repaired[path] = self._coverage_repaired.get(path, 0) + cut
                self._coverage_checked.add(path)
            try:
                append_lines(path, lines)
            except BaseException:
                # Its rollback may have failed too and left a fragment: check the tail again first.
                self._coverage_checked.discard(path)
                raise
            return self._coverage_repaired.pop(path, 0)

    def ensure_coverage(self, venue: str) -> None:
        with self._coverage_lock:
            ensure_file(coverage_path(self._path, venue))


def _trades_in_span(
    path: Path, start_ns: int, end_ns: int
) -> tuple[list[tuple[str, int]], int | None]:
    """
    Return `(trade_id, ts_init)` of one trade file's rows with `ts_init` inside the span, and the
    newest `ts_event` of those rows; ([], None) when the file's name misses the span.
    """
    if not CatalogFileSpan.from_path(path).overlaps(start_ns, end_ns):
        return [], None
    table = pq.read_table(
        path,
        columns=["trade_id", "ts_init", "ts_event"],
        filters=[("ts_init", ">=", start_ns), ("ts_init", "<=", end_ns)],
    )
    stamps = table.column("ts_event").to_pylist()
    ids = list(
        zip(
            (str(t) for t in table.column("trade_id").to_pylist()),
            (int(t) for t in table.column("ts_init").to_pylist()),
            strict=True,
        )
    )
    return ids, (int(max(stamps)) if stamps else None)


def _spanned_files(
    catalog_path: str, data_dir: str, iid: str
) -> list[tuple[CatalogFileSpan, Path]]:
    """
    One instrument's catalog files of one data type, each with its name's `ts_init` span. A name
    the catalog did not write raises `ValueError` (the caller ledgers it): only this collector
    writes its instruments' directories, so such a file is a fault to see, never one to skip.
    """
    return [
        (CatalogFileSpan.from_path(path), path)
        for path in (Path(catalog_path) / "data" / data_dir / iid).glob("*.parquet")
    ]


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
