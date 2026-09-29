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
The received leg of the catalog tool (Story 31.7): what a `BacktestNode` actually hands an actor
for each plan instrument over the day -- `TradeTick` and `DydxSecondSnapshot` loaded by
`BacktestDataConfig` (the snapshot type by string path, `client_id` = the venue), exactly as a
research backtest reads them (NAUT-03). Subject side (`verification.subject`): Nautilus's backtest
reader is the code under test.

`RecordingActor` is loaded by `ImportableActorConfig` string path; each engine is kept
(`dispose_on_completion=False`) so the actor is read back from `engine.trader` after the run --
no module-level state. The actor re-encodes what it receives with the catalog's own serializer
(`nautilus_reads.encode_table`) in chunks of `chunk_rows` objects: the rows with `ts_init` in the day
make the parity digest, and every snapshot with `ts_event` in the day keeps its trade columns for
the candle fold (a snapshot of 23:59:59.5 sampled at 00:00:02.5 is in the fold, not the digest).
Logging is bypassed: the Rust logger installs once per process, so a run that initialised it
would abort the next (`archive/tests/conftest.py`'s `nautilus_log_guard`).

Known limit (streaming): the pinned Nautilus cannot stream a Python custom data type through
`BacktestRunConfig.chunk_size` (`DydxSecondSnapshot` "is not registered with an Arrow schema
containing ts_init", the same limit as `research/application/backtest_runner.py`), so the day is
read one-shot per window instead: one `BacktestNode` per `backtest_windows` window (the margin
before the day, each hour, the margin after), one run config per plan instrument in it, each node
disposed before the next. Memory: the node builds every plan instrument's engine first and runs
the configs one after another, clearing each engine's data after its run, so the process peak is
up to one hour of every plan instrument's objects (freed memory is not always returned to the OS)
plus the engines themselves, and up to 86,400 trade-column tuples per instrument: measured 1.56 GB
peak RSS for the whole Bybit run (four instruments; the busiest, ETHUSDT linear, ~150k trades an
hour) on the Story 31.2 soak.
Upgrade path: one node with `chunk_size` once the custom type streams through the Rust backend.
"""

from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from kernel.second_snapshot import DydxSecondSnapshot

from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.node import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.common.config import ImportableActorConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import CustomData
from nautilus_trader.model.data import DataType
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from verification.domain.catalog_check import SNAPSHOT_TYPE
from verification.domain.catalog_check import TRADE_TYPE
from verification.domain.catalog_check import Digest
from verification.domain.catalog_check import Leg
from verification.domain.catalog_check import Received
from verification.domain.catalog_check import TradeRow
from verification.domain.catalog_check import backtest_windows
from verification.domain.catalog_check import is_day
from verification.subject.nautilus_reads import ENCODE_CHUNK
from verification.subject.nautilus_reads import encode_table
from verification.subject.nautilus_reads import projected


_SNAPSHOT_PATH = "kernel.second_snapshot:DydxSecondSnapshot"
_ACTOR_PATH = "verification.subject.backtest_probe:RecordingActor"
_CONFIG_PATH = "verification.subject.backtest_probe:RecordingActorConfig"


class RecordingActorConfig(ActorConfig, frozen=True):
    """The instrument to record, its client id, the day and each parity type's stored columns."""

    instrument_id: str
    client_id: str
    day_start_ns: int
    trade_columns: tuple[str, ...]
    snapshot_columns: tuple[str, ...]
    chunk_rows: int = ENCODE_CHUNK


class _Chunks:
    """One type's buffered objects and what their flushed chunks added up to."""

    def __init__(self, cls: type, columns: Sequence[str]) -> None:
        self.cls = cls
        self.columns = tuple(columns)
        self.buffer: list[Any] = []
        self.digest = Digest()
        self.missing: set[str] = set()


class RecordingActor(Actor):
    """
    Records every `TradeTick` and snapshot of one instrument the engine dispatches. Invariant: an
    object reaches the digest only through the catalog serializer's re-encoding, and every buffered
    object is flushed before `received` answers.
    """

    def __init__(self, config: RecordingActorConfig) -> None:
        super().__init__(config)
        self._iid = InstrumentId.from_str(config.instrument_id)
        self._client = ClientId(config.client_id)
        self._day = config.day_start_ns
        self._limit = config.chunk_rows
        self._trades = _Chunks(TradeTick, config.trade_columns)
        self._snapshots = _Chunks(DydxSecondSnapshot, config.snapshot_columns)
        self._rows: list[TradeRow] = []

    def on_start(self) -> None:
        self.subscribe_trade_ticks(self._iid)
        self.subscribe_data(
            DataType(DydxSecondSnapshot), client_id=self._client, instrument_id=self._iid
        )

    def on_trade_tick(self, tick: TradeTick) -> None:
        self._take(self._trades, tick)

    def on_data(self, data: Any) -> None:
        inner = data.data if isinstance(data, CustomData) else data
        if isinstance(inner, DydxSecondSnapshot):
            self._take(self._snapshots, inner)

    def _take(self, chunks: _Chunks, item: Any) -> None:
        chunks.buffer.append(item)
        if len(chunks.buffer) >= self._limit:
            self._flush(chunks)

    def _flush(self, chunks: _Chunks) -> None:
        objects, chunks.buffer = chunks.buffer, []
        if not objects:
            return
        table = encode_table(objects, chunks.cls)
        rows, missing = projected(table, chunks.columns)
        chunks.missing.update(missing)
        chunks.digest += Digest.of(
            row for row, o in zip(rows, objects, strict=True) if is_day(o.ts_init, self._day)
        )
        if chunks.cls is DydxSecondSnapshot:
            lacking = [c for c in TradeRow.columns() if c not in table.column_names]
            if lacking:  # the fold cannot read them: the leg names them (`read_mismatch`)
                chunks.missing.update(lacking)
                return
            trade = table.select(TradeRow.columns()).to_pylist()
            self._rows += [
                TradeRow.of(row)
                for row, o in zip(trade, objects, strict=True)
                if is_day(o.ts_event, self._day)
            ]

    def received(self) -> Received:
        """Flush what is buffered and return this run's legs and trade rows."""
        self._flush(self._trades)
        self._flush(self._snapshots)
        legs = {
            TRADE_TYPE: Leg(self._trades.digest, tuple(sorted(self._trades.missing))),
            SNAPSHOT_TYPE: Leg(self._snapshots.digest, tuple(sorted(self._snapshots.missing))),
        }
        return Received(legs, tuple(self._rows))


def _data_configs(catalog: str, iid: str, window: tuple[int, int]) -> list[BacktestDataConfig]:
    start, end = window
    venue = str(InstrumentId.from_str(iid).venue)
    return [
        BacktestDataConfig(
            catalog_path=catalog,
            data_cls=TradeTick,
            instrument_id=iid,
            start_time=start,
            end_time=end,
        ),
        BacktestDataConfig(
            catalog_path=catalog,
            data_cls=_SNAPSHOT_PATH,
            instrument_id=iid,
            client_id=venue,  # a custom type needs a (bookkeeping) client id
            start_time=start,
            end_time=end,
        ),
    ]


def _run_config(
    catalog: str,
    iid: str,
    currency: str,
    window: tuple[int, int],
    actor: Mapping[str, object],
) -> BacktestRunConfig:
    venue = str(InstrumentId.from_str(iid).venue)
    return BacktestRunConfig(
        engine=BacktestEngineConfig(
            logging=LoggingConfig(bypass_logging=True),
            actors=[
                ImportableActorConfig(
                    actor_path=_ACTOR_PATH, config_path=_CONFIG_PATH, config=dict(actor)
                )
            ],
        ),
        venues=[
            BacktestVenueConfig(
                name=venue,
                oms_type="NETTING",
                account_type="MARGIN",
                base_currency=currency,
                starting_balances=[f"10000 {currency}"],
            )
        ],
        data=_data_configs(catalog, iid, window),
        raise_exception=True,
        dispose_on_completion=False,
    )


def _actor_config(shared: Mapping[str, object], iid: str) -> dict[str, object]:
    venue = str(InstrumentId.from_str(iid).venue)
    return {**shared, "instrument_id": iid, "client_id": venue}


class BacktestReads:
    """
    The received leg over one catalog root. Invariant: each window's node is disposed before the
    next is built, so at most one window's engines exist at a time.
    """

    def __init__(self, catalog: Path) -> None:
        self._catalog = str(catalog)

    def _window(
        self,
        currencies: Mapping[str, str],
        window: tuple[int, int],
        actor: Mapping[str, object],
    ) -> dict[str, Received]:
        configs = {
            iid: _run_config(self._catalog, iid, currency, window, _actor_config(actor, iid))
            for iid, currency in currencies.items()
        }
        node = BacktestNode(configs=list(configs.values()))
        try:
            node.run()
            return {iid: self._actor(node, config).received() for iid, config in configs.items()}
        finally:
            node.dispose()

    @staticmethod
    def _actor(node: BacktestNode, config: BacktestRunConfig) -> RecordingActor:
        engine = node.get_engine(config.id)
        if engine is None:
            raise RuntimeError(f"no engine for run config {config.id}")
        (actor,) = engine.trader.actors()
        if not isinstance(actor, RecordingActor):
            raise TypeError(f"run {config.id}: unexpected actor {actor!r}")
        return actor

    def receive(
        self,
        currencies: Mapping[str, str],
        day_start_ns: int,
        columns: Mapping[str, Sequence[str]],
    ) -> dict[str, Received]:
        """Every plan instrument's received legs and snapshot trade rows over the day's windows."""
        actor = {
            "day_start_ns": day_start_ns,
            "trade_columns": tuple(columns[TRADE_TYPE]),
            "snapshot_columns": tuple(columns[SNAPSHOT_TYPE]),
        }
        total = {iid: Received.empty() for iid in currencies}
        for window in backtest_windows(day_start_ns):
            for iid, part in self._window(currencies, window, actor).items():
                total[iid] += part
        return total
