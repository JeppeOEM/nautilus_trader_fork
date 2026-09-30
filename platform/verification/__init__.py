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
The verification context (Epic 31, Story 31.1): an independent source of truth for the market
data capture stores, so every later check compares our archive against the venue instead of
against itself (DATA-02's "a second independent client with zero shared code path").

Invariant: the reference side never imports the code it checks. No module here imports
`nautilus_pyo3`, `capture`, `candles`, `ranking`, `views`, `kernel.fold` or
`kernel.second_snapshot` -- directly or through anything it imports, however deep; it parses the
venues' wire JSON itself, with `aiohttp`, `json`, `pyarrow`'s zstd codec and the standard library,
and takes only venue URLs (`kernel.venue_http`), instrument-id parsing (`kernel.venues`) and the
error ledger (`observability`) from the rest of the platform, all three standard-library only (the
dYdX indexer URLs, which come from the pyo3 bindings, live apart in `kernel.dydx_http`). Importing
the recorder loads no `nautilus_trader` module at all. No other context imports `verification`
(Story 31.11 reserves the one exception, `archive`'s nightly composition root).
`platform/tests/test_boundaries.py` enforces both directions, statically over the transitive import
closure and at runtime over `sys.modules`.

Known limit (common mode): `kernel.venues` and `kernel.venue_http` are shared with the collectors,
so a wrong wire symbol or host there misleads both sides alike (`domain/subscriptions.py`).

- `domain/`: the recording plan (a venue `config.toml` read as data) and the pure subscription,
  REST-poll and frame-classification tables; no I/O. Also the reference signals (Story 31.3,
  `reference_signals.py`: every derived value re-implemented from `docs/DATA_DICTIONARY.md` in
  stdlib `Decimal`, importing nothing of the platform) and their comparison rules
  (`signal_compare.py`). Only the comparator tests (`tests/test_reference_*.py`, declared
  composition roots in `platform/tests/test_boundaries.py`) import the production code they compare.
- `application/`: the ports, the ledger sites and the recorder's supervision loops.
- `infrastructure/`: the hourly zstd JSONL raw store and the aiohttp adapters; imported only by
  the composition roots.
- `recorder.py`: the composition root, `python3 -m verification.recorder --venue V`.
- `subject/` (Story 31.7): the one exception to the invariant -- the code under test
  (`ParquetDataCatalog`, `BacktestNode`, the archive's consolidation) that the catalog tool,
  `python3 -m verification.catalog`, must drive; imported only by that root, never by the oracle
  (`subject/__init__.py` states the rule).
- `candles.py` (Story 31.8): the candles tool, `python3 -m verification.candles`; it reads the bars
  the chart is served only as the local data_api's HTTP response
  (`infrastructure/served_candles.py`), never through `views`, `candles` or `data_api` code.
- `tools/`: operator tools (`record_fixtures`, the test-fixture recorder; `cut_snapshot_fixtures`,
  which cuts the derived-signal tests' real rows from a soak catalog with pyarrow).
"""
