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
"""`build_candles` rebuilds the SQLite store from a real catalog's 1s snapshots; the result must equal
`candle_dicts_from_snapshots` over those same snapshots (one aggregation result, two paths)."""

from pathlib import Path

from dydx_collector.build_candles import all_instruments
from dydx_collector.build_candles import rebuild_instrument
from dydx_collector.tests.test_backfill_minute_rollup import IID
from dydx_collector.tests.test_backfill_minute_rollup import _seed
from ml_signals import candle_store
from ml_signals.candles import candle_dicts_from_snapshots


def test_rebuild_from_catalog_matches_raw_aggregation_and_is_idempotent(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "cat"
    snaps = _seed(catalog_dir)  # 200 seconds straddling a UTC midnight, long ago (not the open day)
    assert all_instruments(str(catalog_dir)) == [IID]

    db = candle_store.connect_rw(str(tmp_path / "candles.db"))
    for _ in range(2):
        assert rebuild_instrument(db, str(catalog_dir), IID, snaps[0].ts_event, snaps[-1].ts_event) == len(snaps)
        for bar in candle_store.BAR_SECONDS:
            got = candle_store.window(db, IID, bar, 1 << 62, 1000)
            want = candle_dicts_from_snapshots(snaps, bar)
            assert [(c["t"], c["o"], c["h"], c["l"], c["c"]) for c in got] == [
                (c["t"], c["o"], c["h"], c["l"], c["c"]) for c in want
            ]
            assert [c["seconds_observed"] for c in got] == [
                sum(1 for s in snaps if s.ts_event // 1_000_000 // (bar * 1000) * (bar * 1000) == c["t"]) for c in got
            ]
