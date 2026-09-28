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
Derived quotes (Story 27.1): the simulated exchange's market for a backtest over second snapshots.

The collector catalog holds no `QuoteTick`, and the simulated exchange rejects every order with "no
market for <instrument>" when it has none, so a snapshot backtest derives one quote per snapshot top
of book (`kernel.catalog_files.query_top_of_book`, level 0 only) into a throwaway catalog. Shared by
`application.backtest_runner` and `strategies.snapshot_backtest`; it lives in the application layer
so the runner depends on no strategy module.
"""

from kernel.catalog_files import TopOfBook

from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.instruments import Instrument


def derived_quotes(instrument: Instrument, tops: list[TopOfBook]) -> list[QuoteTick]:
    """
    One QuoteTick per top of book, ordered by `ts_init`.

    `query_top_of_book` sorts by `ts_event`, but `ParquetDataCatalog.write_data` refuses rows
    whose `ts_init` decreases, and a writer's `ts_init` may trail `ts_event` by up to
    `kernel.clocks.MAX_TS_INIT_SKEW_NS`, so the two orders can disagree. `ts_init` is also the
    clock `BacktestNode` replays on, as `catalog.query` ordered them before.
    """
    return [
        QuoteTick(
            instrument_id=instrument.id,
            bid_price=instrument.make_price(t.bid_price),
            ask_price=instrument.make_price(t.ask_price),
            bid_size=instrument.make_qty(t.bid_size),
            ask_size=instrument.make_qty(t.ask_size),
            ts_event=t.ts_event,
            ts_init=t.ts_init,
        )
        for t in sorted(tops, key=lambda t: t.ts_init)
    ]
