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
`views.coin_detail`: the single-coin read model both UIs share -- the rank-row lookup (moved from
`bot_tui/tests/test_coin_detail.py`), the `snapshots:raw` decode, the metric groups, and the
metrics.db / catalog reads.
"""

from pathlib import Path

import pytest
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger
from ranking_engine import metrics_store

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views import coin_detail
from views.coin_detail import COIN_DETAIL_GROUPS
from views.coin_detail import catalog_snapshot_rows
from views.coin_detail import rank_row_for
from views.coin_detail import snapshot_for


_IID = "BTC-USD-PERP.DYDX"


def _snapshot(iid: str, ts_ns: int = 1_000_000_000) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(iid),
        bid_prices=[100.0, 99.5],
        bid_sizes=[1.0, 2.0],
        ask_prices=[100.5, 101.0],
        ask_sizes=[1.5, 3.0],
        buy_volume=1.0,
        sell_volume=0.5,
        buy_count=2,
        sell_count=1,
        open_price=100.0,
        high_price=100.4,
        low_price=99.9,
        close_price=100.2,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def _ranking(ranks: list[dict]) -> dict:
    return {"mode": "volume", "updated_at": 1, "ranks": ranks}


def test_rank_row_for_finds_matching_instrument() -> None:
    ranking = _ranking(
        [
            {"instrument_id": "ETH-USD-PERP", "spread": 0.1},
            {"instrument_id": "BTC-USD-PERP", "spread": 0.5},
        ]
    )
    assert rank_row_for(ranking, "BTC-USD-PERP") == {"instrument_id": "BTC-USD-PERP", "spread": 0.5}


def test_rank_row_for_none_when_no_ranking_message_yet() -> None:
    assert rank_row_for(None, "BTC-USD-PERP") is None


def test_rank_row_for_none_when_instrument_not_yet_ranked() -> None:
    ranking = _ranking([{"instrument_id": "ETH-USD-PERP"}])
    assert rank_row_for(ranking, "BTC-USD-PERP") is None


def test_rank_row_for_skips_malformed_entry() -> None:
    ranking = _ranking(["not-a-dict", {"instrument_id": "BTC-USD-PERP", "spread": 0.5}])
    assert rank_row_for(ranking, "BTC-USD-PERP") == {"instrument_id": "BTC-USD-PERP", "spread": 0.5}


# --- snapshot_for: `snapshots:raw` is parsed only through `DydxSecondSnapshot.from_dict` ------


def test_snapshot_for_decodes_the_open_coins_entry() -> None:
    batch = [DydxSecondSnapshot.to_dict(_snapshot(i)) for i in ("ETH-USD-PERP.DYDX", _IID)]

    found = snapshot_for(batch, _IID)

    assert isinstance(found, DydxSecondSnapshot)
    assert found.instrument_id.value == _IID
    assert (found.bid_prices, found.ask_sizes) == ([100.0, 99.5], [1.5, 3.0])


def test_snapshot_for_is_none_when_the_coin_is_not_in_the_batch() -> None:
    error_ledger.reset()
    batch = [DydxSecondSnapshot.to_dict(_snapshot("ETH-USD-PERP.DYDX"))]
    assert snapshot_for(batch, _IID) is None
    assert error_ledger.counts() == {}


@pytest.mark.parametrize(
    "bad",
    [
        {"instrument_id": "BTC-USD-PERP"},  # a venue-less id: not an InstrumentId
        {"bid_prices": [1.0]},  # no instrument_id at all
        "not-a-dict",
    ],
)
def test_an_undecodable_entry_is_ledgered_and_skipped_the_rest_still_read(bad: object) -> None:
    error_ledger.reset()
    batch = [bad, DydxSecondSnapshot.to_dict(_snapshot(_IID))]

    found = snapshot_for(batch, _IID)

    assert found is not None
    assert found.instrument_id.value == _IID
    assert error_ledger.counts() == {"views.snapshot_decode": 1}


def test_a_batch_that_is_not_a_list_is_ledgered() -> None:
    error_ledger.reset()
    assert snapshot_for({"not": "a list"}, _IID) is None
    assert error_ledger.counts() == {"views.snapshot_decode": 1}


# --- the metric groups both coin-detail views show ---------------------------------------------


def test_every_coin_detail_metric_is_a_published_rank_entry_key_listed_once() -> None:
    keys = [key for _title, specs in COIN_DETAIL_GROUPS for _label, key, _decimals in specs]
    assert len(keys) == len(set(keys))
    assert {"microprice", "spread", "ofi_10_z", "cvd", "volatility", "volume24h"} <= set(keys)
    counts = {key: d for _t, specs in COIN_DETAIL_GROUPS for _l, key, d in specs}
    assert (counts["buy_count"], counts["sell_count"]) == (0, 0)  # integer counts, no decimals
    assert counts["microprice"] == coin_detail.MIN_INDICATOR_DECIMALS


# --- the metrics.db and catalog reads ------------------------------------------------------------


def test_metrics_reads_return_ranking_contexts_own_rows(tmp_path: Path) -> None:
    db_path = str(tmp_path / "metrics.db")
    now_ns = 1_900_000_000_000_000_000
    metrics_store.write([{"instrument_id": _IID, "ts": now_ns, "price": 100.0}], db_path)

    history = coin_detail.metrics_history(_IID, db_path)
    assert [row["price"] for row in history] == [100.0]
    assert history == metrics_store.history(_IID, db_path, 31)
    assert coin_detail.metrics_nearest(_IID, now_ns, db_path) == metrics_store.nearest(
        _IID, now_ns, db_path
    )


def test_catalog_snapshot_rows_project_book_and_ohlc_fields(tmp_path: Path) -> None:
    catalog_path = str(tmp_path / "catalog")
    ParquetDataCatalog(catalog_path).write_data([_snapshot(_IID, 2_000_000_000)])

    (row,) = catalog_snapshot_rows(catalog_path, _IID, 0, 3_000_000_000)

    assert row == {
        "bid_prices": [100.0, 99.5],
        "bid_sizes": [1.0, 2.0],
        "ask_prices": [100.5, 101.0],
        "ask_sizes": [1.5, 3.0],
        "buy_volume": 1.0,
        "sell_volume": 0.5,
        "ts_event": 2_000_000_000,
        "open_price": 100.0,
        "high_price": 100.4,
        "low_price": 99.9,
        "close_price": 100.2,
    }
