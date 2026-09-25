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
`python -m candles.rebuild` rebuilds the SQLite store from a real catalog's 1s snapshots; the result
must equal the same fold read straight off those snapshots (one fold, two paths).
"""

from pathlib import Path

from candles.application import queries
from candles.application.forming import bars_from_rows
from candles.application.rebuild import all_instruments
from candles.application.rebuild import rebuild_instrument
from candles.application.rebuild import venue_instruments
from candles.domain.fold import BAR_SECONDS
from candles.infrastructure.sqlite_store import CandleStore
from kernel.second_snapshot import DydxSecondSnapshot

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


IID = "BTC-USD-PERP.DYDX"
_SEC = 1_000_000_000
_DAY_NS = 86_400 * _SEC


def _seed(path: Path) -> list[DydxSecondSnapshot]:
    """200 traded seconds straddling a UTC midnight long ago, so two day chunks are crossed."""
    start = 3 * _DAY_NS - 90 * _SEC
    snaps = [
        DydxSecondSnapshot(
            instrument_id=InstrumentId.from_str(IID),
            bid_prices=[100.0 + i - 1],
            bid_sizes=[1.0],
            ask_prices=[100.0 + i + 1],
            ask_sizes=[1.0],
            buy_volume=1.0,
            sell_volume=0.5,
            buy_count=1,
            sell_count=1,
            open_price=100.0 + i,
            high_price=100.5 + i,
            low_price=99.5 + i,
            close_price=100.0 + i,
            ts_event=start + i * _SEC,
            ts_init=start + i * _SEC,
        )
        for i in range(200)
    ]
    ParquetDataCatalog(str(path)).write_data(snaps)
    return snaps


def test_rebuild_from_catalog_matches_raw_aggregation_and_is_idempotent(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "cat"
    snaps = _seed(catalog_dir)  # 200 seconds straddling a UTC midnight, long ago (not the open day)
    assert all_instruments(str(catalog_dir)) == [IID]

    db_path = str(tmp_path / "candles.db")
    for _ in range(2):
        assert rebuild_instrument(
            db_path, str(catalog_dir), IID, snaps[0].ts_event, snaps[-1].ts_event
        ) == len(snaps)
        store = CandleStore(db_path)
        for bar in BAR_SECONDS:
            got = queries.window(store.connection, IID, bar, 1 << 62, 1000)
            want = bars_from_rows(snaps, bar)
            assert [(c["t"], c["o"], c["h"], c["l"], c["c"]) for c in got] == [
                (c["t"], c["o"], c["h"], c["l"], c["c"]) for c in want
            ]
            assert [c["seconds_observed"] for c in got] == [
                sum(
                    1
                    for s in snaps
                    if s.ts_event // 1_000_000 // (bar * 1000) * (bar * 1000) == c["t"]
                )
                for c in got
            ]
        store.close()


def test_venue_instruments_filters_on_the_id_suffix() -> None:
    ids = ["BTC-USD-PERP.DYDX", "BTCUSDT-LINEAR.BYBIT", "BTC-USD-PERP.HYPERLIQUID"]
    assert venue_instruments(ids, "BYBIT") == ["BTCUSDT-LINEAR.BYBIT"]
    assert venue_instruments(ids, "DYDX") == ["BTC-USD-PERP.DYDX"]
    assert venue_instruments(ids, None) == ids
