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
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from nautilus_trader.persistence.writer import StreamingFeatherWriter
from nautilus_trader.trading.strategy import Strategy


logger = logging.getLogger(__name__)

# The six auto-written native types streamed by the recorder (REC-02..REC-04,
# REC-06). FundingRateUpdate is intentionally absent: it is deduped on
# value-change and persisted via a separate writer in Plan 02 (D-01 / Pitfall 1).
_RECORDED_TYPES = [
    TradeTick,
    QuoteTick,
    OrderBookDeltas,
    Bar,
    MarkPriceUpdate,
    IndexPriceUpdate,
]

# Sub-directory (within the feather writer path) for the strategy-owned funding
# writer — kept separate from the kernel "*" writer's root files (Pitfall 1).
_FUNDING_WRITER_SUBDIR = "funding"


class RecorderStrategyConfig(StrategyConfig, frozen=True):
    """
    Configuration for ``RecorderStrategy`` instances.

    Parameters
    ----------
    instrument_ids : list[InstrumentId]
        The configured instrument identifiers to validate and subscribe to.
    linear_instrument_ids : list[InstrumentId]
        The LINEAR-only instrument identifiers — mark/index price subscriptions
        are gated to these (D-04); spot instruments never receive them.
    instrument_depths : dict[InstrumentId, int]
        The per-instrument order book depth used for `subscribe_order_book_deltas`.
    instrument_bar_intervals : dict[InstrumentId, list[str]]
        The per-instrument bar interval strings (e.g. ``["1-MINUTE"]``) used to
        build `BarType` values for `subscribe_bars`.
    catalog_path : str
        The path to the `ParquetDataCatalog` root used for conversion. Must equal
        the streaming root used by `StreamingConfig` (Pitfall 5 / A4).
    instance_id_str : str
        The fixed UUID4 string shared with the `TradingNode` instance_id (D-03).
    conversion_interval_minutes : PositiveInt, default 60
        How often the in-process conversion timer fires (REL-01/D-01).

    """

    instrument_ids: list[InstrumentId]
    linear_instrument_ids: list[InstrumentId]
    instrument_depths: dict[InstrumentId, int]
    instrument_bar_intervals: dict[InstrumentId, list[str]]
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

        # Per-instrument last-seen funding rate, used by `on_funding_rate` to
        # drop unchanged values (D-01 dedup gate, Pitfall 1). Keyed by
        # InstrumentId so dedup is per-instrument (T-2-02).
        self._last_funding_rate: dict[InstrumentId, object] = {}

        # Strategy-owned StreamingFeatherWriter for deduped FundingRateUpdate
        # rows (Task 1 resolved path) -- lazily created on first persisted
        # funding rate so `self.cache`/`self.clock` are available (set during
        # `register`, not `__init__`).
        self._funding_writer: StreamingFeatherWriter | None = None

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
            self.subscribe_quote_ticks(instrument_id)
            self.subscribe_order_book_deltas(
                instrument_id,
                book_type=BookType.L2_MBP,
                depth=self.config.instrument_depths[instrument_id],
            )
            for interval in self.config.instrument_bar_intervals[instrument_id]:
                # Venue-native EXTERNAL kline stream (D-02) — NOT derived from
                # trades. LAST price type, EXTERNAL aggregation source.
                self.subscribe_bars(
                    BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL"),
                )

        # D-04 linear-only gating: mark/index prices exist for derivatives only.
        # The Bybit adapter merely warns + early-returns for spot, so never iterate
        # the full instrument list here (Pitfall 4).
        for instrument_id in self.config.linear_instrument_ids:
            self.subscribe_mark_prices(instrument_id)
            self.subscribe_index_prices(instrument_id)
            self.subscribe_funding_rates(instrument_id)

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

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """
        Actions to be performed when a quote tick is received.

        No manual persistence — quotes auto-flow to the `StreamingFeatherWriter`
        via the kernel's "*" msgbus subscription (REC-02 / REC-07).

        """
        logger.debug("Received %s", tick)

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        """
        Actions to be performed when order book deltas are received.

        No manual persistence — deltas auto-flow to the `StreamingFeatherWriter`
        via the kernel's "*" msgbus subscription (REC-03 / REC-07).

        """
        logger.debug("Received %s", deltas)

    def on_bar(self, bar: Bar) -> None:
        """
        Actions to be performed when a bar is received.

        No manual persistence — bars auto-flow to the `StreamingFeatherWriter`
        via the kernel's "*" msgbus subscription (REC-04 / REC-07).

        """
        logger.debug("Received %s", bar)

    def on_mark_price(self, mark_price: MarkPriceUpdate) -> None:
        """
        Actions to be performed when a mark price update is received.

        No manual persistence — mark prices auto-flow to the
        `StreamingFeatherWriter` via the kernel's "*" msgbus subscription
        (REC-06 / REC-07).

        """
        logger.debug("Received %s", mark_price)

    def on_index_price(self, index_price: IndexPriceUpdate) -> None:
        """
        Actions to be performed when an index price update is received.

        No manual persistence — index prices auto-flow to the
        `StreamingFeatherWriter` via the kernel's "*" msgbus subscription
        (REC-06 / REC-07).

        """
        logger.debug("Received %s", index_price)

    def on_funding_rate(self, funding_rate: FundingRateUpdate) -> None:
        """
        Actions to be performed when a funding rate update is received.

        Drops the update if the rate is unchanged from the last persisted value
        for this instrument (D-01 dedup gate, T-2-02) -- the live ~100ms ticker
        pushes the same rate repeatedly between funding intervals. Only a
        value-change is persisted via the strategy-owned funding writer
        (Task 1 resolved path).

        """
        last_rate = self._last_funding_rate.get(funding_rate.instrument_id)
        if last_rate is not None and last_rate == funding_rate.rate:
            logger.debug("Dropping unchanged funding rate for %s", funding_rate.instrument_id)
            return

        self._last_funding_rate[funding_rate.instrument_id] = funding_rate.rate
        self._persist_funding_rate(funding_rate)

    def _persist_funding_rate(self, funding_rate: FundingRateUpdate) -> None:
        """
        Persist a deduped `FundingRateUpdate` via the strategy-owned
        `StreamingFeatherWriter` (Task 1 resolved path).

        This writer is SEPARATE from the kernel's "*" writer, which deliberately
        EXCLUDES `FundingRateUpdate` from `include_types` (D-01 / Pitfall 1) to
        avoid flooding the catalog with the ~100ms funding ticker. Because
        `class_to_filename(FundingRateUpdate)` resolves to the native
        `funding_rate_update` table, the rows written here convert and read back
        natively via `catalog.funding_rates(...)` exactly like the kernel-written
        types.

        """
        if self._funding_writer is None:
            self._funding_writer = StreamingFeatherWriter(
                path=f"{self.config.catalog_path}/live/{self.config.instance_id_str}",
                cache=self.cache,
                clock=self.clock,
                fs_protocol="file",
                include_types=[FundingRateUpdate],
            )

        self._funding_writer.write(funding_rate)
        self._funding_writer.flush()

    def _convert_stream(self, event: TimeEvent) -> None:
        """
        Convert streamed feather data for this instance into the `ParquetDataCatalog`
        (REL-01).

        A transient conversion error is logged and swallowed PER TYPE so it does not
        crash the recorder nor block the remaining types; Pitfall 2 (non-disjoint
        intervals on mid-day re-conversion) is empirically verified in Plan 04.

        """
        catalog = ParquetDataCatalog(self.config.catalog_path)

        # Flush the strategy-owned funding writer before conversion so any
        # deduped funding rows persisted since the last tick are visible to
        # convert_stream_to_data (the kernel "*" writer is flushed separately
        # by the framework).
        if self._funding_writer is not None:
            self._funding_writer.flush()

        for data_cls in [*_RECORDED_TYPES, FundingRateUpdate]:
            try:
                # WHY: subdirectory default is "backtest"; live runs write under
                # live/ (Pitfall 3). Per-type try/except so one type's transient
                # error does not block the others (A2 / Pitfall 2).
                catalog.convert_stream_to_data(
                    instance_id=self.config.instance_id_str,
                    data_cls=data_cls,
                    subdirectory="live",
                )
            except Exception:
                logger.exception(
                    "Failed to convert %s stream to catalog",
                    data_cls.__name__,
                )
