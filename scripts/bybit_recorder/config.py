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
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.config import StreamingConfig
from nautilus_trader.persistence.writer import RotationMode


logger = logging.getLogger(__name__)

# Anchor for resolving relative `catalog_path` / `streaming_path` values so the
# catalog always lands in the same place regardless of the process's cwd
# (parents[2] from scripts/bybit_recorder/config.py is the repo root).
_REPO_ROOT = Path(__file__).resolve().parents[2]

# D-03 (literal): SPOT order book depth is capped at 50 by the venue.
_SPOT_MAX_DEPTH = 50

# Defense-in-depth (RESEARCH Pitfall 3 / Open Question 2): the Bybit adapter does
# not validate LINEAR depth and a value off this discrete venue set silently
# yields no data, so reject anything outside it at config-load time.
_LINEAR_VALID_DEPTHS = {1, 50, 200, 1000}


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
    product_type : str
        Either ``"linear"`` or ``"spot"`` -- drives per-product-type order book
        depth validation (D-03 / Pitfall 3) and linear-only mark/index price
        subscription gating (D-04).

    """

    id: InstrumentId
    depth: PositiveInt
    bar_intervals: list[str]
    product_type: str


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
    rotation_interval_minutes : PositiveInt, default 1440
        How often the streaming feather writers rotate to a new file. Only a
        ROTATED-OUT (no longer the most-recently-created) feather file is
        guaranteed to never receive new rows, so `_convert_stream` only converts
        such files (REL-01/Pitfall 2). Defaults to 1440 (1 day) for daily
        partitioning (REL-02); lower values (e.g. 1) are useful for testing the
        conversion path without waiting a full day.
    environment : str, default 'mainnet'
        The Bybit environment to connect to.
    instruments : list[InstrumentEntry]
        The resolved instrument entries, linear instruments first then spot.

    """

    trader_id: str
    catalog_path: str
    streaming_path: str
    conversion_interval_minutes: PositiveInt = 60
    rotation_interval_minutes: PositiveInt = 1440
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

    @property
    def linear_instrument_ids(self) -> list[InstrumentId]:
        """
        Return the resolved LINEAR instrument identifiers (D-04 gating).

        Returns
        -------
        list[InstrumentId]

        """
        return [entry.id for entry in self.instruments if entry.product_type == "linear"]


def _resolve_catalog_path(raw_path: str) -> str:
    """
    Resolve a configured catalog/streaming path against the repo root.

    A relative path in `recorder.toml` (e.g. ``"catalog"``) must always resolve
    to the same on-disk location regardless of the cwd the recorder is launched
    from (e.g. systemd `WorkingDirectory`, repo root, or `scripts/bybit_recorder/`).
    Absolute paths are returned unchanged.
    """
    path = Path(raw_path)
    if path.is_absolute():
        return str(path)

    return str(_REPO_ROOT / path)


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
    for entry_raw in linear_raw:
        # InstrumentId.from_str is the V5 boundary: raises ValueError on a
        # malformed instrument id, intentionally failing fast (T-01-03).
        instrument_id = InstrumentId.from_str(entry_raw["id"])
        depth = entry_raw["depth"]

        # Defense-in-depth (RESEARCH Pitfall 3 / Open Question 2): the Bybit
        # adapter does not validate LINEAR depth and a value off this discrete
        # venue set silently yields no data, so reject it at config-load time.
        if depth not in _LINEAR_VALID_DEPTHS:
            raise ValueError(
                f"Invalid linear order book depth {depth} for {instrument_id}: "
                f"must be one of {sorted(_LINEAR_VALID_DEPTHS)}",
            )

        instruments.append(
            InstrumentEntry(
                id=instrument_id,
                depth=depth,
                bar_intervals=entry_raw["bar_intervals"],
                product_type="linear",
            ),
        )

    for entry_raw in spot_raw:
        # InstrumentId.from_str is the V5 boundary: raises ValueError on a
        # malformed instrument id, intentionally failing fast (T-01-03).
        instrument_id = InstrumentId.from_str(entry_raw["id"])
        depth = entry_raw["depth"]

        # D-03 (literal): spot order book depth is capped at 50 by the venue.
        if depth > _SPOT_MAX_DEPTH:
            raise ValueError(
                f"Invalid spot order book depth {depth} for {instrument_id}: "
                f"spot depth is capped at {_SPOT_MAX_DEPTH} by the venue (D-03)",
            )

        instruments.append(
            InstrumentEntry(
                id=instrument_id,
                depth=depth,
                bar_intervals=entry_raw["bar_intervals"],
                product_type="spot",
            ),
        )

    recorder_cfg = RecorderConfig(
        trader_id=recorder_raw["trader_id"],
        catalog_path=_resolve_catalog_path(recorder_raw["catalog_path"]),
        streaming_path=_resolve_catalog_path(recorder_raw["streaming_path"]),
        conversion_interval_minutes=recorder_raw.get("conversion_interval_minutes", 60),
        rotation_interval_minutes=recorder_raw.get("rotation_interval_minutes", 1440),
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
        A daily-rotating streaming configuration with `include_types` covering
        all six auto-written types recorded by Phase 2 (REC-02 to REC-04, REC-06).

    """
    return StreamingConfig(
        # `catalog_path` is set to the recorder's single shared streaming/catalog
        # root (Pitfall 5 / A4) so the conversion `ParquetDataCatalog` later finds
        # the feather files written under `{root}/live/{instance_id}/`.
        catalog_path=recorder_cfg.streaming_path,
        fs_protocol="file",
        # WHY: day-partitioning is a consequence of daily feather rotation, not a
        # free catalog property (Pitfall 1). `rotation_interval_minutes` defaults
        # to 1440 (1 day) but is configurable for testing the conversion path
        # without waiting a full day.
        rotation_mode=RotationMode.SCHEDULED_DATES,
        rotation_interval=pd.Timedelta(minutes=recorder_cfg.rotation_interval_minutes),
        rotation_time=time(0, 0, 0),
        rotation_timezone="UTC",
        # FundingRateUpdate is intentionally OMITTED here: it is deduped on
        # value-change by the strategy and persisted via a separate writer
        # (D-01 / Plan 02), not auto-written through this passthrough.
        # NOTE: the live DataEngine always publishes the plural `OrderBookDeltas`
        # container on the message bus (even for a single delta with
        # buffer_deltas=False) -- StreamingFeatherWriter.write() filters on
        # `obj.__class__` BEFORE any OrderBookDeltas->OrderBookDelta schema
        # mapping, so the singular `OrderBookDelta` here would silently drop
        # all order-book data.
        include_types=[
            TradeTick,
            QuoteTick,
            OrderBookDeltas,
            Bar,
            MarkPriceUpdate,
            IndexPriceUpdate,
        ],
    )
