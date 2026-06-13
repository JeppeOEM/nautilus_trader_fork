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

import logging

import pandas as pd

from nautilus_trader.common.component import TimeEvent
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from nautilus_trader.trading.strategy import Strategy


logger = logging.getLogger(__name__)


class RecorderStrategyConfig(StrategyConfig, frozen=True):
    """
    Configuration for ``RecorderStrategy`` instances.

    Parameters
    ----------
    instrument_ids : list[InstrumentId]
        The configured instrument identifiers to validate and subscribe to.
    catalog_path : str
        The path to the `ParquetDataCatalog` root used for conversion. Must equal
        the streaming root used by `StreamingConfig` (Pitfall 5 / A4).
    instance_id_str : str
        The fixed UUID4 string shared with the `TradingNode` instance_id (D-03).
    conversion_interval_minutes : PositiveInt, default 60
        How often the in-process conversion timer fires (REL-01/D-01).

    """

    instrument_ids: list[InstrumentId]
    catalog_path: str
    instance_id_str: str
    conversion_interval_minutes: PositiveInt = 60


class RecorderStrategy(Strategy):
    """
    Validate configured instruments, subscribe to trade ticks, and periodically
    convert streamed feather data into the `ParquetDataCatalog` (CONF-03, REC-01,
    REL-01).

    Parameters
    ----------
    config : RecorderStrategyConfig
        The configuration for the instance.

    """

    def __init__(self, config: RecorderStrategyConfig) -> None:
        super().__init__(config)

    def on_start(self) -> None:
        """
        Actions to be performed on strategy start.

        Raises
        ------
        RuntimeError
            If any configured instrument is missing from the cache. The message
            lists ALL missing instrument ids (D-06). This intentionally does NOT
            call `self.stop()` (D-05) — the process must exit non-zero so systemd
            surfaces the failure (T-01-06).

        """
        missing: list[str] = []
        for instrument_id in self.config.instrument_ids:
            if self.cache.instrument(instrument_id) is None:
                missing.append(str(instrument_id))

        if missing:
            raise RuntimeError(f"Missing instruments: {', '.join(sorted(missing))}")

        for instrument_id in self.config.instrument_ids:
            self.subscribe_trade_ticks(instrument_id)

        self.clock.set_timer(
            name="convert-stream",
            interval=pd.Timedelta(minutes=self.config.conversion_interval_minutes),
            callback=self._convert_stream,
        )

    def on_trade_tick(self, tick: TradeTick) -> None:
        """
        Actions to be performed when a trade tick is received.

        No manual persistence is performed here — trades auto-flow to the
        `StreamingFeatherWriter` via the kernel's "*" msgbus subscription (REC-07).

        """
        logger.debug("Received %s", tick)

    def _convert_stream(self, event: TimeEvent) -> None:
        """
        Convert streamed feather data for this instance into the `ParquetDataCatalog`
        (REL-01).

        A transient conversion error is logged and swallowed so it does not crash
        the recorder; Pitfall 2 (non-disjoint intervals on mid-day re-conversion) is
        empirically verified in Plan 04.

        """
        try:
            catalog = ParquetDataCatalog(self.config.catalog_path)
            # WHY: subdirectory default is "backtest"; live runs write under
            # live/ (Pitfall 3).
            catalog.convert_stream_to_data(
                instance_id=self.config.instance_id_str,
                data_cls=TradeTick,
                subdirectory="live",
            )
        except Exception:
            logger.exception("Failed to convert stream data to catalog")
