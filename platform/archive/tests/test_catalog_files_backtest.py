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
"""
Story 30.1: a `BacktestNode` streams trade ticks and second snapshots from a catalog whose every
file went through `CatalogFiles.rewrite` (the compact write settings), with zero conversion.

One `BacktestNode` run in this file, and the session's Nautilus log guard held
(`conftest.nautilus_log_guard`): a second logging init in one process aborts it natively.
"""

import shutil
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from kernel.second_snapshot import DydxSecondSnapshot

from archive.infrastructure.catalog_files import CatalogFiles
from archive.infrastructure.compact_parquet import is_compact
from archive.tests import catalog_fixture
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.node import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import TradeTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog


def _rewritten_catalog(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    catalog_fixture.write_day(source, seconds=600)
    rewritten = tmp_path / "rewritten"
    shutil.copytree(source, rewritten)
    writer = CatalogFiles(lambda: (catalog_fixture.DAY0 + 10) * catalog_fixture.DAY_NS)
    for path in catalog_fixture.data_files(rewritten).values():
        writer.rewrite(path, pq.read_table(path))
        assert is_compact(path)
    return rewritten


def _run_config(catalog: str) -> BacktestRunConfig:
    iid = catalog_fixture.IID
    currency = str(catalog_fixture.PERP.settlement_currency)
    return BacktestRunConfig(
        engine=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
        venues=[
            BacktestVenueConfig(
                name=str(iid.venue),
                oms_type="NETTING",
                account_type="MARGIN",
                base_currency=currency,
                starting_balances=[f"10000 {currency}"],
            )
        ],
        data=[
            # The TradeTick config also registers the instrument from the catalog.
            BacktestDataConfig(catalog_path=catalog, data_cls=TradeTick, instrument_id=iid),
            BacktestDataConfig(
                catalog_path=catalog,
                data_cls="kernel.second_snapshot:DydxSecondSnapshot",
                instrument_id=iid,
                client_id=str(iid.venue),  # a custom type needs a (bookkeeping) client id
            ),
        ],
    )


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_backtest_node_streams_rewritten_trade_ticks_and_snapshots(tmp_path: Path) -> None:
    catalog = _rewritten_catalog(tmp_path)
    reader = ParquetDataCatalog(str(catalog))
    ids = [str(catalog_fixture.IID)]
    events = len(reader.query(TradeTick, identifiers=ids))
    events += len(reader.query(DydxSecondSnapshot, identifiers=ids))
    node = BacktestNode(configs=[_run_config(str(catalog))])
    try:
        (result,) = node.run()
    finally:
        node.dispose()
    assert result.iterations == events > 600
