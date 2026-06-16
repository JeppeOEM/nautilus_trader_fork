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
dYdX recorder config parsing.

dYdX's config shape differs from Bybit's: it is a FLAT perpetual list (dYdX v4
has no spot market) with full-depth L2 order books (no `depth` knob) and a bar
interval whitelist instead of a depth whitelist. Path-resolution, streaming, and
threshold-validation helpers are reused from ``scripts.common_recorder.config``.
"""

import logging
import tomllib
from pathlib import Path

from nautilus_trader.common.config import NautilusConfig
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.model.identifiers import InstrumentId

# Shared, exchange-agnostic config helpers live in scripts.common_recorder.config
# (DYDX-01). `build_streaming_config` is re-exported below so the Plan 03
# recorder.py can `from scripts.dydx_recorder.config import build_streaming_config`
# symmetrically with the Bybit recorder.
from scripts.common_recorder.config import _resolve_catalog_path
from scripts.common_recorder.config import _validate_positive_thresholds
from scripts.common_recorder.config import build_streaming_config


logger = logging.getLogger(__name__)

__all__ = [
    "_DYDX_VALID_INTERVALS",
    "DydxInstrumentEntry",
    "DydxRecorderConfig",
    "_map_network",
    "build_streaming_config",
    "load_dydx_recorder_config",
]

# DYDX-06 fail-fast whitelist: the dYdX adapter only supports this discrete set of
# candle resolutions. An interval off this set silently yields no bar data (or
# raises from `from_bar_spec` at subscribe time), so reject it at config load.
# Source: supported set from crates/adapters/dydx/src/common/enums.rs from_bar_spec
# (1/5/15/30-MINUTE, 1/4-HOUR, 1-DAY). NO depth table — dYdX is full-depth L2.
_DYDX_VALID_INTERVALS = {
    "1-MINUTE",
    "5-MINUTE",
    "15-MINUTE",
    "30-MINUTE",
    "1-HOUR",
    "4-HOUR",
    "1-DAY",
}


class DydxInstrumentEntry(NautilusConfig, frozen=True):
    """
    Represent a single configured dYdX perpetual with its recording parameters.

    Unlike the Bybit ``InstrumentEntry`` there is no ``depth`` field (dYdX is
    full-depth L2) and no ``product_type`` axis (dYdX v4 has no spot market — all
    instruments are perpetual derivatives).

    Parameters
    ----------
    id : InstrumentId
        The dYdX perpetual instrument identifier to subscribe to.
    bar_intervals : list[str]
        The bar interval strings to subscribe to, each validated against
        ``_DYDX_VALID_INTERVALS`` at load (DYDX-06).

    """

    id: InstrumentId
    bar_intervals: list[str]


class DydxRecorderConfig(NautilusConfig, frozen=True):
    """
    Represent the parsed dYdX `[recorder]` table plus the resolved perp list.

    Mirrors the Bybit ``RecorderConfig`` fields except the depth-related ones —
    dYdX has no order book depth knob and no linear/spot product split.

    Parameters
    ----------
    trader_id : str
        The trader identifier for the recorder node.
    catalog_path : str
        The path to the `ParquetDataCatalog` root used for conversion (shared
        with the Bybit catalog root — venue suffix keeps ids from colliding).
    streaming_path : str
        The path to the streaming feather root used by `StreamingConfig`.
    conversion_interval_minutes : PositiveInt, default 60
        How often the recorder converts streamed feather files into the catalog.
    rotation_interval_minutes : PositiveInt, default 1440
        How often the streaming feather writers rotate to a new file (1 day for
        daily partitioning; lower values are useful for testing).
    restart_gap_threshold_seconds : PositiveInt, default 60
        On `on_start`, the gap (seconds) since the last recorded `ts_init` above
        which a restart-gap WARNING is logged.
    heartbeat_interval_seconds : PositiveInt, default 30
        How often the heartbeat timer fires.
    max_hot_added_instruments : PositiveInt, default 50
        The lifetime hot-add WARNING threshold (informational, never blocks).
    stale_threshold_default_seconds : PositiveInt, default 90
        The fallback stale threshold for any stream label not present in
        `stale_threshold_seconds`.
    stale_threshold_seconds : dict[str, int], default {}
        Per-stream-label stale thresholds. For dYdX, `quote` is typically
        relaxed (synthesized quotes fire only on top-of-book change) and
        `deltas` is tight (book updates often; silence is a real fault) — DYDX-05.
    environment : str, default 'mainnet'
        The dYdX network to connect to (mapped to `DydxNetwork` via
        ``_map_network``, case-insensitive).
    instruments : list[DydxInstrumentEntry]
        The resolved perpetual instrument entries.

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
    instruments: list[DydxInstrumentEntry] = []

    @property
    def instrument_ids(self) -> list[InstrumentId]:
        """
        Return the resolved perpetual instrument identifiers.

        Returns
        -------
        list[InstrumentId]

        """
        return [entry.id for entry in self.instruments]


def _map_network(env: str) -> DydxNetwork:
    """
    Map an environment string to a `DydxNetwork`, case-insensitively.

    `DydxNetwork.MAINNET.name == "mainnet"` (lowercase — the pyo3 enum reflects
    the Rust serde rename), so the comparison normalizes to lowercase. Anything
    other than ``"mainnet"`` maps to ``TESTNET`` (default-to-mainnet is handled
    by the config default, matching the 24/7 archival intent).

    Parameters
    ----------
    env : str
        The environment string from the config (e.g. ``"mainnet"``, ``"MAINNET"``).

    Returns
    -------
    DydxNetwork

    """
    return DydxNetwork.MAINNET if env.lower() == "mainnet" else DydxNetwork.TESTNET


def load_dydx_recorder_config(
    path: str | Path,
) -> tuple[DydxRecorderConfig, list[InstrumentId]]:
    """
    Load and parse a dYdX recorder TOML configuration file.

    Reads a FLAT ``[[instruments]]`` array (no linear/spot split), validates each
    instrument id and every configured bar interval, and rejects non-positive
    thresholds — all fail-fast at load, before the node starts.

    Parameters
    ----------
    path : str | Path
        The path to the dYdX `recorder.toml` configuration file.

    Returns
    -------
    tuple[DydxRecorderConfig, list[InstrumentId]]
        The parsed recorder configuration and the resolved instrument identifiers.

    Raises
    ------
    KeyError
        If the `[recorder]` table is missing.
    ValueError
        If any instrument `id` is malformed (V5 boundary), any configured bar
        interval is not in `_DYDX_VALID_INTERVALS` (DYDX-06), or any threshold is
        non-positive (T-07-05).

    """
    # tomllib.load requires the file to be opened in binary mode.
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    recorder_raw = raw["recorder"]

    # FLAT instrument array (dYdX has no linear/spot split) — NOT nested under a
    # product-type key as Bybit does.
    instruments_raw: list[dict] = raw.get("instruments", [])

    instruments: list[DydxInstrumentEntry] = []
    for entry_raw in instruments_raw:
        # InstrumentId.from_str is the V5 boundary: raises ValueError on a
        # malformed instrument id, intentionally failing fast (T-07-04).
        instrument_id = InstrumentId.from_str(entry_raw["id"])

        bar_intervals = entry_raw["bar_intervals"]
        for interval in bar_intervals:
            # DYDX-06 fail-fast (T-07-03): an unsupported resolution would crash
            # the node at subscribe time or silently yield no data, so reject it
            # here at config load with the supported set in the message.
            if interval not in _DYDX_VALID_INTERVALS:
                raise ValueError(
                    f"Invalid dYdX bar interval {interval!r} for {instrument_id}: "
                    f"must be one of {sorted(_DYDX_VALID_INTERVALS)}",
                )

        instruments.append(
            DydxInstrumentEntry(id=instrument_id, bar_intervals=bar_intervals),
        )

    heartbeat_interval_seconds = recorder_raw.get("heartbeat_interval_seconds", 30)
    stale_threshold_default_seconds = recorder_raw.get("stale_threshold_default_seconds", 90)
    stale_threshold_seconds = recorder_raw.get("stale_threshold_seconds", {})
    restart_gap_threshold_seconds = recorder_raw.get("restart_gap_threshold_seconds", 60)
    max_hot_added_instruments = recorder_raw.get("max_hot_added_instruments", 50)

    # V5 fail-fast (T-07-05): reject any non-positive interval/threshold at load
    # time via the shared validator extracted in Plan 01.
    _validate_positive_thresholds(
        heartbeat_interval_seconds,
        stale_threshold_default_seconds,
        stale_threshold_seconds,
        restart_gap_threshold_seconds,
        max_hot_added_instruments,
    )

    recorder_cfg = DydxRecorderConfig(
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

    # Log only the instrument count - never credentials (Security V7). The dYdX
    # data client needs no credentials at all (wallet/key are execution-only).
    logger.info(
        "Loaded dYdX recorder config: %d instrument(s) from %s",
        len(recorder_cfg.instruments),
        path,
    )

    return recorder_cfg, recorder_cfg.instrument_ids
