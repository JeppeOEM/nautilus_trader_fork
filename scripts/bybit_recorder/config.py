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
import tomllib
from datetime import time
from pathlib import Path

import pandas as pd

from nautilus_trader.common.config import NautilusConfig
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.config import StreamingConfig
from nautilus_trader.persistence.writer import RotationMode


logger = logging.getLogger(__name__)


class InstrumentEntry(NautilusConfig, frozen=True):
    """
    Represent a single configured instrument with its recording parameters.

    Parameters
    ----------
    id : InstrumentId
        The instrument identifier to subscribe to.
    depth : PositiveInt
        The order book depth to request (parsed now per CONF-02, not yet consumed
        in Phase 1).
    bar_intervals : list[str]
        The bar interval strings to subscribe to (parsed now per CONF-02, not yet
        consumed in Phase 1).

    """

    id: InstrumentId
    depth: PositiveInt
    bar_intervals: list[str]


class RecorderConfig(NautilusConfig, frozen=True):
    """
    Represent the parsed `[recorder]` table plus the resolved instrument list.

    Parameters
    ----------
    trader_id : str
        The trader identifier for the recorder node.
    catalog_path : str
        The path to the `ParquetDataCatalog` root used for conversion.
    streaming_path : str
        The path to the streaming feather root used by `StreamingConfig`.
    conversion_interval_minutes : PositiveInt, default 60
        How often the recorder converts streamed feather files into the catalog
        (D-02 — configurable, not hardcoded).
    environment : str, default 'mainnet'
        The Bybit environment to connect to.
    instruments : list[InstrumentEntry]
        The resolved instrument entries, linear instruments first then spot.

    """

    trader_id: str
    catalog_path: str
    streaming_path: str
    conversion_interval_minutes: PositiveInt = 60
    environment: str = "mainnet"
    instruments: list[InstrumentEntry] = []

    @property
    def instrument_ids(self) -> list[InstrumentId]:
        """
        Return the resolved instrument identifiers in linear-then-spot order.

        Returns
        -------
        list[InstrumentId]

        """
        return [entry.id for entry in self.instruments]


def load_recorder_config(path: str | Path) -> tuple[RecorderConfig, list[InstrumentId]]:
    """
    Load and parse a recorder TOML configuration file.

    Parameters
    ----------
    path : str | Path
        The path to the `recorder.toml` configuration file.

    Returns
    -------
    tuple[RecorderConfig, list[InstrumentId]]
        The parsed recorder configuration and the resolved instrument identifier
        list, in linear-then-spot order.

    Raises
    ------
    KeyError
        If the `[recorder]` table is missing.
    ValueError
        If any configured instrument `id` is not a valid `InstrumentId` (V5
        input-validation boundary).

    """
    # tomllib.load requires the file to be opened in binary mode.
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    recorder_raw = raw["recorder"]

    instruments_raw = raw.get("instruments", {})
    linear_raw: list[dict] = instruments_raw.get("linear", [])
    spot_raw: list[dict] = instruments_raw.get("spot", [])

    instruments: list[InstrumentEntry] = []
    for entry_raw in (*linear_raw, *spot_raw):
        # InstrumentId.from_str is the V5 boundary: raises ValueError on a
        # malformed instrument id, intentionally failing fast (T-01-03).
        instruments.append(
            InstrumentEntry(
                id=InstrumentId.from_str(entry_raw["id"]),
                depth=entry_raw["depth"],
                bar_intervals=entry_raw["bar_intervals"],
            ),
        )

    recorder_cfg = RecorderConfig(
        trader_id=recorder_raw["trader_id"],
        catalog_path=recorder_raw["catalog_path"],
        streaming_path=recorder_raw["streaming_path"],
        conversion_interval_minutes=recorder_raw.get("conversion_interval_minutes", 60),
        environment=recorder_raw.get("environment", "mainnet"),
        instruments=instruments,
    )

    # Log only the instrument count - never credentials (D-09 / Security V7).
    logger.info(
        "Loaded recorder config: %d instrument(s) from %s",
        len(recorder_cfg.instruments),
        path,
    )

    return recorder_cfg, recorder_cfg.instrument_ids


def build_streaming_config(recorder_cfg: RecorderConfig) -> StreamingConfig:
    """
    Build the `StreamingConfig` used by the recorder's `TradingNode`.

    Parameters
    ----------
    recorder_cfg : RecorderConfig
        The parsed recorder configuration.

    Returns
    -------
    StreamingConfig
        A daily-rotating streaming configuration with `include_types=[TradeTick]`
        (REC-07, Phase 1 records only trades).

    """
    return StreamingConfig(
        # `catalog_path` is set to the recorder's single shared streaming/catalog
        # root (Pitfall 5 / A4) so the conversion `ParquetDataCatalog` later finds
        # the feather files written under `{root}/live/{instance_id}/`.
        catalog_path=recorder_cfg.streaming_path,
        fs_protocol="file",
        # WHY: day-partitioning is a consequence of daily feather rotation, not a
        # free catalog property (Pitfall 1).
        rotation_mode=RotationMode.SCHEDULED_DATES,
        rotation_interval=pd.Timedelta(days=1),
        rotation_time=time(0, 0, 0),
        rotation_timezone="UTC",
        # Phase 1 records only trades; widened in Phase 2.
        include_types=[TradeTick],
    )
