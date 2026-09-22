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
The shared kernel (DDD spine AD-D3): the single copy of every type, fold, parser, constant,
transport and read helper more than one bounded context uses.

Invariant: one copy. `DydxSecondSnapshot`/`SecondOHLC`/`OpenInterest` (`second_snapshot`,
`open_interest`), the trades -> second fold (`fold`), the pure indicators (`indicators`,
`performance_metrics`), the only `InstrumentId` parser (`venues`), the two clocks and the one
skew bound `MAX_TS_INIT_SKEW_NS` (`clocks`), the archive-gap marker format (`archive_markers`),
the venue REST transport (`venue_http`), the read-only catalog file helpers (`catalog_files`) and
the one zstd `write_table` patch (`parquet_compat`) -- and nothing else.

The kernel imports no context (not even `observability`), holds no module-level mutable state,
no store, no config loader and no ledger call, so every context may import it without importing
anything else (`platform/tests/test_boundaries.py` enforces it).

Known limit: that guard reads the source with `ast`, so it judges what a binding *looks* like. It
catches a mutable literal, a call to one of the known mutable factories, and any import-time call
at module scope outside its two sanction tables -- bare (`side_effect()`) or bound to a name
(`X = SomeMutableClass()`), so a new one has to be argued for by name rather than slipping in. It
still cannot see state captured in a closure, or reached through an attribute of a sanctioned
result. Upgrade path: bind the check to runtime types (import each kernel module and walk its
module dict for a non-hashable value) once the kernel holds a module-level object the AST rule
cannot classify.

Its only effects outside its own namespace are the two sanctioned ones: each `Data` class's single
`register_arrow` at import (nautilus's Arrow registry, counted by
`platform/tests/test_namespace.py`) and the zstd `write_table` wrapper, installed only when
`parquet_compat.apply_zstd_default()` is called.
"""
