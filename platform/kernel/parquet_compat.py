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
The one zstd default for `pyarrow.parquet.write_table` (DDD spine AD-D3).

`ParquetDataCatalog.write_data()` (pinned nautilus_trader 1.229.0) has no compression
passthrough: it calls `pq.write_table()` with pyarrow's "snappy" default, and
`nautilus_trader/persistence/catalog/parquet.py` cannot be modified (FORK-01). So the writers
(the collector's flush, `backfill_bars`) patch pyarrow's default instead. `pq` is one shared
module object (`sys.modules`), so the patch reaches nautilus's `import pyarrow.parquet as pq`
call site too.

Invariant: `pq.write_table` is wrapped at most once per process whatever the order or number of
`apply_zstd_default()` calls -- the installed wrapper carries a marker attribute, so a second
call is a no-op (no module flag, no stacked wrappers) -- and an explicit `compression=` always
wins. Nothing happens at import: a reader importing the kernel never changes how Parquet is
written.

Known limit: if a future nautilus_trader version passes `compression=` explicitly, this default
is silently ignored -- revisit on a version bump (the upgrade path is that passthrough).
"""

import functools
from collections.abc import Callable
from typing import Any

import pyarrow.parquet as pq


_MARKER = "_platform_zstd_default"


def apply_zstd_default() -> None:
    """Make zstd `pq.write_table`'s default compression (idempotent)."""
    original: Callable[..., None] = pq.write_table
    if getattr(original, _MARKER, False):
        return

    # `wraps` keeps `pq.write_table.__name__`/`__doc__`: the patch is process-wide and reaches
    # nautilus's own call site, so a traceback or a repr must still name pyarrow's function.
    @functools.wraps(original)
    def write_table_zstd(*args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("compression", "zstd")
        original(*args, **kwargs)

    setattr(write_table_zstd, _MARKER, True)
    pq.write_table = write_table_zstd
