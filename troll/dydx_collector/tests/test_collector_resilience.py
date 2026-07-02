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
Tests for VPS-longevity safety behaviors: WS callback fault isolation, the
startup corrupt-parquet quarantine scan, and the crossed-book resync watchdog
(see collector.py's `_on_data` / `quarantine_corrupt_parquet` / `_resync_book`).
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dydx_collector.collector import Collector
from dydx_collector.collector import quarantine_corrupt_parquet
from dydx_collector.config import CollectorConfig
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId


def _make_config(catalog_path: Path) -> CollectorConfig:
    return CollectorConfig(
        network=DydxNetwork.TESTNET,
        catalog_path=str(catalog_path),
        flush_interval_seconds=60,
        snapshot_interval_seconds=1.0,
        config_reload_seconds=60,
        open_interest_poll_seconds=60,
        non_config_retain_hours=24.0,
        liquidity_min_oi_usd=100_000.0,
        liquidity_check_seconds=60,
        instruments=(),
        exclude=frozenset(),
    )


def test_on_data_isolates_bad_message(tmp_path: Path, monkeypatch) -> None:
    collector = Collector(_make_config(tmp_path / "catalog"))

    def _boom(data: object) -> None:
        raise ValueError("simulated malformed message")

    monkeypatch.setattr(collector, "_on_data_unsafe", _boom)

    collector._on_data(object())  # must not raise -- one bad message can't kill the WS client


def test_quarantine_corrupt_parquet_moves_only_bad_files(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    good_dir = catalog / "data" / "trade_tick"
    good_dir.mkdir(parents=True)

    good_file = good_dir / "0-100.parquet"
    table = pa.table({"ts_init": [0, 100]})
    pq.write_table(table, good_file)

    corrupt_file = good_dir / "100-200.parquet"
    corrupt_file.write_bytes(b"not a real parquet file")

    quarantine_corrupt_parquet(str(catalog))

    assert good_file.exists()
    assert not corrupt_file.exists()
    quarantined = catalog / "_quarantine" / "data" / "trade_tick" / "100-200.parquet"
    assert quarantined.exists()


def test_quarantine_corrupt_parquet_missing_catalog_is_noop(tmp_path: Path) -> None:
    quarantine_corrupt_parquet(str(tmp_path / "does-not-exist"))  # must not raise


class _FakeClient:
    """Records subscribe/unsubscribe calls without touching the network."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def unsubscribe_orderbook(self, iid: str) -> None:
        self.calls.append(f"unsubscribe:{iid}")

    async def subscribe_orderbook(self, iid: str) -> None:
        self.calls.append(f"subscribe:{iid}")


@pytest.mark.asyncio
async def test_resync_book_resubscribes_and_drops_local_state(tmp_path: Path) -> None:
    """
    A desynced book (never self-heals from deltas alone) must be rebuilt from
    a fresh venue snapshot -- _resync_book forces that via unsubscribe+subscribe
    and clears the local book so the next OrderBookDeltas rebuilds it clean.
    """
    collector = Collector(_make_config(tmp_path / "catalog"))
    fake_client = _FakeClient()
    collector._client = fake_client  # type: ignore[assignment]

    iid = "BTC-USD-PERP.DYDX"
    collector._live_books[iid] = OrderBook(InstrumentId.from_str(iid), BookType.L2_MBP)
    collector._crossed_since_ns[iid] = 123

    await collector._resync_book(iid)

    assert fake_client.calls == [f"unsubscribe:{iid}", f"subscribe:{iid}"]
    assert iid not in collector._live_books
    assert iid not in collector._crossed_since_ns
