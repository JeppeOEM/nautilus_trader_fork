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
Integration smoke test: subscribe one instrument for ~20 s and verify data lands in the catalog.

Run from repo root:
    PYTHONPATH=troll python troll/dydx_collector/test_integration.py
"""

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

from dydx_collector.client import DydxClient
from dydx_collector.collector import Collector
from dydx_collector.config import CollectorConfig
from dydx_collector.config import InstrumentEntry
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

RUN_SECONDS = 20
FLUSH_INTERVAL = 5


async def main() -> None:
    network = DydxNetwork.MAINNET

    # Discover one instrument (avoids the 50 s subscription burst for all ~100)
    probe = DydxClient(on_data=lambda _: None, network=network)
    all_instruments = await probe.fetch_instruments()
    instrument_id = next(
        (i.id.value for i in all_instruments if "ETH-USD" in i.id.value),
        all_instruments[0].id.value,
    )
    print(f"instrument: {instrument_id}")
    print(f"running {RUN_SECONDS} s (flush every {FLUSH_INTERVAL} s) …\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        config = CollectorConfig(
            network=network,
            catalog_path=tmpdir,
            flush_interval_seconds=FLUSH_INTERVAL,
            config_reload_seconds=9999,
            open_interest_poll_seconds=9999,
            instruments=(InstrumentEntry(id=instrument_id, bar_intervals=()),),
        )
        collector = Collector(config)

        async def _stopper() -> None:
            await asyncio.sleep(RUN_SECONDS)
            print(f"\n{RUN_SECONDS} s elapsed — stopping")
            collector.stop()

        stopper = asyncio.create_task(_stopper())
        await collector.run()
        stopper.cancel()

        files = sorted(Path(tmpdir).rglob("*.parquet"))
        print(f"\n--- catalog audit: {len(files)} parquet file(s) ---")
        for f in files:
            size = f.stat().st_size
            print(f"  {f.relative_to(tmpdir)}  ({size:,} bytes)")

        if not files:
            print("\nFAIL: no parquet files written")
            sys.exit(1)
        print("\nOK")


if __name__ == "__main__":
    asyncio.run(main())
