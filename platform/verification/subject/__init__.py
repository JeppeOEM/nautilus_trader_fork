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
The code under test, driven (Story 31.7): the catalog tool must open every file through
`ParquetDataCatalog`, run the archive's own consolidation and read the day through a
`BacktestNode` -- the readers and writers whose output it judges. This package is the one place in
`verification` that drives them, so the oracle never shares their code (DATA-02).

The subject rule (`platform/tests/test_boundaries.py`):
- it is the only non-test `verification` code that may import `nautilus_trader`,
  `kernel.second_snapshot`, `kernel.open_interest` and the archive's consolidation;
- it may import `verification.domain` (both sides use the one row digest);
- it is imported only by the `verification.catalog` composition root; the oracle
  (`verification.domain`, `verification.application`, `verification.infrastructure`) never
  imports it, and loads no `nautilus_trader` module (a runtime probe);
- it never imports `capture`, `candles`, `ranking`, `views`, `research`, `bots` or
  `nautilus_pyo3` itself.

The DATA-02 walk of the reference side stops here: what this package reaches is the subject, not
the reference.
"""
