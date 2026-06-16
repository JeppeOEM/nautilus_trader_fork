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
from pathlib import Path

from nautilus_trader.common.config import NautilusConfig
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.model.identifiers import InstrumentId

# Shared, exchange-agnostic config helpers now live in scripts.common_recorder.config
# (DYDX-01). They are re-exported below so existing imports like
# `from scripts.bybit_recorder.config import build_streaming_config` still resolve.
from scripts.common_recorder.config import _resolve_catalog_path
from scripts.common_recorder.config import _validate_positive_thresholds
from scripts.common_recorder.config import build_streaming_config


logger = logging.getLogger(__name__)

# Re-export the shared helpers so back-compat imports from this module keep working.
__all__ = [
    "InstrumentEntry",
    "RecorderConfig",
    "_resolve_catalog_path",
    "_validate_positive_thresholds",
    "build_streaming_config",
    "load_recorder_config",
]

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
    restart_gap_threshold_seconds : PositiveInt, default 60
        On `on_start`, if the gap since the last recorded `ts_init` for an
        instrument exceeds this threshold, the recorder logs a WARNING so
        restart-induced gaps are visible in journald (REL-02 gap visibility, D-06).
    heartbeat_interval_seconds : PositiveInt, default 30
        How often the heartbeat timer fires (REL-03). Each firing logs an INFO
        heartbeat and a WARNING for any stream whose idle time exceeds its
        per-data-type stale threshold.
    max_hot_added_instruments : PositiveInt, default 50
        The maximum number of instruments that may be hot-added over the
        recorder's lifetime before a WARNING is logged (HOT-01 / D-08). This is
        informational only — it NEVER blocks further hot-adds — but surfaces
        runaway config thrash. Validated `> 0` at load like its sibling
        thresholds.
    stale_threshold_default_seconds : PositiveInt, default 90
        The fallback stale threshold (in seconds) for any stream label not
        present in `stale_threshold_seconds` (REL-03).
    stale_threshold_seconds : dict[str, int], default {}
        Per-stream-label stale thresholds (in seconds), e.g. ``{"trade": 90,
        "mark": 30}`` (REL-03).
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
    restart_gap_threshold_seconds: PositiveInt = 60
    heartbeat_interval_seconds: PositiveInt = 30
    max_hot_added_instruments: PositiveInt = 50
    stale_threshold_default_seconds: PositiveInt = 90
    stale_threshold_seconds: dict[str, int] = {}
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

    heartbeat_interval_seconds = recorder_raw.get("heartbeat_interval_seconds", 30)
    stale_threshold_default_seconds = recorder_raw.get("stale_threshold_default_seconds", 90)
    stale_threshold_seconds = recorder_raw.get("stale_threshold_seconds", {})
    restart_gap_threshold_seconds = recorder_raw.get("restart_gap_threshold_seconds", 60)
    max_hot_added_instruments = recorder_raw.get("max_hot_added_instruments", 50)

    # V5 fail-fast (T-3-05): reject any non-positive interval/threshold at load
    # time via the shared validator (same messages as the previous inline blocks).
    _validate_positive_thresholds(
        heartbeat_interval_seconds,
        stale_threshold_default_seconds,
        stale_threshold_seconds,
        restart_gap_threshold_seconds,
        max_hot_added_instruments,
    )

    recorder_cfg = RecorderConfig(
        trader_id=recorder_raw["trader_id"],
        catalog_path=_resolve_catalog_path(recorder_raw["catalog_path"]),
        streaming_path=_resolve_catalog_path(recorder_raw["streaming_path"]),
        conversion_interval_minutes=recorder_raw.get("conversion_interval_minutes", 60),
        rotation_interval_minutes=recorder_raw.get("rotation_interval_minutes", 1440),
        restart_gap_threshold_seconds=restart_gap_threshold_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        max_hot_added_instruments=max_hot_added_instruments,
        stale_threshold_default_seconds=stale_threshold_default_seconds,
        stale_threshold_seconds=stale_threshold_seconds,
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
